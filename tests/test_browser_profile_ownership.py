"""A Desktop process must not erase another Chromium's persistent profile lock."""
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine, BrowserProfileInUse


@pytest.fixture
def engine():
    instance = BrowserEngine()
    yield instance
    if instance._profile_lock_fd is not None:
        os.close(instance._profile_lock_fd)
        instance._profile_lock_fd = None


def test_cross_process_profile_guard_preserves_owner_artifacts(engine, tmp_path):
    profile = tmp_path / "browser-profile"
    engine._acquire_profile_lock(profile)
    lock = profile / "SingletonLock"
    lock.symlink_to("active-host-123")
    cache = profile / "Default" / "Cache" / "active-cache"
    cache.parent.mkdir(parents=True)
    cache.write_text("must survive")
    code = """
import sys
from pathlib import Path
from pantheon.apps.builtin.desktop.browser import BrowserEngine, BrowserProfileInUse
try:
    BrowserEngine()._acquire_profile_lock(Path(sys.argv[1]))
except BrowserProfileInUse:
    print('owned-by-another-process')
else:
    raise RuntimeError('A second process claimed the same profile')
"""
    result = subprocess.run([sys.executable, "-c", code, str(profile)], text=True,
                            capture_output=True, timeout=30, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr
    assert "owned-by-another-process" in result.stdout
    assert os.readlink(lock) == "active-host-123"
    assert cache.read_text() == "must survive"


def test_guard_can_be_acquired_after_owner_descriptor_closes(engine, tmp_path):
    engine._acquire_profile_lock(tmp_path)
    old_inode = (tmp_path / ".pantheon-owner.lock").stat().st_ino
    os.close(engine._profile_lock_fd)
    engine._profile_lock_fd = None
    engine._acquire_profile_lock(tmp_path)
    assert (tmp_path / ".pantheon-owner.lock").stat().st_ino == old_inode
    assert not os.get_inheritable(engine._profile_lock_fd)


def test_owner_holds_profile_guard_across_context_death(engine, tmp_path):
    engine._acquire_profile_lock(tmp_path)
    fd = engine._profile_lock_fd
    engine._context_died()
    assert engine._profile_lock_fd == fd
    with pytest.raises(BrowserProfileInUse):
        BrowserEngine()._acquire_profile_lock(tmp_path)


def test_clear_stale_locks_preserves_active_legacy_chromium(tmp_path, monkeypatch):
    lock = tmp_path / "SingletonLock"
    lock.symlink_to(f"{socket.gethostname()}-111")
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[111]))
    with pytest.raises(BrowserProfileInUse, match="running Chromium"):
        BrowserEngine._clear_stale_locks(tmp_path)
    assert lock.is_symlink()


def test_process_matching_requires_exact_user_data_dir(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    processes = [
        NS(info={"pid": 11, "name": "chrome", "cmdline": ["/opt/chrome", f"--user-data-dir={profile}-other"]}),
        NS(info={"pid": 12, "name": "chrome", "cmdline": ["/opt/chrome", "--user-data-dir", str(profile)]}),
        NS(info={"pid": 13, "name": "chromium", "cmdline": ["/opt/chromium", f"--user-data-dir={profile}/."]}),
        NS(info={"pid": 14, "name": "python", "cmdline": ["python", f"--user-data-dir={profile}"]}),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *a: processes)
    assert BrowserEngine._profile_process_owners(profile) == [12, 13]


def test_current_host_dead_pid_is_positive_stale_evidence(tmp_path, monkeypatch):
    for name, target in (("SingletonLock", f"{socket.gethostname()}-111"),
                         ("SingletonSocket", "/tmp/pantheon-missing-test-singleton/socket"),
                         ("SingletonCookie", "dead-cookie")):
        (tmp_path / name).symlink_to(target)
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[]))
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.NoSuchProcess(111)))
    BrowserEngine._clear_stale_locks(tmp_path)
    assert not any((tmp_path / name).is_symlink() for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"))


def test_foreign_host_lock_is_not_proven_stale_by_local_pid_namespace(tmp_path, monkeypatch):
    monkeypatch.delenv("PANTHEON_BROWSER_PROFILE_RECOVERY", raising=False)
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("different-container-111")
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[]))
    with pytest.raises(BrowserProfileInUse, match="another host"):
        BrowserEngine._clear_stale_locks(tmp_path)
    assert os.readlink(lock) == "different-container-111"


def test_verified_exclusive_sandbox_recovers_foreign_stale_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_BROWSER_PROFILE_RECOVERY", "exclusive-modal-sandbox-v1")
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("previous-container-111")
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[]))
    BrowserEngine._clear_stale_locks(tmp_path)
    assert not lock.is_symlink()


@pytest.mark.parametrize("attestation", ["true", "1", "exclusive-modal-sandbox-v2"])
def test_unknown_recovery_value_cannot_override_foreign_lock(tmp_path, monkeypatch, attestation):
    monkeypatch.setenv("PANTHEON_BROWSER_PROFILE_RECOVERY", attestation)
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("previous-container-111")
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[]))
    with pytest.raises(BrowserProfileInUse, match="another host"):
        BrowserEngine._clear_stale_locks(tmp_path)
    assert lock.is_symlink()


def test_exclusive_sandbox_flag_cannot_override_active_local_profile_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_BROWSER_PROFILE_RECOVERY", "exclusive-modal-sandbox-v1")
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("previous-container-111")
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[222]))
    with pytest.raises(BrowserProfileInUse, match="running Chromium"):
        BrowserEngine._clear_stale_locks(tmp_path)
    assert lock.is_symlink()


@pytest.mark.parametrize("attestation", [False, True])
def test_live_singleton_socket_is_preserved_even_without_lock(tmp_path, monkeypatch, attestation):
    if attestation:
        monkeypatch.setenv("PANTHEON_BROWSER_PROFILE_RECOVERY", "exclusive-modal-sandbox-v1")
    monkeypatch.setattr(BrowserEngine, "_profile_process_owners", Mock(return_value=[]))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="pns-") as short:
            target = Path(short) / "socket"
            listener.bind(str(target))
            listener.listen(1)
            (tmp_path / "SingletonSocket").symlink_to(target)
            with pytest.raises(BrowserProfileInUse, match="live owner"):
                BrowserEngine._clear_stale_locks(tmp_path)
            assert (tmp_path / "SingletonSocket").is_symlink()
    finally:
        listener.close()


@pytest.mark.asyncio
async def test_guard_precedes_display_policies_and_cache_mutations(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    engine._acquire_profile_lock = Mock(side_effect=BrowserProfileInUse("already owned"))
    engine._clear_stale_locks = Mock()
    engine._write_policies = Mock()
    engine._ensure_xvfb = AsyncMock()
    engine._evict_volume_caches = Mock()
    with pytest.raises(BrowserProfileInUse):
        await engine._launch_browser()
    engine._clear_stale_locks.assert_not_called()
    engine._write_policies.assert_not_called()
    engine._ensure_xvfb.assert_not_called()
    engine._evict_volume_caches.assert_not_called()
    assert engine._launch_error is None


@pytest.mark.asyncio
async def test_browser_keys_wait_for_shared_input_lock_off_engine_loop(engine, monkeypatch):
    owner_thread = threading.get_ident()
    called_from = []
    engine._send_keys_sync = lambda events: (called_from.append(threading.get_ident()) or len(events))
    engine._native_input_lock.acquire()
    try:
        task = asyncio.create_task(engine.send_keys([{"code": "KeyS", "down": True}]))
        await asyncio.sleep(0.02)
        assert not task.done()
        assert not called_from
    finally:
        engine._native_input_lock.release()
    assert await task == 1
    assert called_from[0] != owner_thread
