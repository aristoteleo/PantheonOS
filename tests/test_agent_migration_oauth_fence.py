"""Real OAuth transactions and legacy migration share the same source fence."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from pantheon.chatroom.migration import fence_legacy
from pantheon.utils.local_data_ownership import DataFencedError, MARKER_NAME
from pantheon.utils.oauth import codex, gemini
from test_agent_migration import legacy


@pytest.mark.parametrize("provider", [codex, gemini])
def test_default_managers_honor_durable_source_reservation(legacy, tmp_path, monkeypatch, provider):
    root = Path(legacy['global_config'])
    monkeypatch.setattr(provider, 'AUTH_DIR', root / 'oauth')
    monkeypatch.setattr(provider, 'AUTH_FILE', root / 'oauth' / 'test.json')
    manager_type = provider.CodexOAuthManager if provider is codex else provider.GeminiCliOAuthManager
    manager = manager_type()
    manager._save({'tokens': {'refresh_token': 'synthetic'}})
    arguments = dict(operation='oauth-transfer', target=tmp_path / 'new-agent', namespace='test')
    with fence_legacy(legacy, **arguments):
        for operation in (manager.get_tokens, lambda: manager._save({'tokens': {}})):
            with pytest.raises(DataFencedError):
                operation()
    # Releasing process locks must not reopen the old login after a failed run.
    with pytest.raises(DataFencedError):
        manager_type().get_tokens()
    with fence_legacy(legacy, **arguments) as resumed:
        resumed.release_sources()
    assert manager_type().get_tokens()['refresh_token'] == 'synthetic'


WORKER = r'''
import base64, json, sys, time
from pathlib import Path
from pantheon.utils.oauth import codex, gemini
provider, root, signals = sys.argv[1:]
root, signals = Path(root), Path(signals)
def jwt():
    payload = base64.urlsafe_b64encode(json.dumps({'exp': 9999999999}).encode()).decode().rstrip('=')
    return 'synthetic.' + payload + '.signature'
def refresh(token):
    assert token == 'old-refresh'
    (signals / 'refreshing').touch()
    deadline = time.monotonic() + 20
    while not (signals / 'finish').exists():
        assert time.monotonic() < deadline
        time.sleep(.01)
    return {'access_token': jwt(), 'refresh_token': 'rotated-refresh',
            'id_token': jwt(), 'expires_at': 9999999999}
if provider == 'codex':
    codex._refresh_tokens = refresh
    manager = codex.CodexOAuthManager(root / 'oauth' / 'test.json', ownership_root=root)
else:
    gemini.refresh_access_token = refresh
    manager = gemini.GeminiCliOAuthManager(root / 'oauth' / 'test.json', ownership_root=root)
assert manager.get_access_token() == jwt()
'''


@pytest.mark.parametrize('provider', ['codex', 'gemini'])
def test_migration_cannot_capture_during_remote_refresh(legacy, tmp_path, provider):
    root = Path(legacy['global_config'])
    auth = root / 'oauth' / 'test.json'
    auth.parent.mkdir()
    auth.write_text(json.dumps({'tokens': {
        'access_token': 'expired', 'refresh_token': 'old-refresh', 'expires_at': 1,
        'project_id': 'test', 'email': 'test@example.invalid',
    }}))
    arguments = dict(operation='oauth-transfer', target=tmp_path / 'new-agent', namespace='test')
    process = subprocess.Popen([sys.executable, '-c', WORKER, provider, str(root), str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not (tmp_path / 'refreshing').exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        with pytest.raises(DataFencedError):
            fence_legacy(legacy, **arguments)
        assert not (root / MARKER_NAME).exists()
        (tmp_path / 'finish').touch()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stdout + stderr
        with fence_legacy(legacy, **arguments) as fence:
            assert json.loads(auth.read_text())['tokens']['refresh_token'] == 'rotated-refresh'
            fence.assert_owned()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_oauth_imports_do_not_load_agent_execution(tmp_path):
    code = '''
import importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name == 'pantheon.agent' or name.startswith('pantheon.chatroom'):
            raise AssertionError('OAuth imported Agent code: ' + name)
sys.meta_path.insert(0, Boundary())
from pathlib import Path
from pantheon.utils.oauth import CodexOAuthManager
root = Path(sys.argv[1])
manager = CodexOAuthManager(root / 'oauth' / 'test.json', ownership_root=root)
manager._save({'tokens': {}})
assert manager.get_tokens() == {}
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
