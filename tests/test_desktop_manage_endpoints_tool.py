"""Tests for the manage_endpoints unified tool."""

import pytest

from pantheon.toolsets.desktop.toolset import DesktopToolSet


class FakeDataServer:
    def __init__(self):
        self.base_url = "http://data.local"
        self.registered = {}

    async def register_endpoint(self, name, handler):
        self.registered[name] = handler
        return f"http://data.local/api/{name}/"

    def list_endpoints(self):
        return [
            {"name": name, "url": f"http://data.local/api/{name}/"}
            for name in self.registered.keys()
        ]

    def endpoint_exists(self, name):
        return name in self.registered

    def url_for_endpoint(self, name):
        if name in self.registered:
            return f"http://data.local/api/{name}/"
        return None

    def unregister_endpoint(self, name):
        if name in self.registered:
            del self.registered[name]
            return True
        return False


@pytest.mark.asyncio
async def test_manage_endpoints_list_returns_empty_when_none(monkeypatch):
    fake_server = FakeDataServer()
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("list")

    assert result == {"success": True, "endpoints": []}


@pytest.mark.asyncio
async def test_manage_endpoints_list_returns_all_registered(monkeypatch):
    fake_server = FakeDataServer()
    fake_server.registered = {"track_a": lambda r: None, "track_b": lambda r: None}
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("list")

    assert result["success"] is True
    assert len(result["endpoints"]) == 2
    names = {ep["name"] for ep in result["endpoints"]}
    assert names == {"track_a", "track_b"}


@pytest.mark.asyncio
async def test_manage_endpoints_info_returns_details_for_existing(monkeypatch):
    fake_server = FakeDataServer()
    fake_server.registered = {"my_track": lambda r: None}
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("info", "my_track")

    assert result == {
        "success": True,
        "name": "my_track",
        "exists": True,
        "url": "http://data.local/api/my_track/",
    }


@pytest.mark.asyncio
async def test_manage_endpoints_info_returns_not_exists_for_missing(monkeypatch):
    fake_server = FakeDataServer()
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("info", "missing")

    assert result == {
        "success": True,
        "name": "missing",
        "exists": False,
        "url": None,
    }


@pytest.mark.asyncio
async def test_manage_endpoints_unregister_removes_endpoint(monkeypatch):
    fake_server = FakeDataServer()
    fake_server.registered = {"temp": lambda r: None}
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("unregister", "temp")

    assert result == {"success": True, "removed": True}
    assert "temp" not in fake_server.registered


@pytest.mark.asyncio
async def test_manage_endpoints_unregister_returns_false_for_nonexistent(monkeypatch):
    fake_server = FakeDataServer()
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("unregister", "missing")

    assert result == {"success": True, "removed": False}


@pytest.mark.asyncio
async def test_manage_endpoints_rejects_invalid_action():
    toolset = DesktopToolSet()

    result = await toolset.manage_endpoints("invalid_action")

    assert result["success"] is False
    assert "Invalid action" in result["error"]


@pytest.mark.asyncio
async def test_manage_endpoints_info_requires_name():
    toolset = DesktopToolSet()

    result = await toolset.manage_endpoints("info")

    assert result["success"] is False
    assert "requires a 'name' parameter" in result["error"]


@pytest.mark.asyncio
async def test_manage_endpoints_unregister_requires_name():
    toolset = DesktopToolSet()

    result = await toolset.manage_endpoints("unregister")

    assert result["success"] is False
    assert "requires a 'name' parameter" in result["error"]


@pytest.mark.asyncio
async def test_manage_endpoints_info_validates_endpoint_name(monkeypatch):
    fake_server = FakeDataServer()
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("info", "bad/name")

    assert result["success"] is False
    assert "Endpoint name" in result["error"]


@pytest.mark.asyncio
async def test_manage_endpoints_unregister_validates_endpoint_name(monkeypatch):
    fake_server = FakeDataServer()
    toolset = DesktopToolSet()

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.manage_endpoints("unregister", "../../../etc")

    assert result["success"] is False
    assert "Endpoint name" in result["error"]
