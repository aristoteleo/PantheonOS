"""Working-tree state kept separate from immutable release history."""
from pathlib import Path

from pantheon.apps.store_release import git


def working_tree(root: Path) -> dict:
    entries = iter(git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split('\0'))
    changes = []
    for entry in entries:
        if not entry:
            continue
        index, worktree, path = entry[0], entry[1], entry[3:]
        original = next(entries, '') if 'R' in (index, worktree) or 'C' in (index, worktree) else None
        changes.append({'path': path, 'original_path': original, 'index': index, 'worktree': worktree,
                        'untracked': index == '?' and worktree == '?'})
    return {'branch': git(root, 'branch', '--show-current').strip(), 'changes': changes,
            'staged': sum(c['index'] not in (' ', '?') for c in changes),
            'unstaged': sum(c['worktree'] not in (' ', '?') for c in changes),
            'untracked': sum(c['untracked'] for c in changes)}
