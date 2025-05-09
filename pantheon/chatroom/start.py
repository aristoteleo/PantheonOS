import asyncio
from pathlib import Path
from typing import Callable, Awaitable

from .room import ChatRoom
from ..agent import Agent
from ..team import Team
from ..memory import MemoryManager

from magique.ai import connect_remote


async def default_agents_factory(endpoint) -> dict:
    assistant_agent = Agent(
        name="Assistant",
        instructions="You are a helpful assistant that can answer questions and help with tasks.",
        model="gpt-4.1",
        icon="🤖",
    )
    s = await endpoint.invoke("get_service", {"service_id_or_name": "python_interpreter"})
    if s is None:
        raise ValueError("Python interpreter service not found")
    await assistant_agent.remote_toolset(s["id"])

    s = await endpoint.invoke("get_service", {"service_id_or_name": "file_manager"})
    if s is None:
        raise ValueError("File manager service not found")
    await assistant_agent.remote_toolset(s["id"])

    web_search_agent = Agent(
        name="Web search",
        instructions="You are a web search agent that can search the web for information.",
        model="gpt-4.1",
        icon="🔍",
    )

    s = await endpoint.invoke("get_service", {"service_id_or_name": "web_browse"})
    if s is None:
        raise ValueError("Web browser service not found")
    await web_search_agent.remote_toolset(s["id"])

    return {
        "triage": assistant_agent,
        "other": [web_search_agent],
    }


async def start_services(
    service_name: str = "pantheon-chatroom",
    memory_path: str = "./.pantheon-chatroom",
    endpoint_service_id: str | None = None,
    workspace_path: str = "./.pantheon-chatroom-workspace",
    agents_factory: Callable[[], Awaitable[list[Agent | Team]]] = default_agents_factory,
    log_level: str = "INFO",
):
    if endpoint_service_id is None:
        from magique.ai.endpoint import Endpoint
        w_path = Path(workspace_path)
        w_path.mkdir(parents=True, exist_ok=True)
        endpoint = Endpoint(workspace_path=workspace_path)
        asyncio.create_task(endpoint.run(log_level=log_level))
        await asyncio.sleep(1.0)
        endpoint_service_id = endpoint.worker.service_id

    endpoint = await connect_remote(endpoint_service_id)

    agents = await agents_factory(endpoint)
    memory_manager = MemoryManager(memory_path)

    chat_room = ChatRoom(
        triage_agent=agents["triage"],
        agents=agents["other"],
        endpoint_service_id=endpoint_service_id,
        memory_manager=memory_manager,
        name=service_name,
    )
    await chat_room.run(log_level=log_level)
