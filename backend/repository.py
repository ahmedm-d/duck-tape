"""Read-only repository snapshots; imported code is never executed here."""
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import stat
from urllib.parse import urlparse, urljoin
import zipfile

MAX_ARCHIVE = 30 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
EXCLUDED_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build', 'vendor', '.next', '.aws', '.ssh'}
TEXT_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.json', '.md', '.rst', '.txt', '.toml', '.yaml', '.yml', '.ini', '.cfg', '.sql', '.html', '.css', '.scss', '.sh', '.xml', '.lock', '.go', '.rs', '.java', '.rb', '.c', '.h', '.cpp'}
SECRET = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,})\b|(?im:^\s*(?:api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*[\"\x27]?(?!\$|os\.|getenv|process\.|example|changeme|[\"\x27]?\s*$)[A-Za-z0-9+/=_-]{12,})')
SECRET_ASSIGNMENT = re.compile(r'''(?ix)\b(?:[a-z0-9]+_)*(?:api[_-]?key|secret|password|access[_-]?token)\b["']?\s*[:=]\s*["']?(?!\$|os\.|getenv|process\.|example|changeme)[a-z0-9+/=_-]{12,}''')


def validate_public_url(url):
    p = urlparse(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 443):
        raise ValueError('Only public HTTPS URLs on port 443 without credentials are allowed')
    try:
        addresses = sorted({x[4][0] for x in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)})
    except OSError as exc:
        raise ValueError('Cannot resolve public host') from exc
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('Private, loopback and reserved addresses are forbidden')
    return p, addresses


def fetch_public(url, max_bytes=MAX_ARCHIVE, *, provenance=None):
    """Pin validated DNS addresses and revalidate every redirect; ignore proxy environment."""
    for _ in range(5):
        p, addresses = validate_public_url(url)
        if provenance is not None:
            provenance.setdefault('requests', []).append({'url': url, 'validated_addresses': addresses, 'pinned_address': addresses[0]})
        connection = http.client.HTTPSConnection(p.hostname, timeout=20, context=ssl.create_default_context())
        raw = socket.create_connection((addresses[0], 443), timeout=20)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=p.hostname)
            connection.request('GET', p.path + ('?' + p.query if p.query else '') or '/', headers={'User-Agent': 'Steward-local-prototype/1', 'Accept': '*/*'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise ValueError(f'Public source returned HTTP {response.status}')
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError('Public source exceeds size limit')
            if provenance is not None:
                provenance['final_url'] = url
            return data
        finally:
            connection.close()
            raw.close()
    raise ValueError('Too many redirects')


safe_fetch = fetch_public


def is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def unsafe_path(name):
    path = PurePosixPath(name)
    return (not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or '\x00' in name
            or any(p.rstrip(' .') != p or p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))} for p in path.parts))


def secret_path(name):
    return any(part.casefold().startswith('.env') or part.casefold() in {'credentials', 'id_rsa', 'id_ed25519', '.npmrc', '.pypirc', '.aws', '.ssh'}
               or Path(part).suffix.casefold() in {'.pem', '.key', '.p12', '.pfx'} for part in PurePosixPath(name).parts)


def import_zip(data: bytes, destination: Path):
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Import destination must be empty')
    if len(data) > MAX_ARCHIVE:
        raise ValueError('Archive exceeds 30 MiB limit')
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('Invalid ZIP archive') from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > 24000 or sum(x.file_size for x in entries) > MAX_EXPANDED:
            raise ValueError('Archive exceeds expanded size/file count limit')
        seen = set()
        prefixes = {}
        for item in entries:
            name = item.filename
            path = PurePosixPath(name)
            mode = item.external_attr >> 16
            if (unsafe_path(name)
                or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise ValueError('Unsafe archive path or special file')
            key = str(path).casefold().rstrip('/')
            if key in seen:
                raise ValueError('Duplicate archive path')
            seen.add(key)
            for count in range(1,len(path.parts)+1):
                prefix='/'.join(path.parts[:count])
                if prefix.casefold() in prefixes and prefixes[prefix.casefold()]!=prefix:
                    raise ValueError('Archive path casing aliases another path')
                prefixes[prefix.casefold()]=prefix
        if is_link(destination):
            raise ValueError('Import destination must not be a link')
        destination.mkdir(parents=True, exist_ok=True)
        modes = {}
        for item in entries:
            target = destination.joinpath(*PurePosixPath(item.filename).parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Archive path escapes destination')
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                mode = 0o755 if (item.external_attr >> 16) & 0o111 else 0o644
                target.chmod(mode)
                modes[PurePosixPath(item.filename).as_posix()] = mode
    children = list(destination.iterdir())
    wrapper_name = None
    if (len(children) == 1 and children[0].is_dir()
            and children[0].name.casefold() not in EXCLUDED_DIRS and not secret_path(children[0].name)):
        wrapper = children[0]
        wrapper_name = wrapper.name
        for child in wrapper.iterdir():
            child.rename(destination / child.name)
        wrapper.rmdir()
        modes = {path.removeprefix(wrapper_name + '/'): mode for path, mode in modes.items()}
    return {'source': 'zip', 'archive_hash': hashlib.sha256(data).hexdigest(), 'file_modes': modes, 'normalized_root': wrapper_name}


def import_git(url: str, destination: Path, *, cancel=None):
    if cancel is not None:
        cancel.raise_if_cancelled()
    p, _ = validate_public_url(url)
    if p.hostname.lower() != 'github.com':
        from .git_transport import import_https
        return import_https(url, destination, cancel=cancel)
    parts = p.path.strip('/').removesuffix('.git').split('/')
    if p.hostname.lower() != 'github.com' or len(parts) != 2 or not all(re.fullmatch(r'[\w.-]+', x) for x in parts) or p.query:
        raise ValueError('Public Git import currently supports GitHub HTTPS repositories; upload ZIP for other hosts')
    def fetch(url, maximum=MAX_ARCHIVE):
        if cancel is not None:
            cancel.raise_if_cancelled()
        data = fetch_public(url, maximum)
        if cancel is not None:
            cancel.raise_if_cancelled()
        return data
    owner, name = parts
    metadata = json.loads(fetch(f'https://api.github.com/repos/{owner}/{name}', 1000000))
    branch = metadata['default_branch']
    from urllib.parse import quote
    commit = json.loads(fetch(f'https://api.github.com/repos/{owner}/{name}/commits/{quote(branch, safe="")}', 1000000))['sha']
    gaps = ['Archive-only history: full Git object history and submodules unavailable; external systems not inspected']
    history_entries = []
    try:
        history = json.loads(fetch(f'https://api.github.com/repos/{owner}/{name}/commits?sha={quote(commit, safe="")}&per_page=20', 1000000))
        if not isinstance(history, list):
            raise ValueError('Invalid history response')
        for entry in history[:20]:
            serialized = json.dumps(entry)
            if SECRET.search(serialized) or SECRET_ASSIGNMENT.search(serialized):
                gaps.append('Credential-bearing commit metadata excluded')
                continue
            detail = entry.get('commit', {})
            author = detail.get('author') or {}
            history_entries.append({'commit': entry['sha'], 'parents': [parent['sha'] for parent in entry.get('parents', [])[:20]],
                                    'message': str(detail.get('message', ''))[:8192], 'author': str(author.get('name', ''))[:200],
                                    'date': author.get('date'), 'trust': 'untrusted upstream metadata'})
    except (ValueError, KeyError, TypeError, OSError) as exc:
        gaps.append('Bounded commit metadata unavailable: ' + type(exc).__name__)
    data = fetch(f'https://api.github.com/repos/{owner}/{name}/zipball/{commit}')
    archive = import_zip(data, destination)
    return {**archive, 'source': url, 'branch': branch, 'commit': commit, 'history': 'archive-only',
            'history_entries': history_entries, 'history_limit': 20, 'gaps': gaps}


def _category(path):
    name = Path(path).name.lower()
    if 'test' in name or '/tests/' in '/' + path: return 'tests'
    if name in {'package.json', 'pyproject.toml', 'requirements.txt', 'cargo.toml', 'go.mod'} or 'lock' in name: return 'dependencies'
    if path.startswith('.github/') or name in {'dockerfile', 'makefile'}: return 'build/CI'
    if Path(path).suffix in {'.md', '.rst'} or name in {'readme','license','changelog','authors'}: return 'documentation'
    if 'migration' in path or Path(path).suffix == '.sql': return 'schema/migration'
    if 'config' in name or Path(path).suffix in {'.toml', '.yaml', '.yml', '.ini'}: return 'configuration'
    return 'implementation'


def _parse_python(content):
    from .symbols import parse_python
    return parse_python(content)


def _parse_js(content, suffix):
    from .symbols import parse_js
    return parse_js(content, suffix)


def index_repository(root: Path, *, repo_id='', source_snapshot_id=''):
    root = Path(root).resolve()
    files, gaps = [], []
    total = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for dirname in list(dirs):
            path = Path(directory) / dirname
            if dirname.casefold() in EXCLUDED_DIRS or is_link(path) or secret_path(path.relative_to(root).as_posix()):
                if len(files) >= 12000:
                    gaps.append('Inventory capped at 12000 entries'); break
                files.append({'path': path.relative_to(root).as_posix() + '/', 'status': 'excluded', 'reason': 'Generated/vendor/VCS directory or symlink; children not enumerated', 'symbols': []})
                dirs.remove(dirname)
        for name in sorted(names):
            if len(files) >= 12000:
                gaps.append('Inventory capped at 12000 entries'); break
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            record = {'path': rel, 'status': 'indexed', 'reason': '', 'symbols': [], 'category': _category(rel), 'language': {'.py':'Python','.ts':'TypeScript','.tsx':'TypeScript','.js':'JavaScript','.jsx':'JavaScript','.mjs':'JavaScript','.cjs':'JavaScript'}.get(path.suffix, path.suffix.lstrip('.') or 'text')}
            files.append(record)
            if is_link(path) or not path.resolve().is_relative_to(root) or secret_path(rel) or unsafe_path(rel):
                record.update(status='excluded', reason='Secret-bearing path or symlink'); continue
            if path.suffix.lower() not in TEXT_SUFFIXES and name.lower() not in {'dockerfile', 'makefile', 'license', 'readme', 'changelog', 'authors', '.gitignore', '.gitmodules'}:
                record.update(status='unsupported', reason='Unsupported extension/binary'); continue
            try:
                size = path.stat().st_size
                if size > 2 * 1024 * 1024 or total + size > MAX_EXPANDED:
                    record.update(status='excluded', reason='File/index size limit'); continue
                data = path.read_bytes()
                content = data.decode('utf-8')
                if '\x00' in content:
                    record.update(status='unsupported', reason='Binary content'); continue
                if SECRET.search(content) or SECRET_ASSIGNMENT.search(content):
                    record.update(status='excluded', reason='Potential credential material detected'); continue
                total += size
                record.update(content=content, hash=hashlib.sha256(data).hexdigest(), capability='text search only', imports=[], calls=[])
                lines = content.splitlines()
                record['chunks'] = [{'start_line': start + 1, 'end_line': min(start + 120, len(lines))} for start in range(0, len(lines), 100)]
                try:
                    if path.suffix == '.py':
                        symbols, imports, calls, capability = _parse_python(content)
                    elif path.suffix in {'.ts','.tsx','.js','.jsx','.mjs','.cjs'}:
                        symbols, imports, calls, capability = _parse_js(content, path.suffix)
                    else: symbols, imports, calls, capability = [], [], [], 'text search only'
                    record.update(symbols=symbols, imports=imports, calls=calls, capability=capability)
                except (SyntaxError, ImportError) as exc:
                    record['capability'] = 'text search only; parser unavailable or syntax invalid'
                    gaps.append(f'{rel}: {type(exc).__name__}')
                record['summary'] = f'{rel}: {record["category"]}; symbols: ' + (', '.join(s['name'] for s in record['symbols'][:20]) or 'none extracted') + '. Structural facts; synthesis unavailable.'
                if name == '.gitmodules': gaps.append('Submodules are not fetched; their external content is unavailable')
            except (OSError, UnicodeError) as exc:
                record.update(status='failed', reason=type(exc).__name__)
        if len(files) >= 12000:
            if 'Inventory capped at 12000 entries' not in gaps and dirs:
                gaps.append('Inventory capped at 12000 entries')
            break
    snapshot = hashlib.sha256(json.dumps([(f['path'],f.get('hash'),f['status']) for f in sorted(files, key=lambda x:x['path'])], separators=(',',':')).encode()).hexdigest()
    from .symbols import resolve
    graph = resolve({'snapshot': source_snapshot_id or snapshot, 'repo_id': repo_id, 'files': files})
    edges = graph['edges']
    packages = [{'path': f['path'], 'kind': f['language'], 'summary': f'Dependency manifest/lockfile at {f["path"]}. See source for declared versions and workspace structure; no runtime architecture inferred.'} for f in files if f.get('category') == 'dependencies' and f['status'] == 'indexed']
    counts = {status: sum(f['status'] == status for f in files) for status in ['indexed','excluded','unsupported','failed']}
    overview = f'Repository snapshot {snapshot[:12]}. {counts["indexed"]} indexed files; {counts["excluded"]} excluded entries; {counts["unsupported"]} unsupported; {counts["failed"]} failed. {len(edges)} source-linked relationships. Structural inventory only: model synthesis unavailable. Dynamic calls and external systems are not fully resolved.'
    return {**graph, 'repo_id': repo_id, 'source_snapshot_id': source_snapshot_id, 'snapshot': snapshot, 'files': files, 'overview': overview, 'gaps': gaps + ['Dynamic calls are incomplete; external systems are not inspected'], 'packages': packages, 'coverage': counts}
