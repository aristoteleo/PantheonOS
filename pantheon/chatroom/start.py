import asyncio
from pathlib import Path
from typing import Callable, Awaitable

from .room import ChatRoom
from ..memory import MemoryManager
from .factory import default_agents_factory

from magique.ai import connect_remote


async def start_services(
    service_name: str = "pantheon-chatroom",
    memory_path: str = "./.pantheon-chatroom",
    endpoint_service_id: str | None = None,
    workspace_path: str = "./.pantheon-chatroom-workspace",
    agents_factory: Callable[[], Awaitable[dict]] = default_agents_factory,
    log_level: str = "INFO",
    endpoint_wait_time: int = 5,
    worker_params: dict | None = None,
    worker_params_endpoint: dict | None = None,
    endpoint_connect_params: dict | None = None,
):
    if endpoint_service_id is None:
        from magique.ai.endpoint import Endpoint
        w_path = Path(workspace_path)
        w_path.mkdir(parents=True, exist_ok=True)
        endpoint = Endpoint(
            workspace_path=workspace_path,
            config={"log_level": log_level},
            worker_params=worker_params_endpoint,
        )
        asyncio.create_task(endpoint.run())
        endpoint_service_id = endpoint.worker.service_id
        await asyncio.sleep(endpoint_wait_time)

    endpoint_connect_params = endpoint_connect_params or {}
    endpoint = await connect_remote(endpoint_service_id, **endpoint_connect_params)

    memory_manager = MemoryManager(memory_path)

    chat_room = ChatRoom(
        endpoint_service_id=endpoint_service_id,
        agent_factory=agents_factory,
        memory_manager=memory_manager,
        name=service_name,
        worker_params=worker_params,
        endpoint_connect_params=endpoint_connect_params,
    )
    await chat_room.run(log_level=log_level)
