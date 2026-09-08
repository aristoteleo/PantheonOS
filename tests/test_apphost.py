"""apphost — the App-as-a-process shim (§04c `process`, P3 groundwork).

Layered on purpose: resolution and construction are unit-tested in-process;
the full CLI boots real apps in a subprocess with --no-remote (construct +
run_setup, no bus). Bus registration itself is ToolSet.run()'s job and is
covered by the existing remote toolset tests.
"""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pantheon.apphost import _construct_kwargs, _resolve_backend

REPO = Path(__file__).resolve().parent.parent


def test_resolve_backend_finds_catalog_apps():
    cls, requires, app = _resolve_backend("file-manager")
    assert cls.__name__ == "FileManagerToolSet"
    assert "fs:workspace" in requires and app.manifest.id == "file-manager"


def test_resolve_backend_refuses_runner_builtins():
    with pytest.raises(SystemExit, match="runner builtin"):
        _resolve_backend("shell")


def test_resolve_backend_refuses_unknown_id():
    with pytest.raises(SystemExit, match="unknown app id"):
        _resolve_backend("no-such-app")


def test_construct_kwargs_follow_placement_contract(tmp_path):
    wd = str(tmp_path)
    assert _construct_kwargs("desktop", ["proc", "fs:workspace"], wd) == {"workdir": wd}
    assert _construct_kwargs("file-manager", ["fs:workspace"], wd) == {"path": wd}
    assert _construct_kwargs("file-transfer", ["fs:workspace", "net"], wd) == {"path": wd}
    assert _construct_kwargs("web", ["net"], wd) == {}


@pytest.mark.parametrize("app_id", ["file-manager", "file-transfer", "web"])
def test_cli_boots_app_without_bus(app_id, tmp_path):
    """The whole CLI path: argparse -> registry -> constructor -> run_setup."""
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-m", "pantheon.apphost", "--app-id", app_id,
         "--workdir", str(tmp_path), "--no-remote"],
        env=env, cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]


@pytest.mark.asyncio
async def test_file_transfer_uses_project_root_from_apphost(tmp_path):
    """A launched transfer App must share the file manager's project root."""
    cls, requires, _ = _resolve_backend("file-transfer")
    transfer = cls("file_transfer", **_construct_kwargs("file-transfer", requires, str(tmp_path)))
    payload = b"\x89PNG\r\n\x1a\nproject screenshot\x00\xff"
    relative_path = "attachments/browser-window.png"
    opened = await transfer.open_file_for_write(relative_path)
    assert opened["success"], opened
    try:
        for chunk in (payload[:8], payload[8:]):
            result = await transfer.write_chunk(opened["handle_id"], base64.b64encode(chunk).decode())
            assert result["success"], result
    finally:
        closed = await transfer.close_file(opened["handle_id"])
    assert closed["success"] and closed["total_size"] == len(payload)
    assert (tmp_path / relative_path).read_bytes() == payload

    opened = await transfer.open_file_for_read(relative_path)
    assert opened["success"], opened
    try:
        result = await transfer.read_chunk_at(opened["handle_id"], 0, len(payload))
        assert result["success"], result
        assert base64.b64decode(result["data"]) == payload
    finally:
        assert (await transfer.close_file(opened["handle_id"]))["success"]


def test_cli_boots_mcp_gateway(tmp_path):
    """The mcp-gateway App's run_setup starts a real FastMCP gateway."""
    pytest.importorskip("fastmcp")
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-m", "pantheon.apphost", "--app-id", "mcp-gateway",
         "--workdir", str(tmp_path), "--no-remote"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1200:]
