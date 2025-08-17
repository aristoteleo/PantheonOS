from typing import Any, Dict, Optional, Callable
from magique.worker import MagiqueWorker
from magique.client import PyFunction, connect_to_server
from .base import RemoteBackend, RemoteService, RemoteWorker, ServiceInfo


class MagiqueBackend(RemoteBackend):
    """Magique implementation of RemoteBackend"""

    def __init__(self, server_urls: Optional[list] = None, **default_kwargs):
        self.server_urls = server_urls
        self.default_kwargs = default_kwargs

    async def connect(self, service_id: str, **kwargs) -> "MagiqueService":
        merged_kwargs = {**self.default_kwargs, **kwargs}
        service = await connect_to_server(url=self.server_urls, **merged_kwargs)
        return MagiqueService(service)

    async def create_worker(self, service_name: str, **kwargs) -> "MagiqueRemoteWorker":
        merged_kwargs = {
            "service_name": service_name,
            "server_url": self.servers,
            "need_auth": False,
            **self.default_kwargs,
            **kwargs,
        }
        worker = MagiqueWorker(**merged_kwargs)
        return MagiqueRemoteWorker(worker)


class MagiqueService(RemoteService):
    def __init__(self, service):
        self._service = service

    async def invoke(self, method: str, args: Dict[str, Any] = None) -> Any:
        if args is not None:
            return await self._service.invoke(method, args)
        return await self._service.invoke(method)

    async def close(self):
        # Magique handles connection lifecycle internally
        pass

    @property
    def service_info(self):
        """Get service information from the underlying magique service"""
        return ServiceInfo(
            service_id=self._service.service_info.service_id,
            service_name=self._service.service_info.service_name,
        )


class MagiqueRemoteWorker(RemoteWorker):
    def __init__(self, worker: MagiqueWorker):
        self._worker = worker

    def register(self, func: Callable, name: Optional[str] = None):
        self._worker.register(func)

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
