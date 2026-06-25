"""Tests for endpoint lifecycle management (list, unregister, info)."""

import httpx
import pytest
from aiohttp import web

from pantheon.toolsets.live_view.data_server import LiveViewDataServer


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
