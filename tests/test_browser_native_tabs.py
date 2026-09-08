"""The native selected tab comes from Chromium, not emulated page visibility."""
import asyncio
import json
import stat
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine


def test_observer_is_private_local_and_has_only_identity_permission():
    engine = BrowserEngine()
    directory = engine._prepare_native_tabs_extension()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["permissions"] == ["debugger"]
    assert "host_permissions" not in manifest and "content_scripts" not in manifest
    assert "externally_connectable" not in manifest
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "native-tabs.js").stat().st_mode) == 0o600
    assert engine._prepare_native_tabs_extension() == directory
    assert engine._native_tabs_worker_url.startswith("chrome-extension://")
    script = (directory / "native-tabs.js").read_text()
    assert "chrome.debugger.getTargets()" in script
    assert "chrome.debugger.attach" not in script and "fetch(" not in script


@pytest.mark.asyncio
async def test_observer_waits_for_its_own_worker_ready_and_ignores_other_extensions():
    engine = BrowserEngine()
    engine._prepare_native_tabs_extension()
    own = NS(url=engine._native_tabs_worker_url)
    other = NS(url="chrome-extension://unrelated/native-tabs.js")
    async def wait_for_event(event, *, predicate, timeout):
        assert event == "serviceworker" and timeout == 5000
        assert not predicate(other) and predicate(own)
        await asyncio.sleep(0)
        return own
    engine._context = NS(service_workers=[other], wait_for_event=AsyncMock(side_effect=wait_for_event))
    assert await engine._native_tabs_worker() is own
    engine._context.wait_for_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_observer_fails_immediately_during_ui_polling():
    engine = BrowserEngine()
    engine._prepare_native_tabs_extension()
    engine._context = NS(service_workers=[], wait_for_event=AsyncMock())
    with pytest.raises(RuntimeError, match="observer is unavailable"):
        await engine._native_tabs_worker(wait=False)
    engine._context.wait_for_event.assert_not_called()


@pytest.mark.asyncio
async def test_native_active_identity_is_window_scoped_and_does_not_depend_on_page_focus():
    engine = BrowserEngine()
    worker = NS(evaluate=AsyncMock(return_value=[
        {"windowId": 12, "tabId": 1, "active": False, "targetIds": ["first"]},
        {"windowId": 12, "tabId": 2, "active": True, "targetIds": ["selected"]},
        {"windowId": 99, "tabId": 3, "active": True, "targetIds": ["unrelated"]},
    ]))
    engine._native_tab_snapshot = worker.evaluate
    assert await engine._native_active_target(12) == "selected"
    assert await engine._native_active_target(99) == "unrelated"
    assert await engine._native_active_target(100) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tabs", [
    [{"windowId": 12, "active": True, "targetIds": []}],
    [{"windowId": 12, "active": True, "targetIds": ["a", "b"]}],
    [{"windowId": 12, "active": True, "targetIds": ["a"]}, {"windowId": 12, "active": True, "targetIds": ["b"]}],
    [{"windowId": 12, "active": True, "targetIds": [None]}],
])
async def test_unmapped_or_ambiguous_native_selection_never_guesses(tabs):
    engine = BrowserEngine()
    engine._native_tab_snapshot = AsyncMock(return_value=tabs)
    with pytest.raises(RuntimeError, match="no unique selected tab identity"):
        await engine._native_active_target(12)


@pytest.mark.asyncio
async def test_headful_launch_enables_only_bundled_extension_and_waits_for_ready(monkeypatch, tmp_path):
    engine = BrowserEngine()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    engine._acquire_profile_lock = Mock()
    engine._clear_stale_locks = Mock()
    engine._write_policies = Mock()
    engine._evict_volume_caches = Mock()
    engine._ensure_xvfb = AsyncMock(return_value=":97")
    engine._ensure_xpra = AsyncMock()
    engine._park_keeper = AsyncMock()
    engine._native_tabs_worker = AsyncMock()
    context = NS(on=Mock(), add_init_script=AsyncMock())
    launch = AsyncMock(return_value=context)
    engine._pw = NS(chromium=NS(launch_persistent_context=launch))
    await engine._launch_browser_once()
    await asyncio.sleep(0)  # Scheduled Xpra warmup completes.
    options = launch.call_args.kwargs
    assert options["channel"] == "chromium"  # Branded Chrome disallows sideload flags.
    assert options["headless"] is False
    assert options["ignore_default_args"] == ["--disable-extensions"]
    assert f"--load-extension={engine._native_extension_dir.name}" in options["args"]
    assert not any(arg.startswith("--disable-extensions-except") for arg in options["args"])
    engine._native_tabs_worker.assert_awaited_once()


@pytest.mark.asyncio
async def test_idle_worker_is_woken_and_read_on_fresh_exact_cdp_session():
    engine = BrowserEngine()
    engine._prepare_native_tabs_extension()
    events = {}
    expected = [{"windowId": 12, "active": True, "targetIds": ["selected"]}]
    async def send(method, params=None):
        if method == "Target.getTargets":
            return {"targetInfos": [
                {"targetId": "unrelated", "type": "service_worker", "url": "chrome-extension://other/native-tabs.js"},
                {"targetId": "observer", "type": "service_worker", "url": engine._native_tabs_worker_url},
            ]}
        if method == "Target.attachToTarget":
            assert params == {"targetId": "observer", "flatten": False}
            return {"sessionId": "fresh-session"}
        if method == "Target.sendMessageToTarget":
            request = json.loads(params["message"])
            assert params["sessionId"] == "fresh-session"
            assert request["params"]["expression"] == "globalThis.pantheonNativeTabs()"
            events["Target.receivedMessageFromTarget"]({"sessionId": "fresh-session", "message": json.dumps({
                "id": 1, "result": {"result": {"value": expected}},
            })})
            return {}
        assert method == "Target.detachFromTarget" and params == {"sessionId": "fresh-session"}
        return {}
    browser_cdp = NS(send=AsyncMock(side_effect=send), detach=AsyncMock(), on=lambda event, callback: events.update({event: callback}))
    wake = NS(send=AsyncMock(), detach=AsyncMock())
    page = NS(is_closed=lambda: False)
    engine._context = NS(
        pages=[page], new_cdp_session=AsyncMock(return_value=wake),
        browser=NS(new_browser_cdp_session=AsyncMock(return_value=browser_cdp)),
        service_workers=[NS(url=engine._native_tabs_worker_url, evaluate=AsyncMock(side_effect=AssertionError("stale Worker must not be used")))],
    )
    assert await engine._native_tab_snapshot() == expected
    assert [call.args[0] for call in wake.send.await_args_list] == ["ServiceWorker.enable", "ServiceWorker.startWorker"]
    wake.send.assert_any_await("ServiceWorker.startWorker", {"scopeURL": engine._native_tabs_worker_url.rsplit("/", 1)[0] + "/"})
    wake.detach.assert_awaited_once()
    browser_cdp.detach.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_native_tab_observer_never_creates_visible_page_to_recover():
    engine = BrowserEngine()
    engine._prepare_native_tabs_extension()
    wake = NS(send=AsyncMock(), detach=AsyncMock())
    cdp = NS(send=AsyncMock(return_value={"targetInfos": []}), detach=AsyncMock())
    engine._context = NS(pages=[NS(is_closed=lambda: False)], new_cdp_session=AsyncMock(return_value=wake),
        browser=NS(new_browser_cdp_session=AsyncMock(return_value=cdp)), new_page=AsyncMock())
    with pytest.raises(RuntimeError, match="unavailable or ambiguous"):
        await engine._native_tab_snapshot()
    engine._context.new_page.assert_not_called()
    assert [call.args[0] for call in cdp.send.await_args_list] == ["Target.getTargets"]
    cdp.detach.assert_awaited_once()
