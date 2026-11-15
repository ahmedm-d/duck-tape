"""A bounded SWE file-tool session over an immutable snapshot; never executes code."""
import json

from . import agent_adapters, edits, model_schemas, repository, snapshots

MAX_TURNS = 8
MAX_TOOL_TEXT = 12000


def run(snapshot_id, payload, invoke, destination, *, cancel=None):
    """invoke(payload, turn) uses the worker's existing persistent budget/checkpoint."""
    before = snapshots.read_files(snapshot_id)
    contents, read, history = dict(before), set(), []
    protected = {edit['path'] for key in ('required_regression_edits', 'required_acceptance_edits') for edit in payload.get(key, [])}
    for item in payload.get('files', []):
        path = item['path']
        if item.get('complete') and path in before and item.get('hash') == edits._hash(before[path]) and item.get('content', '').encode() == before[path]:
            read.add(path)
    actions = 0
    def guard():
        if cancel:
            cancel.raise_if_cancelled()
    for turn in range(MAX_TURNS):
        guard()
        response = agent_adapters.parse_completion('implementer', invoke({**payload, 'tool_history': list(history)}, turn))
        guard()
        model_schemas.validate(response, 'tool_turn')
        if 'decision' in response:
            if contents != before:
                raise ValueError('A decision must precede file edits')
            return response
        history.append({'role': 'assistant', 'content': json.dumps(response)})
        if not response['tool_calls']:
            operations = agent_adapters.operations_from_files(snapshot_id, contents, response['text'])
            guard()
            candidate = edits.apply(snapshot_id, operations['edits'], destination)
            guard()
            # Derive the returned operations from the actual Git candidate, not a model claim.
            after = dict(before)
            for item in candidate.files:
                if item['hash'] is None:
                    after.pop(item['path'])
                else:
                    after[item['path']] = item['after'].encode('utf-8')
            return agent_adapters.operations_from_files(snapshot_id, after, response['text'])
        for call in response['tool_calls']:
            guard()
            actions += 1
            if actions > 32:
                raise ValueError('Specialist tool action budget exhausted')
            args = json.loads(call['arguments']) if isinstance(call['arguments'], str) else call['arguments']
            if not isinstance(args, dict):
                raise ValueError('Tool arguments must be an object')
            name = call['name']
            aliases = {'open_file': 'read_file', 'view': 'read_file', 'search_dir': 'search',
                       'search_file': 'search', 'str_replace': 'edit_file'}
            name = aliases.get(name, name)
            if name in ('str_replace_editor', 'str_replace_based_edit_tool'):
                command = args.get('command')
                name = {'view': 'read_file', 'str_replace': 'edit_file', 'create': 'write_file'}.get(command)
            if name not in ('read_file', 'search', 'find_file', 'edit_file', 'write_file', 'apply_patch'):
                raise ValueError('Unsupported specialist tool; shell/network execution is forbidden')
            path = edits._path(args['path']) if 'path' in args else None
            if name == 'read_file':
                if path not in contents:
                    raise ValueError('File is absent from the isolated snapshot')
                text = contents[path].decode('utf-8')
                if len(text) > MAX_TOOL_TEXT:
                    raise ValueError('File exceeds bounded tool read; request scoped controller evidence')
                if path in before:
                    edits.read_file(snapshot_id, path, edits._hash(before[path]))
                read.add(path)
                output = {'path': path, 'hash': edits._hash(contents[path]), 'complete': True, 'content': text}
            elif name in ('search', 'find_file'):
                query = args.get('query', args.get('search_term', args.get('file_name', '')))
                if not isinstance(query, str) or not 1 <= len(query) <= 1000:
                    raise ValueError('Search needs a bounded literal query')
                matches = []
                size = 0
                for candidate, data in sorted(contents.items()):
                    guard()
                    if path and candidate != path and not candidate.startswith(path + '/'):
                        continue
                    if name == 'find_file':
                        lines = [(1, candidate)] if query in candidate else []
                    else:
                        lines = [(n, line) for n, line in enumerate(data.decode('utf-8', errors='replace').splitlines(), 1) if query in line]
                    for number, line in lines:
                        if len(matches) >= 40 or size >= 8000:
                            break
                        matches.append({'path': candidate, 'line': number, 'text': line[:200]})
                        size += len(line[:200]) + len(candidate)
                    if len(matches) >= 40 or size >= 8000:
                        break
                output = {'matches': matches, 'bounded': True}
            elif name == 'apply_patch':
                patch = args.get('patch', args.get('diff'))
                if not isinstance(patch, str):
                    raise ValueError('Patch must be a unified diff string')
                updated = agent_adapters.patched_files(patch, contents)
                touched = {p for p in contents.keys() | updated.keys() if contents.get(p) != updated.get(p)}
                if touched & protected:
                    raise ValueError('Cannot overwrite controller test edits')
                if any(p in contents and p not in read for p in touched):
                    raise ValueError('Read existing files before editing')
                contents = updated
                output = {'changed_paths': sorted(touched)}
            else:
                if path in protected:
                    raise ValueError('Cannot overwrite controller test edits')
                if path is None or path in contents and path not in read:
                    raise ValueError('Read existing files before editing')
                if name == 'edit_file':
                    old, new = args.get('old', args.get('old_str')), args.get('new', args.get('new_str'))
                    if path not in contents or not isinstance(old, str) or not old or not isinstance(new, str):
                        raise ValueError('Edit needs existing path and nonempty old/new strings')
                    old, new = edits._content(old), edits._content(new)
                    if contents[path].count(old) != 1:
                        raise ValueError('Edit preimage must match exactly once')
                    contents[path] = contents[path].replace(old, new, 1)
                else:
                    contents[path] = edits._content(args.get('content', args.get('file_text')))
                output = {'path': path, 'hash': edits._hash(contents[path]), 'edited': True}
            if len(contents) > snapshots.MAX_FILES or sum(map(len, contents.values())) > repository.MAX_EXPANDED:
                raise ValueError('Candidate exceeds snapshot limits')
            guard()
            history.append({'role': 'user', 'content': 'Untrusted tool result ' + call['id'] + ': ' + json.dumps(output)})
    raise ValueError('Specialist turn budget exhausted (8 turns)')
