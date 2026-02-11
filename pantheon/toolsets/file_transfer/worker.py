import base64
import uuid
from pathlib import Path

from pantheon.toolset import tool
from pantheon.toolsets.file.file_manager import FileManagerToolSetBase


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
        **kwargs,
    ):
        super().__init__(name, path, **kwargs)
        self._handles = {}

    @tool
    async def open_file_for_write(self, file_path: str):
        """Open a file for writing."""
        if ".." in file_path:
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
    async def write_chunk(self, handle_id: str, data):
        """Write a chunk to a file.

        Args:
            handle_id: File handle ID from open_file_for_write.
            data: Binary data as bytes (from cloudpickle) or
                  base64-encoded string (from JSON/frontend clients).
        """
        if handle_id not in self._handles:
            return {"success": False, "error": "Handle not found"}
        handle = self._handles[handle_id]
        # Frontend JSON clients send base64-encoded strings
        if isinstance(data, str):
            data = base64.b64decode(data)
        elif not isinstance(data, (bytes, bytearray)):
            return {"success": False, "error": f"Unsupported data type: {type(data).__name__}"}
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
    async def open_file_for_read(self, file_path: str):
        """Open a file for chunked reading. Returns handle_id and total_size."""
        if ".." in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"success": False, "error": "File does not exist"}
        handle_id = str(uuid.uuid4())
        try:
            handle = open(path, "rb")
            total_size = path.stat().st_size
            self._handles[handle_id] = handle
            return {"success": True, "handle_id": handle_id, "total_size": total_size}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def read_chunk(self, handle_id: str, size: int = 512 * 1024):
        """Read a chunk from an open file handle.

        Args:
            handle_id: File handle ID from open_file_for_read.
            size: Number of bytes to read (before base64 encoding).
        """
        if handle_id not in self._handles:
            return {"success": False, "error": "Handle not found"}
        handle = self._handles[handle_id]
        try:
            data = handle.read(size)
            if not data:
                return {"success": True, "data": "", "bytes_read": 0, "eof": True}
            return {
                "success": True,
                "data": base64.b64encode(data).decode("utf-8"),
                "bytes_read": len(data),
                "eof": len(data) < size,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def read_file(
        self, file_path: str, receive_chunk=None, chunk_size: int = 1024
    ):
        """Read a file."""
        if ".." in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"success": False, "error": "File does not exist"}

        if receive_chunk is None:
            # Non-streaming mode: return full file content for proxy calls (base64 encoded for JSON compatibility)
            with open(path, "rb") as f:
                file_data = f.read()
                return {
                    "success": True,
                    "data": base64.b64encode(file_data).decode("utf-8"),
                    "total_size": len(file_data),
                    "encoding": "base64",
                }
        else:
            # Streaming mode: use callback function for direct connections
            with open(path, "rb") as f:
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    await receive_chunk(data)
            return {"success": True}
