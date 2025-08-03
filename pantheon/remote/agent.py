import sys
import asyncio
from typing import Callable

from magique.worker import MagiqueWorker
from magique.client import PyFunction
from pantheon.toolsets.utils.remote import connect_remote
from pantheon.toolsets.utils.constant import SERVER_URLS

from ..agent import Agent, AgentInput


class AgentService:
    def __init__(
            self,
            agent: Agent,
            worker_params: dict | None = None,
            ):
        self.agent = agent
        _worker_params = {
            "service_name": "remote_agent_" + self.agent.name,
            "server_url": SERVER_URLS,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.setup_worker()

    async def response(self, msg, **kwargs):
        resp = await self.agent.run(msg, **kwargs)
        return resp

    async def get_info(self):
        return {
            "name": self.agent.name,
            "instructions": self.agent.instructions,
            "models": self.agent.models,
            "functions_names": list(self.agent.functions.keys()),
            "toolset_proxies_names": list(self.agent.toolset_proxies.keys()),
        }

    async def get_message_queue(self):
        return await self.agent.events_queue.get()

    async def check_message_queue(self):
        # check if there is a message in the queue
        return not self.agent.events_queue.empty()

    async def add_tool(self, func: Callable):
        self.agent.tool(func)
        return {"success": True}

    def setup_worker(self):
        self.worker.register(self.response)
        self.worker.register(self.get_info)
        self.worker.register(self.get_message_queue)
        self.worker.register(self.check_message_queue)
        self.worker.register(self.add_tool)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        logger.info(f"Remote Server: {self.worker.servers}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.worker.service_id}")
        return await self.worker.run()


class RemoteAgent:
    def __init__(
            self,
            service_id_or_name: str,
            server_url: str | list[str] | None = None,
            **remote_kwargs,
            ):
        self.service_id_or_name = service_id_or_name
        if isinstance(server_url, str):
            server_urls = [server_url]
        elif server_url is None:
            server_urls = SERVER_URLS
        else:
            server_urls = server_url
        self.server_urls = server_urls
        self.remote_kwargs = remote_kwargs
        self.name = None
        self.instructions = None
        self.model = None
        self.events_queue = RemoteAgentMessageQueue(self)

    async def _connect(self):
        return await connect_remote(
            self.service_id_or_name,
            self.server_urls,
            **self.remote_kwargs,
        )

    async def fetch_info(self):
        s = await self._connect()
        info = await s.invoke("get_info")
        self.name = info["name"]
        self.instructions = info["instructions"]
        self.models = info["models"]
        self.functions_names = info["functions_names"]
        self.toolset_proxies_names = info["toolset_proxies_names"]
        return info

    async def run(self, msg: AgentInput, **kwargs):
        await self.fetch_info()
        s = await self._connect()
        return await s.invoke("response", {"msg": msg, **kwargs})

    async def tool(self, func: Callable):
        s = await self._connect()
        await s.invoke("add_tool", {"func": PyFunction(func)})

    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        await self.fetch_info()
        from ..repl.single import Repl
        repl = Repl(self)
        await repl.run(message)


class RemoteAgentMessageQueue:
    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    async def get(self, interval: float = 0.2):
        s = await self.agent._connect()
        while True:
            res = await s.invoke("check_message_queue")
            if res:
                return await s.invoke("get_message_queue")
            await asyncio.sleep(interval)
