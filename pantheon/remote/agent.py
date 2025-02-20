import sys

from magique.worker import MagiqueWorker

from ..agent import Agent
from ..types import AgentInput
from .constant import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
from .utils import connect_remote


class AgentService:
    def __init__(
            self,
            agent: Agent,
            worker_params: dict | None = None,
            ):
        self.agent = agent
        _worker_params = {
            "service_name": "remote_agent_" + self.agent.name,
            "server_host": DEFAULT_SERVER_HOST,
            "server_port": DEFAULT_SERVER_PORT,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.setup_worker()

    async def response(self, msg, **kwargs):
        resp = await self.agent.run(msg, **kwargs)
        return resp

    def setup_worker(self):
        self.worker.register(self.response)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        logger.info(f"Remote Server: {self.worker.server_uri}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.worker.service_id}")
        return await self.worker.run()


class RemoteAgent:
    def __init__(
            self,
            service_id_or_name: str,
            server_host: str = DEFAULT_SERVER_HOST,
            server_port: int = DEFAULT_SERVER_PORT,
            ):
        self.service_id_or_name = service_id_or_name
        self.server_host = server_host
        self.server_port = server_port

    async def run(self, msg: AgentInput, **kwargs):
        s = await connect_remote(
            self.service_id_or_name,
            self.server_host,
            self.server_port,
        )
        return await s.invoke("response", {"msg": msg, **kwargs})

