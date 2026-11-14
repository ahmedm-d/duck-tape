"""Snapshot-scoped FTS chunks, graph expansion and validated model citations."""
import fnmatch
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import time

from . import db

CHUNK_VERSION = 'source-chunks-1'
STOP_WORDS = {'the', 'a', 'an', 'how', 'what', 'is', 'does', 'and', 'in', 'to', 'of', 'for', 'it', 'this'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _check(cancel):
    if cancel is not None:
        cancel.raise_if_cancelled()


def load_index(repo_id, snapshot_id):
    repo = db.get('repositories', repo_id)
    if not repo:
        raise ValueError('Repository not found')
    source = db.get('source_snapshots', snapshot_id)
    if source and source['repo_id'] != repo_id:
        raise ValueError('Snapshot belongs to another repository')
    if snapshot_id in {repo.get('source_snapshot_id'), repo.get('snapshot')}:
        return repo['index']
    stored = db.get('snapshots', repo_id + ':' + snapshot_id)
    if stored and stored.get('repo_id') == repo_id:
        return stored['index']
    for stored in db.list_records('snapshots'):
        if stored.get('repo_id') == repo_id and stored.get('source_snapshot_id') == snapshot_id:
            return stored['index']
    if not source:
        raise ValueError('Snapshot not found in this repository')
    from . import repository, snapshots, symbols
    files, gaps = [], list(source.get('gaps', []))
    for path, content in snapshots.read_files(snapshot_id).items():
        try:
            text = content.decode('utf-8')
            if '\x00' in text or len(content) > 2 * 1024 * 1024:
                continue
            suffix = PurePosixPath(path).suffix
            names, imports, calls, capability = symbols.parse_python(text) if suffix == '.py' else symbols.parse_js(text, suffix) if suffix in symbols.JS_SUFFIXES else ([], [], [], 'text search only')
            files.append({'path': path, 'status': 'indexed', 'content': text, 'hash': hashlib.sha256(content).hexdigest(),
                          'symbols': names, 'imports': imports, 'calls': calls, 'category': repository._category(path), 'capability': capability})
        except (UnicodeError, SyntaxError, ImportError):
            gaps.append(f'{path}: symbol extraction unavailable')
    graph = symbols.resolve({'repo_id': repo_id, 'snapshot': snapshot_id, 'files': files})
    return {**graph, 'files': files, 'snapshot': snapshot_id, 'overview': f'Historical immutable snapshot; {len(files)} searchable files.', 'gaps': gaps}


def _chunks(index, repo_id, snapshot_id):
    chunks, gaps = [], []
    for record in index['files']:
        if record.get('status') != 'indexed' or not isinstance(record.get('content'), str):
            continue
        lines, ranges = record['content'].splitlines(), set()
        start, size = 0, 0
        for number, line in enumerate(lines):
            length = len(line.encode('utf-8')) + 1
            if length > 12000:
                if start < number:
                    ranges.add((start + 1, number))
                gaps.append(f'{record["path"]}:{number + 1}: line exceeds 12 KiB chunk capacity; exact source retained')
                start, size = number + 1, 0
                continue
            if number - start >= 80 or size + length > 4000:
                if start < number:
                    ranges.add((start + 1, number))
                start, size = number, 0
            size += length
        if start < len(lines):
            ranges.add((start + 1, len(lines)))
        for symbol in record.get('symbols', []):
            first, last = symbol['start_line'], symbol['end_line']
            if 1 <= first <= last <= len(lines) and len('\n'.join(lines[first - 1:last]).encode()) <= 4000:
                ranges.add((first, last))
        for first, last in sorted(ranges):
            names = [symbol for symbol in record.get('symbols', []) if symbol['start_line'] <= last and symbol['end_line'] >= first]
            identity = _hash([CHUNK_VERSION, repo_id, snapshot_id, record['path'], record['hash'], first, last])
            chunks.append({'chunk_id': identity, 'path': record['path'], 'start_line': first, 'end_line': last,
                           'content': '\n'.join(lines[first - 1:last]), 'hash': record['hash'], 'symbols': names,
                           'category': record.get('category', 'implementation')})
    return chunks, gaps


def index_chunks(repo_id, snapshot_id, index=None):
    db.init()
    index = load_index(repo_id, snapshot_id) if index is None else index
    if index.get('repo_id') not in (None, '', repo_id):
        raise ValueError('Index belongs to another repository')
    fingerprint = _hash([CHUNK_VERSION, [(f['path'], f.get('hash'), f.get('status')) for f in index['files']]])
    key = _hash([repo_id, snapshot_id, CHUNK_VERSION])
    previous = db.get('chunk_manifests', key)
    if previous and previous['fingerprint'] == fingerprint:
        details = {item['chunk_id']: item for item in previous['chunks']}
        return [{**chunk, **details[chunk['chunk_id']]} for chunk in db.list_chunks(repo_id, snapshot_id)]
    if previous:
        raise ValueError('Immutable snapshot index changed')
    chunks, gaps = _chunks(index, repo_id, snapshot_id)
    db.index_chunks(repo_id, snapshot_id, chunks)
    db.put('chunk_manifests', {'id': key, 'repo_id': repo_id, 'snapshot_id': snapshot_id, 'fingerprint': fingerprint,
                             'chunks': [{k: v for k, v in chunk.items() if k != 'content'} for chunk in chunks], 'gaps': gaps})
    return chunks


def _cosine(first, second):
    if len(first) != len(second) or not first or any(not math.isfinite(value) for value in [*first, *second]):
        raise ValueError('Invalid or inconsistent embedding dimensions')
    divisor = math.sqrt(sum(x*x for x in first) * sum(x*x for x in second))
    return sum(a*b for a, b in zip(first, second)) / divisor if divisor else 0


def retrieve(repo_id, snapshot_id, query=None, budget_tokens=7000, paths=None, *, knowledge=None, limit=12, graph=True, semantic=None, cancel=None):
    started = time.perf_counter()
    legacy = isinstance(repo_id, dict)
    if legacy:
        index = repo_id
        knowledge = query if isinstance(query, list) else knowledge
        query = snapshot_id
        snapshot_id = index.get('source_snapshot_id') or index['snapshot']
        repo_id = index.get('repo_id') or 'legacy:' + index['snapshot']
    else:
        index = load_index(repo_id, snapshot_id)
        if knowledge is None:
            knowledge = [item for item in db.list_records('knowledge') if item.get('repo_id') == repo_id]
    if not isinstance(query, str) or type(budget_tokens) is not int or budget_tokens < 256:
        raise ValueError('Query and a context budget of at least 256 tokens are required')
    _check(cancel)
    limit = max(1, min(int(limit), 24))
    chunks = index_chunks(repo_id, snapshot_id, index)
    metadata = db.get('chunk_manifests', _hash([repo_id, snapshot_id, CHUNK_VERSION]))
    details = {item['chunk_id']: item for item in metadata['chunks']}
    available = {chunk['chunk_id']: {**chunk, **details[chunk['chunk_id']]} for chunk in chunks}
    if paths is not None:
        available = {key: item for key, item in available.items() if any(item['path'] == path or fnmatch.fnmatchcase(item['path'], path) for path in paths)}
    tokens = sorted({token.rstrip('./-') for token in re.findall(r'[a-zA-Z_][\w./-]*', query.lower())} - STOP_WORDS)
    scores, reasons = {}, {}
    fts_query = ' OR '.join('"' + token.replace('"', '""') + '"' for token in tokens)
    if fts_query:
        for hit in db.search_chunks(repo_id, snapshot_id, fts_query, limit=200):
            key = hit['chunk_id']
            if key in available:
                scores[key] = 5 + sum(min(hit['content'].lower().count(token), 8) for token in tokens)
                reasons[key] = ['persisted full-text chunk match']
    for key, chunk in available.items():
        names = ' '.join(symbol['name'] for symbol in chunk.get('symbols', [])).lower()
        exact = sum(128 * (token == chunk['path'].lower()) + 24 * (token in chunk['path'].lower()) + 32 * (token in names) for token in tokens)
        if exact:
            scores[key] = scores.get(key, 0) + exact
            reasons.setdefault(key, []).append('exact symbol/path match')
    semantic_used = False
    semantic_note = 'Embeddings unavailable; deterministic keyword/graph fallback'
    from . import providers
    use_semantic = semantic if semantic is not None else bool(graph and providers.configuration()['embedding_model'])
    if use_semantic and available:
        try:
            ordered = list(available.values())
            encoded_query = providers.embed(repo_id, snapshot_id, [{'chunk_id': 'query:' + _hash(query), 'content': query}], cancel=cancel)
            if not encoded_query:
                raise ValueError('Embedding model unavailable')
            query_vector, vectors = encoded_query[0]['embedding'], []
            for start in range(0, len(ordered), 32):
                _check(cancel)
                batch = ordered[start:start + 32]
                encoded = providers.embed(repo_id, snapshot_id, batch, cancel=cancel)
                if len(encoded) != len(batch):
                    raise ValueError('Embedding provider returned incomplete vectors')
                vectors.extend(item['embedding'] for item in encoded)
            for chunk, vector in zip(ordered, vectors):
                similarity = _cosine(query_vector, vector)
                if similarity > 0:
                    key = chunk['chunk_id']
                    scores[key] = scores.get(key, 0) + similarity * 20
                    reasons.setdefault(key, []).append(f'semantic similarity {similarity:.3f}')
            semantic_used = True
            semantic_note = f'Semantic coverage: {len(ordered)}/{len(available)} eligible chunks; cached by repository/snapshot/model/content'
        except Exception as exc:
            _check(cancel)
            semantic_note = f'Embedding provider unavailable ({type(exc).__name__}); keyword/graph fallback'
    by_path = {}
    for key, chunk in available.items():
        by_path.setdefault(chunk['path'], []).append(key)
    if graph:
        required_lines = {}
        frontier = {available[key]['path'] for key in sorted(scores, key=scores.get, reverse=True)[:4]}
        visited = set(frontier)
        for depth in range(3):
            following = set()
            for edge in index.get('edges', []):
                for origin, related, ref in [(edge['source'], edge['target'], edge.get('target_evidence')), (edge['target'], edge['source'], edge.get('evidence'))]:
                    if origin not in frontier or related not in by_path:
                        continue
                    line = (ref or {}).get('start_line', 1)
                    required_lines.setdefault(related, set()).add(line)
                    key = min(by_path[related], key=lambda item: (not available[item]['start_line'] <= line <= available[item]['end_line'], available[item]['end_line'] - available[item]['start_line']))
                    scores[key] = max(scores.get(key, 0), 10 - depth * 2)
                    reasons.setdefault(key, []).append(f'graph {edge["kind"]} ({edge["confidence"]}) via {origin}')
                    if related not in visited:
                        following.add(related)
                        visited.add(related)
            frontier = following
        for path, lines in required_lines.items():
            for key in by_path[path]:
                covered = sum(available[key]['start_line'] <= line <= available[key]['end_line'] for line in lines)
                if covered and key in scores:
                    scores[key] += min(32, covered * 8)
                    reasons[key].append('preserves resolved import/call evidence ranges')
        for path, keys in by_path.items():
            if available[keys[0]].get('category') in {'documentation', 'configuration', 'dependencies'}:
                scores.setdefault(keys[0], 1)
                reasons.setdefault(keys[0], ['repository documentation/configuration context'])
    missing = list(index.get('gaps', [])) + metadata.get('gaps', [])
    if graph or use_semantic:
        missing.append(semantic_note)
    if len(missing) > 32:
        missing = missing[:32] + [f'{len(missing) - 32} further inventory gaps available in the source index']
    overview = index.get('overview', '')[:1000]
    policies = []
    selected_paths = {available[key]['path'] for key in scores}
    for record in knowledge or []:
        if record.get('status') != 'team-approved' or record.get('stale') or record.get('conflicting'):
            continue
        if not legacy and record.get('repo_id') not in (None, '', repo_id):
            continue
        scope = record.get('scope') or '*'
        if scope in {'*', 'repository', 'global'} or any(fnmatch.fnmatchcase(path, scope) or path.startswith(scope.rstrip('/') + '/') for path in selected_paths):
            policies.append({key: record[key] for key in ['id', 'title', 'content', 'scope', 'version', 'updated_at'] if key in record})
    mode = 'keyword' + (' + semantic' if semantic_used else '') + (' + graph' if graph else ' only' if not semantic_used else '')
    # shortcut: UTF-8 bytes bound tokens conservatively; use model tokenization when available.
    base_fields = {'missing': missing, 'overview': overview, 'repo_id': repo_id, 'snapshot': snapshot_id, 'snapshot_id': snapshot_id,
                   'mode': mode, 'context_hash': '0' * 64, 'token_estimate': budget_tokens, 'latency_ms': 99999999,
                   'token_count_method': 'conservative UTF-8 byte upper bound', 'items': [], 'knowledge': policies}
    base_cost = len(json.dumps(base_fields, ensure_ascii=False).encode()) + 128
    if base_cost > budget_tokens:
        raise ValueError('Applicable approved constraints or context metadata exceed the model budget')
    remaining = budget_tokens - base_cost
    items = []
    for key in sorted(scores, key=lambda item: (-scores[item], available[item]['end_line'] - available[item]['start_line'], available[item]['path'], available[item]['start_line'])):
        _check(cancel)
        chunk = available[key]
        if any(item['path'] == chunk['path'] and item['start_line'] <= chunk['end_line'] and chunk['start_line'] <= item['end_line'] for item in items):
            continue
        item = {k: chunk[k] for k in ('path', 'start_line', 'end_line', 'content', 'hash', 'chunk_id', 'symbols')}
        item['symbols'] = [{name: symbol[name] for name in ('name', 'kind', 'start_line', 'end_line')} for symbol in item['symbols']]
        item.update(repo_id=repo_id, snapshot=snapshot_id, snapshot_id=snapshot_id, blob_hash=chunk['hash'], reason='; '.join(dict.fromkeys(reasons[key])))
        cost = len(json.dumps(item, ensure_ascii=False).encode())
        if cost > remaining:
            continue
        remaining -= cost
        items.append(item)
        if len(items) >= limit:
            break
    if not items:
        missing.append('No indexed evidence matches this question within the context budget')
    return {'items': items, 'missing': missing, 'mode': mode, 'snapshot': snapshot_id, 'snapshot_id': snapshot_id, 'repo_id': repo_id,
            'overview': overview, 'knowledge': policies, 'context_hash': _hash([repo_id, snapshot_id, items, policies]),
            'token_estimate': budget_tokens - remaining, 'token_count_method': 'conservative UTF-8 byte upper bound',
            'latency_ms': round((time.perf_counter() - started) * 1000, 3)}


def validate_citations(citations, items):
    validated = []
    for citation in citations:
        if type(citation.get('start_line')) is not int or type(citation.get('end_line')) is not int:
            raise ValueError('Provider citation has an invalid range')
        evidence = next((item for item in items if item['path'] == citation['path']
                         and item['start_line'] <= citation['start_line'] <= citation['end_line'] <= item['end_line']
                         and citation.get('snapshot_id', item['snapshot']) == item['snapshot']
                         and citation.get('repo_id', item['repo_id']) == item['repo_id']), None)
        if evidence is None:
            raise ValueError('Provider citation does not resolve to supplied evidence')
        lines = evidence['content'].splitlines()
        start = citation['start_line'] - evidence['start_line']
        end = citation['end_line'] - evidence['start_line'] + 1
        validated.append({**citation, 'snapshot': evidence['snapshot'], 'snapshot_id': evidence['snapshot'],
                          'repo_id': evidence['repo_id'], 'blob_hash': evidence['hash'], 'chunk_id': evidence['chunk_id'],
                          'content': '\n'.join(lines[start:end]), 'reason': 'Validated supplied source evidence'})
    if not validated:
        raise ValueError('Provider did not cite repository evidence')
    return validated


def answer(repo_id, snapshot_id, query=None, cancel=None, *, knowledge=None, live=None):
    legacy = isinstance(repo_id, dict)
    if legacy:
        index = repo_id
        knowledge = query if isinstance(query, list) else knowledge
        query = snapshot_id
        context = retrieve(index, query, knowledge=knowledge, cancel=cancel)
        live = bool(live)
    else:
        index = load_index(repo_id, snapshot_id)
        context = retrieve(repo_id, snapshot_id, query, knowledge=knowledge, cancel=cancel)
        live = True if live is None else live
    error = None
    if live:
        from . import providers
        _check(cancel)
        if providers.health().get('available'):
            try:
                model_context = {key: value for key, value in context.items() if key != 'latency_ms'}
                if legacy:
                    result, telemetry = providers.complete('qa', {'question': query, 'context': model_context}, 'low')
                else:
                    from .contracts import ProviderRequest
                    wrapped = providers.complete(ProviderRequest(task='qa', payload={'question': query, 'context': model_context},
                                                 repo_id=repo_id, snapshot_id=snapshot_id, max_output_tokens=1024), cancel=cancel)
                    result, telemetry = wrapped.output, wrapped.telemetry
                _check(cancel)
                try:
                    citations = validate_citations(result['citations'], context['items'])
                except ValueError:
                    providers.invalidate_cached_result(result, telemetry)
                    raise
                return {'answer': result['answer'], 'citations': citations, 'mode': 'live evidence-linked answer',
                        'context': context, 'provider': telemetry, 'claim_type': 'inferred synthesis; cited source ranges validated'}
            except Exception as exc:
                _check(cancel)
                error = f'Live synthesis unavailable or invalid ({type(exc).__name__}); structural evidence shown instead.'
        else:
            error = 'Live model unavailable; structural evidence shown instead.'
    citations = [{key: item[key] for key in ['path', 'start_line', 'end_line', 'snapshot', 'repo_id', 'blob_hash', 'chunk_id', 'reason']} for item in context['items']]
    paths = {item['path'] for item in context['items']}
    relations = [f'{edge["source"]} → {edge["target"]} ({edge["kind"]}, {edge["confidence"]})' for edge in index.get('edges', []) if edge['source'] in paths and edge['target'] in paths]
    text = 'Offline structural answer: no live model synthesis is being claimed. '
    text += ('Relevant indexed evidence: ' + ', '.join(dict.fromkeys(item['path'] for item in citations)) + '.\n' + '\n'.join(dict.fromkeys(relations))) if citations else 'No matching indexed evidence was found.'
    if context['knowledge']:
        text += '\nApplicable approved decisions: ' + '; '.join(item.get('title', 'Decision') + ': ' + item.get('content', '') for item in context['knowledge'])
    text += '\nLimits: ' + '; '.join(context['missing'])
    return {'answer': (error + '\n' if error else '') + text, 'citations': citations, 'mode': 'offline structural', 'context': context}
