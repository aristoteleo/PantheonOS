import sys

from magique.worker import MagiqueWorker
from magique.ai.constant import DEFAULT_SERVER_URL
from magique.ai.utils.remote import connect_remote

from ..memory import MemoryManager


class MemoryManagerService:
    def __init__(
            self,
            memory_dir: str,
            name: str = "pantheon-memory",
            worker_params: dict | None = None,
            server_url: str = DEFAULT_SERVER_URL,
            ):
        self.memory_manager = MemoryManager(memory_dir)
        self.server_url = server_url
        _worker_params = {
            "service_name": name,
            "server_url": server_url,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.setup_handlers()

    async def new_memory(self, name: str | None = None) -> dict:
        memory = self.memory_manager.new_memory(name)
        return {"id": memory.id, "name": memory.name}

    async def get_memory(self, id: str) -> dict:
        memory = self.memory_manager.get_memory(id)
        return {"id": memory.id, "name": memory.name}

    async def delete_memory(self, id: str):
        self.memory_manager.delete_memory(id)
        await self.save()

    async def get_messages(self, memory_id: str) -> list[dict]:
        memory = self.memory_manager.get_memory(memory_id)
        return memory.get_messages()

    async def add_messages(self, memory_id: str, messages: list[dict]):
        memory = self.memory_manager.get_memory(memory_id)
        memory.add_messages(messages)
        await self.save()

    async def update_memory_name(self, memory_id: str, name: str):
        memory = self.memory_manager.get_memory(memory_id)
        memory.name = name
        await self.save()

    async def list_memories(self):
        return self.memory_manager.list_memories()

    async def save(self):
        self.memory_manager.save()

    def setup_handlers(self):
        self.worker.register(self.new_memory)
        self.worker.register(self.get_messages)
        self.worker.register(self.add_messages)
        self.worker.register(self.save)
        self.worker.register(self.list_memories)
        self.worker.register(self.update_memory_name)
        self.worker.register(self.get_memory)
        self.worker.register(self.delete_memory)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        logger.info(f"Remote Server: {self.worker.server_url}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.worker.service_id}")
        return await self.worker.run()


class RemoteMemory:
    def __init__(
            self,
            service,
            id: str,
            name: str,
            ):
        self.service = service
        self.id = id
        self.name = name

    async def get_messages(self) -> list[dict]:
        return await self.service.invoke("get_messages", {"memory_id": self.id})

    async def add_messages(self, messages: list[dict]):
        await self.service.invoke(
            "add_messages",
            {"memory_id": self.id, "messages": messages},
        )


class RemoteMemoryManager:
    def __init__(
            self,
            service_id_or_name: str,
            server_url: str = DEFAULT_SERVER_URL,
            ):
        self.service_id_or_name = service_id_or_name
        self.server_url = server_url
        self.service = None

    async def connect(self):
        if self.service is None:
            self.service = await connect_remote(
                self.service_id_or_name,
                self.server_url,
                )

    async def new_memory(self, name: str | None = None) -> RemoteMemory:
        await self.connect()
        assert self.service is not None
        memory_info = await self.service.invoke("new_memory", {"name": name})
        return RemoteMemory(self.service, memory_info["id"], memory_info["name"])

    async def get_memory(self, id: str) -> RemoteMemory:
        await self.connect()
        assert self.service is not None
        memory_info = await self.service.invoke("get_memory", {"id": id})
        return RemoteMemory(self.service, memory_info["id"], memory_info["name"])

    async def delete_memory(self, id: str):
        await self.connect()
        assert self.service is not None
        await self.service.invoke("delete_memory", {"id": id})

    async def list_memories(self):
        await self.connect()
        assert self.service is not None
        return await self.service.invoke("list_memories", {})

    async def save(self):
        await self.connect()
        assert self.service is not None
        await self.service.invoke("save", {})

    async def update_memory_name(self, memory_id: str, name: str):
        await self.connect()
        assert self.service is not None
        await self.service.invoke(
            "update_memory_name",
            {"memory_id": memory_id, "name": name},
        )


async def start_memory_service(
        memory_dir: str,
        name: str = "pantheon-memory",
        log_level: str = "INFO",
        ):
    service = MemoryManagerService(memory_dir, name)
    await service.run(log_level)


if __name__ == "__main__":
    import fire
    fire.Fire(start_memory_service)
