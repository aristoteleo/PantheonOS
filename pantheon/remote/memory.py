import sys
from pathlib import Path

from magique.worker import MagiqueWorker
from magique.ai.constant import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
from magique.ai.utils.remote import connect_remote

from ..memory import MemoryManager


class MemoryManagerService:
    def __init__(
            self,
            memory_dir: str,
            name: str = "pantheon-memory",
            worker_params: dict | None = None,
            ):
        self.memory_manager = MemoryManager()
        self.memory_dir = Path(memory_dir)
        self.memory_manager.load(self.memory_dir)
        _worker_params = {
            "service_name": name,
            "server_host": DEFAULT_SERVER_HOST,
            "server_port": DEFAULT_SERVER_PORT,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.setup_handlers()

    async def new_memory(self, name: str | None = None) -> str:
        memory = self.memory_manager.new_memory(name)
        return memory.name

    async def get_messages(self, memory_name: str) -> list[dict]:
        memory = self.memory_manager.get_memory(memory_name)
        return memory.get_messages()

    async def add_messages(self, memory_name: str, messages: list[dict]):
        memory = self.memory_manager.get_memory(memory_name)
        memory.add_messages(messages)
        self.memory_manager.save(self.memory_dir)

    async def save(self):
        self.memory_manager.save(self.memory_dir)

    def setup_handlers(self):
        self.worker.register(self.new_memory)
        self.worker.register(self.get_messages)
        self.worker.register(self.add_messages)
        self.worker.register(self.save)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        logger.info(f"Remote Server: {self.worker.server_uri}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.worker.service_id}")
        return await self.worker.run()


class RemoteMemory:
    def __init__(self, service, name: str):
        self.service = service
        self.name = name

    async def get_messages(self) -> list[dict]:
        return await self.service.invoke("get_messages", {"memory_name": self.name})

    async def add_messages(self, messages: list[dict]):
        await self.service.invoke("add_messages", {"memory_name": self.name, "messages": messages})


class RemoteMemoryManager:
    def __init__(
            self,
            service_id_or_name: str,
            server_host: str = DEFAULT_SERVER_HOST,
            server_port: int = DEFAULT_SERVER_PORT,
            ):
        self.service_id_or_name = service_id_or_name
        self.server_host = server_host
        self.server_port = server_port
        self.service = None

    async def connect(self):
        if self.service is None:
            self.service = await connect_remote(
                self.service_id_or_name,
                self.server_host,
                self.server_port,
                )

    async def new_memory(self, name: str | None = None) -> RemoteMemory:
        await self.connect()
        memory_name = await self.service.invoke("new_memory", {"name": name})
        return RemoteMemory(self.service, memory_name)

    async def save(self):
        await self.connect()
        await self.service.invoke("save", {})


async def start_memory_service(memory_dir: str, name: str = "pantheon-memory", log_level: str = "INFO"):
    service = MemoryManagerService(memory_dir, name)
    await service.run(log_level)


if __name__ == "__main__":
    import fire
    fire.Fire(start_memory_service)
