"""Local credential durability and real process contention (synthetic tokens only)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from pantheon.utils.oauth.storage import CredentialStorageError, OAuthStorage


def test_atomic_failure_preserves_login(tmp_path, monkeypatch):
    store = OAuthStorage(tmp_path / "auth.json")
    original = {"tokens": {"refresh_token": "synthetic-original"}}
    store.save(original)

    def fail_replace(*args):
        raise OSError("injected replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected"):
            store.save({"tokens": {"refresh_token": "synthetic-new"}})
    assert store.load() == original
    assert not list(tmp_path.glob(".auth.json.*"))
    store.save({"tokens": {"refresh_token": "synthetic-new"}})
    assert store.load()["tokens"]["refresh_token"] == "synthetic-new"
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("raw", [b'{"secret":"synthetic-secret",', b'[]', b'\xff'])
def test_invalid_record_requires_recovery_without_secret_in_error(tmp_path, raw):
    path = tmp_path / "auth.json"
    path.write_bytes(raw)
    with pytest.raises(CredentialStorageError) as error:
        OAuthStorage(path).load()
    assert "synthetic-secret" not in str(error.value)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_linked_credentials_are_not_read_or_replaced(tmp_path, link):
    source = tmp_path / "source.json"
    source.write_text('{"tokens": {}}')
    target = tmp_path / "auth.json"
    try:
        if link == "symlink":
            target.symlink_to(source)
        else:
            os.link(source, target)
    except OSError:
        pytest.skip("filesystem does not permit links")
    store = OAuthStorage(target)
    for operation in (store.load, lambda: store.save({"changed": True})):
        with pytest.raises((CredentialStorageError, OSError)):
            operation()
    assert source.read_text() == '{"tokens": {}}'


def test_independent_objects_share_nested_transaction(tmp_path):
    first = OAuthStorage(tmp_path / "auth.json")
    second = OAuthStorage(tmp_path / "auth.json")
    with first.transaction(timeout=.1):
        second.save({"tokens": {"access_token": "synthetic"}})
        assert first.load() == second.load()


def test_other_thread_cannot_borrow_transaction(tmp_path):
    first = OAuthStorage(tmp_path / "auth.json")
    second = OAuthStorage(tmp_path / "auth.json")

    def attempt():
        with second.transaction(timeout=.05):
            pytest.fail("another thread borrowed the owner's transaction")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.transaction():
            future = pool.submit(attempt)
            with pytest.raises(CredentialStorageError, match="busy"):
                future.result(timeout=5)
    second.save({"recovered": True})


def test_dead_process_releases_lock(tmp_path):
    holder = r'''
import sys, time
from pathlib import Path
from pantheon.utils.oauth.storage import OAuthStorage
root = Path(sys.argv[1])
with OAuthStorage(root / 'auth.json').transaction():
    (root / 'ready').touch()
    time.sleep(60)
'''
    process = subprocess.Popen([sys.executable, "-c", holder, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    store = OAuthStorage(tmp_path / "auth.json")
    try:
        deadline = time.monotonic() + 30
        while not (tmp_path / "ready").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        with pytest.raises(CredentialStorageError, match="busy"):
            with store.transaction(timeout=.05):
                pytest.fail("another process's lock was ignored")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    # A leftover sidecar is harmless; OS lock ownership ended with its process.
    with store.transaction(timeout=.2):
        store.save({"recovered": True})
    assert store.load() == {"recovered": True}


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_fork_cannot_inherit_parent_transaction(tmp_path):
    store = OAuthStorage(tmp_path / "auth.json")
    with store.transaction():
        pid = os.fork()
        if pid == 0:
            try:
                with store.transaction(timeout=.05):
                    os._exit(2)
            except CredentialStorageError:
                os._exit(0)
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0


WORKER = r'''
import base64, json, sys, time
from pathlib import Path
from pantheon.utils.oauth import codex, gemini
provider, directory, identity = sys.argv[1:]
root = Path(directory)
def jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=')
    return 'synthetic.' + payload + '.signature'
def refresh(token):
    assert token == 'old-refresh'
    with (root / 'refresh-count').open('a') as stream:
        stream.write(identity + '\n')
    time.sleep(.2)
    return {'access_token': jwt(9999999999), 'refresh_token': 'rotated-refresh',
            'id_token': jwt(9999999999), 'expires_at': 9999999999}
if provider == 'codex':
    codex._refresh_tokens = refresh
    manager = codex.CodexOAuthManager(root / 'auth.json')
else:
    gemini.refresh_access_token = refresh
    manager = gemini.GeminiCliOAuthManager(root / 'auth.json')
(root / ('ready-' + identity)).touch()
deadline = time.monotonic() + 15
while not (root / 'go').exists():
    assert time.monotonic() < deadline
    time.sleep(.01)
assert manager.get_access_token() == jwt(9999999999)
assert manager.get_tokens()['refresh_token'] == 'rotated-refresh'
'''


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_processes_refresh_once_and_read_rotated_record(tmp_path, provider):
    OAuthStorage(tmp_path / "auth.json").save({"tokens": {
        "access_token": "expired", "refresh_token": "old-refresh", "expires_at": 1,
        "project_id": "test-project", "email": "test@example.invalid",
    }})
    processes = []
    try:
        for identity in ("one", "two"):
            processes.append(subprocess.Popen(
                [sys.executable, "-c", WORKER, provider, str(tmp_path), identity],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        deadline = time.monotonic() + 30
        while not all((tmp_path / ("ready-" + identity)).exists() for identity in ("one", "two")):
            assert all(process.poll() is None for process in processes)
            assert time.monotonic() < deadline, "worker initialization timed out"
            time.sleep(.02)
        (tmp_path / "go").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stdout + stderr
        assert len((tmp_path / "refresh-count").read_text().splitlines()) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


def test_google_credentials_use_rotated_refresh_and_expiry(tmp_path, monkeypatch):
    from pantheon.utils.oauth import gemini
    manager = gemini.GeminiCliOAuthManager(tmp_path / "auth.json")
    manager.save({"tokens": {
        "access_token": "old", "refresh_token": "old-refresh", "expires_at": 1,
        "project_id": "test-project", "email": "test@example.invalid",
    }})
    monkeypatch.setattr(gemini, "resolve_oauth_client_config", lambda: ("id", "secret"))
    monkeypatch.setattr(gemini, "refresh_access_token", lambda token: {
        "access_token": "fresh", "refresh_token": "rotated", "expires_at": 2000000000,
    })
    credentials = manager.build_google_credentials()
    assert credentials.token == "fresh"
    assert credentials.refresh_token == "rotated"
    assert credentials.expiry == gemini.datetime.utcfromtimestamp(2000000000)
