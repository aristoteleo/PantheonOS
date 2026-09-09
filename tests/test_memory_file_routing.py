import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.internal.memory_system.file_routing import memory_path, route_memory_file, MAX_PREVIEW_BYTES


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_STATE_URL", "https://state.invalid")
    monkeypatch.setattr("pantheon.settings.get_settings", lambda: SimpleNamespace(
        work_dir=tmp_path, max_file_read_chars=50000, max_file_read_lines=800))
    root = tmp_path / "project/.pantheon/memory-store"
    root.mkdir(parents=True)
    return root


@pytest.mark.asyncio
async def test_preview_and_agent_read_return_the_same_memory_bytes(memory_root):
    file = memory_root / "reference.md"
    content = "# Notebook\n中文 memory\n"
    file.write_text(content)
    read = await route_memory_file("read_file", {"file_path": str(file), "start_line": 2, "end_line": 2})
    assert read["success"] and read["content"] == "中文 memory\n"
    served = await route_memory_file("serve_local_data", {"path": str(file)})
    assert served["success"] and served["source"] == "agent-memory"
    assert base64.b64decode(served["url"].split(",", 1)[1]).decode() == content
    assert served["url"].startswith("data:text/plain;")


@pytest.mark.asyncio
async def test_save_and_update_reach_the_same_authoritative_file(memory_root):
    file = memory_root / "new.md"
    assert (await route_memory_file("write_file", {"file_path": str(file), "content": "first\n"}))["success"]
    assert (await route_memory_file("update_file", {"file_path": str(file), "old_string": "first", "new_string": "second"}))["success"]
    assert file.read_text() == "second\n"


@pytest.mark.asyncio
async def test_normal_files_and_non_topology_installations_keep_existing_routing(memory_root, monkeypatch):
    assert await route_memory_file("read_file", {"file_path": "Desktop/report.md"}) is None
    assert await route_memory_file("desktop_read", {"window_id": "win-1"}) is None
    monkeypatch.delenv("PANTHEON_STATE_URL")
    assert await route_memory_file("read_file", {"file_path": str(memory_root / "reference.md")}) is None


def test_project_relative_paths_and_directory_boundaries(memory_root, tmp_path):
    assert memory_path(".pantheon/memory-store/new.md", str(memory_root.parents[1])) == memory_root / "new.md"
    assert memory_path(".pantheon/MEMORY.md", str(memory_root.parents[1])) == memory_root.parent / "MEMORY.md"
    assert memory_path(str(memory_root / "../settings.json")) is None
    assert memory_path(str(tmp_path.parent / ".pantheon/memory-store/outside.md")) is None
    secret = tmp_path / "outside.txt"
    secret.write_text("not memory")
    (memory_root / "link.md").symlink_to(secret)
    assert memory_path(str(memory_root / "link.md")) is None
    (memory_root.parent / "settings.json").write_text("not memory either")
    (memory_root / "settings.md").symlink_to(memory_root.parent / "settings.json")
    assert memory_path(str(memory_root / "settings.md")) is None


@pytest.mark.asyncio
async def test_preview_missing_binary_and_oversize_files_are_reported(memory_root):
    file = memory_root / "missing.md"
    assert (await route_memory_file("serve_local_data", {"path": str(file)}))["success"] is False
    file.write_bytes(b"\xff")
    assert (await route_memory_file("serve_local_data", {"path": str(file)}))["success"] is False
    file.write_bytes(b"x" * (MAX_PREVIEW_BYTES + 1))
    assert "too large" in (await route_memory_file("serve_local_data", {"path": str(file)}))["error"]


@pytest.mark.asyncio
async def test_agent_proxy_does_not_send_memory_reads_to_workspace(memory_root):
    from pantheon.apps.proxy import ToolsetProxy
    file = memory_root / "reference.md"
    file.write_text("agent state")
    resolver = SimpleNamespace(_workdir=str(memory_root.parents[1]))
    proxy = ToolsetProxy("memory-routing-test").bind_instance(resolver, "file_manager")
    proxy._ensure_connected = AsyncMock(side_effect=AssertionError("wrong filesystem"))
    assert (await proxy.invoke("read_file", {"file_path": str(file)}))["content"] == "agent state"
    proxy._ensure_connected.assert_not_awaited()


@pytest.mark.asyncio
async def test_desktop_preview_and_save_bypass_workspace_resolver(memory_root):
    from pantheon.chatroom.room import ChatRoom
    room = ChatRoom.__new__(ChatRoom)
    room._project_dir_for_chat = AsyncMock(return_value=str(memory_root.parents[1]))
    file = memory_root / "reference.md"
    file.write_text("original")
    result = await room.proxy_toolset("serve_local_data", {"path": str(file)}, "desktop")
    assert result["success"] and base64.b64decode(result["url"].split(",", 1)[1]) == b"original"
    saved = await room.proxy_toolset("write_file", {"file_path": str(file), "content": "saved"}, "file_manager")
    assert saved["success"] and file.read_text() == "saved"
