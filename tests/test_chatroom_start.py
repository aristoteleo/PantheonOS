import subprocess

from pantheon.chatroom import start


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
