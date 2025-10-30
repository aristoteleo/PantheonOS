from ...remote import connect_remote


class FileTransferClient:
    """Client for file transfer toolset.
    Use for sending and fetching and managing files to/from the file transfer toolset.

    Args:
        service_id_or_name: The id or name of the service.
        server_url: The server url to connect to.
        connect_params: The parameters for the connection.
    """

    def __init__(
        self,
        service_id_or_name: str,
        server_url: str | list[str] | None = None,
        connect_params: dict | None = None,
    ):
        self._service = None
        self.service_id_or_name = service_id_or_name
        if isinstance(server_url, str):
            server_urls = [server_url]
        else:
            server_urls = server_url
        self.server_urls = server_urls
        self.connect_params = connect_params

    async def connect(self):
        """Connect to the file transfer toolset."""
        if self._service is None:
            params = self.connect_params or {}
            self._service = await connect_remote(
                self.service_id_or_name,
                self.server_urls,
                **params,
            )
        return self._service

    async def list_files(self, sub_dir: str | None = None) -> list[dict]:
        """List all files in the directory."""
        service = await self.connect()
        resp = await service.invoke("list_files", {"sub_dir": sub_dir})
        if resp.get("error"):
            raise Exception(resp["error"])
        return resp

    async def create_directory(self, sub_dir: str):
        """Create a new directory."""
        service = await self.connect()
        resp = await service.invoke("create_directory", {"sub_dir": sub_dir})
        if resp.get("error"):
            raise Exception(resp["error"])
        return resp

    async def delete_directory(self, sub_dir: str):
        """Delete a directory."""
        service = await self.connect()
        resp = await service.invoke("delete_directory", {"sub_dir": sub_dir})
        if resp.get("error"):
            raise Exception(resp["error"])
        return resp

    async def delete_file(self, file_path: str):
        """Delete a file."""
        service = await self.connect()
        resp = await service.invoke("delete_file", {"file_path": file_path})
        if resp.get("error"):
            raise Exception(resp["error"])
        return resp

    async def send_file(self, file: str, target_file_path: str, chunk_size: int = 1024):
        """Send a file to the file transfer toolset.

        Args:
            file: The path to the file to send.
            target_file_path: The path to the file to send to.
            chunk_size: The chunk size for the file transfer.

        Returns:
            A dictionary containing the result of the operation.
        """
        service = await self.connect()
        resp = await service.invoke(
            "open_file_for_write", {"file_path": target_file_path}
        )
        if resp.get("error"):
            raise Exception(resp["error"])
        handle_id = resp["handle_id"]
        with open(file, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                resp = await service.invoke(
                    "write_chunk", {"handle_id": handle_id, "data": data}
                )
                if resp.get("error"):
                    await service.invoke("close_file", {"handle_id": handle_id})
                    raise Exception(resp["error"])
        resp = await service.invoke("close_file", {"handle_id": handle_id})
        if resp.get("error"):
            raise Exception(resp["error"])
        return resp

    async def fetch_file(
        self, local_file_path: str, file_path: str, chunk_size: int = 1024
    ):
        """Fetch a file from the file transfer toolset.

        Args:
            local_file_path: The path to the file to save to.
            file_path: The path to the file to fetch.
            chunk_size: The chunk size for the file transfer.

        """
        service = await self.connect()
        with open(local_file_path, "wb") as f:

            async def receive_chunk(data: bytes):
                f.write(data)

            resp = await service.invoke(
                "read_file",
                {
                    "file_path": file_path,
                    "receive_chunk": receive_chunk,
                    "chunk_size": chunk_size,
                },
            )
            if resp.get("error"):
                raise Exception(resp["error"])
        return resp
