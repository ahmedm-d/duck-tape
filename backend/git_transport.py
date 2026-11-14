"""Read-only HTTPS Git fetch through a DNS-pinned, bounded egress broker."""
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import socketserver
import subprocess
import threading
import time
from urllib.parse import urlunparse
import uuid

from . import db, repository

MAX_SECONDS = 120
MAX_DISK = 240 * 1024 * 1024
MAX_LOG = 1024 * 1024


class PinnedHTTPSProxy:
    def __init__(self, *, timeout=MAX_SECONDS, max_bytes=repository.MAX_ARCHIVE, cancel=None):
        self.deadline = time.monotonic() + timeout
        self.max_bytes, self.cancel = max_bytes, cancel
        self.bytes = 0
        self.error = ''
        self.destinations = []
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.sockets = set()

    def check(self):
        if self.cancel is not None:
            self.cancel.raise_if_cancelled()
        if self.stopped.is_set() or time.monotonic() >= self.deadline:
            raise ValueError('Git HTTPS transport deadline exceeded or stopped')
        if self.error:
            raise ValueError(self.error)

    def __enter__(self):
        broker = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                upstream, established = None, False
                try:
                    self.request.settimeout(5)
                    header = b''
                    while b'\r\n\r\n' not in header:
                        broker.check()
                        part = self.request.recv(4096)
                        if not part:
                            return
                        header += part
                        if len(header) > 8192:
                            raise ValueError('Git proxy header limit exceeded')
                    request, pending = header.split(b'\r\n\r\n', 1)
                    method, authority, version = request.split(b'\r\n', 1)[0].decode('ascii').split(' ')
                    if method != 'CONNECT' or version not in {'HTTP/1.0', 'HTTP/1.1'}:
                        raise ValueError('Git broker permits HTTPS CONNECT only')
                    parsed, addresses = repository.validate_public_url('https://' + authority + '/')
                    if parsed.port != 443 or parsed.path != '/' or parsed.query:
                        raise ValueError('Git broker permits public port 443 only')
                    broker.check()
                    with broker.lock:
                        if len(broker.destinations) >= 32:
                            raise ValueError('Git HTTPS connection limit exceeded')
                        broker.destinations.append({'host': parsed.hostname, 'validated_addresses': addresses, 'pinned_address': addresses[0]})
                    upstream = socket.create_connection((addresses[0], 443), timeout=min(10, max(.1, broker.deadline - time.monotonic())))
                    upstream.settimeout(5)
                    with broker.lock:
                        broker.sockets.update([self.request, upstream])
                    self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    established = True

                    def forward(data, target):
                        with broker.lock:
                            broker.bytes += len(data)
                            if broker.bytes > broker.max_bytes:
                                raise ValueError('Git HTTPS byte limit exceeded')
                        target.sendall(data)

                    if pending:
                        forward(pending, upstream)
                    while True:
                        broker.check()
                        readable, _, _ = select.select([self.request, upstream], [], [], .1)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            forward(data, upstream if source is self.request else self.request)
                except Exception as exc:
                    with broker.lock:
                        broker.error = broker.error or str(exc)
                    if not established:
                        try:
                            self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                        except OSError:
                            pass
                finally:
                    with broker.lock:
                        broker.sockets.discard(self.request)
                        broker.sockets.discard(upstream)
                    if upstream:
                        upstream.close()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False

        self.server = Server(('127.0.0.1', 0), Handler)
        self.address = self.server.server_address
        self.url = f'http://127.0.0.1:{self.address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        with self.lock:
            for connection in self.sockets:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class GitSession:
    def __init__(self, workspace, broker):
        self.workspace, self.broker = workspace, broker
        self.executable = shutil.which('git')
        if not self.executable:
            raise ValueError('Trusted Git executable unavailable')
        self.templates = workspace / 'empty-template'
        self.templates.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
                        GIT_ALLOW_PROTOCOL='https', GIT_ATTR_NOSYSTEM='1', GIT_LFS_SKIP_SMUDGE='1',
                        HTTPS_PROXY=broker.url, HTTP_PROXY=broker.url, NO_PROXY='')
        self.options = ['--no-pager', '-c', 'core.longpaths=true', '-c', 'core.hooksPath=' + str(self.templates),
                        '-c', 'core.attributesFile=' + os.devnull, '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false',
                        '-c', 'credential.helper=', '-c', 'credential.interactive=false', '-c', 'protocol.allow=never',
                        '-c', 'protocol.https.allow=always', '-c', 'http.proxy=' + broker.url, '-c', 'http.sslVerify=true',
                        '-c', 'http.followRedirects=false', '-c', 'fetch.recurseSubmodules=false', '-c', 'submodule.recurse=false',
                        '-c', 'transfer.fsckObjects=true', '-c', 'pack.threads=1', '-c', 'pack.windowMemory=16m',
                        '-c', 'core.packedGitLimit=64m', '-c', 'gc.auto=0']
        if os.name == 'nt':
            self.options.extend(['-c', 'http.sslBackend=openssl'])

    def run(self, args, *, cwd=None, data=None, output_limit=MAX_LOG, output_file=None):
        self.broker.check()
        output = output_file or self.workspace / (uuid.uuid4().hex + '.out')
        errors = self.workspace / (uuid.uuid4().hex + '.err')
        command = [self.executable, *self.options, *args]
        with output.open('wb') as out, errors.open('wb') as err:
            process = subprocess.Popen(command, cwd=cwd or self.workspace, env=self.env, stdout=out, stderr=err,
                                       stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
                                       start_new_session=os.name != 'nt')
            try:
                if data is not None:
                    process.stdin.write(data)
                    process.stdin.close()
                while process.poll() is None:
                    self.broker.check()
                    if output.stat().st_size > output_limit or errors.stat().st_size > MAX_LOG:
                        raise ValueError('Git command output/log limit exceeded')
                    size = 0
                    for directory, _, names in os.walk(self.workspace):
                        for name in names:
                            try:
                                size += (Path(directory) / name).stat().st_size
                            except FileNotFoundError:
                                pass
                    if size > MAX_DISK:
                        raise ValueError('Git import disk limit exceeded')
                    time.sleep(.05)
                self.broker.check()
            except BaseException:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise
        try:
            if output.stat().st_size > output_limit or errors.stat().st_size > MAX_LOG:
                raise ValueError('Git command output/log limit exceeded')
            if process.returncode:
                detail = errors.read_bytes()[:2000].decode('utf-8', errors='replace')
                if repository.SECRET.search(detail) or repository.SECRET_ASSIGNMENT.search(detail):
                    detail = 'Credential-bearing diagnostic excluded'
                raise ValueError('Read-only Git transport failed: ' + detail)
            return output.read_bytes() if output_file is None else output
        finally:
            errors.unlink(missing_ok=True)
            if output_file is None:
                output.unlink(missing_ok=True)


def _resolve_git_endpoint(url):
    provenance = {}
    advertisement = repository.fetch_public(url.rstrip('/') + '/info/refs?service=git-upload-pack', 1024 * 1024, provenance=provenance)
    if not advertisement.startswith(b'001e# service=git-upload-pack\n'):
        raise ValueError('Public Git source does not advertise smart HTTPS upload-pack')
    parsed, _ = repository.validate_public_url(provenance['final_url'])
    if not parsed.path.endswith('/info/refs') or parsed.query != 'service=git-upload-pack':
        raise ValueError('Unsupported Git advertisement redirect')
    resolved = urlunparse((parsed.scheme, parsed.netloc, parsed.path[:-len('/info/refs')], '', '', ''))
    return resolved, provenance


def _clone(session, url, target):
    resolved, provenance = _resolve_git_endpoint(url)
    session.provenance = provenance
    session.run(['clone', '--bare', '--no-checkout', '--depth=20', '--single-branch', '--no-tags', '--no-local',
                 '--template=' + str(session.templates), '--', resolved, str(target)])


def _parse_tree(data):
    entries, seen, total = [], set(), 0
    prefixes = {}
    for row in data.split(b'\x00'):
        if not row:
            continue
        try:
            prefix, raw_path = row.split(b'\t', 1)
            mode, kind, oid, size = prefix.split()
            path = raw_path.decode('utf-8')
        except (ValueError, UnicodeError) as exc:
            raise ValueError('Invalid or non-UTF8 Git inventory') from exc
        if (repository.unsafe_path(path) or any(ord(char) < 32 for char in path)
                or path.casefold() in seen or not re.fullmatch(rb'[0-9a-f]{40}|[0-9a-f]{64}', oid)):
            raise ValueError('Unsafe or duplicate Git source path')
        parts = path.split('/')
        if len(path) > 4096 or len(parts) > 128:
            raise ValueError('Git path exceeds portable depth/length limits')
        for depth in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:depth])
            previous = prefixes.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                raise ValueError('Git paths contain a case alias')
        seen.add(path.casefold())
        if len(seen) > 12000:
            raise ValueError('Git inventory exceeds 12000-entry limit')
        if mode not in {b'100644', b'100755', b'120000', b'160000'} or kind != (b'commit' if mode == b'160000' else b'blob'):
            raise ValueError('Unsupported Git source object')
        length = 0 if size == b'-' else int(size)
        total += length
        if length < 0 or total > repository.MAX_EXPANDED:
            raise ValueError('Git source exceeds expanded size limit')
        entries.append({'path': path, 'mode': mode, 'oid': oid, 'size': length, 'kind': kind})
    return entries


def import_https(url, destination, *, cancel=None):
    if cancel is not None:
        cancel.raise_if_cancelled()
    if not isinstance(url, str) or any(ord(char) < 33 for char in url):
        raise ValueError('A public HTTPS Git URL without whitespace is required')
    parsed, _ = repository.validate_public_url(url)
    if parsed.query or not parsed.path.strip('/'):
        raise ValueError('Git repository URL must not contain query options')
    destination = Path(destination)
    if repository.is_link(destination) or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise ValueError('Git import destination must be an empty regular directory')
    base = (db.DATA_DIR / 'ingestion').resolve()
    scratch = base / uuid.uuid4().hex
    scratch.mkdir(parents=True)
    try:
        with PinnedHTTPSProxy(cancel=cancel) as broker:
            session = GitSession(scratch, broker)
            bare = scratch / 'repository.git'
            _clone(session, url, bare)
            commit = session.run(['rev-parse', 'HEAD'], cwd=bare).decode().strip()
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
                raise ValueError('Invalid Git commit identity')
            branch = session.run(['symbolic-ref', '--short', 'HEAD'], cwd=bare).decode().strip()
            entries = _parse_tree(session.run(['ls-tree', '-rlz', '--full-tree', commit], cwd=bare, output_limit=4 * 1024 * 1024))
            files = [entry for entry in entries if entry['mode'] in {b'100644', b'100755'}]
            gaps = ['Git history limited to at most 20 commits on the fetched default branch; other branches and external systems unavailable']
            gaps.extend(entry['path'] + ': submodule or symbolic link not fetched; external content unavailable' for entry in entries if entry['mode'] not in {b'100644', b'100755'})
            packed = scratch / 'raw-blobs'
            session.run(['cat-file', '--batch'], cwd=bare, data=b''.join(entry['oid'] + b'\n' for entry in files),
                        output_file=packed, output_limit=repository.MAX_EXPANDED + len(files) * 128)
            destination.mkdir(parents=True, exist_ok=True)
            modes = {}
            with packed.open('rb') as stream:
                for entry in files:
                    broker.check()
                    header = stream.readline(160).split()
                    if header != [entry['oid'], b'blob', str(entry['size']).encode()]:
                        raise ValueError('Git blob identity/size mismatch')
                    content = stream.read(entry['size'])
                    if len(content) != entry['size'] or stream.read(1) != b'\n':
                        raise ValueError('Incomplete Git source blob')
                    if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                        gaps.append(entry['path'] + ': Git LFS pointer retained; external payload not fetched')
                    target = destination.joinpath(*entry['path'].split('/'))
                    if not target.resolve().is_relative_to(destination.resolve()):
                        raise ValueError('Git path escapes destination')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    modes[entry['path']] = 0o755 if entry['mode'] == b'100755' else 0o644
                    target.chmod(modes[entry['path']])
            history = []
            raw_history = session.run(['log', '-20', '--format=%H%x00%P%x00%an%x00%aI%x00%s', commit], cwd=bare)
            for row in raw_history.decode('utf-8', errors='replace').splitlines():
                fields = row.split('\x00')
                if len(fields) != 5 or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', fields[0]):
                    gaps.append('Unparseable commit metadata excluded')
                    continue
                if repository.SECRET.search(row) or repository.SECRET_ASSIGNMENT.search(row):
                    gaps.append('Credential-bearing commit metadata excluded')
                    continue
                sha, parents, author, date, message = fields
                history.append({'commit': sha, 'parents': parents.split(), 'author': author[:200], 'date': date,
                                'message': message[:8192], 'trust': 'untrusted upstream metadata'})
            return {'source': url, 'branch': branch, 'commit': commit, 'history': 'bounded shallow Git fetch', 'history_limit': 20,
                    'history_entries': history, 'gaps': gaps, 'file_modes': modes,
                    'transport': {'kind': 'DNS-pinned public HTTPS CONNECT broker', 'bytes': broker.bytes,
                                  'destinations': broker.destinations, 'advertisement': getattr(session, 'provenance', {})}}
    finally:
        if not scratch.resolve().is_relative_to(base.resolve()):
            raise ValueError('Refusing cleanup outside ingestion directory')
        def writable_remove(function, path, error):
            os.chmod(path, 0o700)
            function(path)
        shutil.rmtree(scratch, onerror=writable_remove)
