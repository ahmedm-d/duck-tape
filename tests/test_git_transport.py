import shutil
import socket
import socketserver
import threading
from urllib.parse import urlparse

import pytest

from backend import repository, sample


@pytest.mark.parametrize('authority', ['127.0.0.1:443', 'localhost:443', 'example.com:80', 'user:pass@example.com:443'])
def test_git_proxy_rejects_private_ports_and_credentials(authority):
    from backend import git_transport
    with git_transport.PinnedHTTPSProxy(timeout=5) as broker:
        with socket.create_connection(broker.address, timeout=2) as client:
            client.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
            assert client.recv(100).startswith(b'HTTP/1.1 403')


@pytest.mark.parametrize('behavior', ['byte_limit', 'dns_rebinding'])
def test_git_proxy_pins_validated_address_and_bounds_tunnel_bytes(monkeypatch, behavior):
    from backend import git_transport
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while data := self.request.recv(4096):
                self.request.sendall(data)
    upstream = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo)
    upstream.daemon_threads = True
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    actual_connect, actual_resolve = socket.create_connection, socket.getaddrinfo
    pinned = []
    current_address = ['1.1.1.1']
    def resolve(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (current_address[0], 443))] if host == 'public.example' else actual_resolve(host, *args, **kwargs)
    def connect(address, *args, **kwargs):
        if address == ('1.1.1.1', 443):
            pinned.append(address)
            return actual_connect(upstream.server_address, *args, **kwargs)
        return actual_connect(address, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(socket, 'create_connection', connect)
    try:
        with git_transport.PinnedHTTPSProxy(timeout=5, max_bytes=32) as broker:
            with socket.create_connection(broker.address, timeout=2) as client:
                client.sendall(b'CONNECT public.example:443 HTTP/1.1\r\n\r\n')
                assert client.recv(100).startswith(b'HTTP/1.1 200')
                client.sendall(b'asset')
                assert client.recv(5) == b'asset'
                if behavior == 'byte_limit':
                    client.sendall(b'x' * 64)
                    try:
                        assert not client.recv(100)
                    except ConnectionResetError:
                        pass
            if behavior == 'byte_limit':
                assert 'byte' in broker.error.lower()
            else:
                current_address[0] = '127.0.0.1'
                with socket.create_connection(broker.address, timeout=2) as client:
                    client.sendall(b'CONNECT public.example:443 HTTP/1.1\r\n\r\n')
                    assert client.recv(100).startswith(b'HTTP/1.1 403')
        assert pinned == [('1.1.1.1', 443)]
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(2)


def test_generic_git_export_preserves_bytes_modes_and_ignores_archive_attributes(tmp_path, monkeypatch):
    from backend import db, git_transport
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    source = tmp_path / 'reviewed-fixture'
    source.mkdir()
    (source / '.gitattributes').write_bytes(b'asset.bin export-ignore\nrun.sh export-subst\n')
    (source / 'asset.bin').write_bytes(b'\x00\xffasset\r\n')
    (source / 'run.sh').write_bytes(b'#!/bin/sh\r\necho "$Format:%H$"\r\n')
    sample.git(source, 'init', '-b', 'main')
    sample.git(source, 'add', '.')
    sample.git(source, 'update-index', '--chmod=+x', 'run.sh')
    sample.git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-m', 'Untrusted commit text is metadata')
    monkeypatch.setattr(repository, 'validate_public_url', lambda url: (urlparse(url), ['1.1.1.1']))
    def clone(session, url, target):
        shutil.copytree(source / '.git', target)
    monkeypatch.setattr(git_transport, '_clone', clone)
    result = git_transport.import_https('https://public.example/project.git', tmp_path / 'import')
    assert (tmp_path / 'import' / 'asset.bin').read_bytes() == b'\x00\xffasset\r\n'
    assert (tmp_path / 'import' / 'run.sh').read_bytes() == b'#!/bin/sh\r\necho "$Format:%H$"\r\n'
    assert result['file_modes']['run.sh'] == 0o755
    assert result['branch'] == 'main'
    assert result['history_entries'][0]['trust'] == 'untrusted upstream metadata'
    assert not (tmp_path / 'import' / '.git').exists()


@pytest.mark.parametrize('name', ['../escape', 'C:/escape', 'NUL.py', 'a./b.py', 'a\\b.py'])
def test_generic_git_rejects_unsafe_tree_paths(name):
    from backend import git_transport
    tree = b'100644 blob ' + b'a' * 40 + b' 3\t' + name.encode() + b'\x00'
    with pytest.raises(ValueError):
        git_transport._parse_tree(tree)


@pytest.mark.parametrize('url', ['ssh://public.example/repo', 'https://user:pass@example.com/repo', '--upload-pack=evil', 'https://127.0.0.1/repo'])
def test_generic_git_rejects_unsafe_urls_before_fetch(tmp_path, url):
    from backend import git_transport
    with pytest.raises(ValueError):
        git_transport.import_https(url, tmp_path / 'import')


def test_generic_git_cancellation_prevents_dns_or_process_start(tmp_path, monkeypatch):
    from backend import git_transport
    from backend.contracts import CancellationToken, CancelledError
    def unexpected(url):
        raise AssertionError('Cancelled import contacted DNS')
    monkeypatch.setattr(repository, 'validate_public_url', unexpected)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancelledError):
        git_transport.import_https('https://example.com/repo.git', tmp_path / 'import', cancel=token)


def test_generic_git_command_output_is_bounded(tmp_path):
    from backend import git_transport
    with git_transport.PinnedHTTPSProxy(timeout=5) as broker:
        session = git_transport.GitSession(tmp_path, broker)
        with pytest.raises(ValueError, match='output/log limit'):
            session.run(['--version'], output_limit=1)


def test_git_transport_does_not_inherit_global_config_or_credentials(tmp_path, monkeypatch):
    from backend import git_transport
    host_config = tmp_path / 'host-config'
    host_config.write_text('[steward]\nmarker = host-only\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(host_config))
    monkeypatch.setenv('SSH_AUTH_SOCK', 'must-not-inherit')
    monkeypatch.setenv('GIT_ASKPASS', 'must-not-run')
    with git_transport.PinnedHTTPSProxy(timeout=5) as broker:
        session = git_transport.GitSession(tmp_path, broker)
        with pytest.raises(ValueError, match='transport failed'):
            session.run(['config', '--get', 'steward.marker'])
        assert 'SSH_AUTH_SOCK' not in session.env and 'GIT_ASKPASS' not in session.env


def test_git_broker_deadline_is_enforced():
    from backend import git_transport
    with git_transport.PinnedHTTPSProxy(timeout=-1) as broker:
        with pytest.raises(ValueError, match='deadline'):
            broker.check()


def test_git_inventory_rejects_case_alias_directories():
    from backend import git_transport
    tree = b'100644 blob ' + b'a' * 40 + b' 1\tDir/one.py\x00'
    tree += b'100644 blob ' + b'b' * 40 + b' 1\tdir/two.py\x00'
    with pytest.raises(ValueError, match='alias'):
        git_transport._parse_tree(tree)


@pytest.mark.parametrize('redirect', ['https://user:pass@public.example/repo/info/refs?service=git-upload-pack', 'https://127.0.0.1/repo/info/refs?service=git-upload-pack'])
def test_git_advertisement_redirect_revalidates_credentials_and_destinations(monkeypatch, redirect):
    from backend import git_transport
    def response(url, max_bytes, *, provenance):
        provenance['final_url'] = redirect
        return b'001e# service=git-upload-pack\n0000'
    monkeypatch.setattr(repository, 'fetch_public', response)
    with pytest.raises(ValueError):
        git_transport._resolve_git_endpoint('https://public.example/repo')
