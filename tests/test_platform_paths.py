from backend import sample


def test_git_supports_deep_owned_working_directory(tmp_path):
    root = tmp_path / ('working-' + 'a' * 90)
    root.mkdir()
    (root / 'hello.txt').write_text('hello\n', encoding='utf-8')
    sample.git(root, 'init', '-b', 'test')
    sample.git(root, 'add', '.')
    assert 'hello.txt' in sample.git(root, 'ls-files')
