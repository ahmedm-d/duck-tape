import io
import zipfile
from pathlib import Path

import pytest

from backend.repository import import_zip, index_repository, validate_public_url
from backend.retrieval import retrieve, answer


def archive(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', 'C:/escape.py', 'a/../../escape', 'a\\..\\escape'])
def test_archive_boundary(tmp_path, name):
    with pytest.raises(ValueError):
        import_zip(archive({name: 'x'}), tmp_path / 'repo')
    assert not (tmp_path / 'escape.py').exists()


def test_archive_symlink(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        info = zipfile.ZipInfo('link')
        info.external_attr = 0o120777 << 16
        z.writestr(info, '/etc/passwd')
    with pytest.raises(ValueError):
        import_zip(buf.getvalue(), tmp_path / 'repo')


@pytest.mark.parametrize('url', ['http://example.com', 'https://localhost/x', 'https://127.0.0.1/x', 'https://user:pass@example.com/x', 'https://[::1]/'])
def test_ssrf(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_index_graph_retrieval_refresh(tmp_path):
    (tmp_path / 'config.py').write_text('LIMIT = 10\n')
    (tmp_path / 'pricing.py').write_text('from config import LIMIT\ndef total(items):\n    return sum(items[:LIMIT])\n')
    (tmp_path / 'api.py').write_text('from pricing import total\ndef checkout(items):\n    return total(items)\n')
    (tmp_path / 'test_api.py').write_text('from api import checkout\ndef test_checkout():\n    assert checkout([2]) == 2\n')
    (tmp_path / '.env').write_text('SECRET=never-index-this')
    (tmp_path / 'README.md').write_text('Checkout uses pricing and a configurable item limit.')
    first = index_repository(tmp_path)
    assert next(f for f in first['files'] if f['path'] == '.env')['status'] == 'excluded'
    assert 'never-index-this' not in str(first)
    paths = {x['path'] for x in retrieve(first, 'checkout')['items']}
    assert {'api.py', 'pricing.py', 'config.py', 'test_api.py'} <= paths
    assert any(e['source'] == 'api.py' and e['target'] == 'pricing.py' for e in first['edges'])
    response = answer(first, 'checkout')
    assert response['citations'] and 'structural' in response['mode']
    (tmp_path / 'pricing.py').unlink()
    second = index_repository(tmp_path)
    assert second['snapshot'] != first['snapshot']
    assert 'pricing.py' not in {x['path'] for x in retrieve(second, 'checkout')['items']}
    assert not any(e['target'] == 'pricing.py' for e in second['edges'])


def test_typescript_parser(tmp_path):
    (tmp_path / 'helper.ts').write_text('export function normalize(value: string): string { return value.trim(); }')
    (tmp_path / 'route.ts').write_text("import { normalize } from './helper';\nexport const route = (name: string) => normalize(name);")
    index = index_repository(tmp_path)
    route = next(f for f in index['files'] if f['path'] == 'route.ts')
    assert route['capability'] == 'parsed TypeScript/JavaScript'
    assert any(s['name'] == 'route' for s in route['symbols'])
    assert any(e['source'] == 'route.ts' and e['target'] == 'helper.ts' for e in index['edges'])


def test_team_notes_not_retrieved(tmp_path):
    (tmp_path / 'a.py').write_text('answer = 42')
    result = retrieve(index_repository(tmp_path), 'answer', [{'title':'secret note','content':'do not include','status':'observed'}, {'title':'policy','content':'keep compatibility','status':'team-approved','scope':'*'}])
    assert 'do not include' not in str(result)
    assert 'keep compatibility' in str(result)


def test_excludes_embedded_credentials_and_symlinks(tmp_path):
    (tmp_path / 'settings.py').write_text('api_key = "abcdefghijklmnop123456"')
    (tmp_path / 'good.py').write_text('api_key = os.getenv("API_KEY")')
    result = index_repository(tmp_path)
    assert next(f for f in result['files'] if f['path'] == 'settings.py')['status'] == 'excluded'
    assert next(f for f in result['files'] if f['path'] == 'good.py')['status'] == 'indexed'
    assert 'abcdefghijklmnop' not in str(result)


def test_scoped_knowledge_and_snapshot_isolation(tmp_path):
    (tmp_path / 'api.py').write_text('def checkout(): return 1')
    first = index_repository(tmp_path)
    result = retrieve(first, 'checkout', [{'status':'team-approved','scope':'elsewhere/*','content':'unrelated policy'}, {'status':'team-approved','scope':'*','stale':True,'content':'stale policy'}])
    assert not result['knowledge']
    assert all(item['snapshot'] == first['snapshot'] for item in result['items'])
    (tmp_path / 'api.py').write_text('def checkout(): return 2')
    second = index_repository(tmp_path)
    assert first['snapshot'] != second['snapshot']
    assert 'return 1' not in str(retrieve(second, 'checkout'))


def test_fixed_retrieval_recall(tmp_path):
    (tmp_path / 'config.py').write_text('MAXIMUM = 5')
    (tmp_path / 'helper.py').write_text('from config import MAXIMUM\ndef clamp(n): return min(n, MAXIMUM)')
    (tmp_path / 'endpoint.py').write_text('from helper import clamp\ndef purchase(n): return clamp(n)')
    (tmp_path / 'test_endpoint.py').write_text('from endpoint import purchase\ndef test_purchase(): assert purchase(7) == 5')
    index = index_repository(tmp_path)
    expected = {'config.py','helper.py','endpoint.py','test_endpoint.py'}
    basic = retrieve(index, 'purchase', graph=False)
    hybrid = retrieve(index, 'purchase')
    assert len(expected & {i['path'] for i in basic['items']}) == 2
    assert len(expected & {i['path'] for i in hybrid['items']}) == 4


def test_semantic_adapter_and_failure_fallback(tmp_path, monkeypatch):
    from backend import providers
    (tmp_path / 'opaque.py').write_text('value = 42')
    index = index_repository(tmp_path)
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL','test-embedding')
    seen = []
    def embed(repo_id,snapshot_id,chunks,cancel=None):
        seen.append((repo_id,snapshot_id,chunks))
        return [{**chunk,'embedding':[1.0,0.0]} for chunk in chunks]
    monkeypatch.setattr(providers, 'embed', embed)
    result = retrieve(index, 'meaning')
    assert result['mode'] == 'keyword + semantic + graph'
    assert result['items'][0]['path'] == 'opaque.py'
    assert all(snapshot==index['snapshot'] for _,snapshot,_ in seen)
    assert all(repo for repo,_,_ in seen)
    def fail(*args,**kwargs): raise TimeoutError()
    monkeypatch.setattr(providers, 'embed', fail)
    result = retrieve(index,'value')
    assert result['mode'] == 'keyword + graph'
    assert any('TimeoutError' in m for m in result['missing'])


def test_extensionless_readme(tmp_path):
    (tmp_path / 'README').write_text('A public repository description')
    index = index_repository(tmp_path)
    assert index['coverage']['indexed'] == 1
    assert index['files'][0]['category'] == 'documentation'
