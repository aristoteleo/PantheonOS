from typing import Any, Dict, Callable
from magique.worker import MagiqueWorker
from magique.client import connect_to_server, ServiceProxy
from .base import RemoteBackend, RemoteService, RemoteWorker, ServiceInfo


class MagiqueBackend(RemoteBackend):
    """Magique implementation of RemoteBackend"""

    def __init__(self, server_urls: list[str], **default_kwargs):
        self.server_urls = server_urls
        self.default_kwargs = default_kwargs

    async def connect(self, service_id: str, **kwargs) -> "MagiqueService":
        merged_kwargs = {**self.default_kwargs, **kwargs}
        server = await connect_to_server(url=self.server_urls, **merged_kwargs)
        service = await server.get_service(service_id)
        return MagiqueService(service=service)

    def create_worker(self, service_name: str, **kwargs) -> "MagiqueRemoteWorker":
        merged_kwargs = {
            "service_name": service_name,
            "server_url": self.server_urls,
            "need_auth": False,
            **self.default_kwargs,
            **kwargs,
        }
        worker = MagiqueWorker(**merged_kwargs)
        return MagiqueRemoteWorker(worker)

    @property
    def servers(self):
        return self.server_urls


class MagiqueService(RemoteService):
    def __init__(self, service: ServiceProxy):
        self._service: ServiceProxy = service

    async def invoke(self, method: str, parameters: Dict[str, Any] = None) -> Any:
        if parameters is not None:
            return await self._service.invoke(method, parameters)
        return await self._service.invoke(method)

    async def close(self):
        # Magique handles connection lifecycle internally
        pass

    @property
    def service_info(self):
        """Get service information from the underlying magique service"""
        _service_info = self._service.service_info
        return ServiceInfo(
            service_id=_service_info.service_id,
            service_name=_service_info.service_name,
            description=_service_info.description,
            functions_description=_service_info.functions_description,
        )

    async def fetch_service_info(self) -> ServiceInfo:
        return self.service_info


class MagiqueRemoteWorker(RemoteWorker):
    def __init__(self, worker: MagiqueWorker):
        self._worker = worker

    def register(self, func: Callable, **kwargs):
        self._worker.register(func, **kwargs)

    async def run(self):
        return await self._worker.run()

    async def stop(self):
        # Implement stop logic for MagiqueWorker if available
        pass

    @property
    def service_id(self) -> str:
        return self._worker.service_id

    @property
    def service_name(self) -> str:
        return self._worker.service_name

    @property
    def servers(self):
        """Expose servers property for compatibility"""
        return self._worker.servers

    @property
    def functions(self) -> Dict[str, tuple]:
        """Expose functions property for compatibility with hypha and MCP"""
        return self._worker.functions
