"""Native process ownership, app identity and graceful close contracts."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine
from pantheon.apps.builtin.desktop.native_apps import NativeAppManager, NativeSession


class Process:
    pid = 43120
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


@pytest.fixture
def native(tmp_path, monkeypatch):
    engine = SimpleNamespace(
        _xvfb_display=":97", ensure_native_stage=AsyncMock(return_value={
            "mode": "seamless", "username": "test", "password": "test-pass",
        }),
    )
    manager = NativeAppManager(engine)
    manager._settings = lambda: SimpleNamespace(workspace=tmp_path, work_dir=tmp_path)
    manager._executable = lambda: "/opt/qupath/bin/QuPath"
    manager._find_main_window = Mock(return_value=73)
    manager._window_exists = Mock(return_value=True)
    spawn = Mock(side_effect=lambda *a, **kw: Process())
    monkeypatch.setattr("subprocess.Popen", spawn)
    return manager, spawn


@pytest.mark.asyncio
async def test_native_display_and_browser_share_one_start_without_chromium(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/xpra")
    engine = BrowserEngine()
    engine._xpra_password = "test-pass"
    engine._start_seamless = Mock(return_value=True)
    engine._xpra_alive = Mock(return_value=True)
    engine._ensure_browser = AsyncMock()
    first, display, second = await asyncio.gather(
        engine.ensure_native_stage(), engine._ensure_xvfb(), engine.ensure_native_stage(),
    )
    assert display == ":97"
    assert first == second
    assert first["mode"] == "seamless"
    engine._start_seamless.assert_called_once_with(":97")
    engine._ensure_browser.assert_not_called()


@pytest.mark.asyncio
async def test_native_display_rejects_shadow_without_starting_browser(monkeypatch):
    monkeypatch.setattr("pantheon.apps.builtin.desktop.browser.xpra_mode", lambda: "shadow")
    engine = BrowserEngine()
    engine._ensure_xvfb = AsyncMock()
    with pytest.raises(RuntimeError, match="seamless"):
        await engine.ensure_native_stage()
    engine._ensure_xvfb.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_attach_uses_one_process_and_stable_safe_identity(native):
    manager, spawn = native
    first, second = await asyncio.gather(
        manager.launch("qupath", "win-1"), manager.launch("qupath", "win-1"),
    )
    assert first == second
    assert first["window_class"].startswith("pantheon-native-qupath-")
    assert first["running"]
    spawn.assert_called_once()
    assert spawn.call_args.args[0] == ["/opt/qupath/bin/QuPath", "--quiet"]
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert "shell" not in spawn.call_args.kwargs


@pytest.mark.asyncio
async def test_changed_path_does_not_replace_live_app(native, tmp_path):
    manager, spawn = native
    image = tmp_path / "slide image.tif"
    image.touch()
    await manager.launch("qupath", "win-1")
    with pytest.raises(ValueError, match="different file"):
        await manager.launch("qupath", "win-1", str(image))
    spawn.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,flag", [("image name.tiff", "--image"), ("study.qpproj", "--project")])
async def test_path_is_one_literal_argument(native, tmp_path, filename, flag):
    manager, spawn = native
    image = tmp_path / filename
    image.touch()
    result = await manager.launch("qupath", "win-1", filename)
    assert result["path"] == str(image)
    assert spawn.call_args.args[0][-1] == f"{flag}={image}"


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected_before_display_or_process(native, tmp_path):
    manager, spawn = native
    outside = tmp_path.parent / f"{tmp_path.name}-outside.tif"
    outside.touch()
    (tmp_path / "escape.tif").symlink_to(outside)
    with pytest.raises(ValueError, match="inside the workspace"):
        await manager.launch("qupath", "win-1", "escape.tif")
    spawn.assert_not_called()
    manager.engine.ensure_native_stage.assert_not_called()


@pytest.mark.asyncio
async def test_qpdata_is_not_silently_treated_as_an_image(native, tmp_path):
    manager, spawn = native
    (tmp_path / "data.qpdata").touch()
    with pytest.raises(ValueError, match="project"):
        await manager.launch("qupath", "win-1", "data.qpdata")
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_app_cannot_execute_arbitrary_program(native):
    manager, spawn = native
    with pytest.raises(ValueError, match="Unsupported"):
        await manager.launch("bash", "win-1")
    spawn.assert_not_called()


def test_java_preferences_persist_but_caches_are_local_and_options_preserved(native, tmp_path, monkeypatch):
    manager, _ = native
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Dexisting=yes -Xmx1g -Dprism.order=es2")
    env = manager._environment("test-session")
    assert env["DISPLAY"] == ":97"
    assert env["JAVA_TOOL_OPTIONS"].startswith("-Dexisting=yes -Xmx1g -Dprism.order=es2")
    assert "-Xmx2g" not in env["JAVA_TOOL_OPTIONS"]
    assert "prism.order=sw" not in env["JAVA_TOOL_OPTIONS"]
    assert str(tmp_path / ".pantheon" / "qupath") in env["JAVA_TOOL_OPTIONS"]
    assert '-Dqupath.win.width="1200"' in env["JAVA_TOOL_OPTIONS"]
    assert '-Dqupath.win.height="800"' in env["JAVA_TOOL_OPTIONS"]
    assert not Path(env["TMPDIR"]).is_relative_to(tmp_path)
    assert Path(env["XDG_CACHE_HOME"]).parent == Path(env["TMPDIR"])


@pytest.mark.asyncio
async def test_initial_javafx_size_uses_bounded_host_geometry(native):
    manager, spawn = native
    await manager.launch("qupath", "win-1", width=99999, height=10)
    options = spawn.call_args.kwargs["env"]["JAVA_TOOL_OPTIONS"]
    assert '-Dqupath.win.width="8192"' in options
    assert '-Dqupath.win.height="240"' in options
    assert manager._find_main_window.call_args.args[1:] == (8192, 240)


@pytest.mark.asyncio
async def test_close_requests_save_dialog_and_never_kills_process(native, monkeypatch):
    manager, _ = native
    await manager.launch("qupath", "win-1")
    manager._request_close = Mock(return_value=True)
    kill = Mock()
    monkeypatch.setattr("os.killpg", kill)
    result = await manager.close("win-1")
    assert result["close_requested"] and result["running"]
    kill.assert_not_called()
    manager._request_close.assert_called_once_with(manager.sessions["win-1"])


@pytest.mark.asyncio
async def test_native_exit_is_reported_without_relaunch(native):
    manager, spawn = native
    await manager.launch("qupath", "win-1")
    manager.sessions["win-1"].process.returncode = 0
    assert not (await manager.status("win-1"))["running"]
    assert not (await manager.close("win-1"))["close_requested"]
    assert not (await manager.status("missing"))["running"]
    spawn.assert_called_once()


@pytest.mark.asyncio
async def test_startup_timeout_cleans_only_owned_process_group(native, monkeypatch):
    manager, _ = native
    manager.START_TIMEOUT = 0
    kill = Mock()
    monkeypatch.setattr("os.killpg", kill)
    with pytest.raises(RuntimeError, match="startup timeout"):
        await manager.launch("qupath", "win-1")
    assert kill.call_args.args[0] == Process.pid
    assert "win-1" not in manager.sessions


@pytest.mark.asyncio
async def test_early_exit_surfaces_launch_error_without_leaving_session(native):
    manager, spawn = native
    exited = Process()
    exited.returncode = 7
    spawn.side_effect = None
    spawn.return_value = exited
    with pytest.raises(RuntimeError, match="code 7"):
        await manager.launch("qupath", "win-1")
    assert "win-1" not in manager.sessions


def test_main_identity_excludes_foreign_splash_and_transient_windows(tmp_path):
    def window(xid, pid=43120, kind="_NET_WM_WINDOW_TYPE_NORMAL", transient=None, children=()):
        win = Mock(id=xid)
        props = {"_NET_WM_PID": SimpleNamespace(value=[pid]),
                 "_NET_WM_WINDOW_TYPE": SimpleNamespace(value=[kind])}
        win.get_full_property.side_effect = lambda atom, _: props.get(atom)
        win.get_attributes.return_value = SimpleNamespace(map_state=1)
        win.get_wm_transient_for.return_value = transient
        win.get_wm_class.return_value = ("qupath.lib.gui.QuPathApp", "QuPath")
        win.query_tree.return_value = SimpleNamespace(children=children)
        return win

    foreign = window(1, pid=999)
    splash = window(2, kind="_NET_WM_WINDOW_TYPE_SPLASH")
    dialog = window(3, transient=object())
    main = window(4)
    root = window(0, children=[foreign, splash, dialog, main])
    d = Mock()
    d.intern_atom.side_effect = lambda name: name
    d.screen.return_value = SimpleNamespace(root=root)
    engine = SimpleNamespace(_x_display=lambda: d, _reset_x_display=Mock())
    manager = NativeAppManager(engine)
    manager._owned_pids = lambda pid: {pid}
    session = NativeSession("win-1", "qupath", "", "pantheon-native-qupath-test", Process(), tmp_path / "log")
    assert manager._find_main_window(session, 1000, 700) == 4
    for ignored in (foreign, splash, dialog):
        ignored.set_wm_class.assert_not_called()
    main.set_wm_class.assert_called_once_with("pantheon-native-qupath-test", "QuPath")
    main.configure.assert_called_once_with(width=1000, height=700)


def test_close_protocol_keeps_pid_owned_and_does_not_destroy_window(tmp_path):
    pytest.importorskip("Xlib")
    win = Mock()
    win.get_wm_class.return_value = ("pantheon-native-qupath-test", "QuPath")
    win.get_wm_protocols.return_value = [10]
    win.get_full_property.return_value = SimpleNamespace(value=[Process.pid])
    d = Mock()
    d.create_resource_object.return_value = win
    d.intern_atom.side_effect = lambda name: {"WM_DELETE_WINDOW": 10, "WM_PROTOCOLS": 11, "_NET_WM_PID": 12}[name]
    engine = SimpleNamespace(_x_display=lambda: d, _reset_x_display=Mock())
    manager = NativeAppManager(engine)
    manager._owned_pids = lambda pid: {pid}
    session = NativeSession("win-1", "qupath", "", "pantheon-native-qupath-test", Process(), tmp_path / "log", xid=73)
    # A real Window object supplies its XID during ClientMessage encoding.
    win.__window__ = lambda: 73
    from unittest.mock import patch
    with patch("Xlib.protocol.event.ClientMessage") as event:
        assert manager._request_close(session)
        assert event.call_args.kwargs["data"] == (32, [10, 0, 0, 0, 0])
    win.destroy.assert_not_called()
    win.kill_client.assert_not_called()


@pytest.mark.asyncio
async def test_close_waits_for_startup_and_status_does_not_report_false_exit(native):
    manager, spawn = native
    manager._find_main_window.return_value = None
    launch = asyncio.create_task(manager.launch("qupath", "win-1"))
    while not spawn.called:
        await asyncio.sleep(0)
    result = await manager.status("win-1")
    assert result["running"] and result["state"] == "starting"
    manager._request_close = Mock(return_value=True)
    close = asyncio.create_task(manager.close("win-1"))
    await asyncio.sleep(0)
    assert not close.done()
    manager._find_main_window.return_value = 73
    await launch
    assert (await close)["close_requested"]


@pytest.mark.asyncio
async def test_cancelled_launch_cleans_owned_process(native, monkeypatch):
    manager, spawn = native
    manager._find_main_window.return_value = None
    kill = Mock()
    monkeypatch.setattr("os.killpg", kill)
    launch = asyncio.create_task(manager.launch("qupath", "win-1"))
    while not spawn.called:
        await asyncio.sleep(0)
    launch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await launch
    assert kill.call_args.args[0] == Process.pid
    assert not (await manager.status("win-1"))["running"]


def test_display_failure_is_not_reported_as_native_exit(tmp_path):
    engine = SimpleNamespace(_x_display=Mock(side_effect=OSError("socket reset")), _reset_x_display=Mock())
    manager = NativeAppManager(engine)
    session = NativeSession("win-1", "qupath", "", "pantheon-native-qupath-test", Process(), tmp_path / "log", xid=73)
    with pytest.raises(RuntimeError, match="Could not inspect"):
        manager._window_exists(session)
    engine._reset_x_display.assert_called_once()


def test_foreign_pid_cannot_be_closed_even_if_window_class_was_reused(tmp_path):
    win = Mock()
    win.get_wm_class.return_value = ("pantheon-native-qupath-test", "QuPath")
    win.get_full_property.return_value = SimpleNamespace(value=[999])
    d = Mock()
    d.create_resource_object.return_value = win
    engine = SimpleNamespace(_x_display=lambda: d, _reset_x_display=Mock())
    manager = NativeAppManager(engine)
    manager._owned_pids = lambda pid: {pid}
    session = NativeSession("win-1", "qupath", "", "pantheon-native-qupath-test", Process(), tmp_path / "log", xid=73)
    assert not manager._request_close(session)
    win.send_event.assert_not_called()


def test_desktop_rpc_manifest_matches_native_contract():
    from pantheon.apps.registry import builtin_apps, reflected_tools
    from pantheon.apps.reflect import signature_diff

    manifest = next(app.manifest for app in builtin_apps() if app.manifest.id == "desktop")
    assert not signature_diff(manifest.provides.tools, reflected_tools(manifest))
    tools = {t.name: t for t in manifest.provides.tools}
    assert all(tools[name].hidden for name in ("native_ui_launch", "native_ui_close", "native_ui_status"))
    for name in ("native_ui_launch", "native_ui_close", "native_ui_status"):
        names = {param.name for param in tools[name].params}
        assert "native_session_id" in names
        assert "session_id" not in names


@pytest.mark.asyncio
async def test_rpc_preserves_native_identity_apart_from_framework_session():
    from pantheon.apps.builtin.desktop.toolset import DesktopToolSet

    manager = SimpleNamespace(
        launch=AsyncMock(return_value={"session_id": "win-42", "running": True}),
        status=AsyncMock(return_value={"session_id": "win-42", "running": True}),
        close=AsyncMock(return_value={"session_id": "win-42", "running": True}),
    )
    async def call(coro):
        return await coro
    desktop = DesktopToolSet()
    desktop._browser_engine = lambda: SimpleNamespace(native_apps=lambda: manager, call=call)
    result = await desktop.native_ui_launch(native_session_id="win-42", session_id="__global__")
    assert result == {"success": True, "session_id": "win-42", "running": True}
    manager.launch.assert_awaited_once_with("qupath", "win-42", "", 1200, 800)
    await desktop.native_ui_status(native_session_id="win-42", session_id="__global__")
    await desktop.native_ui_close(native_session_id="win-42", session_id="__global__")
    manager.status.assert_awaited_once_with("win-42")
    manager.close.assert_awaited_once_with("win-42")
