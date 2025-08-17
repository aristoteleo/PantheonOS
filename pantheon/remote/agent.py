import asyncio
import sys

# Use TYPE_CHECKING to avoid circular imports
from typing import TYPE_CHECKING, Callable, Optional

from .backend.base import RemoteWorker
from .config import RemoteConfig
from .factory import RemoteBackendFactory

if TYPE_CHECKING:
    from ..agent import Agent, AgentInput
else:
    # For runtime, we'll import these when needed
    Agent = None
    AgentInput = None


class AgentService:
    def __init__(
        self,
        agent: "Agent",
        backend_config: Optional[RemoteConfig] = None,
        worker_params: dict | None = None,
    ):
        self.agent = agent
        self.backend_config = backend_config or RemoteConfig.from_env()
        self.backend = RemoteBackendFactory.create_backend(self.backend_config)

        # Merge worker parameters
        default_params = {"service_name": f"remote_agent_{self.agent.name}"}
        if worker_params:
            default_params.update(worker_params)
        self.worker_params = default_params

        self.worker: RemoteWorker = None

    async def response(self, msg, **kwargs):
        resp = await self.agent.run(msg, **kwargs)
        return resp

    async def get_info(self):
        return {
            "name": self.agent.name,
            "instructions": self.agent.instructions,
            "models": self.agent.models,
            "functions_names": list(self.agent.functions.keys()),
            "toolset_proxies_names": list(self.agent.toolset_services.keys()),
        }

    async def get_message_queue(self):
        return await self.agent.events_queue.get()

    async def check_message_queue(self):
        # check if there is a message in the queue
        return not self.agent.events_queue.empty()

    async def add_tool(self, func: Callable):
        self.agent.tool(func)
        return {"success": True}

    async def _ensure_worker(self):
        """Lazy initialization of worker"""
        if self.worker is None:
            self.worker = await self.backend.create_worker(**self.worker_params)
            self.setup_worker()

    def setup_worker(self):
        """Register methods with the worker"""
        self.worker.register(self.response)
        self.worker.register(self.get_info)
        self.worker.register(self.get_message_queue)
        self.worker.register(self.check_message_queue)
        self.worker.register(self.add_tool)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level=log_level)

        await self._ensure_worker()

        logger.info(f"Remote Backend: {self.backend_config.backend}")
        logger.info(f"Service Name: {self.worker_params['service_name']}")
        if hasattr(self.worker, "service_id"):
            logger.info(f"Service ID: {self.worker.service_id}")
        if hasattr(self.worker, "servers"):
            logger.info(f"Remote Servers: {self.worker.servers}")

        return await self.worker.run()


class RemoteAgent:
    def __init__(
        self,
        service_id_or_name: str,
        backend_config: Optional[RemoteConfig] = None,
        **remote_kwargs,
    ):
        self.service_id_or_name = service_id_or_name
        self.backend_config = backend_config or RemoteConfig.from_env()
        self.backend = RemoteBackendFactory.create_backend(self.backend_config)
        self.remote_kwargs = remote_kwargs
        self.name = None
        self.instructions = None
        self.model = None
        self.events_queue = RemoteAgentMessageQueue(self)

    async def _connect(self):
        return await self.backend.connect(self.service_id_or_name, **self.remote_kwargs)

    async def fetch_info(self):
        service = await self._connect()
        info = await service.invoke("get_info")
        self.name = info["name"]
        self.instructions = info["instructions"]
        self.models = info["models"]
        self.functions_names = info["functions_names"]
        self.toolset_proxies_names = info["toolset_proxies_names"]
        await service.close()
        return info

    async def run(self, msg: "AgentInput", **kwargs):
        await self.fetch_info()
        service = await self._connect()
        try:
            return await service.invoke("response", {"msg": msg, **kwargs})
        finally:
            await service.close()

    async def tool(self, func: Callable):
        service = await self._connect()
        try:
            # Convert function for backend compatibility
            if self.backend_config.backend == "magique":
                from magique.client import PyFunction

                func_arg = {"func": PyFunction(func)}
            else:
                func_arg = {"func": func}
            await service.invoke("add_tool", func_arg)
        finally:
            await service.close()

    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        await self.fetch_info()
        from ..repl.core import Repl

        repl = Repl(self)
        await repl.run(message)


class RemoteAgentMessageQueue:
    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    async def get(self, interval: float = 0.2):
        service = await self.agent._connect()
        try:
            while True:
                res = await service.invoke("check_message_queue")
                if res:
                    return await service.invoke("get_message_queue")
                await asyncio.sleep(interval)
        finally:
            await service.close()
