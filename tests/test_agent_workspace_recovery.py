"""Recover actual archived workspace structures without touching the originals."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_recovery import recover_tree
from test_agent_migration import legacy
from test_agent_migration_backup import ownership

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX workspace permission recovery')


@pytest.fixture
def workspace(legacy):
    root = Path(legacy['project_config']) / 'workspaces/chat-one'
    (root / 'bin').mkdir(parents=True)
    (root / 'empty').mkdir()
    (root / 'empty').chmod(0o750)
    program = root / 'bin/run'
    program.write_text('#!/bin/sh\nprintf recovered')
    program.chmod(0o751)
    (root / 'bin/alias').symlink_to('run')
    (root / 'absolute').symlink_to(program)
    (root / 'dangling').symlink_to('empty/future')
    return legacy, root


def backup(spec, tmp_path):
    with ownership(spec, tmp_path / 'app') as guard:
        return backup_legacy(spec, fence=guard, directory=tmp_path / 'backup')


def recover(saved, source, target):
    return recover_tree(saved['directory'], digest=saved['sha256'], source=source, directory=target)


def test_workspace_recovers_after_source_loss_and_preserves_execution(workspace, tmp_path):
    spec, source = workspace
    saved = backup(spec, tmp_path)
    shutil.rmtree(source)
    receipt = recover(saved, source, tmp_path / 'recovered')
    assert receipt['phase'] == 'complete'
    root = Path(receipt['tree'])
    assert (root / 'empty').is_dir() and not list((root / 'empty').iterdir())
    assert (root / 'empty').stat().st_mode & 0o777 == 0o750
    assert (root / 'bin/run').stat().st_mode & 0o777 == 0o751
    assert os.readlink(root / 'bin/alias') == 'run'
    assert not os.path.isabs(os.readlink(root / 'absolute'))
    assert (root / 'absolute').resolve() == root / 'bin/run'
    assert os.readlink(root / 'dangling') == 'empty/future'
    # This is test-authored code, never execution of archived user programs.
    assert subprocess.check_output([str(root / 'absolute')]) == b'recovered'
    assert (tmp_path / 'recovered').stat().st_mode & 0o077 == 0
    assert not source.exists()
    with pytest.raises(FileExistsError):
        recover(saved, source, tmp_path / 'recovered')


@pytest.mark.parametrize('kind', ['external', 'relative-escape', 'cycle', 'chained-escape', 'privileged'])
def test_unsafe_recovery_needs_conversion_before_any_destination_is_created(workspace, tmp_path, kind):
    spec, source = workspace
    if kind == 'external': (source / 'outside').symlink_to('/usr/bin/python3')
    elif kind == 'relative-escape': (source / 'outside').symlink_to('../other-workspace')
    elif kind == 'cycle': (source / 'cycle').symlink_to('cycle')
    elif kind == 'chained-escape':
        (source / 'bin/root').symlink_to('..')
        (source / 'escape').symlink_to('bin/root/../outside')
    else: (source / 'bin/run').chmod(0o4751)
    saved = backup(spec, tmp_path)
    with pytest.raises(ValueError):
        recover(saved, source, tmp_path / 'recovered')
    assert not (tmp_path / 'recovered').exists()
    assert (source / 'bin/run').read_text().endswith('recovered')


def test_recovery_never_overwrites_foreign_or_source_trees(workspace, tmp_path):
    spec, source = workspace
    saved = backup(spec, tmp_path)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'keep').write_text('user file')
    with pytest.raises(FileExistsError): recover(saved, source, foreign)
    assert (foreign / 'keep').read_text() == 'user file'
    for target in (source, source / 'copy', source.parent, Path(saved['directory']) / 'copy'):
        with pytest.raises(ValueError): recover(saved, source, target)
    assert not (source / 'copy').exists()


def test_failed_recovery_is_not_published_and_can_retry_to_new_destination(workspace, tmp_path, monkeypatch):
    from pantheon.chatroom import migration_recovery as module
    spec, source = workspace
    saved = backup(spec, tmp_path)
    original = module._hash_file
    def interrupt(path, **kwargs):
        kwargs['copy_to'].write(b'partial')
        raise OSError('interrupted copy')
    monkeypatch.setattr(module, '_hash_file', interrupt)
    with pytest.raises(OSError): recover(saved, source, tmp_path / 'failed')
    assert not (tmp_path / 'failed/tree').exists()
    assert json.loads((tmp_path / 'failed/recovery.json').read_text())['phase'] == 'incomplete'
    monkeypatch.setattr(module, '_hash_file', original)
    receipt = recover(saved, source, tmp_path / 'retry')
    assert receipt['phase'] == 'complete'
    assert (source / 'bin/run').read_bytes() == (Path(receipt['tree']) / 'bin/run').read_bytes()


def test_recovery_checks_blob_integrity_before_publication(workspace, tmp_path, monkeypatch):
    from pantheon.chatroom import migration_recovery as module
    spec, source = workspace
    saved = backup(spec, tmp_path)
    original = module._hash_file
    def mismatch(path, **kwargs):
        result = original(path, **kwargs)
        return {**result, 'sha256': '0' * 64}
    monkeypatch.setattr(module, '_hash_file', mismatch)
    with pytest.raises(ValueError, match='changed during recovery'):
        recover(saved, source, tmp_path / 'changed')
    assert not (tmp_path / 'changed/tree').exists()


def test_internal_absolute_link_keeps_symlink_then_parent_semantics(workspace, tmp_path):
    spec, source = workspace
    (source / 'a').mkdir()
    (source / 'b/c').mkdir(parents=True)
    (source / 'a/inner').symlink_to('../b/c')
    (source / 'a/file').write_text('wrong lexical normalization')
    (source / 'b/file').write_text('correct symlink resolution')
    (source / 'complex').symlink_to(source / 'a/inner/../file')
    saved = backup(spec, tmp_path)
    shutil.rmtree(source)
    root = Path(recover(saved, source, tmp_path / 'recovered')['tree'])
    assert (root / 'complex').read_text() == 'correct symlink resolution'
