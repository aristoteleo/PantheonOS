"""Tests for endpoint lifecycle management (list, unregister, info)."""

import httpx
import pytest
from aiohttp import web

from pantheon.apps.builtin.desktop.data_server import LiveViewDataServer


@pytest.mark.asyncio
async def test_list_endpoints_returns_empty_when_none_registered(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    endpoints = server.list_endpoints()

    assert endpoints == []


@pytest.mark.asyncio
async def test_list_endpoints_includes_all_registered(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler1(_request):
        return web.json_response({"id": 1})

    async def handler2(_request):
        return web.json_response({"id": 2})

    url1 = await server.register_endpoint("track_a", handler1)
    url2 = await server.register_endpoint("track_b", handler2)

    endpoints = server.list_endpoints()

    assert len(endpoints) == 2
    assert {"name": "track_a", "url": url1} in endpoints
    assert {"name": "track_b", "url": url2} in endpoints


@pytest.mark.asyncio
async def test_unregister_endpoint_removes_handler(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler(_request):
        return web.json_response({"ok": True})

    url = await server.register_endpoint("temp", handler)
    removed = server.unregister_endpoint("temp")

    assert removed is True

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unregister_nonexistent_endpoint_returns_false(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    removed = server.unregister_endpoint("does_not_exist")

    assert removed is False


@pytest.mark.asyncio
async def test_endpoint_exists_returns_true_for_registered(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler(_request):
        return web.json_response({"ok": True})

    await server.register_endpoint("track", handler)

    assert server.endpoint_exists("track") is True


@pytest.mark.asyncio
async def test_endpoint_exists_returns_false_for_unregistered(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    assert server.endpoint_exists("missing") is False


@pytest.mark.asyncio
async def test_endpoint_exists_returns_false_for_invalid_name(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    assert server.endpoint_exists("bad/name") is False


@pytest.mark.asyncio
async def test_unregister_then_register_same_name_works(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler1(_request):
        return web.json_response({"version": 1})

    async def handler2(_request):
        return web.json_response({"version": 2})

    url = await server.register_endpoint("track", handler1)
    server.unregister_endpoint("track")
    new_url = await server.register_endpoint("track", handler2)

    assert url == new_url

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {"version": 2}


@pytest.mark.asyncio
async def test_list_endpoints_after_unregister_excludes_removed(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler(_request):
        return web.json_response({"ok": True})

    await server.register_endpoint("keep", handler)
    await server.register_endpoint("remove", handler)

    server.unregister_endpoint("remove")
    endpoints = server.list_endpoints()

    assert len(endpoints) == 1
    assert endpoints[0]["name"] == "keep"


@pytest.mark.asyncio
async def test_endpoint_handler_error_returns_generic_message(tmp_path):
    """Ensure handler exceptions return generic error, not internal details."""
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def failing_handler(_request):
        raise ValueError("Secret internal error message")

    url = await server.register_endpoint("broken", failing_handler)

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 500
    assert response.text == "Internal server error"
    assert "Secret" not in response.text
    assert "ValueError" not in response.text


@pytest.mark.asyncio
async def test_endpoint_invalid_return_type_returns_generic_message(tmp_path):
    """Ensure invalid handler return types produce generic error messages."""
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def bad_handler(_request):
        return {"not": "a response object"}

    url = await server.register_endpoint("invalid", bad_handler)

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 500
    assert response.text == "Internal server error"
    assert "dict" not in response.text
    assert "StreamResponse" not in response.text


def test_app_sync_writes_a_batch_and_prunes_what_the_tree_dropped(tmp_path, monkeypatch):
    """One call for the whole package tree, and it stays inside it.

    Fifty-eight files at one RPC each is most of the wait a user spends
    looking at "Preparing the desktop" — and none of it is work, it is
    round trips. Write-through stays unconditional; only the cost changes.
    """
    import asyncio
    import json

    from pantheon.apps.builtin.desktop.toolset import DesktopToolSet

    class FakeSettings:
        workspace = tmp_path

    monkeypatch.setattr("pantheon.settings.get_settings", lambda: FakeSettings())
    ts = DesktopToolSet.__new__(DesktopToolSet)

    first = asyncio.run(ts.desktop_sync_apps(
        files={"viv/frontend/index.js": "one", "viv/manifest.json": "{}"},
        manifest=["viv/frontend/index.js", "viv/manifest.json"]))
    assert first["success"] and first["written"] == 2
    root = tmp_path / ".pantheon" / "apps"
    assert (root / "viv" / "frontend" / "index.js").read_text() == "one"
    assert json.loads((root / ".sync-manifest.json").read_text()) == [
        "viv/frontend/index.js", "viv/manifest.json"]

    # The tree drops a file: the copy on the volume must go with it, or it
    # keeps feeding the app state the code no longer produces.
    second = asyncio.run(ts.desktop_sync_apps(
        files={"viv/frontend/index.js": "two"},
        manifest=["viv/frontend/index.js"]))
    assert second["pruned"] == 1
    assert not (root / "viv" / "manifest.json").exists()
    assert (root / "viv" / "frontend" / "index.js").read_text() == "two"


def test_app_sync_refuses_to_write_outside_the_app_tree(tmp_path, monkeypatch):
    """It writes packages, not arbitrary files."""
    import asyncio

    from pantheon.apps.builtin.desktop.toolset import DesktopToolSet

    class FakeSettings:
        workspace = tmp_path

    monkeypatch.setattr("pantheon.settings.get_settings", lambda: FakeSettings())
    ts = DesktopToolSet.__new__(DesktopToolSet)

    out = asyncio.run(ts.desktop_sync_apps(files={"../../escaped.txt": "no"}))
    assert out["success"] and out["written"] == 0
    assert out["refused"] == ["../../escaped.txt"]
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_the_registry_reads_every_scope_in_order(tmp_path, monkeypatch):
    """Workspace wins over user wins over builtin, and a scope that cannot
    be read is reported — booting with no apps and no complaint is the
    failure this replaces."""
    import asyncio
    import json

    from pantheon.apps.builtin.desktop.toolset import DesktopToolSet

    ws = tmp_path / "ws"
    home = tmp_path / "home"
    for root, app_id, version in ((ws / ".pantheon/apps/viv", "viv", "2.0"),
                                  (home / ".pantheon/apps/viv", "viv", "1.0"),
                                  (home / ".pantheon/apps/igv", "igv", "1.0")):
        root.mkdir(parents=True)
        (root / "atrium.json").write_text(json.dumps(
            {"id": app_id, "entry": "index.js", "version": version}))
    # No manifest at all: not an app, and not a crash either.
    (ws / ".pantheon/apps/rubbish").mkdir(parents=True)

    class FakeSettings:
        workspace = ws

    monkeypatch.setattr("pantheon.settings.get_settings", lambda: FakeSettings())
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ts = DesktopToolSet.__new__(DesktopToolSet)

    out = asyncio.run(ts.desktop_app_registry())
    assert out["success"]
    by_id = {a["manifest"]["id"]: a for a in out["apps"]}
    assert set(by_id) == {"viv", "igv"}
    assert by_id["viv"]["manifest"]["version"] == "2.0", "workspace wins"
    assert by_id["viv"]["scope"] == "workspace"
    assert by_id["igv"]["scope"] == "user"
    assert out["scopes_read"] >= 2
