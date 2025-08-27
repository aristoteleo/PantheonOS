import uuid
from pathlib import Path

from ...toolset import tool
from ..file_manager import FileManagerToolSetBase


class FileTransferToolSet(FileManagerToolSetBase):
    """File transfer toolset.
    This class is a toolset that provides the basic file transfer functionality, including:
    - open file for write
    - write chunk
    - close file
    - read file
    """
    def __init__(
            self,
            name: str,
            path: str | Path,
            worker_params: dict | None = None,
            **kwargs,
            ):
        super().__init__(name, path, worker_params, **kwargs)
        self._handles = {}

    @tool
    async def open_file_for_write(self, file_path: str):
        """Open a file for writing."""
        if '..' in file_path:
            return {"error": "File path cannot contain '..'"}
        path = self.path / file_path
        handle_id = str(uuid.uuid4())
        try:
            handle = open(path, "wb")
            self._handles[handle_id] = handle
            return {"success": True, "handle_id": handle_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def write_chunk(self, handle_id: str, data: bytes):
        """Write a chunk to a file."""
        if handle_id not in self._handles:
            return {"success": False, "error": "Handle not found"}
        handle = self._handles[handle_id]
        handle.write(data)
        return {"success": True}

    @tool
    async def close_file(self, handle_id: str):
        """Close a file."""
        if handle_id not in self._handles:
            return {"success": False, "error": "Handle not found"}
        handle = self._handles[handle_id]
        handle.close()
        del self._handles[handle_id]
        return {"success": True}

    @tool
    async def read_file(self, file_path: str, receive_chunk, chunk_size: int = 1024):
        """Read a file."""
        if '..' in file_path:
            return {"error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"success": False, "error": "File does not exist"}
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                await receive_chunk(data)
        return {"success": True}