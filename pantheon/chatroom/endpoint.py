import os
import sys
from pathlib import Path
import base64
import shutil
import uuid

from magique.worker import MagiqueWorker
from magique.ai.toolset import run_toolsets, ToolSet
from magique.ai.tools.file_manager import FileManagerToolSet
from magique.ai.tools.web_browse import WebBrowseToolSet
from magique.ai import connect_remote
from magique.ai.constant import DEFAULT_SERVER_URL

from .python_interpreter import ScientificPythonInterpreterToolSet


class Endpoint:
    def __init__(
        self,
        name: str = "pantheon-chatroom-endpoint",
        workspace_path: str = "./.pantheon-chatroom-workspace",
        worker_params: dict | None = None,
    ):
        self.name = name
        _worker_params = {
            "service_name": name,
            "server_url": DEFAULT_SERVER_URL,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.services: list[ToolSet] = []
        self.outer_services: list[dict] = []
        self.setup_handlers()
        self.path = Path(workspace_path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.create_services()
        self._handles = {}

    def setup_handlers(self):
        self.worker.register(self.list_services)
        self.worker.register(self.add_service)
        self.worker.register(self.get_service)
        # File management
        self.worker.register(self.fetch_image_base64)
        self.worker.register(self.list_files)
        self.worker.register(self.create_directory)
        self.worker.register(self.delete_directory)
        self.worker.register(self.delete_file)
        self.worker.register(self.open_file_for_write)
        self.worker.register(self.write_chunk)
        self.worker.register(self.close_file)
        self.worker.register(self.read_file)

    async def list_services(self) -> list[dict]:
        res = []
        for service in self.services:
            res.append({
                "name": service.worker.service_name,
                "id": service.worker.service_id,
            })
        for s in self.outer_services:
            res.append({
                "name": s["name"],
                "id": s["id"],
            })
        return res

    async def fetch_image_base64(self, image_path: str) -> dict:
        """Fetch an image and return the base64 encoded image."""
        if '..' in image_path:
            return {"success": False, "error": "Image path cannot contain '..'"}
        i_path = self.path / image_path
        if not i_path.exists():
            return {"success": False, "error": "Image does not exist"}
        format = i_path.suffix.lower()
        if format not in [".jpg", ".jpeg", ".png", ".gif"]:
            return {"success": False, "error": "Image format must be jpg, jpeg, png or gif"}
        with open(i_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
            data_uri = f"data:image/{format};base64,{b64}"
        return {
            "success": True,
            "image_path": image_path,
            "data_uri": data_uri,
        }

    async def list_files(self, sub_dir: str | None = None) -> dict:
        """List all files in the directory."""
        if not self.path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if (sub_dir is not None) and ('..' in sub_dir):
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        if sub_dir is None or sub_dir == "":
            files = list(self.path.glob("*"))
        else:
            files = list(self.path.glob(f"{sub_dir}/*"))
        return {
            "success": True,
            "files": [
                {
                    "name": file.name,
                    "size": file.stat().st_size if file.is_file() else 0,
                    "type": "file" if file.is_file() else "directory",
                }
                for file in files
            ],
        }

    async def create_directory(self, sub_dir: str):
        """Create a new directory."""
        if '..' in sub_dir:
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        new_dir = self.path / sub_dir
        new_dir.mkdir(parents=True, exist_ok=True)
        return {"success": True}

    async def delete_directory(self, sub_dir: str):
        """Delete a directory and all its contents recursively."""
        if '..' in sub_dir:
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        dir_path = self.path / sub_dir
        if not dir_path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if not dir_path.is_dir():
            return {"success": False, "error": "Path is not a directory"}
        shutil.rmtree(dir_path)
        return {"success": True}

    async def delete_file(self, file_path: str):
        """Delete a file."""
        if '..' in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"success": False, "error": "File does not exist"}
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return {"success": True}

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
            return {"error": str(e)}

    async def write_chunk(self, handle_id: str, data: bytes):
        """Write a chunk to a file."""
        if handle_id not in self._handles:
            return {"error": "Handle not found"}
        handle = self._handles[handle_id]
        handle.write(data)
        return {"success": True}

    async def close_file(self, handle_id: str):
        """Close a file."""
        if handle_id not in self._handles:
            return {"error": "Handle not found"}
        handle = self._handles[handle_id]
        handle.close()
        del self._handles[handle_id]
        return {"success": True}

    async def read_file(self, file_path: str, receive_chunk, chunk_size: int = 1024):
        """Read a file."""
        if '..' in file_path:
            return {"error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"error": "File does not exist"}
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                await receive_chunk(data)
        return {"success": True}

    async def add_service(self, service_id: str):
        """Add a service to the endpoint."""
        for s in self.services:
            if s.worker.service_id == service_id:
                return {"success": False, "error": "Service already exists"}
        try:
            s = await connect_remote(service_id, DEFAULT_SERVER_URL)
            info = await s.fetch_service_info()
            self.outer_services.append({
                "id": service_id,
                "name": info.service_name,
            })
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_service(self, service_id_or_name: str) -> dict | None:
        """Get a service by id or name."""
        for s in self.services:
            if (
                s.worker.service_id == service_id_or_name
                or s.worker.service_name == service_id_or_name
            ):
                return {
                    "id": s.worker.service_id,
                    "name": s.worker.service_name,
                }
        for s in self.outer_services:
            if (
                s["id"] == service_id_or_name
                or s["name"] == service_id_or_name
            ):
                return s
        return None

    def create_services(self):
        toolset = ScientificPythonInterpreterToolSet(
            name="python_interpreter",
            workdir=str(self.path),
        )
        self.services.append(toolset)
        toolset = FileManagerToolSet(
            name="file_manager",
            path=str(self.path),
        )
        self.services.append(toolset)
        toolset = WebBrowseToolSet(
            name="web_browse",
        )
        self.services.append(toolset)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        async with run_toolsets(self.services, log_level=log_level):
            logger.info(f"Remote Server: {self.worker.server_url}")
            logger.info(f"Service Name: {self.worker.service_name}")
            logger.info(f"Service ID: {self.worker.service_id}")
            return await self.worker.run()
