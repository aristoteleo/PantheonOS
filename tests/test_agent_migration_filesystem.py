"""Archive metadata needed before relocating or recovering a legacy workspace."""
import json
from pathlib import Path

import pytest

from pantheon.chatroom.migration_backup import backup_legacy, verify_backup
from test_agent_migration import legacy
from test_agent_migration_backup import ownership


def test_backup_captures_empty_directories_and_executable_modes(legacy, tmp_path):
    workspace = Path(legacy['project_config']) / 'workspaces/chat-one'
    empty = workspace / 'empty'
    empty.mkdir(parents=True)
    empty.chmod(0o750)
    script = workspace / 'run.sh'
    script.write_text('#!/bin/sh\nprintf restored')
    script.chmod(0o751)
    with ownership(legacy, tmp_path / 'app') as guard:
        result = backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
    snapshot = Path(result['directory'])
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    assert manifest['filesystem_metadata'] == 1
    assert next(d for d in manifest['directories'] if d['source'] == str(empty))['source_mode'] == 0o750
    row = next(f for f in manifest['files'] if f['source'] == str(script))
    assert row['source_mode'] == 0o751
    assert (snapshot / row['blob']).stat().st_mode & 0o777 == 0o600
    assert verify_backup(snapshot, digest=result['sha256']) == result


@pytest.mark.parametrize('change', ['empty-directory', 'file-mode', 'directory-mode'])
def test_metadata_change_during_copy_is_not_published(legacy, tmp_path, monkeypatch, change):
    from pantheon.chatroom import migration_backup as module
    root = Path(legacy['project_config']) / 'brain'
    original = module._hash_file
    changed = False
    def mutate(path, **kwargs):
        nonlocal changed
        result = original(path, **kwargs)
        if kwargs.get('copy_to') is not None and not changed:
            changed = True
            if change == 'empty-directory': (root / 'new-empty').mkdir()
            elif change == 'file-mode': (root / 'saved.md').chmod(0o700)
            else: root.chmod(0o750 if root.stat().st_mode & 0o777 != 0o750 else 0o700)
        return result
    monkeypatch.setattr(module, '_hash_file', mutate)
    with ownership(legacy, tmp_path / 'app') as guard:
        with pytest.raises(ValueError, match='sources changed'):
            backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
    assert not (tmp_path / 'backup/snapshot').exists()
