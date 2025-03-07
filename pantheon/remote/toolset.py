from typing import Callable
from functools import partial
import inspect
import sys
from abc import ABC
from contextlib import asynccontextmanager

from executor.engine import Engine, ProcessJob
from magique.worker import MagiqueWorker

from ..utils.log import logger
from .constant import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT


def tool(func: Callable | None = None, **kwargs):
    """Mark tool in a ToolSet class
    
    Args:
        job_type: "local", "thread" or "process"
            Different job types will be executed in different ways.
            Default "local"
    """
    if func is None:
        return partial(tool, **kwargs)
    func._is_tool = True
    func._tool_params = kwargs
    return func


class ToolSet(ABC):
    def __init__(
            self, name: str,
            worker_params: dict | None = None
            ):
        _worker_params = {
            "service_name": name,
            "server_host": DEFAULT_SERVER_HOST,
            "server_port": DEFAULT_SERVER_PORT,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.setup_tools()

    def setup_tools(self):
        methods = inspect.getmembers(self, inspect.ismethod)
        for _, method in methods:
            if hasattr(method, "_is_tool"):
                _kwargs = getattr(method, "_tool_params", {})
                self.worker.register(method, **_kwargs)

    @property
    def service_id(self):
        return self.worker.service_id

    async def run_setup(self):
        """Setup the toolset before running it."""
        pass

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        await self.run_setup()
        logger.info(f"Remote Server: {self.worker.server_uri}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.service_id}")
        return await self.worker.run()


async def _run_toolset(toolset: ToolSet, log_level: str = "WARNING"):
    await toolset.run(log_level)


@asynccontextmanager
async def run_toolsets(
        toolsets: list[ToolSet],
        engine: Engine | None = None,
        log_level: str = "WARNING",
        ):
    logger.remove()
    logger.add(sys.stderr, level=log_level)
    if engine is None:
        engine = Engine()
    jobs = []
    for toolset in toolsets:
        job = ProcessJob(
            _run_toolset,
            args=(toolset, log_level),
        )
        jobs.append(job)
    await engine.submit_async(*jobs)
    for job in jobs:
        await job.wait_until_status("running")
    yield
    for job in jobs:
        await job.cancel()
    await engine.wait_async()
    engine.stop()


def toolset_cli(toolset_type: type[ToolSet], default_service_name: str):
    import fire

    async def main(service_name: str = default_service_name, **kwargs):
        toolset = toolset_type(service_name, **kwargs)
        await toolset.run()

    fire.Fire(main)
