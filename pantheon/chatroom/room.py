import sys
import uuid
import asyncio
from datetime import datetime
from typing import Callable

from magique.worker import MagiqueWorker
from magique.ai import connect_remote
from magique.ai.constant import SERVER_URLS

from ..agent import Agent
from ..team import SwarmCenterTeam
from ..memory import MemoryManager
from ..remote.memory import RemoteMemoryManager
from ..remote.agent import RemoteAgent
from ..utils.misc import run_func
from ..utils.log import logger
from .thread import Thread
from .factory import default_agents_factory


class ChatRoom:
    def __init__(
        self,
        endpoint_service_id: str,
        agent_factory: Callable = default_agents_factory,
        memory_manager: MemoryManager | RemoteMemoryManager | None = None,
        default_memory_dir: str = "./.pantheon-chatroom",
        name: str = "pantheon-chatroom",
        description: str = "Chatroom for Pantheon agents",
        worker_params: dict | None = None,
        server_url: str | list[str] | None = None,
        endpoint_connect_params: dict | None = None,
    ):
        self.agent_factory = agent_factory
        self.endpoint_service_id = endpoint_service_id
        if memory_manager is None:
            memory_manager = MemoryManager(default_memory_dir)
        self.memory_manager = memory_manager
        self.name = name
        self.description = description
        if isinstance(server_url, str):
            server_urls = [server_url]
        elif server_url is None:
            server_urls = SERVER_URLS
        else:
            server_urls = server_url
        self.server_urls = server_urls
        _worker_params = {
            "service_name": name,
            "server_url": server_urls,
            "need_auth": False,
        }
        if worker_params is not None:
            _worker_params.update(worker_params)
        self.worker = MagiqueWorker(**_worker_params)
        self.setup_handlers()
        self.endpoint_connect_params = endpoint_connect_params or {}
        self.threads: dict[str, Thread] = {}

    async def setup_agents(self):
        endpoint = await connect_remote(
            self.endpoint_service_id,
            self.server_urls,
            **self.endpoint_connect_params,
        )
        agents = await self.agent_factory(endpoint)
        triage_agent = agents["triage"]
        agents = agents["other"]
        self.team = SwarmCenterTeam(
            triage=triage_agent,
            agents=agents,
        )
        await self.team.async_setup()

    def setup_handlers(self):
        self.worker.register(self.create_chat)
        self.worker.register(self.delete_chat)
        self.worker.register(self.chat)
        self.worker.register(self.list_chats)
        self.worker.register(self.get_chat_messages)
        self.worker.register(self.update_chat_name)
        self.worker.register(self.get_endpoint)
        self.worker.register(self.set_endpoint)
        self.worker.register(self.get_agents)
        self.worker.register(self.set_active_agent)
        self.worker.register(self.get_active_agent)
        self.worker.register(self.attach_hooks)

    async def get_endpoint(self) -> dict:
        s = await connect_remote(
            self.endpoint_service_id,
            self.server_urls,
            **self.endpoint_connect_params,
        )
        info = await s.fetch_service_info()
        return {
            "success": True,
            "service_name": info.service_name,
            "service_id": info.service_id,
        }

    async def set_endpoint(self, endpoint_service_id: str) -> dict:
        try:
            await connect_remote(
                endpoint_service_id,
                self.server_urls,
                **self.endpoint_connect_params,
            )
            self.endpoint_service_id = endpoint_service_id
            await self.setup_agents()
            return {"success": True, "message": "Endpoint set successfully"}
        except Exception as e:
            logger.error(f"Error setting endpoint: {e}")
            return {"success": False, "message": str(e)}

    async def get_agents(self) -> dict:
        def get_agent_info(agent: Agent | RemoteAgent):
            return {
                "name": agent.name,
                "instructions": agent.instructions,
                "tools": [t for t in agent.functions.keys()],
                "toolsets": [
                    {
                        'id': s.service_info.service_id,
                        'name': s.service_info.service_name,
                    } for s in agent.toolset_proxies.values()
                ],
                "icon": agent.icon,
            }
        return {
            "success": True,
            "agents": [get_agent_info(a) for a in self.team.agents.values()],
        }

    async def set_active_agent(self, chat_name: str, agent_name: str):
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        agent = next((a for a in self.team.agents.values() if a.name == agent_name), None)
        if agent is None:
            return {"success": False, "message": "Agent not found"}
        self.team.set_active_agent(memory, agent_name)
        return {"success": True, "message": "Agent set as active"}

    async def get_active_agent(self, chat_name: str) -> dict:
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        active_agent = self.team.get_active_agent(memory)
        return {
            "success": True,
            "agent": active_agent.name,
        }

    async def create_chat(self, chat_name: str | None = None) -> dict:
        memory = await run_func(self.memory_manager.new_memory, chat_name)
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()
        return {
            "success": True,
            "message": "Chat created successfully",
            "chat_name": memory.name,
            "chat_id": memory.id,
        }

    async def delete_chat(self, chat_id: str):
        try:
            await run_func(self.memory_manager.delete_memory, chat_id)
            await run_func(self.memory_manager.save)
            return {"success": True, "message": "Chat deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting chat: {e}")
            return {"success": False, "message": str(e)}

    async def list_chats(self) -> dict:
        try:
            ids = await run_func(self.memory_manager.list_memories)
            chats = []
            for id in ids:
                memory = await run_func(self.memory_manager.get_memory, id)
                chats.append({
                    "id": id,
                    "name": memory.name,
                    "running": memory.extra_data.get("running", False),
                    "last_activity_date": memory.extra_data.get("last_activity_date", None),
                })

            chats.sort(
                key=lambda x: datetime.fromisoformat(x["last_activity_date"]) 
                              if x["last_activity_date"] 
                              else datetime.min,
                reverse=True
            )

            return {
                "success": True,
                "chats": chats,
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Error listing chats: {e}")
            return {"success": False, "message": str(e)}

    async def get_chat_messages(self, chat_id: str, filter_out_images: bool = False):
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            messages = await run_func(memory.get_messages)
            if filter_out_images:
                new_messages = []
                for message in messages:
                    if "raw_content" in message:
                        if isinstance(message["raw_content"], dict):
                            if "base64_uri" in message["raw_content"]:
                                del message["raw_content"]["base64_uri"]
                    new_messages.append(message)
                messages = new_messages
            return {"success": True, "messages": messages}
        except Exception as e:
            logger.error(f"Error getting chat messages: {e}")
            return {"success": False, "message": str(e)}

    async def update_chat_name(self, chat_id: str, chat_name: str):
        try:
            await run_func(
                self.memory_manager.update_memory_name,
                chat_id,
                chat_name,
                )
            return {
                "success": True,
                "message": "Chat name updated successfully",
            }
        except Exception as e:
            logger.error(f"Error updating chat name: {e}")
            return {
                "success": False,
                "message": str(e),
                }

    async def attach_hooks(
            self, chat_id: str,
            process_chunk: Callable | None = None,
            process_step_message: Callable | None = None,
            wait: bool = True,
            time_delta: int = 0.1,
            ):
        thread = self.threads.get(chat_id, None)
        if thread is None:
            return {"success": False, "message": "Chat doesn't have a thread"}
        if process_chunk is not None:
            thread.add_chunk_hook(process_chunk)
        if process_step_message is not None:
            thread.add_step_message_hook(process_step_message)
        while wait:  # wait for thread end, for keep hooks alive
            if chat_id not in self.threads:
                break
            await asyncio.sleep(time_delta)
        return {"success": True, "message": "Hooks attached successfully"}

    async def chat(
        self,
        chat_id: str,
        message: list[dict],
        process_chunk=None,
        process_step_message=None,
    ):
        if chat_id in self.threads:
            return {"success": False, "message": "Chat is already running"}
        memory = await run_func(self.memory_manager.get_memory, chat_id)
        memory.extra_data["running"] = True
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()

        thread = Thread(self.team, memory, message)
        self.threads[chat_id] = thread
        await self.attach_hooks(chat_id, process_chunk, process_step_message, wait=False)
        await thread.run()

        memory.extra_data["running"] = False
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()
        await run_func(self.memory_manager.save)
        del self.threads[chat_id]
        return thread.response

    async def run(self, log_level: str = "INFO"):
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=log_level)
        logger.info(f"Remote Servers: {self.worker.servers}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.worker.service_id}")
        await self.setup_agents()
        return await self.worker.run()
