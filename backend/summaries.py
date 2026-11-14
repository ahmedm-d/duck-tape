"""Cached, evidence-linked synthesis with an inspectable structural fallback."""
from pathlib import PurePosixPath

from . import db, providers, retrieval
from .contracts import ProviderRequest

PROMPT_VERSION = 'evidence-summaries-1'


def build(repo_id, snapshot_id, cancel=None):
    retrieval._check(cancel)
    index = retrieval.load_index(repo_id, snapshot_id)
    chunks = retrieval.index_chunks(repo_id, snapshot_id, index)
    files = {item['path']: item for item in index['files'] if item.get('status') == 'indexed'}
    groups = [('repository', '*', set(files))]
    for path in files:
        if PurePosixPath(path).name in {'package.json', 'pyproject.toml', '__init__.py'}:
            directory = str(PurePosixPath(path).parent)
            groups.append(('package', directory, {name for name in files if directory == '.' or name.startswith(directory + '/')}))
    groups.extend(('module', path, {path}) for path in files)
    health = providers.health()
    model = health.get('model') if health.get('available') else None
    config = providers.configuration()
    calls, results = 0, []
    by_path = {}
    for chunk in chunks:
        by_path.setdefault(chunk['path'], []).append(chunk)
    for kind, name, members in groups:
        retrieval._check(cancel)
        dependencies = sorted((path, files[path]['hash']) for path in members)
        key = retrieval._hash([repo_id, snapshot_id, kind, name, dependencies, model, config['small_model'], providers.capabilities('summary'), config['endpoint'], config['kind'], PROMPT_VERSION, getattr(providers, 'PROMPT_VERSION', '')])
        cached = db.get('summaries', key)
        if cached and (not model or cached['mode'] == 'inferred synthesis'):
            results.append(cached)
            continue
        selected, size = [], 0
        for chunk in (chunk for path in sorted(members) for chunk in by_path.get(path, [])):
            if any(item['path'] == chunk['path'] and item['start_line'] <= chunk['end_line'] and chunk['start_line'] <= item['end_line'] for item in selected):
                continue
            cost = len(chunk['content'].encode()) + 400
            if size + cost > 6000:
                continue
            selected.append({**chunk, 'repo_id': repo_id, 'snapshot': snapshot_id})
            size += cost
        if not selected:
            continue
        evidence = [{'path': item['path'], 'start_line': item['start_line'], 'end_line': item['end_line'], 'repo_id': repo_id,
                     'snapshot_id': snapshot_id, 'blob_hash': item['hash'], 'chunk_id': item['chunk_id']} for item in selected]
        names = sorted({symbol['name'] for chunk in selected for symbol in chunk.get('symbols', [])
                        if chunk['start_line'] <= symbol['start_line'] <= symbol['end_line'] <= chunk['end_line']})
        text = f'{kind.capitalize()} {name}: {len(members)} indexed files. Parsed symbols: ' + (', '.join(names[:30]) or 'none extracted') + '. Structural facts; semantic synthesis unavailable.'
        missing, telemetry, mode = [], {'usage': None}, 'structural fallback'
        incomplete = False
        for path in members:
            through = 0
            for first, last in sorted((item['start_line'], item['end_line']) for item in selected if item['path'] == path):
                if first > through + 1:
                    break
                through = max(through, last)
            incomplete |= through < len(files[path]['content'].splitlines())
        if incomplete:
            missing.append('Summary input budget covers selected source ranges; remaining files are inventoried but not synthesized')
        if model and calls < 12:
            calls += 1
            try:
                wrapped = providers.complete(ProviderRequest(task='summary', repo_id=repo_id, snapshot_id=snapshot_id,
                                             payload={'scope': {'kind': kind, 'name': name}, 'evidence': selected,
                                                      'instruction': 'Describe only supplied source evidence; label inferences and list missing coverage.'},
                                             max_output_tokens=1024), cancel=cancel)
                result, telemetry = wrapped.output, wrapped.telemetry
                try:
                    validated = retrieval.validate_citations(result['evidence'], selected)
                except ValueError:
                    providers.invalidate_cached_result(result, telemetry)
                    raise
                text = result['summary']
                evidence = [{key: ref[key] for key in ('path', 'start_line', 'end_line', 'repo_id', 'snapshot_id', 'blob_hash', 'chunk_id')} for ref in validated]
                missing.extend(result.get('missing', []))
                mode = 'inferred synthesis'
            except Exception as exc:
                retrieval._check(cancel)
                missing.append('Model summary unavailable or invalid: ' + type(exc).__name__)
        else:
            missing.append('Live model unavailable' if not model else 'Model summary budget exhausted (12 calls)')
        retrieval._check(cancel)
        record = db.put('summaries', {'id': key, 'repo_id': repo_id, 'snapshot_id': snapshot_id, 'kind': kind, 'scope': name,
                                     'dependency_hashes': dependencies, 'model': telemetry.get('model', model), 'prompt_version': PROMPT_VERSION,
                                     'summary': text, 'mode': mode, 'evidence': evidence, 'missing': missing, 'provider': telemetry})
        results.append(record)
    return results
