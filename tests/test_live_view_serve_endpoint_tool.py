import sys

import pytest
from aiohttp import web

from pantheon.toolsets.live_view.toolset import LiveViewToolSet


class FakeDataServer:
    def __init__(self):
        self.base_url = "http://data.local"
        self.registered = {}

    async def register_endpoint(self, name, handler):
        self.registered[name] = handler
        return f"http://data.local/api/{name}/"


@pytest.mark.asyncio
async def test_serve_endpoint_loads_handle_export(monkeypatch, tmp_path):
    module = tmp_path / "endpoint.py"
    module.write_text(
        "from aiohttp import web\n"
        "async def handle(request):\n"
        "    return web.json_response({'path': request.match_info.get('tail', '')})\n",
        encoding="utf-8",
    )
    fake_server = FakeDataServer()
    toolset = LiveViewToolSet()
    monkeypatch.setattr(toolset, "_data_roots", lambda: [tmp_path])

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.serve_endpoint("track", str(module))

    assert result == {
        "success": True,
        "base_url": "http://data.local",
        "url": "http://data.local/api/track/",
    }
    response = await fake_server.registered["track"](
        type("Req", (), {"match_info": {"tail": "chr1"}})(),
    )
    assert isinstance(response, web.Response)
    assert response.text == '{"path": "chr1"}'


@pytest.mark.asyncio
async def test_serve_endpoint_loads_build_export(monkeypatch, tmp_path):
    module = tmp_path / "endpoint.py"
    module.write_text(
        "from aiohttp import web\n"
        "def build():\n"
        "    async def handler(request):\n"
        "        return web.json_response({'ok': True})\n"
        "    return handler\n",
        encoding="utf-8",
    )
    fake_server = FakeDataServer()
    toolset = LiveViewToolSet()
    monkeypatch.setattr(toolset, "_data_roots", lambda: [tmp_path])

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.serve_endpoint("built", str(module))

    assert result["success"] is True
    assert "built" in fake_server.registered


@pytest.mark.asyncio
async def test_serve_endpoint_passes_config_to_build(monkeypatch, tmp_path):
    module = tmp_path / "endpoint.py"
    module.write_text(
        "from aiohttp import web\n"
        "def build(config):\n"
        "    async def handler(request):\n"
        "        return web.json_response({'sample': config['sample'], 'n': config['n']})\n"
        "    return handler\n",
        encoding="utf-8",
    )
    fake_server = FakeDataServer()
    toolset = LiveViewToolSet()
    monkeypatch.setattr(toolset, "_data_roots", lambda: [tmp_path])

    async def fake_data_server():
        return fake_server

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)

    result = await toolset.serve_endpoint(
        "configured", str(module), {"sample": "GM12878", "n": 7},
    )

    assert result["success"] is True
    response = await fake_server.registered["configured"](
        type("Req", (), {"match_info": {}})(),
    )
    assert response.text == '{"sample": "GM12878", "n": 7}'


@pytest.mark.asyncio
async def test_serve_endpoint_rejects_non_json_config(tmp_path):
    module = tmp_path / "endpoint.py"
    module.write_text("async def handle(request): pass\n", encoding="utf-8")
    toolset = LiveViewToolSet()
    toolset._data_roots = lambda: [tmp_path]

    result = await toolset.serve_endpoint("track", str(module), {"bad": object()})

    assert result["success"] is False
    assert "JSON-serializable" in result["error"]


@pytest.mark.asyncio
async def test_serve_endpoint_rejects_config_when_build_takes_no_args(
    monkeypatch, tmp_path,
):
    module = tmp_path / "endpoint.py"
    module.write_text(
        "from aiohttp import web\n"
        "def build():\n"
        "    async def handler(request):\n"
        "        return web.json_response({'ok': True})\n"
        "    return handler\n",
        encoding="utf-8",
    )
    toolset = LiveViewToolSet()
    monkeypatch.setattr(toolset, "_data_roots", lambda: [tmp_path])

    result = await toolset.serve_endpoint("track", str(module), {"x": 1})

    assert result["success"] is False
    assert "does not accept config" in result["error"]


def test_load_endpoint_handler_does_not_leave_unique_module_in_sys_modules(
    tmp_path,
):
    module = tmp_path / "endpoint.py"
    module.write_text(
        "from aiohttp import web\n"
        "async def handle(request):\n"
        "    return web.json_response({'ok': True})\n",
        encoding="utf-8",
    )
    toolset = LiveViewToolSet()

    toolset._load_endpoint_handler(module)

    leaked = [
        name for name in sys.modules
        if name.startswith("_pantheon_live_view_endpoint_")
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_serve_endpoint_rejects_bad_name(tmp_path):
    module = tmp_path / "endpoint.py"
    module.write_text("async def handle(request): pass\n", encoding="utf-8")
    toolset = LiveViewToolSet()

    result = await toolset.serve_endpoint("bad/name", str(module))

    assert result["success"] is False
    assert "Endpoint name" in result["error"]


@pytest.mark.asyncio
async def test_serve_endpoint_rejects_file_outside_served_roots(tmp_path):
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    module = tmp_path / "endpoint.py"
    module.write_text("async def handle(request): pass\n", encoding="utf-8")
    toolset = LiveViewToolSet()
    toolset._data_roots = lambda: [allowed]

    result = await toolset.serve_endpoint("track", str(module))

    assert result["success"] is False
    assert "outside the LiveView data server roots" in result["error"]


@pytest.mark.asyncio
async def test_serve_endpoint_rejects_missing_file():
    toolset = LiveViewToolSet()

    result = await toolset.serve_endpoint("track", "/no/such/endpoint.py")

    assert result["success"] is False
    assert "Path does not exist" in result["error"]


@pytest.mark.asyncio
async def test_serve_endpoint_requires_handle_or_build(tmp_path):
    module = tmp_path / "endpoint.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    toolset = LiveViewToolSet()
    toolset._data_roots = lambda: [tmp_path]

    result = await toolset.serve_endpoint("track", str(module))

    assert result["success"] is False
    assert "export `handle(request)` or `build()`" in result["error"]
