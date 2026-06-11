import subprocess
import asyncio
import time

import pytest

from pantheon.chatroom import start


@pytest.mark.asyncio
async def test_start_endpoint_embedded_can_return_before_setup(monkeypatch, tmp_path):
    import pantheon.endpoint

    run_started = asyncio.Event()

    class FakeEndpoint:
        run_calls = 0

        def __init__(self, config, workspace_path, id_hash):
            self.config = config
            self.workspace_path = workspace_path
            self.id_hash = id_hash
            self._setup_completed = False
            self.service_name = "fake-endpoint"
            self.service_id = f"svc-{id_hash}"

        async def run(self, remote=True, log_level=None):
            assert remote is True
            assert log_level == "DEBUG"
            FakeEndpoint.run_calls += 1
            run_started.set()
            self._setup_completed = True

    monkeypatch.setattr(pantheon.endpoint, "Endpoint", FakeEndpoint)
    start_after = asyncio.Event()

    t0 = time.perf_counter()
    endpoint = await start._start_endpoint_embedded(
        endpoint_id_hash="chat-a-endpoint",
        workspace_path=str(tmp_path),
        log_level="DEBUG",
        enable_notebook_streaming=True,
        wait_for_setup=False,
        start_after=start_after,
    )
    elapsed = time.perf_counter() - t0

    assert endpoint.service_id == "svc-chat-a-endpoint"
    assert endpoint.config == {"enable_notebook_streaming": True}
    assert elapsed < 0.2
    await asyncio.sleep(0)
    assert FakeEndpoint.run_calls == 0
    assert endpoint._setup_completed is False

    start_after.set()
    await asyncio.wait_for(run_started.wait(), timeout=0.5)
    assert endpoint._setup_completed is True


@pytest.mark.asyncio
async def test_start_services_starts_embedded_endpoint_without_waiting(monkeypatch, tmp_path):
    calls = {}

    class FakeEndpoint:
        service_id = "endpoint-service"
        _setup_completed = False

    async def fake_start_endpoint_embedded(**kwargs):
        calls["endpoint_kwargs"] = kwargs
        return FakeEndpoint()

    class FakeChatRoom:
        def __init__(self, **kwargs):
            calls["chatroom_kwargs"] = kwargs
            self._worker_ready = asyncio.Event()

        async def run(self, log_level=None, remote=True):
            calls["run_kwargs"] = {"log_level": log_level, "remote": remote}
            return self

    monkeypatch.setattr(start, "_start_endpoint_embedded", fake_start_endpoint_embedded)
    monkeypatch.setattr(start, "ChatRoom", FakeChatRoom)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = await start.start_services(
        service_name="test-chatroom",
        memory_dir=str(tmp_path / "memory"),
        workspace_path=str(workspace),
        log_level="DEBUG",
        speech_to_text_model="test-stt",
        id_hash="chat-a",
        endpoint_mode="embedded",
        auto_start_nats=False,
        auto_ui=False,
    )

    assert isinstance(result, FakeChatRoom)
    assert calls["endpoint_kwargs"]["wait_for_setup"] is False
    assert isinstance(calls["endpoint_kwargs"]["start_after"], asyncio.Event)
    assert calls["endpoint_kwargs"]["endpoint_id_hash"] == "chat-a-endpoint"
    assert calls["endpoint_kwargs"]["enable_notebook_streaming"] is True
    assert calls["chatroom_kwargs"]["endpoint"].service_id == "endpoint-service"
    assert calls["chatroom_kwargs"]["id_hash"] == "chat-a"
    assert calls["run_kwargs"] == {"log_level": "DEBUG", "remote": True}


async def _wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0)


def test_is_wsl_detects_microsoft_release(monkeypatch):
    monkeypatch.setattr(start.platform, "system", lambda: "Linux")
    monkeypatch.setattr(start.platform, "release", lambda: "5.15.153.1-microsoft-standard-WSL2")

    assert start._is_wsl() is True


def test_open_browser_url_prefers_windows_browser_on_wsl(monkeypatch):
    calls = []

    monkeypatch.setattr(start, "_is_wsl", lambda: True)
    monkeypatch.setattr(start, "_open_url_in_windows_browser", lambda url: calls.append(url) or True)

    def fail_webbrowser_open(url):
        raise AssertionError("webbrowser.open should not be called when WSL fallback succeeds")

    monkeypatch.setattr("webbrowser.open", fail_webbrowser_open)

    assert start._open_browser_url("https://example.com") is True
    assert calls == ["https://example.com"]


def test_open_url_in_windows_browser_falls_back_to_cmd(monkeypatch):
    commands = []

    def fake_run(command, check, stdout, stderr):
        commands.append(command)
        if command[0] == "powershell.exe":
            raise FileNotFoundError("powershell.exe not found")
        return None

    monkeypatch.setattr(start.subprocess, "run", fake_run)

    assert start._open_url_in_windows_browser("https://example.com") is True
    assert commands == [
        ["powershell.exe", "-NoProfile", "-Command", 'Start-Process "https://example.com"'],
        ["cmd.exe", "/c", 'start "" "https://example.com"'],
    ]


def test_open_url_in_windows_browser_quotes_url_with_ampersand(monkeypatch):
    """URLs with & (common in query strings) must be quoted for cmd.exe."""
    commands = []

    def fake_run(command, check, stdout, stderr):
        commands.append(command)
        return None

    monkeypatch.setattr(start.subprocess, "run", fake_run)

    url = "http://localhost:5173/#/?nats=ws://localhost:8080&service=abc&auto=true"
    assert start._open_url_in_windows_browser(url) is True
    # PowerShell command should have the URL quoted
    assert f'"{url}"' in commands[0][3]


def test_open_url_in_windows_browser_raises_when_all_launchers_fail(monkeypatch):
    def fake_run(command, check, stdout, stderr):
        raise subprocess.CalledProcessError(returncode=1, cmd=command)

    monkeypatch.setattr(start.subprocess, "run", fake_run)

    try:
        start._open_url_in_windows_browser("https://example.com")
    except RuntimeError as exc:
        assert "Failed to open Windows browser from WSL" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when all Windows browser launchers fail")
