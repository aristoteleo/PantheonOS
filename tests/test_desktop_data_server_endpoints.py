import os

import httpx
import pytest
from aiohttp import web

from pantheon.apps.builtin.desktop.data_server import LiveViewDataServer


@pytest.mark.asyncio
async def test_registered_endpoint_is_fetchable(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler(request):
        return web.json_response({
            "tail": request.match_info.get("tail", ""),
            "query": request.query.get("q"),
        })

    url = await server.register_endpoint("ab_track", handler)

    async with httpx.AsyncClient() as client:
        response = await client.get(url + "chr1/segments", params={"q": "TP53"})

    assert response.status_code == 200
    assert response.json() == {"tail": "chr1/segments", "query": "TP53"}


@pytest.mark.asyncio
async def test_registering_same_endpoint_name_replaces_handler(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def first(_request):
        return web.json_response({"version": 1})

    async def second(_request):
        return web.json_response({"version": 2})

    url = await server.register_endpoint("track", first)
    replacement_url = await server.register_endpoint("track", second)

    assert replacement_url == url

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.json() == {"version": 2}


@pytest.mark.asyncio
async def test_endpoint_accepts_browser_post_json_params(tmp_path):
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])

    async def handler(request):
        payload = await request.json()
        return web.json_response({
            "method": request.method,
            "region": payload["region"],
            "threshold": payload["threshold"],
        })

    url = await server.register_endpoint("params", handler)

    async with httpx.AsyncClient() as client:
        preflight = await client.options(url)
        response = await client.post(
            url,
            json={"region": "chr1:1-1000", "threshold": 0.25},
        )

    assert preflight.status_code == 200
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]
    assert response.status_code == 200
    assert response.json() == {
        "method": "POST",
        "region": "chr1:1-1000",
        "threshold": 0.25,
    }


@pytest.mark.asyncio
async def test_server_mode_endpoint_requires_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_VIEW_DATA_TOKEN", "secret-token")
    monkeypatch.setenv("LIVE_VIEW_DATA_PORT", "0")
    server = LiveViewDataServer()
    await server.ensure_started([tmp_path])
    server.set_tunnel_base(server._base_url)

    async def handler(_request):
        return web.json_response({"ok": True})

    url = await server.register_endpoint("secure", handler)
    no_token_url = url.replace("?token=secret-token", "")

    async with httpx.AsyncClient() as client:
        denied = await client.get(no_token_url)
        allowed = await client.get(url)

    assert denied.status_code == 403
    assert denied.text == "forbidden"
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}


@pytest.mark.parametrize("name", ["", "bad/name", "../x", "has space"])
def test_endpoint_names_are_validated(name):
    with pytest.raises(ValueError):
        LiveViewDataServer.validate_endpoint_name(name)
