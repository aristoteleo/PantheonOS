"""Keep durable memory file operations on the topology Agent that owns them.

The Agent's /workspace is local brain state, not the workspace node's volume.
Only the memory store/index are routed here; data files still use Fleet apps.
"""
from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

MAX_PREVIEW_BYTES = 256 * 1024  # base64 + RPC envelope stay below NATS' payload limit
_FILE_METHODS = {
    "read_file": {"start_line", "end_line", "max_chars", "symbol"},
    "write_file": {"content", "overwrite", "append"},
    "update_file": {"old_string", "new_string", "replace_all", "start_line", "end_line"},
}


def is_memory_file_request(method: str, args: dict) -> bool:
    if not os.environ.get("PANTHEON_STATE_URL"):
        return False
    if method not in _FILE_METHODS and method != "serve_local_data":
        return False
    path = args.get("path" if method == "serve_local_data" else "file_path")
    return isinstance(path, str) and (
        ".pantheon/memory-store/" in path or path.endswith(".pantheon/MEMORY.md"))


def memory_path(path: str, workdir: str | None = None) -> Path | None:
    if not os.environ.get("PANTHEON_STATE_URL") or not isinstance(path, str):
        return None
    from pantheon.settings import get_settings

    home = Path(get_settings().work_dir).absolute()
    # Topology owns one user's /workspace. Local/test installations retain
    # their configured root, and do not gain access to other directories.
    root = Path("/workspace") if home.is_relative_to("/workspace") else home
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(workdir or home) / candidate
    if ".." in candidate.parts or not candidate.is_relative_to(root):
        return None
    parts = candidate.relative_to(root).parts
    is_store = any(parts[i:i + 2] == (".pantheon", "memory-store")
                   for i in range(len(parts) - 2))
    is_index = parts[-2:] == (".pantheon", "MEMORY.md")
    if not (is_store or is_index):
        return None
    resolved = candidate.resolve()
    # Reject symlinks out of the memory directory, even within the workspace.
    state_dir = next(parent for parent in candidate.parents if parent.name == ".pantheon")
    allowed = state_dir.resolve() / ("memory-store" if is_store else "MEMORY.md")
    if not resolved.is_relative_to(allowed) or not state_dir.resolve().is_relative_to(root.resolve()):
        return None
    return resolved


async def route_memory_file(method: str, args: dict, *, workdir: str | None = None) -> dict | None:
    """None means this is a normal workspace request, not a memory request."""
    if not is_memory_file_request(method, args):
        return None
    path = memory_path(args.get("path" if method == "serve_local_data" else "file_path"), workdir)
    if path is None:
        return None
    if method == "serve_local_data":
        try:
            def read_bounded():
                with path.open("rb") as f:
                    return f.read(MAX_PREVIEW_BYTES + 1)
            data = await asyncio.to_thread(read_bounded)
            if len(data) > MAX_PREVIEW_BYTES:
                return {"success": False, "error": "Memory file is too large to preview (limit: 256 KiB)"}
            data.decode("utf-8")  # A memory preview is text, never executable HTML.
            return {"success": True, "url": "data:text/plain;charset=utf-8;base64," + base64.b64encode(data).decode(),
                    "path": str(path), "source": "agent-memory"}
        except (OSError, UnicodeError) as error:
            return {"success": False, "error": f"Cannot preview memory file: {error}"}

    from pantheon.apps.builtin.file import FileManagerToolSet
    manager = FileManagerToolSet("agent-memory-files", path=path.parent)
    kwargs = {key: value for key, value in args.items() if key in _FILE_METHODS[method]}
    return await getattr(manager, method)(file_path=str(path), **kwargs)
