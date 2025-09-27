import asyncio
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

import openai

from pantheon.remote import (
    RemoteBackendFactory,
    RemoteConfig,
)
from pantheon.remote.backend.base import RemoteBackend

from ..agent import Agent, RemoteAgent
from ..factory import create_agents_from_template
from ..factory.template_manager import get_template_manager, ChatroomTemplate
from ..memory import MemoryManager
from ..team import PantheonTeam
from ..utils.log import logger
from ..utils.misc import run_func
from .thread import Thread
from .suggestion_generator import get_centralized_suggestion_manager


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
        endpoint_connect_params: The parameters for the endpoint service connection.
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
        endpoint_connect_params: dict | None = None,
        speech_to_text_model: str = "gpt-4o-mini-transcribe",
        check_before_chat: Callable | None = None,
        get_db_info: Callable | None = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.memory_manager = MemoryManager(self.memory_dir)

        # Initialize template manager - no more direct agents_template handling
        self.template_manager = get_template_manager()

        self.endpoint_service_id = endpoint_service_id
        self.name = name
        self.description = description
        if isinstance(server_url, str):
            server_url = [server_url]
        self.server_urls = server_url

        # Per-chat team management
        self.default_team: PantheonTeam = None  # Will be initialized in setup_agents
        self.chat_teams: dict[str, PantheonTeam] = {}  # Per-chat teams cache

        # Store worker params for later initialization in setup_agents
        self._worker_params = {
            "service_name": name,
        }
        if worker_params is not None:
            self._worker_params.update(worker_params)

        self.endpoint_connect_params = endpoint_connect_params or {}

        # Properly structure backend config to avoid parameter conflicts
        backend_config = {"server_urls": self.server_urls} if self.server_urls else {}
        if self.endpoint_connect_params:
            backend_config.update(self.endpoint_connect_params)

        self.backend = RemoteBackendFactory.create_backend(
            RemoteConfig.from_config(
                backend=backend,
                backend_config=backend_config,
            )
        )
        self.worker = self.backend.create_worker(**self._worker_params)

        # Check if backend supports streaming (only NATS backend supports streaming)
        self.supports_streaming = hasattr(self.backend, "get_or_create_stream")
        if self.supports_streaming:
            logger.info(f"ChatRoom: Streaming support enabled for {backend} backend")
        else:
            logger.info(f"ChatRoom: Streaming not supported for {backend} backend")

        self.setup_handlers()
        self.speech_to_text_model = speech_to_text_model
        self.threads: dict[str, Thread] = {}
        self.check_before_chat = check_before_chat
        self._get_db_info_func = get_db_info

    async def setup_agents(self):
        """Setup the agents from the template and initialize the worker.
        The template is a dictionary with the following keys:
        - triage: The triage agent.
        - other: The other agents.
        """
        endpoint_service = await self.backend.connect(self.endpoint_service_id)

        # Get default template from template_manager
        default_template = self.template_manager.get_template("default")
        if not default_template:
            raise RuntimeError("Default template not found in template manager")

        agents = await create_agents_from_template(
            endpoint_service, default_template.agents_config
        )
        triage_agent = agents["triage"]
        agents = agents["other"]
        for agent in [triage_agent, *agents]:
            agent.enable_rich_conversations()
        # Create default team for compatibility and fallback
        self.default_team = PantheonTeam(
            triage=triage_agent,
            agents=agents,
        )
        await self.default_team.async_setup()

        # Keep self.team for backward compatibility with existing code
        self.team = self.default_team

    # Removed: save_agents_template - using template_manager only

    async def get_team_for_chat(self, chat_id: str) -> PantheonTeam:
        """Get the team for a specific chat, creating from memory if needed."""
        # FIX for performance, history chat will get team even not needed.
        # 1. Check if team already exists in cache
        if chat_id in self.chat_teams:
            return self.chat_teams[chat_id]

        # 2. Try to load team from persistent memory
        team = await self._load_team_from_memory(chat_id)
        self.chat_teams[chat_id] = team  # Cache it

        return team

    async def _load_team_from_memory(self, chat_id: str) -> PantheonTeam:
        """Load team from chat's persistent memory."""
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)

            # Check for stored team template
            if hasattr(memory, "extra_data") and memory.extra_data:
                team_template = memory.extra_data.get("team_template")
                if team_template:
                    return await self._create_team_from_template(team_template)

        except Exception as e:
            logger.warning(f"⚠️ Failed to load team from memory for chat {chat_id}: {e}")

        logger.info(f"Load default team for chat {chat_id}")

        return self.default_team

    async def _create_team_from_template(self, team_template: dict) -> PantheonTeam:
        """Create a team from a stored template configuration."""
        template_name = team_template.get("template_name", "unknown")

        agents_config = team_template.get("agents_config", {})
        logger.info(
            f"🏗️ Creating team from template '{template_name}' with {len(agents_config)} agents"
        )

        # Connect to endpoint service
        endpoint_service = await self.backend.connect(
            self.endpoint_service_id, **self.endpoint_connect_params
        )

        # Create agents from template
        agents = await create_agents_from_template(endpoint_service, agents_config)
        triage_agent = agents["triage"]
        other_agents = agents["other"]

        logger.info(
            f"📋 Created agents: triage='{triage_agent.name}', others=[{', '.join(a.name for a in other_agents)}]"
        )

        # Enable rich conversations for all agents
        for agent in [triage_agent, *other_agents]:
            agent.enable_rich_conversations()

        # Create and setup team
        team = PantheonTeam(triage=triage_agent, agents=other_agents)
        await team.async_setup()

        total_agents = len(team.agents)  # Use actual team.agents count
        logger.info(
            f"✅ Successfully created team from template '{template_name}' with {total_agents} agents"
        )

        return team

    async def setup_team_for_chat(self, chat_id: str, template_obj: dict):
        """Setup/update team for a specific chat using full template object."""
        try:
            logger.info(
                f"Setting up team for chat {chat_id} with template: {template_obj.get('name', 'unknown')}"
            )

            # Store full template in memory
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            if not hasattr(memory, "extra_data"):
                memory.extra_data = {}

            # Store complete template configuration
            memory.extra_data["team_template"] = {
                "template_id": template_obj.get("id", "custom"),
                "template_name": template_obj.get("name", "Custom Template"),
                "agents_config": template_obj.get("agents_config", {}),
                "required_toolsets": template_obj.get("required_toolsets", []),
                "created_at": datetime.now().isoformat(),
            }
            if "active_agent" in memory.extra_data:
                del memory.extra_data["active_agent"]
            await run_func(self.memory_manager.save)

            # Clear cached team (force recreation next time)
            if chat_id in self.chat_teams:
                del self.chat_teams[chat_id]

            # Start missing toolsets in background (non-blocking)
            required_toolsets = template_obj.get("required_toolsets", [])
            if required_toolsets:
                logger.info(
                    f"Template requires toolsets: {required_toolsets}. Starting them in background..."
                )
                asyncio.create_task(self._start_toolsets_background(required_toolsets))

            return {
                "success": True,
                "message": f"Team template '{template_obj.get('name', 'Custom')}' prepared for chat",
                "template": template_obj,
                "chat_id": chat_id,
            }

        except Exception as e:
            return {"success": False, "message": f"Template setup failed: {str(e)}"}

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
        self.worker.register(self.get_endpoint)
        self.worker.register(self.set_endpoint)
        self.worker.register(self.get_toolsets)
        self.worker.register(self.proxy_toolset)
        self.worker.register(self.get_agents)
        self.worker.register(self.set_active_agent)
        self.worker.register(self.get_active_agent)
        self.worker.register(self.attach_hooks)
        self.worker.register(self.speech_to_text)
        self.worker.register(self.get_db_info)
        # Register suggestion methods
        self.worker.register(self.get_suggestions)
        self.worker.register(self.refresh_suggestions)
        # Register template methods
        self.worker.register(self.list_templates)
        self.worker.register(self.setup_team_for_chat)
        self.worker.register(self.get_chat_template)
        self.worker.register(self.validate_template)
        # Register chat context management methods
        self.worker.register(self.get_chat_context)
        self.worker.register(self.update_chat_context)
        self.worker.register(self.manage_notebook_session)

    async def get_db_info(self) -> dict:
        """Get the database info."""
        if hasattr(self, "_get_db_info_func") and self._get_db_info_func is not None:
            return {
                "success": True,
                "info": await self._get_db_info_func(),
            }
        return {"success": False, "message": "Not implemented"}

    async def _publish_chunk(self, chat_id: str, chunk: dict):
        """Publish a chunk to stream.

        Args:
            chat_id: The chat ID
            chunk: The chunk data to publish
        """
        if not self.supports_streaming:
            return

        try:
            import time
            publish_start = time.time()

            from pantheon.remote.backend.base import StreamType, StreamMessage

            message = StreamMessage(
                type=StreamType.CHAT,
                session_id=f"chat_{chat_id}",
                timestamp=time.time(),  # Use actual timestamp for ordering
                data={"type": "chunk", "chunk": chunk, "chat_id": chat_id},
            )
            # Get or create stream channel (simplified with new API)
            stream_channel = await self.backend.get_or_create_stream(
                f"chat_{chat_id}", StreamType.CHAT
            )
            await stream_channel.publish(message)

            publish_time = time.time() - publish_start
            chunk_type = chunk.get("begin", chunk.get("stop", "content"))

            if chunk.get("begin"):
                logger.info(f"📤 [ChatRoom] Published BEGIN chunk to stream: {publish_time:.3f}s")
            elif chunk.get("stop"):
                logger.info(f"📤 [ChatRoom] Published STOP chunk to stream: {publish_time:.3f}s")
            elif chunk.get("content"):
                content_preview = chunk.get("content", "")[:20]
                logger.debug(f"📤 [ChatRoom] Published content chunk: {publish_time:.3f}s ('{content_preview}...')")
            else:
                logger.debug(f"📤 [ChatRoom] Published chunk: {publish_time:.3f}s")

        except Exception as e:
            logger.error(
                f"ChatRoom: Failed to publish chunk to NATS for chat {chat_id}: {e}"
            )

    async def _publish_step_message(self, chat_id: str, step_message: dict):
        """Publish a step message to stream.

        Args:
            chat_id: The chat ID
            step_message: The step message data to publish
        """
        if not self.supports_streaming:
            return

        try:
            from pantheon.remote.backend.base import StreamType, StreamMessage

            message = StreamMessage(
                type=StreamType.CHAT,
                session_id=f"chat_{chat_id}",
                timestamp=0,  # Will be set automatically
                data={
                    "type": "step_message",
                    "step_message": step_message,
                    "chat_id": chat_id,
                },
            )
            # Get or create stream channel (simplified with new API)
            stream_channel = await self.backend.get_or_create_stream(
                f"chat_{chat_id}", StreamType.CHAT
            )
            await stream_channel.publish(message)
            logger.info(f"ChatRoom: Published step message to NATS for chat {chat_id}")
        except Exception as e:
            logger.error(
                f"ChatRoom: Failed to publish step message to NATS for chat {chat_id}: {e}"
            )

    async def get_endpoint(self) -> dict:
        """Get the endpoint service info."""
        try:
            s = await self.backend.connect(
                self.endpoint_service_id, **self.endpoint_connect_params
            )
            info = await s.fetch_service_info()
            return {
                "success": True,
                "service_name": info.service_name if info else self.endpoint_service_id,
                "service_id": info.service_id if info else self.endpoint_service_id,
            }
        except Exception as e:
            logger.error(f"Error getting remote service info: {e}")
            return {"success": False, "message": str(e)}

    async def set_endpoint(self, endpoint_service_id: str) -> dict:
        """Set the endpoint service ID.

        Args:
            endpoint_service_id: The service ID of the endpoint service.
        """
        try:
            self.endpoint_service_id = endpoint_service_id
            await self.setup_agents()
            return {"success": True, "message": "Endpoint service set successfully"}
        except Exception as e:
            logger.error(f"Error setting endpoint service: {e}")
            return {"success": False, "message": str(e)}

    async def get_toolsets(self) -> dict:
        """Get all available toolsets from the endpoint service.

        Returns:
            A dictionary with the following keys:
            - success: Whether the operation was successful.
            - services: A list of available toolset services.
            - error: Error message if operation failed.
        """
        try:
            s = await self.backend.connect(
                self.endpoint_service_id, **self.endpoint_connect_params
            )
            result = await s.invoke("list_services")
            if isinstance(result, dict) and "success" in result:
                return result
            else:
                # If result is directly the services list
                return {"success": True, "services": result}
        except Exception as e:
            logger.error(f"Error getting toolsets: {e}")
            return {"success": False, "error": str(e)}

    async def proxy_toolset(
        self,
        method_name: str,
        args: dict | None = None,
        toolset_name: str | None = None,
    ) -> dict:
        """Proxy call to any toolset method in the endpoint service or specific toolset.

        Args:
            method_name: The name of the toolset method to call.
            args: Arguments to pass to the method.
            toolset_name: The name of the specific toolset to call. If None, calls endpoint directly.

        Returns:
            The result from the toolset method call.
        """
        # DONT support reverse callback proxy!
        try:
            endpoint = await self.backend.connect(
                self.endpoint_service_id, **self.endpoint_connect_params
            )

            # Add debug logging
            logger.info(
                f"chatroom proxy_toolset: method_name={method_name}, toolset_name={toolset_name}, args={args}"
            )

            # Use endpoint's proxy_toolset method for unified handling
            result = await endpoint.invoke(
                "proxy_toolset",
                {
                    "method_name": method_name,
                    "args": args or {},
                    "toolset_name": toolset_name,
                },
            )

            return result

        except Exception as e:
            logger.error(
                f"Error calling toolset method {method_name} on {toolset_name or 'endpoint'}: {e}"
            )
            return {"success": False, "error": str(e)}

    async def get_agents(self, chat_id: str = None) -> dict:
        """Get the agents info for a specific chat or default team.

        Args:
            chat_id: The chat ID to get agents for. If None, uses default team.

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

        # Get the appropriate team for this chat
        if chat_id:
            team = await self.get_team_for_chat(chat_id)
        else:
            team = self.default_team

        return {
            "success": True,
            "agents": [get_agent_info(a) for a in team.agents.values()],
        }

    async def set_active_agent(self, chat_name: str, agent_name: str):
        """Set the active agent for a chat.

        Args:
            chat_name: The name of the chat.
            agent_name: The name of the agent.
        """
        # Get the team for this specific chat
        team = await self.get_team_for_chat(chat_name)
        memory = await run_func(self.memory_manager.get_memory, chat_name)

        agent = next((a for a in team.agents.values() if a.name == agent_name), None)
        if agent is None:
            return {"success": False, "message": "Agent not found"}
        team.set_active_agent(memory, agent_name)
        return {"success": True, "message": "Agent set as active"}

    async def get_active_agent(self, chat_name: str) -> dict:
        """Get the active agent for a chat.

        Args:
            chat_name: The name of the chat.
        """
        # Get the team for this specific chat
        team = await self.get_team_for_chat(chat_name)
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        active_agent = team.get_active_agent(memory)
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

        logger.error(f"Received message: {chat_id}|{message}")

        if chat_id in self.threads:
            return {"success": False, "message": "Chat is already running"}
        memory = await run_func(self.memory_manager.get_memory, chat_id)
        memory.extra_data["running"] = True
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()

        async def team_getter():
            return await self.get_team_for_chat(chat_id)

        thread = Thread(
            team_getter,  # Pass team getter
            memory,
            message,
        )
        self.threads[chat_id] = thread

        # Always attach notebook detection hook for automatic session management
        thread.add_step_message_hook(self._process_notebook_detection_hook)
        # Always add streaming support if backend supports it
        if self.supports_streaming:

            async def nats_chunk_processor(chunk: dict):
                await self._publish_chunk(chat_id, chunk)

            async def nats_step_processor(step_message: dict):
                await self._publish_step_message(chat_id, step_message)

            thread.add_chunk_hook(nats_chunk_processor)
            thread.add_step_message_hook(nats_step_processor)

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

    async def get_suggestions(self, chat_id: str) -> dict:
        """Get suggestion questions for a chat."""
        return await self._handle_suggestions(chat_id, force_refresh=False)

    async def refresh_suggestions(self, chat_id: str) -> dict:
        """Refresh suggestion questions for a chat."""
        return await self._handle_suggestions(chat_id, force_refresh=True)

    async def _handle_suggestions(
        self, chat_id: str, force_refresh: bool = False
    ) -> dict:
        """Common suggestion handling logic using centralized suggestion generator."""
        try:
            # Get chat memory directly from memory manager
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            messages = memory.get_messages()

            if len(messages) < 2:
                return {
                    "success": False,
                    "message": "Not enough messages to generate suggestions",
                }

            # Check cache (unless forcing refresh)
            if not force_refresh:
                cached = memory.extra_data.get("cached_suggestions", [])
                last_suggestion_message_count = memory.extra_data.get(
                    "last_suggestion_message_count", 0
                )

                # Use cached suggestions if still valid
                if cached and len(messages) <= last_suggestion_message_count:
                    return {
                        "success": True,
                        "suggestions": cached,
                        "chat_id": chat_id,
                        "from_cache": True,
                    }

            # Convert messages to the format expected by suggestion generator
            formatted_messages = []
            for msg in messages:
                if hasattr(msg, "to_dict"):
                    formatted_messages.append(msg.to_dict())
                elif isinstance(msg, dict):
                    formatted_messages.append(msg)
                else:
                    # Handle other message formats
                    formatted_messages.append(
                        {
                            "role": getattr(msg, "role", "unknown"),
                            "content": getattr(msg, "content", str(msg)),
                        }
                    )

            # Use centralized suggestion generator
            suggestion_generator = get_centralized_suggestion_manager()
            suggestions_objects = await suggestion_generator.generate_suggestions(
                formatted_messages
            )

            # Convert to dict format
            suggestions = [
                {"text": s.text, "category": s.category} for s in suggestions_objects
            ]

            # Cache suggestions in memory
            if suggestions:
                memory.extra_data["cached_suggestions"] = suggestions
                memory.extra_data["last_suggestion_message_count"] = len(messages)
                memory.extra_data["suggestions_generated_at"] = (
                    datetime.now().isoformat()
                )
                await run_func(self.memory_manager.save)

            logger.info(f"Generated {len(suggestions)} suggestions for chat {chat_id}")

            return {
                "success": True,
                "suggestions": suggestions,
                "chat_id": chat_id,
                "from_cache": False,
            }

        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Error handling suggestions for chat {chat_id}: {str(e)}")
            return {"success": False, "message": str(e)}

    # Template Management Methods

    async def list_templates(self) -> dict:
        """List all available chatroom templates."""
        try:
            template_manager = get_template_manager()
            templates = template_manager.list_templates()

            return {
                "success": True,
                "templates": [template.to_dict() for template in templates],
            }
        except Exception as e:
            logger.error(f"Error listing templates: {e}")
            return {"success": False, "message": str(e)}

    async def get_chat_template(self, chat_id: str) -> dict:
        """Get the current template for a specific chat."""
        memory = await run_func(self.memory_manager.get_memory, chat_id)

        # Check if chat has a stored template
        if hasattr(memory, "extra_data") and memory.extra_data:
            team_template = memory.extra_data.get("team_template")
            if team_template:
                # Return the stored template information
                return {
                    "success": True,
                    "template": {
                        "id": team_template.get("template_id", "custom"),
                        "name": team_template.get("template_name", "Custom Template"),
                        "agents_config": team_template.get("agents_config", {}),
                        "required_toolsets": team_template.get("required_toolsets", []),
                        "created_at": team_template.get("created_at"),
                        "partial_setup": team_template.get("partial_setup", False),
                    },
                }

        # No template found, return default template info
        template_manager = get_template_manager()
        default_template = template_manager.get_template("default")
        if default_template:
            return {
                "success": True,
                "template": default_template.to_dict(),
                "is_default": True,
            }

        # Fallback if no default template found
        return {
            "success": False,
            "message": "No template found and no default template available",
        }

    async def validate_template(self, template: dict) -> dict:
        """Validate if a template is compatible with current endpoint."""
        try:
            # Convert dict to ChatroomTemplate object
            from ..factory.template_manager import ChatroomTemplate

            template_obj = ChatroomTemplate(
                id=template.get("id", ""),
                name=template.get("name", ""),
                description=template.get("description", ""),
                icon=template.get("icon", ""),
                category=template.get("category", ""),
                version=template.get("version", "1.0"),
                agents_config=template.get("agents_config", {}),
                required_toolsets=template.get("required_toolsets", []),
                tags=template.get("tags", []),
            )

            # Validate template structure
            template_manager = get_template_manager()
            validation_errors = template_manager.validate_template(template_obj)
            if validation_errors:
                return {
                    "success": False,
                    "message": "Template validation failed",
                    "validation_errors": validation_errors,
                }

            # Check toolset availability

            s = await self.backend.connect(
                self.endpoint_service_id, **self.endpoint_connect_params
            )
            available_toolsets_resp = await s.invoke("list_services")

            if (
                isinstance(available_toolsets_resp, dict)
                and "success" in available_toolsets_resp
            ):
                if available_toolsets_resp["success"]:
                    available_services = available_toolsets_resp.get("services", [])
                    available_toolsets = [
                        svc.get("name", svc.get("id", "")) for svc in available_services
                    ]
                else:
                    available_toolsets = []
            else:
                available_toolsets = (
                    available_toolsets_resp
                    if isinstance(available_toolsets_resp, list)
                    else []
                )

            missing_toolsets = []
            for required_toolset in template_obj.required_toolsets:
                if required_toolset not in available_toolsets:
                    missing_toolsets.append(required_toolset)

            return {
                "success": True,
                "compatible": len(missing_toolsets) == 0,
                "required_toolsets": template_obj.required_toolsets,
                "available_toolsets": available_toolsets,
                "missing_toolsets": missing_toolsets,
                "template": template_obj.to_dict(),
            }

        except Exception as e:
            logger.error(f"Error validating template compatibility: {e}")
            return {"success": False, "message": str(e)}

    async def _start_toolsets_background(self, missing_toolsets: list[str]):
        """Start missing toolsets in background without blocking."""

        logger.info(f"🚀 Background task: Starting toolsets {missing_toolsets}")

        s = await self.backend.connect(
            self.endpoint_service_id, **self.endpoint_connect_params
        )
        result = await s.invoke(
            "ensure_toolsets", {"required_toolsets": missing_toolsets}
        )

        if result.get("success", False):
            started = result.get("starting_toolsets", [])
            still_missing = result.get("missing_toolsets", [])

            if started:
                logger.info(f"✅ Successfully started toolsets: {', '.join(started)}")
            if still_missing:
                logger.warning(
                    f"⚠️ Could not start toolsets: {', '.join(still_missing)} (they may not exist or have errors)"
                )
        else:
            error_msg = result.get("error", "Unknown error")
            logger.warning(f"⚠️ Toolset startup failed: {error_msg}")

    async def run(self, log_level: str = "INFO"):
        """Run the chatroom service.

        Args:
            log_level: The level of the log.
        """
        logger.set_level(log_level)
        logger.info(f"Chat Room setup: endpoint_id {self.endpoint_service_id}")
        await self.setup_agents()
        logger.info(
            f"Remote Servers: {self.worker.servers} Service Name: {self.worker.service_name} Service ID: {self.worker.service_id}"
        )

        return await self.worker.run()

    async def get_chat_context(self, chat_id: str) -> dict:
        """Get chat context information including notebook sessions and other metadata.

        Args:
            chat_id: The chat ID to get context for.

        Returns:
            A dictionary containing the chat context information.
        """
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            if not hasattr(memory, "extra_data"):
                memory.extra_data = {}

            context = memory.extra_data.get("chat_context", {})
            return {"success": True, "chat_id": chat_id, "context": context}
        except Exception as e:
            logger.error(f"Error getting chat context for {chat_id}: {e}")
            return {"success": False, "error": str(e)}

    async def update_chat_context(self, chat_id: str, context_data: dict) -> dict:
        """Update chat context information.

        Args:
            chat_id: The chat ID to update context for.
            context_data: The context data to update.

        Returns:
            A dictionary with success status and updated context.
        """
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            if not hasattr(memory, "extra_data"):
                memory.extra_data = {}

            if "chat_context" not in memory.extra_data:
                memory.extra_data["chat_context"] = {}

            # Merge new context data
            memory.extra_data["chat_context"].update(context_data)
            memory.extra_data["last_context_update"] = datetime.now().isoformat()

            await run_func(self.memory_manager.save)
            return {
                "success": True,
                "chat_id": chat_id,
                "updated_context": memory.extra_data["chat_context"],
            }
        except Exception as e:
            logger.error(f"Error updating chat context for {chat_id}: {e}")
            return {"success": False, "error": str(e)}

    def _extract_session_id_from_step_message(self, step_message: dict) -> str | None:
        """Extract session_id from step message content (matches frontend logic)"""
        try:
            # Step messages can contain tool responses
            if step_message.get("role") == "tool":
                content = step_message.get("content", {})

                # Handle string content (try to parse as JSON)
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, ValueError):
                        # If not JSON, check for session_id pattern in string
                        session_id_match = re.search(
                            r'session_id[\'\":][\s]*[\'\""]?([^\'\"",\s]+)[\'\""]?',
                            content,
                        )
                        if session_id_match:
                            return session_id_match.group(1)
                        return None

                # Extract session_id from different possible field names
                return (
                    content.get("session_id")
                    or content.get("sessionId")
                    or content.get("id")
                    or content.get("result", {}).get("session_id")
                    or content.get("data", {}).get("session_id")
                )
        except Exception as e:
            logger.warning(f"Error extracting session_id from step message: {e}")

        return None

    async def _process_notebook_detection_hook(self, step_message: dict):
        """Hook to detect and auto-store notebook sessions from step messages"""
        try:
            chat_id = step_message.get("chat_id")
            if not chat_id:
                return

            # Check if this is a tool response for notebook session creation
            if step_message.get("role") == "tool":
                tool_call_id = step_message.get("tool_call_id")

                # We need to find the corresponding tool call to check the function name
                # For now, try to extract session_id and assume it's notebook-related if found
                session_id = self._extract_session_id_from_step_message(step_message)

                if session_id:
                    # Auto-store the detected session
                    await self.manage_notebook_session(
                        chat_id=chat_id,
                        action="add",
                        session_data={
                            "session_id": session_id,
                            "kernel_spec": "python3",  # Default, could be extracted from args
                            "created_by": "agent",
                            "tool_call_id": tool_call_id,
                            "detected_at": datetime.now().isoformat(),
                        },
                    )
                    logger.info(
                        f"Auto-detected and stored notebook session {session_id} for chat {chat_id}"
                    )

        except Exception as e:
            logger.error(f"Error in notebook detection hook: {e}")

    async def manage_notebook_session(
        self, chat_id: str, action: str, session_data: dict | None = None
    ) -> dict:
        """Manage notebook sessions for a specific chat.

        Args:
            chat_id: The chat ID to manage sessions for.
            action: The action to perform ('add', 'remove', 'update', 'list').
            session_data: The session data for add/update/remove operations.

        Returns:
            A dictionary with success status and session information.
        """
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            if not hasattr(memory, "extra_data"):
                memory.extra_data = {}

            if "chat_context" not in memory.extra_data:
                memory.extra_data["chat_context"] = {}

            if "notebook_sessions" not in memory.extra_data["chat_context"]:
                memory.extra_data["chat_context"]["notebook_sessions"] = []

            sessions = memory.extra_data["chat_context"]["notebook_sessions"]

            if action == "add":
                if session_data:
                    new_session = {
                        **session_data,
                        "created_at": datetime.now().isoformat(),
                        "status": "active",
                    }
                    sessions.append(new_session)
                    logger.info(
                        f"Added notebook session {session_data.get('session_id')} to chat {chat_id}"
                    )

            elif action == "remove":
                if session_data and "session_id" in session_data:
                    sessions_before = len(sessions)
                    sessions = [
                        s
                        for s in sessions
                        if s.get("session_id") != session_data["session_id"]
                    ]
                    memory.extra_data["chat_context"]["notebook_sessions"] = sessions
                    sessions_removed = sessions_before - len(sessions)
                    logger.info(
                        f"Removed {sessions_removed} notebook session(s) from chat {chat_id}"
                    )

            elif action == "update":
                if session_data and "session_id" in session_data:
                    for session in sessions:
                        if session.get("session_id") == session_data["session_id"]:
                            session.update(session_data)
                            session["updated_at"] = datetime.now().isoformat()
                            logger.info(
                                f"Updated notebook session {session_data['session_id']} in chat {chat_id}"
                            )
                            break

            elif action == "list":
                # Enhance session data with real-time information from toolsets
                try:
                    # Try to get live session information from integrated_notebook toolset
                    live_sessions = []
                    try:
                        # Call integrated_notebook toolset to get current active sessions
                        notebook_result = await self.proxy_toolset(
                            method_name="list_notebook_sessions",
                            toolset_name="integrated_notebook"
                        )
                        if notebook_result and notebook_result.get("success"):
                            live_sessions = notebook_result.get("sessions", [])
                    except Exception as e:
                        logger.warning(f"Failed to get live notebook sessions: {e}")

                    # Merge stored metadata with live session data
                    enhanced_sessions = []
                    for stored_session in sessions:
                        session_id = stored_session.get("session_id")

                        # Find corresponding live session
                        live_session = None
                        for live in live_sessions:
                            if live.get("session_id") == session_id:
                                live_session = live
                                break

                        # Merge data: stored metadata + live status
                        enhanced_session = {
                            **stored_session,  # Basic metadata from chat memory
                        }

                        if live_session:
                            # Add live information
                            enhanced_session.update({
                                "notebook_path": live_session.get("notebook_path"),
                                "notebook_title": live_session.get("notebook_title"),
                                "kernel_status": live_session.get("kernel_status"),
                                "execution_count": live_session.get("execution_count"),
                                "is_active": True
                            })
                        else:
                            # Session exists in memory but not in toolset (may be dead)
                            enhanced_session.update({
                                "kernel_status": "dead",
                                "is_active": False
                            })

                        enhanced_sessions.append(enhanced_session)

                    # Also add any live sessions not in stored memory
                    stored_session_ids = {s.get("session_id") for s in sessions}
                    for live in live_sessions:
                        live_session_id = live.get("session_id")
                        if live_session_id not in stored_session_ids:
                            # This is a session created directly via toolset, add it
                            enhanced_sessions.append({
                                "session_id": live_session_id,
                                "notebook_path": live.get("notebook_path"),
                                "notebook_title": live.get("notebook_title"),
                                "kernel_status": live.get("kernel_status"),
                                "execution_count": live.get("execution_count"),
                                "created_by": "user",  # Default, could be enhanced
                                "status": "active",
                                "is_active": True,
                                "created_at": datetime.now().isoformat(),
                            })

                    # Update sessions to return enhanced data
                    sessions = enhanced_sessions

                except Exception as e:
                    logger.warning(f"Failed to enhance session data with live information: {e}")
                    # Fallback to stored sessions only
                    pass
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}. Supported actions: add, remove, update, list",
                }

            await run_func(self.memory_manager.save)
            return {
                "success": True,
                "action": action,
                "chat_id": chat_id,
                "sessions": sessions,
            }

        except Exception as e:
            logger.error(f"Error managing notebook session for chat {chat_id}: {e}")
            return {"success": False, "error": str(e)}
