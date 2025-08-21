import asyncio
import io
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

import openai
import yaml

from pantheon.toolsets.remote import (
    SERVER_URLS,
    RemoteBackendFactory,
    RemoteConfig,
    RemoteWorker,
)

from ..agent import Agent, RemoteAgent
from ..factory import DEFAULT_AGENTS_TEMPLATE_PATH, create_agents_from_template
from ..memory import MemoryManager
from ..team import PantheonTeam
from ..utils.log import logger
from ..utils.misc import run_func
from .thread import Thread


class ChatRoom:
    """
    ChatRoom is a service that allow user to interact with a team of agents.
    It can connect to a remote service to get the agents and tools,
    and be connected with Pantheon-UI to provide a user-friendly interface.

    A chatroom contains a series of chats, which are identified by a chat_id.
    Each chats will be associated with a memory, which is a file in the memory_dir.

    Args:
        endpoint_service_id: The service ID of the endpoint service.
        agents_template: The template of the agents.
        memory_dir: The directory to store the memory.
        name: The name of the chatroom.
        description: The description of the chatroom.
        worker_params: The parameters for the worker.
        server_url: The URL of the server.
        remote_service_params: The parameters for the remote service connection.
        speech_to_text_model: The model to use for speech to text.
        check_before_chat: The function to check before chat.
        get_db_info: The function to get the database info.
    """

    def __init__(
        self,
        endpoint_service_id: str,
        agents_template: dict | str | None = None,
        memory_dir: str = "./.pantheon-chatroom",
        name: str = "pantheon-chatroom",
        description: str = "Chatroom for Pantheon agents",
        worker_params: dict | None = None,
        server_url: str | list[str] | None = None,
        backend: str | None = None,
        remote_service_params: dict | None = None,
        speech_to_text_model: str = "gpt-4o-mini-transcribe",
        check_before_chat: Callable | None = None,
        get_db_info: Callable | None = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.memory_manager = MemoryManager(self.memory_dir)

        if agents_template is None:
            if (self.memory_dir / "agents_template.yaml").exists():
                with open(self.memory_dir / "agents_template.yaml", "r") as f:
                    self.agents_template = yaml.safe_load(f)
            else:
                with open(DEFAULT_AGENTS_TEMPLATE_PATH, "r") as f:
                    self.agents_template = yaml.safe_load(f)
                self.save_agents_template()
        elif isinstance(agents_template, str):
            with open(agents_template, "r") as f:
                self.agents_template = yaml.safe_load(f)
            if not (self.memory_dir / "agents_template.yaml").exists():
                self.save_agents_template()
        else:
            self.agents_template = agents_template

        self.endpoint_service_id = endpoint_service_id
        self.name = name
        self.description = description
        if isinstance(server_url, str):
            server_urls = [server_url]
        elif server_url is None:
            server_urls = SERVER_URLS
        else:
            server_urls = server_url
        self.server_urls = server_urls

        # Store worker params for later initialization in setup_agents
        self._worker_params = {
            "service_name": name,
        }
        if worker_params is not None:
            self._worker_params.update(worker_params)

        self.remote_service_params = remote_service_params or {}

        # Properly structure backend config to avoid parameter conflicts
        backend_config = {"server_urls": self.server_urls}
        if self.remote_service_params:
            backend_config.update(self.remote_service_params)

        self.backend = RemoteBackendFactory.create_backend(
            RemoteConfig.from_config(
                backend=backend,
                backend_config=backend_config,
            )
        )
        self.worker = self.backend.create_worker(**self._worker_params)
        self.setup_handlers()
        self.speech_to_text_model = speech_to_text_model
        self.threads: dict[str, Thread] = {}
        self.check_before_chat = check_before_chat
        self.get_db_info = get_db_info

    async def setup_agents(self):
        """Setup the agents from the template and initialize the worker.
        The template is a dictionary with the following keys:
        - triage: The triage agent.
        - other: The other agents.
        """
        endpoint_service = await self.backend.connect(self.endpoint_service_id)
        agents = await create_agents_from_template(
            endpoint_service, self.agents_template
        )
        triage_agent = agents["triage"]
        agents = agents["other"]
        self.team = PantheonTeam(
            triage=triage_agent,
            agents=agents,
        )
        await self.team.async_setup()

    def save_agents_template(self):
        """Save the agents template to the memory_dir."""
        with open(self.memory_dir / "agents_template.yaml", "w") as f:
            yaml.dump(self.agents_template, f)

    def setup_handlers(self):
        """Setup the handlers for the worker.
        To expose the chatroom interfaces to the Pantheon-UI.
        """
        self.worker.register(self.create_chat)
        self.worker.register(self.delete_chat)
        self.worker.register(self.chat)
        self.worker.register(self.stop_chat)
        self.worker.register(self.list_chats)
        self.worker.register(self.get_chat_messages)
        self.worker.register(self.update_chat_name)
        self.worker.register(self.get_remote_service)
        self.worker.register(self.set_remote_service)
        self.worker.register(self.get_agents)
        self.worker.register(self.set_active_agent)
        self.worker.register(self.get_active_agent)
        self.worker.register(self.attach_hooks)
        self.worker.register(self.speech_to_text)
        self.worker.register(self.get_db_info)

    async def get_db_info(self) -> dict:
        """Get the database info."""
        if self.get_db_info is not None:
            return {
                "success": True,
                "info": await self.get_db_info(),
            }
        return {"success": False, "message": "Not implemented"}

    async def get_remote_service(self) -> dict:
        """Get the remote service info."""
        try:
            s = await self.backend.connect(self.endpoint_service_id)
            info = await s.fetch_service_info()
            return {
                "success": True,
                "service_name": info.service_name if info else self.endpoint_service_id,
                "service_id": info.service_id if info else self.endpoint_service_id,
            }
        except Exception as e:
            logger.error(f"Error getting remote service info: {e}")
            return {"success": False, "message": str(e)}

    async def set_remote_service(self, remote_service_id: str) -> dict:
        """Set the remote service ID.

        Args:
            remote_service_id: The service ID of the remote service.
        """
        try:
            self.endpoint_service_id = remote_service_id
            await self.setup_agents()
            return {"success": True, "message": "Remote service set successfully"}
        except Exception as e:
            logger.error(f"Error setting remote service: {e}")
            return {"success": False, "message": str(e)}

    async def get_agents(self) -> dict:
        """Get the agents info.

        Returns:
            A dictionary with the following keys:
            - success: Whether the operation was successful.
            - agents: A list of dictionaries, each containing the info of an agent.
        """

        def get_agent_info(agent: Agent | RemoteAgent):
            if hasattr(agent, "not_loaded_toolsets"):
                not_loaded_toolsets = agent.not_loaded_toolsets
            else:
                not_loaded_toolsets = []
            return {
                "name": agent.name,
                "instructions": agent.instructions,
                "toolful": getattr(agent, "toolful", False),
                "tools": [t for t in agent.functions.keys()],
                "toolsets": [
                    {
                        "id": s.service_info.service_id,
                        "name": s.service_info.service_name,
                    }
                    for s in agent.toolset_services.values()
                ],
                "icon": agent.icon,
                "not_loaded_toolsets": not_loaded_toolsets,
            }

        return {
            "success": True,
            "agents": [get_agent_info(a) for a in self.team.agents.values()],
        }

    async def set_active_agent(self, chat_name: str, agent_name: str):
        """Set the active agent for a chat.

        Args:
            chat_name: The name of the chat.
            agent_name: The name of the agent.
        """
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        agent = next(
            (a for a in self.team.agents.values() if a.name == agent_name), None
        )
        if agent is None:
            return {"success": False, "message": "Agent not found"}
        self.team.set_active_agent(memory, agent_name)
        return {"success": True, "message": "Agent set as active"}

    async def get_active_agent(self, chat_name: str) -> dict:
        """Get the active agent for a chat.

        Args:
            chat_name: The name of the chat.
        """
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        active_agent = self.team.get_active_agent(memory)
        return {
            "success": True,
            "agent": active_agent.name,
        }

    async def create_chat(self, chat_name: str | None = None) -> dict:
        """Create a new chat.

        Args:
            chat_name: The name of the chat.
        """
        memory = await run_func(self.memory_manager.new_memory, chat_name)
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()
        return {
            "success": True,
            "message": "Chat created successfully",
            "chat_name": memory.name,
            "chat_id": memory.id,
        }

    async def delete_chat(self, chat_id: str):
        """Delete a chat.

        Args:
            chat_id: The ID of the chat.
        """
        try:
            await run_func(self.memory_manager.delete_memory, chat_id)
            await run_func(self.memory_manager.save)
            return {"success": True, "message": "Chat deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting chat: {e}")
            return {"success": False, "message": str(e)}

    async def list_chats(self) -> dict:
        """List all the chats.

        Returns:
            A dictionary with the following keys:
            - success: Whether the operation was successful.
            - chats: A list of dictionaries, each containing the info of a chat.
        """
        try:
            ids = await run_func(self.memory_manager.list_memories)
            chats = []
            for id in ids:
                memory = await run_func(self.memory_manager.get_memory, id)
                chats.append(
                    {
                        "id": id,
                        "name": memory.name,
                        "running": memory.extra_data.get("running", False),
                        "last_activity_date": memory.extra_data.get(
                            "last_activity_date", None
                        ),
                    }
                )

            chats.sort(
                key=lambda x: datetime.fromisoformat(x["last_activity_date"])
                if x["last_activity_date"]
                else datetime.min,
                reverse=True,
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
        """Get the messages of a chat.

        Args:
            chat_id: The ID of the chat.
            filter_out_images: Whether to filter out the images.
        """
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
                            for _k in [
                                "stdout",
                                "stderr",
                            ]:  # truncate large stdout/stderr outputs
                                MAX_LENGTH = 10000
                                if _k in message["raw_content"]:
                                    message["raw_content"][_k] = message["raw_content"][
                                        _k
                                    ][:MAX_LENGTH]
                    new_messages.append(message)
                messages = new_messages
            return {"success": True, "messages": messages}
        except Exception as e:
            logger.error(f"Error getting chat messages: {e}")
            return {"success": False, "message": str(e)}

    async def update_chat_name(self, chat_id: str, chat_name: str):
        """Update the name of a chat.

        Args:
            chat_id: The ID of the chat.
            chat_name: The new name of the chat.
        """
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
        self,
        chat_id: str,
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        wait: bool = True,
        time_delta: float = 0.1,
    ):
        """Attach hooks to a chat. Hooks are used to process the messages of the chat.

        Args:
            chat_id: The ID of the chat.
            process_chunk: The function to process the chunk.
            process_step_message: The function to process the step message.
            wait: Whether to wait for the thread to end.
            time_delta: The time delta to wait for the thread to end.
        """
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
        """Start a chat, send a message to the chat.

        Args:
            chat_id: The ID of the chat.
            message: The messages to send to the chat.
            process_chunk: The function to process the chunk.
            process_step_message: The function to process the step message.
        """
        if self.check_before_chat is not None:
            try:
                await self.check_before_chat(chat_id, message)
            except Exception as e:
                logger.error(f"Error in check_before_chat: {e}")
                return {"success": False, "message": str(e)}

        if chat_id in self.threads:
            return {"success": False, "message": "Chat is already running"}
        memory = await run_func(self.memory_manager.get_memory, chat_id)
        memory.extra_data["running"] = True
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()

        thread = Thread(self.team, memory, message)
        self.threads[chat_id] = thread
        await self.attach_hooks(
            chat_id, process_chunk, process_step_message, wait=False
        )
        await thread.run()

        memory.extra_data["running"] = False
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()
        await run_func(self.memory_manager.save)
        del self.threads[chat_id]
        return thread.response

    async def stop_chat(self, chat_id: str):
        """Stop a chat.

        Args:
            chat_id: The ID of the chat.
        """
        thread = self.threads.get(chat_id, None)
        if thread is None:
            return {"success": False, "message": "Chat doesn't have a thread"}
        await thread.stop()
        return {"success": True, "message": "Chat stopped successfully"}

    async def speech_to_text(self, bytes_data: bytes):
        """Convert speech to text.

        Args:
            bytes_data: The bytes data of the audio.
        """
        try:
            client = openai.OpenAI()

            # Try different audio formats until one works
            formats = ["webm", "mp4", "wav", "mp3"]
            last_error = None

            for fmt in formats:
                try:
                    # Create a BytesIO object with a proper filename for format detection
                    audio_file = io.BytesIO(bytes_data)
                    audio_file.name = f"audio.{fmt}"

                    response = client.audio.transcriptions.create(
                        model=self.speech_to_text_model,
                        file=audio_file,
                    )

                    return {
                        "success": True,
                        "text": response.text,
                    }
                except Exception as format_error:
                    last_error = format_error
                    logger.debug(f"Failed with format {fmt}: {format_error}")
                    continue

            # If all formats failed, raise the last error
            if last_error:
                raise last_error
            else:
                raise Exception("No audio formats worked")

        except Exception as e:
            logger.error(f"Error transcribing speech: {e}")
            return {
                "success": False,
                "text": str(e),
            }

    async def run(self, log_level: str = "INFO"):
        """Run the chatroom service.

        Args:
            log_level: The level of the log.
        """
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level=log_level)
        await self.setup_agents()
        logger.info(f"Remote Servers: {self.worker.servers}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.worker.service_id}")
        return await self.worker.run()
