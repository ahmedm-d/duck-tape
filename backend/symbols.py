"""Evidence-linked static bindings for Python and JavaScript/TypeScript."""
import ast
import fnmatch
import hashlib
import json
import posixpath
from pathlib import PurePosixPath
import re
import tomllib

JS_SUFFIXES = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')
HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head'}


def parse_python(content):
    tree = ast.parse(content)
    symbols, imports, calls, shadows = [], [], [], {'': set()}

    class Visitor(ast.NodeVisitor):
        scope = ''

        def visit_FunctionDef(self, node):
            self.function(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def function(self, node):
            qualified = '.'.join(filter(None, [self.scope, node.name]))
            symbol = {'name': node.name, 'qualified_name': qualified, 'scope': self.scope, 'kind': type(node).__name__,
                      'start_line': node.lineno, 'end_line': node.end_lineno,
                      'decorators': [ast.unparse(item) for item in node.decorator_list], 'routes': [],
                      'summary': f'Parsed {type(node).__name__} {qualified}'}
            for item in node.decorator_list:
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr in HTTP_METHODS:
                    if item.args and isinstance(item.args[0], ast.Constant) and isinstance(item.args[0].value, str):
                        symbol['routes'].append({'path': item.args[0].value, 'method': item.func.attr.upper(),
                                                 'line': item.lineno, 'framework': 'FastAPI-style declaration', 'confidence': 'parsed declaration; framework binding not proven'})
            symbols.append(symbol)
            shadows.setdefault(self.scope, set()).add(node.name)
            previous = self.scope
            self.scope = qualified
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            shadows[self.scope] = {arg.arg for arg in args}
            if node.args.vararg:
                shadows[self.scope].add(node.args.vararg.arg)
            if node.args.kwarg:
                shadows[self.scope].add(node.args.kwarg.arg)
            for child in node.body:
                self.visit(child)
            self.scope = previous

        def visit_ClassDef(self, node):
            qualified = '.'.join(filter(None, [self.scope, node.name]))
            symbols.append({'name': node.name, 'qualified_name': qualified, 'scope': self.scope, 'kind': 'ClassDef',
                            'start_line': node.lineno, 'end_line': node.end_lineno, 'decorators': [], 'summary': f'Parsed class {qualified}'})
            previous = self.scope
            shadows.setdefault(previous, set()).add(node.name)
            self.scope = qualified
            shadows[self.scope] = set()
            for child in node.body:
                self.visit(child)
            self.scope = previous

        def visit_Import(self, node):
            for alias in node.names:
                imports.append({'module': alias.name, 'names': [alias.name], 'line': node.lineno, 'scope': self.scope,
                                'bindings': [{'local': alias.asname or alias.name.split('.')[0], 'imported': '*',
                                              'module_binding': True, 'unaliased_module': not bool(alias.asname)}]})

        def visit_ImportFrom(self, node):
            imports.append({'module': '.' * node.level + (node.module or ''), 'names': [item.name for item in node.names],
                            'line': node.lineno, 'scope': self.scope,
                            'bindings': [{'local': item.asname or item.name, 'imported': item.name, 'module_binding': False} for item in node.names]})

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store):
                shadows.setdefault(self.scope, set()).add(node.id)

        def visit_Call(self, node):
            calls.append({'name': ast.unparse(node.func), 'line': node.lineno, 'scope': self.scope})
            self.generic_visit(node)

    Visitor().visit(tree)
    for call in calls:
        scope = call['scope']
        blocked = set()
        while True:
            blocked.update(shadows.get(scope, set()))
            if not scope:
                break
            scope = scope.rpartition('.')[0]
        call['shadowed'] = call['name'].split('.')[0] in blocked
    return symbols, imports, calls, 'parsed Python AST'


def parse_js(content, suffix):
    from tree_sitter import Language, Parser
    import tree_sitter_javascript
    import tree_sitter_typescript
    grammar = tree_sitter_typescript.language_tsx() if suffix == '.tsx' else tree_sitter_typescript.language_typescript() if suffix == '.ts' else tree_sitter_javascript.language()
    tree = Parser(Language(grammar)).parse(content.encode())
    symbols, imports, calls, shadows = [], [], [], {'': set()}

    def text(node):
        return node.text.decode('utf-8') if node else ''

    def visit(node, scope=''):
        name_node = node.child_by_field_name('name')
        if node.type in {'function_declaration', 'class_declaration', 'method_definition', 'interface_declaration', 'type_alias_declaration', 'variable_declarator'} and name_node:
            name = text(name_node)
            declaration = node.parent if node.type == 'variable_declarator' else node
            exported = declaration.parent is not None and declaration.parent.type == 'export_statement'
            export_names = ['default' if text(declaration.parent).startswith('export default') else name] if exported else []
            symbols.append({'name': name, 'qualified_name': '.'.join(filter(None, [scope, name])), 'scope': scope,
                            'kind': node.type, 'start_line': node.start_point.row + 1, 'end_line': node.end_point.row + 1,
                            'summary': f'Parsed {node.type} {name}', 'export_names': export_names})
            shadows.setdefault(scope, set()).add(name)
        if node.type in {'import_statement', 'export_statement'}:
            source = node.child_by_field_name('source')
            if source:
                bindings = []
                stack = list(node.named_children)
                while stack:
                    item = stack.pop(0)
                    if item.type in {'import_specifier', 'export_specifier'}:
                        names = [text(child) for child in item.named_children]
                        bindings.append({'local': names[-1], 'imported': names[0], 'module_binding': False})
                    elif item.type == 'namespace_import':
                        bindings.append({'local': text(item.named_children[-1]), 'imported': '*', 'module_binding': True})
                    elif item.type == 'import_clause':
                        for child in item.named_children:
                            if child.type == 'identifier':
                                bindings.append({'local': text(child), 'imported': 'default', 'module_binding': False})
                        stack.extend(item.named_children)
                    else:
                        stack.extend(item.named_children)
                imports.append({'module': text(source).strip('\"\x27'), 'names': [b['imported'] for b in bindings],
                                'line': node.start_point.row + 1, 'scope': scope, 'bindings': bindings,
                                'reexport': node.type == 'export_statement'})
        if node.type == 'call_expression':
            function = text(node.child_by_field_name('function'))
            args = node.child_by_field_name('arguments')
            call = {'name': function, 'line': node.start_point.row + 1, 'scope': scope}
            if function == 'import':
                call['dynamic'] = True
            if function == 'require' and args and args.named_children and args.named_children[0].type == 'string':
                imports.append({'module': text(args.named_children[0]).strip('\"\x27'), 'names': [],
                                'line': call['line'], 'scope': scope, 'bindings': []})
            method = function.rsplit('.', 1)[-1]
            if '.' in function and method in HTTP_METHODS and args and args.named_children and args.named_children[0].type == 'string':
                call['route'] = {'path': text(args.named_children[0]).strip('\"\x27'), 'method': method.upper(), 'line': call['line'],
                                 'framework': 'Express-style declaration', 'confidence': 'parsed declaration; framework binding not proven'}
            calls.append(call)
        child_scope = scope
        if node.type == 'class_declaration' and name_node:
            child_scope = '.'.join(filter(None, [scope, text(name_node)]))
            shadows.setdefault(child_scope, set())
        elif node.type == 'statement_block':
            child_scope = '.'.join(filter(None, [scope, f'@block{node.start_byte}']))
            shadows.setdefault(child_scope, set())
        if node.type in {'function_declaration', 'method_definition', 'arrow_function', 'function_expression'}:
            child_scope = '.'.join(filter(None, [scope, text(name_node) or f'@{node.start_byte}']))
            parameters = node.child_by_field_name('parameters')
            names = set()
            pending = list(parameters.named_children) if parameters else []
            single = node.child_by_field_name('parameter')
            if single: pending.append(single)
            while pending:
                parameter = pending.pop()
                if parameter.type in {'identifier', 'shorthand_property_identifier_pattern'}:
                    names.add(text(parameter))
                elif parameter.type in {'object_pattern', 'array_pattern', 'rest_pattern'}:
                    pending.extend(parameter.named_children)
                else:
                    field = 'value' if parameter.type == 'pair_pattern' else 'left' if parameter.type in {'assignment_pattern', 'object_assignment_pattern'} else 'pattern'
                    candidate = parameter.child_by_field_name(field)
                    if candidate:
                        pending.append(candidate)
            shadows[child_scope] = names
        for child in node.named_children:
            visit(child, child_scope)

    visit(tree.root_node)
    for statement in tree.root_node.named_children:
        if statement.type != 'export_statement' or statement.child_by_field_name('source'):
            continue
        for clause in statement.named_children:
            if clause.type == 'export_clause':
                for item in clause.named_children:
                    names = [text(child) for child in item.named_children]
                    if names:
                        for symbol in symbols:
                            if symbol['name'] == names[0] and not symbol['scope']:
                                symbol['export_names'].append(names[-1])
    for call in calls:
        scope = call['scope']
        blocked = set()
        while True:
            blocked.update(shadows.get(scope, set()))
            if not scope:
                break
            scope = scope.rpartition('.')[0]
        call['shadowed'] = call['name'].split('.')[0] in blocked
    return symbols, imports, calls, 'parsed TypeScript/JavaScript' + (' (syntax errors present)' if tree.root_node.has_error else '')


def _jsonc(content):
    strings_or_comments = r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/'
    stripped = re.sub(strings_or_comments, lambda match: match.group(1) or ' ', content)
    stripped = re.sub(r'("(?:\\.|[^"\\])*")|,\s*([}\]])', lambda match: match.group(1) or match.group(2), stripped)
    return json.loads(stripped)


def read_configuration(files):
    result = {'python_roots': [''], 'tsconfigs': [], 'packages': {}}
    root_workspaces = []
    try:
        package = json.loads(files.get('package.json', {}).get('content', '{}'))
        root_workspaces = package.get('workspaces', [])
        if isinstance(root_workspaces, dict):
            root_workspaces = root_workspaces.get('packages', [])
    except (ValueError, AttributeError):
        pass
    for path, record in files.items():
        try:
            if PurePosixPath(path).name == 'pyproject.toml':
                data = tomllib.loads(record['content']).get('tool', {}).get('setuptools', {})
                base = str(PurePosixPath(path).parent)
                roots = [data.get('package-dir', {}).get('', ''), *data.get('packages', {}).get('find', {}).get('where', [])] if isinstance(data.get('packages', {}), dict) else []
                result['python_roots'].extend(posixpath.normpath(posixpath.join(base, root)) for root in roots if root)
            elif PurePosixPath(path).name == 'tsconfig.json':
                data = _jsonc(record['content']).get('compilerOptions', {})
                result['tsconfigs'].append({'directory': str(PurePosixPath(path).parent), **data})
            elif PurePosixPath(path).name == 'package.json':
                data = json.loads(record['content'])
                directory = str(PurePosixPath(path).parent)
                if path == 'package.json' or any(fnmatch.fnmatch(directory, pattern) for pattern in root_workspaces):
                    if data.get('name'):
                        result['packages'][data['name']] = {'directory': directory, 'exports': data.get('exports'), 'entry': data.get('types') or data.get('module') or data.get('main') or 'index'}
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def resolve(records: dict, configuration: dict | None = None) -> dict:
    files = records['files']
    if isinstance(files, list):
        files = {record['path']: record for record in files if record.get('status') == 'indexed'}
    config = configuration or read_configuration(files)
    snapshot = records.get('snapshot', records.get('id', ''))
    repo_id = records.get('repo_id', '')
    nodes, edges, unresolved, routes, boundaries = [], [], [], [], []
    module_paths = {}
    for path in files:
        if path.endswith('.py'):
            for root in config.get('python_roots', ['']):
                prefix = root.rstrip('/') + '/' if root and root != '.' else ''
                if path.startswith(prefix):
                    module = path[len(prefix):-3].replace('/', '.').removesuffix('.__init__')
                    module_paths[module] = path

    def ref(path, start=1, end=None):
        record = files[path]
        last = max(1, len(record['content'].splitlines()))
        return {'repo_id': repo_id, 'snapshot_id': snapshot, 'path': path, 'blob_hash': record['hash'],
                'start_line': min(start, last), 'end_line': min(end or start, last)}

    def candidate(path):
        path = posixpath.normpath(path)
        if path == '..' or path.startswith('../') or path.startswith('/'):
            return None
        choices = [path]
        if path.endswith(('.js', '.jsx', '.mjs', '.cjs')):
            choices.extend(path.rsplit('.', 1)[0] + suffix for suffix in ('.ts', '.tsx'))
        choices.extend(path + suffix for suffix in (*JS_SUFFIXES, '/index.ts', '/index.tsx', '/index.js', '/index.jsx'))
        return next((name for name in choices if name in files), None)

    def js_target(path, module):
        if module.startswith('.'):
            return candidate(posixpath.join(str(PurePosixPath(path).parent), module))
        configs = sorted(config.get('tsconfigs', []), key=lambda item: len(item['directory']), reverse=True)
        for ts in configs:
            directory = ts['directory']
            if directory != '.' and not path.startswith(directory.rstrip('/') + '/'):
                continue
            base = posixpath.join(directory, ts.get('baseUrl', '.'))
            for pattern, targets in ts.get('paths', {}).items():
                expression = '^' + re.escape(pattern).replace(r'\*', '(.*)') + '$'
                match = re.match(expression, module)
                if match:
                    for target in targets:
                        found = candidate(posixpath.join(base, target.replace('*', match.group(1) if match.groups() else '')))
                        if found:
                            return found
            if ts.get('baseUrl'):
                found = candidate(posixpath.join(base, module))
                if found:
                    return found
        for name, package in sorted(config.get('packages', {}).items(), key=lambda item: len(item[0]), reverse=True):
            if module != name and not module.startswith(name + '/'):
                continue
            subpath = module[len(name):]
            exports = package.get('exports')
            entry = exports.get('.' + subpath) if isinstance(exports, dict) and any(key.startswith('.') for key in exports) else exports if not subpath else None
            if isinstance(entry, dict):
                entry = entry.get('types') or entry.get('import') or entry.get('default')
            if entry is None and not exports:
                entry = subpath.lstrip('/') or package['entry']
            if isinstance(entry, str):
                return candidate(posixpath.join(package['directory'], entry))
        return None

    def py_target(path, module, binding):
        dots = len(module) - len(module.lstrip('.'))
        if dots:
            current = next((name for name, target in module_paths.items() if target == path), '')
            parts = current.split('.') if path.endswith('/__init__.py') else current.split('.')[:-1]
            if dots > len(parts):
                return None
            module = '.'.join([*parts[:len(parts) - dots + 1], *filter(None, module[dots:].split('.'))])
        imported = binding.get('imported', '')
        base = module_paths.get(module)
        if imported != '*' and (not base or not any(s['name'] == imported and not s.get('scope') for s in files[base].get('symbols', []))):
            return module_paths.get('.'.join(filter(None, [module, imported]))) or base
        return base

    bindings = {}
    for path, record in files.items():
        nodes.append({'id': f'{snapshot}:{path}', 'path': path, 'kind': 'file', 'evidence': ref(path, 1, max(1, len(record['content'].splitlines())))})
        for symbol in record.get('symbols', []):
            nodes.append({'id': f'{snapshot}:{path}:{symbol.get("qualified_name", symbol["name"])}:{symbol["start_line"]}', 'path': path, 'name': symbol['name'],
                          'kind': symbol['kind'], 'evidence': ref(path, symbol['start_line'], symbol['end_line'])})
            routes.extend({**route, 'source': path, 'evidence': ref(path, route['line'])} for route in symbol.get('routes', []))
        routes.extend({**call['route'], 'source': path, 'evidence': ref(path, call['line'])} for call in record.get('calls', []) if call.get('route'))
        if record.get('category') in {'dependencies', 'configuration', 'schema/migration', 'build/CI', 'tests', 'documentation'}:
            boundaries.append({'path': path, 'kind': record['category'], 'evidence': ref(path)})
        bindings[path] = {}
        for imp in record.get('imports', []):
            found = set()
            for binding in imp.get('bindings', []) or [{'local': '', 'imported': '*', 'module_binding': True}]:
                target = py_target(path, imp['module'], binding) if path.endswith('.py') else js_target(path, imp['module'])
                if target:
                    found.add(target)
                    bindings[path][(imp.get('scope', ''), binding['local'])] = {**binding, 'target': target, 'module': imp['module'], 'reexport': imp.get('reexport', False)}
            for target in sorted(found):
                edges.append({'source': path, 'target': target, 'kind': 'reexports' if imp.get('reexport') else 'tests' if record.get('category') == 'tests' else 'imports',
                              'confidence': 'parsed/resolved', 'line': imp['line'], 'evidence': ref(path, imp['line']), 'target_evidence': ref(target)})
            if not found:
                imp['resolution'] = 'external or unresolved'
                unresolved.append({'path': path, 'line': imp['line'], 'reference': imp['module'], 'reason': 'external or unresolved import', 'evidence': ref(path, imp['line'])})

    def export_target(path, name, seen=None):
        seen = set() if seen is None else seen
        if (path, name) in seen:
            return None
        seen.add((path, name))
        for symbol in files[path].get('symbols', []):
            matches = symbol['name'] == name if path.endswith('.py') else name in symbol.get('export_names', [])
            if matches and not symbol.get('scope'):
                return path, symbol
        bound = bindings[path].get(('', name))
        if bound and (path.endswith('.py') or bound.get('reexport')):
            return export_target(bound['target'], bound['imported'], seen)
        if not path.endswith('.py'):
            for item in bindings[path].values():
                if item.get('reexport') and item['imported'] == '*':
                    target = export_target(item['target'], name, seen)
                    if target:
                        return target
        if name == 'default':
            source = files[path]['content']
            match = re.search(r'export\s+default\s+(?:function\s+)?([A-Za-z_$][\w$]*)', source)
            if match:
                return export_target(path, match.group(1), seen)
        return None

    for path, record in files.items():
        for call in record.get('calls', []):
            parts = call['name'].split('.')
            scope = call.get('scope', '')
            bound = None
            while True:
                bound = bindings[path].get((scope, parts[0]))
                if bound or not scope:
                    break
                scope = scope.rpartition('.')[0]
            target = None
            if bound and not call.get('shadowed'):
                if bound['module_binding']:
                    target_path = bound['target']
                    if path.endswith('.py') and bound.get('unaliased_module') and len(parts) > 2:
                        target_path = module_paths.get('.'.join(parts[:-1]), target_path)
                    if len(parts) >= 2:
                        target = export_target(target_path, parts[-1])
                elif len(parts) == 1:
                    target = export_target(bound['target'], bound['imported'])
                elif path.endswith('.py') and bound['target'].endswith('/' + bound['imported'] + '.py'):
                    target = export_target(bound['target'], parts[-1])
            if target:
                target_path, symbol = target
                edges.append({'source': path, 'target': target_path, 'kind': 'calls', 'symbol': call['name'], 'confidence': 'parsed/resolved binding',
                              'line': call['line'], 'evidence': ref(path, call['line']), 'target_evidence': ref(target_path, symbol['start_line'], symbol['end_line'])})
            else:
                unresolved.append({'path': path, 'line': call['line'], 'reference': call['name'],
                                   'reason': 'dynamic import or computed reference' if call.get('dynamic') else 'call binding not statically resolved',
                                   'evidence': ref(path, call['line'])})
    return {'nodes': nodes, 'edges': edges, 'unresolved': unresolved, 'routes': routes, 'boundaries': boundaries,
            'capabilities': ['Python AST bindings; JavaScript/TypeScript Tree-sitter bindings',
                             'Dynamic dispatch, computed imports, runtime aliases and unsupported framework conventions remain unresolved']}


def extract(snapshot) -> dict:
    from . import repository, snapshots
    if hasattr(snapshot, 'model_dump'):
        snapshot = snapshot.model_dump()
    contents = snapshots.read_files(snapshot['id'])
    records, gaps = [], list(snapshot.get('gaps', []))
    for path, content in contents.items():
        try:
            text = content.decode('utf-8')
            if '\x00' in text or len(content) > 2 * 1024 * 1024:
                gaps.append(f'{path}: binary or oversized source retained; symbol extraction unavailable')
                continue
            suffix = PurePosixPath(path).suffix
            try:
                if suffix == '.py':
                    symbols, imports, calls, capability = parse_python(text)
                elif suffix in JS_SUFFIXES:
                    symbols, imports, calls, capability = parse_js(text, suffix)
                else:
                    symbols, imports, calls, capability = [], [], [], 'text inventory'
            except (SyntaxError, ImportError) as exc:
                symbols, imports, calls, capability = [], [], [], 'text inventory'
                gaps.append(f'{path}: {type(exc).__name__}; parsed symbols unavailable')
            records.append({'path': path, 'content': text, 'hash': hashlib.sha256(content).hexdigest(), 'status': 'indexed',
                            'symbols': symbols, 'imports': imports, 'calls': calls, 'category': repository._category(path), 'capability': capability})
        except UnicodeError:
            gaps.append(f'{path}: non-UTF8 source retained; symbol extraction unavailable')
    return {**resolve({'repo_id': snapshot['repo_id'], 'snapshot': snapshot['id'], 'files': records}), 'gaps': gaps}
