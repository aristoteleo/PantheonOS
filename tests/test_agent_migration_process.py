"""Process-death recovery for real backed-up legacy conversation imports.

Synthetic user files use the ordinary inventory, backup, SQLite identity store,
source fences and destination admission. No live profiles or credentials touched.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from pantheon.chatroom.app_data import AgentAppData
from pantheon.chatroom.data_fence import DataFencedError, LegacyDataLease
from pantheon.chatroom.data_transition import transition_state
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_import import import_backup
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_agent_migration_import import prepared, view


@pytest.mark.parametrize('phase', ['file-copy', 'identity-seed', 'receipt', 'commit'])
def test_import_recovers_after_process_exit_without_replaying_or_overwriting(prepared, tmp_path, phase):
    spec, guard, backup, root = prepared
    originals = {key: user_tree(Path(spec[key])) for key in ('home_memory', 'global_config', 'project_config')}
    guard.close()  # Child must acquire the real exclusive source leases itself.
    request = tmp_path/'crash-import.json'
    request.write_text(json.dumps(dict(spec=spec, backup=backup, root=str(root), phase=phase)))
    request.chmod(0o600)
    code = '''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from pantheon.chatroom.migration import fence_legacy
import pantheon.chatroom.migration_import as module
from pantheon.factory.instance_store import AgentInstanceStore
request = json.loads(Path(sys.argv[2]).read_text())
phase = request['phase']
if phase == 'file-copy':
    original = module._copy
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(73)
    module._copy = interrupted
elif phase == 'identity-seed':
    original = AgentInstanceStore.seed_legacy_members
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(73)
    AgentInstanceStore.seed_legacy_members = interrupted
else:
    original = module._atomic_json
    def interrupted(path, value):
        original(path, value)
        if ((phase == 'receipt' and path.name == 'migration-receipt.json') or
                (phase == 'commit' and path.name == 'migration.json' and value['phase'] == 'committed')):
            os._exit(73)
    module._atomic_json = interrupted
with fence_legacy(request['spec'], operation='move', target=request['root'], namespace='migrated-agent') as guard:
    module.import_backup(request['backup']['directory'], digest=request['backup']['sha256'], fence=guard)
raise AssertionError('fault did not execute')
'''
    child = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parents[1]),
                            str(request)], capture_output=True, text=True, timeout=30)
    assert child.returncode == 73, child.stdout + child.stderr
    state = transition_state(root)
    assert state['phase'] == ('committed' if phase == 'commit' else 'importing')
    for key in originals:
        assert user_tree(Path(spec[key])) == originals[key]
        with pytest.raises(DataFencedError): LegacyDataLease().acquire(spec[key])
    if phase != 'commit':
        with pytest.raises(ValueError, match='not committed'):
            AgentAppData(root, namespace='migrated-agent', projects=view(spec))
    database = root/'instances/instances.sqlite3'
    with sqlite3.connect(database) as db:
        before = db.execute('SELECT * FROM instances ORDER BY conversation_id').fetchall()
    with fence_legacy(spec, operation='move', target=root, namespace='migrated-agent') as resumed:
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=resumed)
        assert receipt['conversations'] == 2 and len(receipt['members']) == 2
        with sqlite3.connect(database) as db:
            after = db.execute('SELECT * FROM instances ORDER BY conversation_id').fetchall()
        if before:
            assert after == before
        app = AgentAppData(root, namespace='migrated-agent', projects=view(spec))
        app.close()
        # A lost reply after commit must not cause another copy over live data.
        copied_history = next(root/row['target'] for row in receipt['files'] if row['target'].endswith('.jsonl'))
        with copied_history.open('ab') as stream:
            stream.write(b'{"role":"user","content":"new destination write"}\n')
        live_history = copied_history.read_bytes()
        assert import_backup(backup['directory'], digest=backup['sha256'], fence=resumed) == receipt
        assert copied_history.read_bytes() == live_history
    for key in originals:
        assert user_tree(Path(spec[key])) == originals[key]
