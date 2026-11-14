from pathlib import Path

from backend import repository


def write(root, files):
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')


def edges(index, kind):
    return {(edge['source'], edge['target'], edge.get('symbol')) for edge in index['edges'] if edge['kind'] == kind}


def test_python_src_relative_submodule_aliases_and_shadowing(tmp_path):
    write(tmp_path, {
        'pyproject.toml': '[tool.setuptools.package-dir]\n"" = "src"\n',
        'src/shop/__init__.py': '',
        'src/shop/helper.py': 'def calculate(): return 1\n',
        'src/shop/other.py': 'def calculate(): return 2\n',
        'src/shop/api.py': 'from .helper import calculate as price\nfrom . import other as spare\ndef route():\n    return price() + spare.calculate()\ndef shadow(price):\n    return price()\n',
        'tests/test_api.py': 'from shop.api import route\ndef test_route():\n    assert route() == 3\n',
    })
    index = repository.index_repository(tmp_path)
    assert edges(index, 'calls') == {
        ('src/shop/api.py', 'src/shop/helper.py', 'price'),
        ('src/shop/api.py', 'src/shop/other.py', 'spare.calculate'),
        ('tests/test_api.py', 'src/shop/api.py', 'route'),
    }
    assert not any(edge['line'] == 6 for edge in index['edges'] if edge['kind'] == 'calls')


def test_same_name_call_without_import_binding_is_unresolved(tmp_path):
    write(tmp_path, {
        'helpers.py': 'def same(): return 1\n',
        'main.py': 'from helpers import same as imported\ndef run(obj):\n    return obj.same()\n',
    })
    index = repository.index_repository(tmp_path)
    assert not edges(index, 'calls')
    assert any(item['reference'] == 'obj.same' for item in index['unresolved'])


def test_typescript_alias_reexport_emitted_js_and_dynamic_import(tmp_path):
    write(tmp_path, {
        'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}',
        'src/helper.ts': 'export function normalize(value: string) { return value.trim(); }',
        'src/public.ts': 'export { normalize as clean } from "./helper.js";',
        'src/route.ts': 'import { clean as tidy } from "@app/public";\nexport function route(value: string) { return tidy(value); }\nconst moduleName = "./unknown"; import(moduleName);',
    })
    index = repository.index_repository(tmp_path)
    assert ('src/route.ts', 'src/helper.ts', 'tidy') in edges(index, 'calls')
    assert ('src/public.ts', 'src/helper.ts', None) in edges(index, 'reexports')
    assert any(item['reason'].startswith('dynamic') for item in index['unresolved'])


def test_npm_workspace_package_exports_and_duplicate_names(tmp_path):
    write(tmp_path, {
        'package.json': '{"private":true,"workspaces":["packages/*"]}',
        'packages/utils/package.json': '{"name":"@shop/utils","exports":{".":"./src/index.ts"}}',
        'packages/utils/src/index.ts': 'export function total() { return 1; }',
        'packages/other.ts': 'export function total() { return 99; }',
        'app.ts': 'import { total as add } from "@shop/utils"; export const result = add();',
    })
    index = repository.index_repository(tmp_path)
    assert edges(index, 'calls') == {('app.ts', 'packages/utils/src/index.ts', 'add')}


def test_evidence_resolves_to_snapshot_ranges_and_deleted_edges_disappear(tmp_path):
    write(tmp_path, {'helper.py': 'def greet():\n    return "hello"\n', 'api.py': 'from helper import greet\ndef route(): return greet()\n'})
    old = repository.index_repository(tmp_path)
    contents = {item['path']: item for item in old['files'] if item['status'] == 'indexed'}
    for item in [*old['nodes'], *old['edges']]:
        ref = item['evidence']
        assert ref['snapshot_id'] == old['snapshot']
        assert ref['blob_hash'] == contents[ref['path']]['hash']
        assert 1 <= ref['start_line'] <= ref['end_line'] <= len(contents[ref['path']]['content'].splitlines())
    (tmp_path / 'helper.py').unlink()
    new = repository.index_repository(tmp_path)
    assert not edges(new, 'calls')
    assert ('api.py', 'helper.py', 'greet') in edges(old, 'calls')


def test_snapshot_extract_inventory_and_routes(tmp_path, monkeypatch):
    from backend import db, snapshots, symbols
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    source = tmp_path / 'source'
    write(source, {
        'api.py': 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/orders")\ndef orders(): return []\n',
        'routes.ts': 'import express from "express"; const app = express(); app.post("/orders", () => []);',
        '.github/workflows/test.yml': 'name: tests\n',
        'migrations/001.sql': 'CREATE TABLE orders(id INTEGER);',
    })
    source_snapshot = snapshots.capture('repo', source, {})
    result = symbols.extract(source_snapshot)
    assert {'/orders'} == {route['path'] for route in result['routes']}
    assert {route['method'] for route in result['routes']} == {'GET', 'POST'}
    assert any(item['kind'] == 'schema/migration' for item in result['boundaries'])
    assert all(node['evidence']['repo_id'] == 'repo' for node in result['nodes'])


def test_js_unexported_symbols_and_parameter_shadowing_never_create_edges(tmp_path):
    write(tmp_path, {
        'helper.ts': 'function privateName() { return 1; } export function exported() { return 2; }',
        'route.ts': 'import { privateName, exported as alias } from "./helper";\nfunction shadow(alias: () => number) { return alias(); }\nprivateName();',
    })
    assert not edges(repository.index_repository(tmp_path), 'calls')


def test_js_local_named_reexport_and_jsonc_configuration(tmp_path):
    write(tmp_path, {
        'tsconfig.json': '{ // supported TypeScript config\n"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"],},},}',
        'src/helper.ts': 'function localName() { return 1; } export { localName as exposed };',
        'route.ts': 'import { exposed } from "@app/helper"; exposed();',
    })
    assert edges(repository.index_repository(tmp_path), 'calls') == {('route.ts', 'src/helper.ts', 'exposed')}


def test_extract_keeps_invalid_syntax_file_with_explicit_capability_gap(tmp_path, monkeypatch):
    from backend import db, snapshots, symbols
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    source = tmp_path / 'source'
    write(source, {'broken.py': 'def broken(:\n'})
    result = symbols.extract(snapshots.capture('repo', source, {}))
    assert any(node['path'] == 'broken.py' for node in result['nodes'])
    assert any('broken.py' in gap for gap in result['gaps'])


def test_js_class_members_have_unique_identity_and_block_shadowing_stays_local(tmp_path):
    write(tmp_path, {
        'helper.ts': 'export function value() { return 1; }',
        'main.ts': 'import { value } from "./helper";\nclass One { method() {} }\nclass Two { method() {} }\nif (true) { const value = () => 2; value(); }\nvalue();',
    })
    index = repository.index_repository(tmp_path)
    ids = [node['id'] for node in index['nodes']]
    assert len(ids) == len(set(ids))
    calls = [edge for edge in index['edges'] if edge['kind'] == 'calls']
    assert len(calls) == 1 and calls[0]['line'] == 5
