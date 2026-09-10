import asyncio
from types import SimpleNamespace

import httpx
import pytest

from pantheon.apps.builtin.desktop.data_server import LiveViewDataServer, _prefix_for
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


def test_store_source_repositories_are_allowed_without_exposing_store_records(
    monkeypatch, tmp_path,
):
    settings = SimpleNamespace(
        work_dir=tmp_path / "workspace",
        workspace=tmp_path / "workspace",
        skills_dir=tmp_path / "workspace/.pantheon/skills",
        global_skills_dir=tmp_path / ".pantheon/skills",
        factory_skills_dir=tmp_path / "factory/skills",
    )
    monkeypatch.setattr("pantheon.settings.get_settings", lambda: settings)
    toolset = DesktopToolSet()
    user_apps = tmp_path / ".pantheon/apps"
    monkeypatch.setattr(toolset, "_app_scope_roots", lambda: [(user_apps, "user")])

    roots = toolset._data_roots()
    store = user_apps.parent / "app-store"
    assert store / "repositories" in roots
    assert store / "snapshots" in roots
    assert store / "forks" in roots
    assert store not in roots
    assert user_apps.parent not in roots


@pytest.fixture(params=[False, True], ids=["local", "server"])
def data_server(request, monkeypatch):
    monkeypatch.setenv("LIVE_VIEW_DATA_PORT", "0")
    if request.param:
        monkeypatch.setenv("LIVE_VIEW_DATA_TOKEN", "test-source-token")
    else:
        monkeypatch.delenv("LIVE_VIEW_DATA_TOKEN", raising=False)
    return LiveViewDataServer()


@pytest.mark.asyncio
async def test_source_created_after_first_viewer_can_be_read_and_updated(
    tmp_path, data_server,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    existing = workspace / "existing.txt"
    existing.write_text("existing document")
    repositories = tmp_path / ".pantheon/app-store/repositories"
    roots = [workspace, repositories]
    base = await data_server.ensure_started(roots)
    data_server.set_tunnel_base(base.replace("0.0.0.0", "127.0.0.1"))
    existing_url = data_server.url_for(existing)

    source = repositories / "builtin/pdf-viewer/frontend/main.js"
    source.parent.mkdir(parents=True)
    source.write_text("export const version = 1;\n")
    assert data_server.url_for(source) is None
    assert await data_server.ensure_started(roots) == base
    assert data_server.url_for(existing) == existing_url

    async with httpx.AsyncClient(trust_env=False) as client:
        url = data_server.url_for(source)
        response = await client.get(url)
        assert response.status_code == 200
        assert response.text == source.read_text()
        assert "no-store" in response.headers["cache-control"]
        partial = await client.get(url, headers={"Range": "bytes=0-5"})
        assert partial.status_code == 206
        assert partial.text == "export"
        source.write_text("export const version = 2;\n")
        assert (await client.get(url)).text == source.read_text()
        assert (await client.get(existing_url)).text == "existing document"


@pytest.mark.asyncio
async def test_new_source_root_preserves_path_and_token_guards(tmp_path, data_server):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = await data_server.ensure_started([workspace])
    data_server.set_tunnel_base(base.replace("0.0.0.0", "127.0.0.1"))
    repositories = tmp_path / "repositories"
    repositories.mkdir()
    source = repositories / "main.js"
    source.write_text("source code")
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (repositories / "escape.txt").symlink_to(outside)
    await data_server.ensure_started([workspace, repositories])
    assert data_server.url_for(outside) is None
    assert data_server.url_for(repositories / "escape.txt") is None
    url = data_server.url_for(source)
    root_url = url.rsplit("/", 1)[0] + "/"

    async with httpx.AsyncClient(trust_env=False) as client:
        assert (await client.get(url)).status_code == 200
        assert (await client.get(root_url)).status_code == 404
        assert (await client.get(root_url + "escape.txt")).status_code == 403
        traversal = await client.get(root_url + "%2e%2e%2fprivate.txt")
        assert traversal.status_code == 403
        unknown = url.replace(_prefix_for(repositories), "unregistered")
        assert (await client.get(unknown)).status_code == 404
        if data_server._server_mode:
            denied = await client.get(url.replace("test-source-token", "wrong-token"))
            assert denied.status_code == 403
            # The localhost route must not bypass the server-mode token gate.
            bypass = (
                base.replace("0.0.0.0", "127.0.0.1")
                + f"/{_prefix_for(repositories)}/main.js"
            )
            assert (await client.get(bypass)).status_code == 404


@pytest.mark.asyncio
async def test_concurrent_start_preserves_all_authorized_roots(tmp_path, data_server):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.txt").write_text("a")
    (second / "b.txt").write_text("b")
    bases = await asyncio.gather(
        data_server.ensure_started([first]), data_server.ensure_started([second]),
    )
    assert bases[0] == bases[1]
    data_server.set_tunnel_base(bases[0].replace("0.0.0.0", "127.0.0.1"))
    async with httpx.AsyncClient(trust_env=False) as client:
        assert (await client.get(data_server.url_for(first / "a.txt"))).text == "a"
        assert (await client.get(data_server.url_for(second / "b.txt"))).text == "b"
