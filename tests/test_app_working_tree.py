from apps.desktop.app_git import working_tree
from pantheon.apps.store_release import git


def test_working_tree_retains_staged_unstaged_untracked_and_rename(tmp_path):
    git(tmp_path, 'init', '-q')
    for name in ['edit.txt', 'old name.txt']:
        (tmp_path / name).write_text('initial\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'initial')
    git(tmp_path, 'mv', 'old name.txt', 'new name.txt')
    (tmp_path / 'edit.txt').write_text('staged\n')
    git(tmp_path, 'add', 'edit.txt')
    (tmp_path / 'edit.txt').write_text('unstaged\n')
    (tmp_path / 'new\nfile.txt').write_text('untracked')
    state = working_tree(tmp_path)
    assert (state['staged'], state['unstaged'], state['untracked']) == (2, 1, 1)
    rename = next(c for c in state['changes'] if c['path'] == 'new name.txt')
    assert rename['original_path'] == 'old name.txt'
    assert any(c['path'] == 'new\nfile.txt' and c['untracked'] for c in state['changes'])
