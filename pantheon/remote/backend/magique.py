"""
Magique远程后端实现
Magique Remote Backend Implementation
仅支持RPC调用，流式传输功能为unimplemented
"""

from typing import Any, Dict, Callable, Optional
from magique.worker import MagiqueWorker
from magique.client import connect_to_server, ServiceProxy
from .base import RemoteBackend, RemoteService, RemoteWorker, ServiceInfo, StreamType, StreamMessage, StreamChannel


class MagiqueBackend(RemoteBackend):
    """Magique远程后端 - 仅支持RPC调用"""

    def __init__(self, server_urls: list[str], **default_kwargs):
        self.server_urls = server_urls
        self.default_kwargs = default_kwargs

    # RPC接口实现
    async def connect(self, service_id: str, **kwargs) -> "MagiqueService":
        """连接到远程服务"""
        merged_kwargs = {**self.default_kwargs, **kwargs}
        server = await connect_to_server(url=self.server_urls, **merged_kwargs)
        service = await server.get_service(service_id)
        return MagiqueService(service=service)

    def create_worker(self, service_name: str, **kwargs) -> "MagiqueRemoteWorker":
        """创建远程工作器"""
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

    # 流式传输接口 - Magique不支持
    async def get_or_create_stream(
        self,
        stream_id: str,
        stream_type: StreamType = StreamType.CUSTOM,
        **kwargs
    ) -> StreamChannel:
        """获取现有流或创建新的流式传输通道 - Magique不支持"""
        raise NotImplementedError("Magique backend does not support streaming. Use NATS backend for streaming functionality.")



class MagiqueService(RemoteService):
    """Magique服务客户端"""

    def __init__(self, service: ServiceProxy):
        self._service: ServiceProxy = service

    async def invoke(self, method: str, parameters: Dict[str, Any] = None) -> Any:
        """调用远程方法"""
        if parameters is not None:
            return await self._service.invoke(method, parameters)
        return await self._service.invoke(method)

    async def close(self):
        """关闭连接"""
        # Magique handles connection lifecycle internally
        pass

    @property
    def service_info(self):
        """获取服务信息"""
        _service_info = self._service.service_info
        return ServiceInfo(
            service_id=_service_info.service_id,
            service_name=_service_info.service_name,
            description=_service_info.description,
            functions_description=_service_info.functions_description,
        )

    async def fetch_service_info(self) -> ServiceInfo:
        """获取服务信息"""
        return self.service_info


class MagiqueRemoteWorker(RemoteWorker):
    """Magique远程工作器"""

    def __init__(self, worker: MagiqueWorker):
        self._worker = worker
        # Auto-register ping function for connection checking
        self.register(self._ping)

    async def _ping(self) -> dict:
        """Ping function for connection checking"""
        return {"status": "ok", "service_id": self.service_id}

    def register(self, func: Callable, **kwargs):
        """注册函数"""
        self._worker.register(func, **kwargs)

    async def run(self):
        """启动工作器"""
        return await self._worker.run()

    async def stop(self):
        """停止工作器"""
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
        """获取服务器列表"""
        return self._worker.servers

    @property
    def functions(self) -> Dict[str, tuple]:
        """获取注册的函数"""
        return self._worker.functions