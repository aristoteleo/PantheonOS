"""Private source backups, interruption and corruption recovery on actual files."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

import pytest

from pantheon.chatroom.data_fence import DataFencedError, LegacyDataLease, CONTROL_FILES, MigrationFence
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy, verify_backup
from test_agent_migration import legacy, tree
from test_agent_data_fence import child


def ownership(spec, target):
    return fence_legacy(spec, operation='migration-1', target=target, namespace='agent-1')


def user_tree(root):
    return {path: raw for path, raw in tree(root).items() if Path(path).name not in CONTROL_FILES}


def test_complete_private_backup_includes_opaque_config_and_verifies_without_sources(legacy, tmp_path):
    originals = {key: user_tree(Path(legacy[key])) for key in ('global_config', 'project_config')}
    with ownership(legacy, tmp_path / 'target') as guard:
        result = backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
        root = Path(result['directory'])
        manifest = json.loads((root / 'manifest.json').read_text())
        assert result['files'] == 11
        assert not result['ready_to_import']
        assert manifest['inventory']['issues']  # Backup is not configuration conversion.
        for entry in manifest['files']:
            blob = root / entry['blob']
            assert blob.read_bytes() == Path(entry['source']).read_bytes()
            assert not blob.stat().st_mode & 0o077
            assert sha256(blob.read_bytes()).hexdigest() == entry['sha256']
        settings = next(item for item in manifest['files'] if item['source'].endswith('settings.json'))
        assert settings['target'] is None and settings['category'] == 'opaque-configuration'
        assert b'private-do-not-read' in (root / settings['blob']).read_bytes()
        assert 'private-do-not-read' not in json.dumps(result)
        assert 'private-do-not-read' not in (root / 'manifest.json').read_text()
        assert not root.stat().st_mode & 0o077
        # A retry verifies and preserves the published snapshot, including mtimes.
        times = {p.name: p.stat().st_mtime_ns for p in root.iterdir()}
        assert backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup') == result
        assert {p.name: p.stat().st_mtime_ns for p in root.iterdir()} == times
    for key, before in originals.items():
        assert user_tree(Path(legacy[key])) == before
    shutil.rmtree(Path(legacy['project_config']))
    shutil.rmtree(Path(legacy['global_config']))
    assert verify_backup(root, digest=result['sha256']) == result


def test_backup_requires_live_matching_fence_and_safe_destination(legacy, tmp_path):
    with pytest.raises(ValueError): backup_legacy(legacy, fence=True, directory=tmp_path / 'backup')
    target = tmp_path / 'target'
    with MigrationFence([tmp_path / 'other-source'], operation='migration-1', target=target, namespace='agent-1') as wrong:
        with pytest.raises(ValueError, match='exact source'):
            backup_legacy(legacy, fence=wrong, directory=tmp_path / 'backup')
    with ownership(legacy, target) as guard:
        for destination in (tmp_path, Path(legacy['project_config']) / 'unsafe', Path(legacy['home_memory']) / 'unsafe'):
            with pytest.raises(ValueError): backup_legacy(legacy, fence=guard, directory=destination)
        # Actual private Fleet storage layout beneath global config is allowed.
        destination = Path(legacy['global_config']) / 'fleet-node' / 'backups' / 'migration-1'
        result = backup_legacy(legacy, fence=guard, directory=destination)
        assert verify_backup(result['directory'], digest=result['sha256']) == result
    with pytest.raises(DataFencedError): backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')


def test_interrupted_copy_resumes_verified_blobs_and_preserves_sources(legacy, tmp_path, monkeypatch):
    import pantheon.chatroom.migration_backup as module
    original = module._hash_file
    counter = 0
    def interrupt(path, **kwargs):
        nonlocal counter
        if kwargs.get('copy_to') is not None:
            counter += 1
            if counter == 2:
                kwargs['copy_to'].write(b'partial copy')
                raise OSError('simulated disk failure')
        return original(path, **kwargs)
    directory = tmp_path / 'backup'
    with ownership(legacy, tmp_path / 'target') as guard:
        monkeypatch.setattr(module, '_hash_file', interrupt)
        with pytest.raises(OSError): backup_legacy(legacy, fence=guard, directory=directory)
        assert not (directory / 'snapshot').exists()
        first = directory / '.pending/000000.bin'
        time = first.stat().st_mtime_ns
        monkeypatch.setattr(module, '_hash_file', original)
        result = backup_legacy(legacy, fence=guard, directory=directory)
        assert (Path(result['directory']) / first.name).stat().st_mtime_ns == time
        assert not (directory / '.pending').exists()
        assert verify_backup(result['directory'], digest=result['sha256']) == result


@pytest.mark.skipif(os.name == 'nt', reason='Requires real Windows process/locking acceptance')
def test_killed_backup_can_resume_after_reacquiring_source_fence(legacy, tmp_path):
    spec = tmp_path / 'spec.json'; spec.write_text(json.dumps(legacy))
    code = '''
import json, sys
from pathlib import Path
from pantheon.chatroom.migration import fence_legacy
import pantheon.chatroom.migration_backup as module
spec = json.loads(Path(sys.argv[1]).read_text())
original = module._hash_file
def suspended(path, **kwargs):
    if kwargs.get('copy_to') is not None:
        kwargs['copy_to'].write(b'incomplete')
        kwargs['copy_to'].flush()
        print('ready', flush=True)
        sys.stdin.read()
    return original(path, **kwargs)
module._hash_file = suspended
with fence_legacy(spec, operation='migration-1', target=sys.argv[2], namespace='agent-1') as guard:
    module.backup_legacy(spec, fence=guard, directory=sys.argv[3])
'''
    target, directory = tmp_path / 'target', tmp_path / 'backup'
    with child(code, spec, target, directory):
        assert not (directory / 'snapshot').exists()
    with pytest.raises(DataFencedError): LegacyDataLease().acquire(legacy['home_memory'])
    with ownership(legacy, target) as guard:
        result = backup_legacy(legacy, fence=guard, directory=directory)
        assert verify_backup(result['directory'], digest=result['sha256']) == result


@pytest.mark.parametrize('kind', ['content', 'manifest', 'missing', 'extra', 'symlink'])
def test_snapshot_corruption_is_detected_without_repair_or_overwrite(legacy, tmp_path, kind):
    directory = tmp_path / 'backup'
    with ownership(legacy, tmp_path / 'target') as guard:
        result = backup_legacy(legacy, fence=guard, directory=directory)
        snapshot = Path(result['directory'])
        blob = snapshot / '000000.bin'
        if kind == 'content': blob.write_bytes(b'corrupt')
        elif kind == 'manifest': (snapshot / 'manifest.json').write_text('{}')
        elif kind == 'missing': blob.unlink()
        elif kind == 'extra': (snapshot / 'unexpected').write_text('unknown')
        else:
            blob.unlink(); blob.symlink_to(legacy['home_memory'])
        before = tree(snapshot)
        with pytest.raises((ValueError, OSError)): verify_backup(snapshot, digest=result['sha256'])
        with pytest.raises((ValueError, OSError)): backup_legacy(legacy, fence=guard, directory=directory)
        assert tree(snapshot) == before


def test_changed_sources_do_not_reuse_an_old_backup_intent(legacy, tmp_path):
    directory = tmp_path / 'backup'
    with ownership(legacy, tmp_path / 'target') as guard:
        result = backup_legacy(legacy, fence=guard, directory=directory)
        snapshot = Path(result['directory']); before = tree(snapshot)
        (Path(legacy['project_config']) / 'settings.json').write_text('{"changed":"configuration"}')
        with pytest.raises(ValueError, match='different source bytes'):
            backup_legacy(legacy, fence=guard, directory=directory)
        assert tree(snapshot) == before
        # Previous immutable backup remains independently recoverable.
        assert verify_backup(snapshot, digest=result['sha256']) == result


def test_source_change_during_copy_never_publishes_partial_snapshot(legacy, tmp_path, monkeypatch):
    import pantheon.chatroom.migration_backup as module
    original = module._hash_file
    changed = False
    def change_source(path, **kwargs):
        nonlocal changed
        result = original(path, **kwargs)
        if kwargs.get('copy_to') is not None and not changed:
            changed = True
            (Path(legacy['home_memory']) / 'new-chat.json').write_text(
                '{"id":"new-chat","name":"Added","messages":[]}')
        return result
    monkeypatch.setattr(module, '_hash_file', change_source)
    with ownership(legacy, tmp_path / 'target') as guard:
        with pytest.raises(ValueError, match='sources changed'):
            backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
    assert not (tmp_path / 'backup/snapshot').exists()
    with pytest.raises(DataFencedError): LegacyDataLease().acquire(legacy['home_memory'])


def test_limits_unmapped_data_and_external_links_are_not_silently_ignored(legacy, tmp_path):
    unknown = Path(legacy['project_config']) / 'custom-extension'
    unknown.mkdir(); (unknown / 'saved.dat').write_bytes(b'keep unknown bytes')
    with ownership(legacy, tmp_path / 'target') as guard:
        with pytest.raises(ValueError): backup_legacy(legacy, fence=guard, directory=tmp_path / 'small', max_bytes=2)
        result = backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
        manifest = json.loads((Path(result['directory']) / 'manifest.json').read_text())
        entry = next(item for item in manifest['files'] if item['source'].endswith('saved.dat'))
        assert entry['target'] is None
        assert (Path(result['directory']) / entry['blob']).read_bytes() == b'keep unknown bytes'
        (unknown / 'external').symlink_to(tmp_path / 'backup')
        with pytest.raises(ValueError, match='symlink'):
            backup_legacy(legacy, fence=guard, directory=tmp_path / 'reject-link')


@pytest.mark.parametrize('kind', ['public-directory', 'symlink-directory', 'symlink-intent', 'foreign-data'])
def test_unsafe_archive_destinations_are_not_adopted_or_overwritten(legacy, tmp_path, kind):
    directory = tmp_path / 'backup'
    original = tmp_path / 'existing'; original.mkdir(mode=0o700)
    (original / 'keep').write_bytes(b'keep original data')
    if kind == 'symlink-directory': directory.symlink_to(original)
    else:
        directory.mkdir(mode=0o700)
        if kind == 'public-directory': directory.chmod(0o755)
        elif kind == 'symlink-intent': (directory / 'intent.json').symlink_to(original / 'keep')
        else: (directory / 'foreign-file').write_bytes(b'not a backup')
    with ownership(legacy, tmp_path / 'target') as guard:
        with pytest.raises(ValueError): backup_legacy(legacy, fence=guard, directory=directory)
    assert (original / 'keep').read_bytes() == b'keep original data'
    assert not (original / 'snapshot').exists()


def test_verification_rejects_wrong_digest_and_does_not_create_missing_directories(tmp_path):
    root = tmp_path / 'missing'
    with pytest.raises(FileNotFoundError): verify_backup(root, digest='0' * 64)
    assert not root.exists()
