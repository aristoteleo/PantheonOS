import asyncio
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

import openai

from ..agent import Agent
from ..factory import create_agents_from_template
from ..factory.template_manager import get_template_manager
from ..memory import MemoryManager
from ..team import PantheonTeam
from ..toolset import ToolSet, tool
from ..toolsets import PlanModeToolSet
from ..utils.log import logger
from ..utils.misc import run_func
from .special_agents import get_suggestion_generator
from .thread import Thread


class ChatRoom(ToolSet):
    """
    ChatRoom is a service that allow user to interact with a team of agents.
    It can connect to a remote service to get the agents and tools,
    and be connected with Pantheon-UI to provide a user-friendly interface.

    A chatroom contains a series of chats, which are identified by a chat_id.
    Each chats will be associated with a memory, which is a file in the memory_dir.

    Args:
        endpoint_service_id: The service ID of the endpoint service.
        memory_dir: The directory to store the memory.
        name: The name of the chatroom.
        description: The description of the chatroom.
        speech_to_text_model: The model to use for speech to text.
        check_before_chat: The function to check before chat.
        **kwargs: Additional parameters passed to ToolSet (e.g., id_hash).
    """

    def __init__(
        self,
        endpoint: "Endpoint | None" = None,
        endpoint_service_id: str | None = None,
        memory_dir: str = "./.pantheon-chatroom",
        name: str = "pantheon-chatroom",
        description: str = "Chatroom for Pantheon agents",
        speech_to_text_model: str = "gpt-4o-mini-transcribe",
        check_before_chat: Callable | None = None,
        agents_template: dict | str | None = None,
        **kwargs,
    ):
        # Initialize ToolSet (will handle worker creation in run())
        super().__init__(name=name, **kwargs)

        # Determine endpoint connection mode
        if endpoint is not None:
            # Embed mode: directly hold Endpoint instance
            self._endpoint_embed = True
            self._endpoint = endpoint
            self.endpoint_service_id = None
        elif endpoint_service_id is not None:
            # Process mode: hold endpoint service_id, connect lazily
            self._endpoint_embed = False
            self._endpoint = None
            self.endpoint_service_id = endpoint_service_id
        else:
            raise ValueError("Must provide either 'endpoint' or 'endpoint_service_id'")

        self._endpoint_service = None
        self._backend = None

        # ChatRoom self startup mode: will be set in run_setup() based on remote parameter
        # True: ChatRoom itself is started as a remote service (streams needed)
        # False: ChatRoom itself is embedded (no need for stream publish)
        self._embed = True  # Default to True, will be updated by run_setup()

        # ChatRoom specific initialization
        self.memory_dir = Path(memory_dir)
        self.memory_manager = MemoryManager(self.memory_dir)

        # Initialize template manager (supports old and new formats, manages agents.yaml library)
        self.template_manager = get_template_manager()

        self.description = description

        # Per-chat team management
        self.default_team: PantheonTeam = None  # Will be initialized in run_setup
        self.chat_teams: dict[str, PantheonTeam] = {}  # Per-chat teams cache

        self.speech_to_text_model = speech_to_text_model
        self.agents_template = agents_template
        self.threads: dict[str, Thread] = {}
        self.check_before_chat = check_before_chat

    async def _get_endpoint_service(self):
        """Get endpoint service object (instance or RemoteService)."""
        if self._endpoint_embed:
            # Embed mode: directly return instance
            return self._endpoint
        else:
            # Process mode: lazy connect to remote service
            if self._endpoint_service is None:
                from pantheon.remote import RemoteBackendFactory

                self._backend = RemoteBackendFactory.create_backend()
                self._endpoint_service = await self._backend.connect(
                    self.endpoint_service_id
                )
            return self._endpoint_service

    async def _call_endpoint_method(self, endpoint_method_name: str, **kwargs):
        from pantheon.utils.misc import call_endpoint_method

        endpoint_service = await self._get_endpoint_service()
        return await call_endpoint_method(
            endpoint_service, endpoint_method_name=endpoint_method_name, **kwargs
        )

    async def _publish_stream(self, chat_id: str, message_type: str, data: dict):
        """
        Unified method to publish stream messages.

        Args:
            chat_id: Chat ID
            message_type: Type of message ("chunk" or "step")
            data: Message data
        """
        # Embed mode: ChatRoom is embedded (not started as remote service), no need to send stream
        if self._embed:
            logger.debug(f"Embed mode: skip stream publish for {message_type}")
            return

        # Remote mode: ChatRoom is started as a remote service, need to send stream
        if self._backend is None:
            from pantheon.remote import RemoteBackendFactory

            self._backend = RemoteBackendFactory.create_backend()

        import time
        from pantheon.remote.backend.base import StreamMessage, StreamType

        stream_type = StreamType.CHAT
        message = StreamMessage(
            type=stream_type,
            session_id=f"chat_{chat_id}",
            timestamp=time.time(),
            data={**data, "chat_id": chat_id},
        )

        stream_channel = await self._backend.get_or_create_stream(
            f"chat_{chat_id}", stream_type
        )
        try:
            await stream_channel.publish(message)
        except Exception as e:
            logger.error(f"Error publishing stream: {e}")

    async def run(self, log_level: str | None = None, remote: bool = True):
        self._embed = not remote

        # Call parent's run method with the original parameters
        return await super().run(log_level=log_level, remote=remote)

    async def run_setup(self):
        """Setup the chatroom (ToolSet hook called before run)."""
        if self._endpoint_embed:
            logger.info(
                f"ChatRoom: endpoint_mode=embed, endpoint_id={self._endpoint.service_id}"
            )
        else:
            logger.info(
                f"ChatRoom: endpoint_mode=process, endpoint_id={self.endpoint_service_id}"
            )

        # Log ChatRoom's own startup mode
        if self._embed:
            logger.info("ChatRoom: startup_mode=embed (no stream publish)")
        else:
            logger.info("ChatRoom: startup_mode=remote (stream publish enabled)")

    def _save_team_template_to_memory(self, memory, template_obj: dict) -> None:
        """Save complete team template to memory for persistence."""
        if not hasattr(memory, "extra_data"):
            memory.extra_data = {}

        # Save complete template configuration
        memory.extra_data["team_template"] = {
            # Identifiers
            "template_id": template_obj.get("id", "custom"),
            "template_name": template_obj.get("name", "Custom Template"),
            # Core agent configuration (unified architecture)
            "agents_config": template_obj.get("agents_config", {}),
            "sub_agents": template_obj.get("sub_agents", []),
            # Service requirements
            "required_toolsets": template_obj.get("required_toolsets", []),
            "required_mcp_servers": template_obj.get("required_mcp_servers", []),
            # Metadata
            "created_at": datetime.now().isoformat(),
        }

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
        """Load team from chat's persistent memory.

        If no team template is found in memory, create a new team from default template
        and save it to memory for this chat.
        """
        memory = await run_func(self.memory_manager.get_memory, chat_id)

        # Check for stored team template
        team_template = None
        if hasattr(memory, "extra_data") and memory.extra_data:
            team_template = memory.extra_data.get("team_template")

        # If no template found, use default template
        if not team_template:
            logger.info(
                f"No team template in memory, creating default team for chat {chat_id}"
            )
            default_template = self.template_manager.get_template(
                "default", self.agents_template
            )
            if not default_template:
                raise RuntimeError("Default template not found in template manager")

            team_template = default_template.to_dict()
            # Save default template to memory for this chat
            self._save_team_template_to_memory(memory, team_template)
            await run_func(self.memory_manager.save)
            logger.info(f"Saved default template to memory for chat {chat_id}")
        else:
            logger.info(
                f"Loading team from stored template '{team_template.get('template_name', 'unknown')}' for chat {chat_id}"
            )

        # Create team with per-chat toolsets
        return await self._create_team_from_template(team_template, chat_id=chat_id)

    async def _ensure_services(
        self,
        service_type: str,
        required_services: list[str],
    ):
        """Ensure required services (MCP servers or ToolSets) are started."""
        if not required_services:
            return

        service_name_plural = "MCP servers" if service_type == "mcp" else "ToolSets"
        service_name_past = "started" if service_type == "mcp" else "started"

        try:
            logger.info(
                f"Ensuring {service_name_plural} are started: {required_services}"
            )
            result = await self._call_endpoint_method(
                endpoint_method_name="manage_service",
                action="start",
                service_type=service_type,
                name=required_services,
            )
            if not result.get("success"):
                logger.warning(
                    f"Failed to start some {service_name_plural}: {result.get('errors', [])}"
                )
            else:
                logger.info(
                    f"{service_name_plural} {service_name_past}: {result.get('started', [])}"
                )

        except Exception as e:
            logger.warning(f"Error ensuring {service_name_plural}: {e}")

    async def _create_team_from_template(
        self, team_template: dict, chat_id: str = None
    ) -> PantheonTeam:
        """Create a team from unified template configuration."""
        template_name = team_template.get("template_name") or team_template.get(
            "name", "unknown"
        )

        logger.info(f"🏗️ Creating team from template '{template_name}'")

        # Connect to endpoint service
        endpoint_service = await self._get_endpoint_service()

        # ===== STEP 1: Collect agent configs =====
        # Use template_manager to collect and validate configs
        inline_agents_config, sub_agents_config = (
            self.template_manager.collect_agent_configs(team_template)
        )

        # Merge configs for unified processing (inline agents first, then sub-agents)
        all_agents_config = {**inline_agents_config, **sub_agents_config}

        logger.debug(
            f"Collected {len(inline_agents_config)} inline agents, "
            f"{len(sub_agents_config)} sub-agents"
        )

        # ===== STEP 2: Add default services to all configs (if chat_id provided) =====
        if chat_id:
            # Add built-in toolsets and MCP servers to all agent configs
            self.template_manager.add_default_services_to_configs(all_agents_config)
        logger.info(f"Inline agents config: {inline_agents_config}")
        # ===== STEP 3: Compute and ensure all required services =====
        # Collect all toolsets and mcp servers from all agents (one loop)
        required_toolsets = set()
        required_mcp_servers = set()

        for agent_config in all_agents_config.values():
            required_toolsets.update(agent_config.get("toolsets", []))
            required_mcp_servers.update(agent_config.get("mcp_servers", []))

        await self._ensure_services("mcp", list(required_mcp_servers))
        await self._ensure_services("toolset", list(required_toolsets))

        logger.debug(
            f"Ensured services: {len(required_mcp_servers)} MCP servers, "
            f"{len(required_toolsets)} toolsets"
        )

        # ===== STEP 4: Create agents =====
        # Create inline agents (triage is first, ensured by dict order)
        inline_agents = await create_agents_from_template(
            endpoint_service, inline_agents_config
        )
        logger.info(f"Created {len(inline_agents)} inline agents")

        # Create sub-agents (empty dict produces empty list)
        sub_agents_list = await create_agents_from_template(
            endpoint_service, sub_agents_config
        )

        logger.info(f"Created {len(sub_agents_list)} sub-agents")

        # ===== STEP 5: Add plan toolset to inline agents (if chat_id provided) =====
        if chat_id:
            for agent in inline_agents:
                plan_mode_toolset = PlanModeToolSet(agent=agent, name="plan_mode")
                await agent.toolset(plan_mode_toolset)
                logger.debug(f"Agent '{agent.name}': Added PlanModeToolSet")

        # ===== STEP 6: Create and setup team =====
        team = PantheonTeam(
            inline_agents=inline_agents,
            sub_agents=sub_agents_list,
        )
        await team.async_setup()

        feature_list = []
        if team.has_transfer_agents:
            feature_list.append(f"transfer ({len(team.inline_agents)} inline agents)")
        if team.has_sub_agents:
            feature_list.append(f"discovery ({len(team.sub_agents)} sub-agents)")
        features = " + ".join(feature_list) if feature_list else "none"

        logger.info(f"✅ Team '{template_name}' created (Features: {features})")
        return team

    @tool
    async def setup_team_for_chat(self, chat_id: str, template_obj: dict):
        """Setup/update team for a specific chat using full template object."""
        try:
            logger.info(
                f"Setting up team for chat {chat_id} with template: {template_obj.get('name', 'unknown')}"
            )

            # Store full template in memory using consolidated method
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            self._save_team_template_to_memory(memory, template_obj)

            if "active_agent" in memory.extra_data:
                del memory.extra_data["active_agent"]
            await run_func(self.memory_manager.save)

            # Clear cached team (force recreation next time)
            if chat_id in self.chat_teams:
                del self.chat_teams[chat_id]

            return {
                "success": True,
                "message": f"Team template '{template_obj.get('name', 'Custom')}' prepared for chat",
                "template": template_obj,
                "chat_id": chat_id,
            }

        except Exception as e:
            return {"success": False, "message": f"Template setup failed: {str(e)}"}

    @tool
    async def get_endpoint(self) -> dict:
        """Get the endpoint service info."""
        try:
            if self._endpoint_embed:
                # Embed mode: directly access endpoint properties
                endpoint = await self._get_endpoint_service()
                return {
                    "success": True,
                    "service_name": endpoint.service_name
                    if hasattr(endpoint, "service_name")
                    else "endpoint",
                    "service_id": endpoint.service_id
                    if hasattr(endpoint, "service_id")
                    else "unknown",
                }
            else:
                # Process mode: fetch through RPC
                s = await self._get_endpoint_service()
                try:
                    info = await s.fetch_service_info()
                    return {
                        "success": True,
                        "service_name": info.service_name
                        if info
                        else self.endpoint_service_id,
                        "service_id": info.service_id
                        if info
                        else self.endpoint_service_id,
                    }
                except Exception:
                    # Fallback if fetch_service_info not available
                    return {
                        "success": True,
                        "service_name": "endpoint",
                        "service_id": self.endpoint_service_id,
                    }
        except Exception as e:
            logger.error(f"Error getting endpoint service info: {e}")
            return {"success": False, "message": str(e)}

    @tool
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

    @tool
    async def get_toolsets(self) -> dict:
        """Get all available toolsets from the endpoint service.

        Returns:
            A dictionary with the following keys:
            - success: Whether the operation was successful.
            - services: A list of available toolset services.
            - error: Error message if operation failed.
        """
        try:
            result = await self._call_endpoint_method(
                endpoint_method_name="manage_service",
                action="list",
                service_type="toolset",
            )
            if isinstance(result, dict) and "success" in result:
                return result
            else:
                # If result is directly the services list
                return {"success": True, "services": result}
        except Exception as e:
            logger.error(f"Error getting toolsets: {e}")
            return {"success": False, "error": str(e)}

    @tool
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
        try:
            # Add debug logging
            logger.debug(
                f"chatroom proxy_toolset: method_name={method_name}, toolset_name={toolset_name}, args={args}"
            )

            # Use unified endpoint call method
            result = await self._call_endpoint_method(
                endpoint_method_name="proxy_toolset",
                method_name=method_name,
                args=args or {},
                toolset_name=toolset_name,
            )

            return result

        except Exception as e:
            logger.error(
                f"Error calling toolset method {method_name} on {toolset_name or 'endpoint'}: {e}"
            )
            return {"success": False, "error": str(e)}

    @tool
    async def get_agents(self, chat_id: str = None) -> dict:
        """Get the inline agents info for a specific chat or default team."""

        def get_agent_info(agent: Agent):
            if hasattr(agent, "not_loaded_toolsets"):
                not_loaded_toolsets = agent.not_loaded_toolsets
            else:
                not_loaded_toolsets = []
            return {
                "name": agent.name,
                "instructions": agent.instructions,
                "toolful": getattr(agent, "toolful", False),
                "tools": [t for t in agent.functions.keys()],
                "toolsets": [],
                "icon": agent.icon,
                "not_loaded_toolsets": not_loaded_toolsets,
            }

        logger.debug(f"get agents {chat_id}")

        # chat_id must be provided - this is a per-chat operation
        if not chat_id:
            logger.debug(
                "get_agents called without chat_id - returning empty mock data"
            )
            return {
                "success": True,
                "agents": [],
                "can_switch_agents": False,
                "has_sub_agents": False,
                "has_transfer_agents": False,
            }

        # Get the appropriate team for this chat
        team = await self.get_team_for_chat(chat_id)

        # Only expose inline agents (not sub-agents)
        # Sub-agents are internal implementation, managed by inline agents
        agents_to_expose = team.inline_agents
        logger.debug(f"Team has {len(team.inline_agents)} inline agents")

        return {
            "success": True,
            "agents": [get_agent_info(a) for a in agents_to_expose],
            "can_switch_agents": len(team.inline_agents) > 1,
            "has_sub_agents": team.has_sub_agents,
            "has_transfer_agents": team.has_transfer_agents,
        }

    @tool
    async def set_active_agent(self, chat_name: str, agent_name: str):
        """Set the active agent for a chat."""
        # Get the team for this specific chat
        team = await self.get_team_for_chat(chat_name)

        # Verify agent is an inline agent (not a sub-agent)
        if agent_name not in team._inline_agent_names:
            return {
                "success": False,
                "message": f"'{agent_name}' is not an inline agent. Can only switch between inline agents.",
            }

        # Verify agent exists
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        agent = team.agents.get(agent_name)
        if agent is None:
            return {
                "success": False,
                "message": f"Agent '{agent_name}' not found in team",
            }

        # Set active agent
        team.set_active_agent(memory, agent_name)
        logger.info(f"Set active agent to '{agent_name}' for chat '{chat_name}'")
        return {
            "success": True,
            "message": f"Agent '{agent_name}' set as active",
        }

    @tool
    async def get_active_agent(self, chat_name: str) -> dict:
        """Get the active agent for a chat."""
        # Get the team for this specific chat
        team = await self.get_team_for_chat(chat_name)
        memory = await run_func(self.memory_manager.get_memory, chat_name)
        active_agent = team.get_active_agent(memory)
        return {
            "success": True,
            "agent": active_agent.name,
        }

    @tool
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

    @tool
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

    @tool
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

    @tool
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

    @tool
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

    @tool
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

    @tool
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

        logger.info(f"Received message: {chat_id}|{message}")

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

        # Add streaming hooks (will fail silently if backend doesn't support streaming)
        async def nats_chunk_processor(chunk: dict):
            await self._publish_stream(
                chat_id, "chunk", {"type": "chunk", "chunk": chunk}
            )

        async def nats_step_processor(step_message: dict):
            role = step_message.get("role", None)
            # Fix front end duplicate user message
            if role == "user":
                return
            await self._publish_stream(
                chat_id,
                "step",
                {
                    "type": "step_message",
                    "step_message": step_message,
                },
            )

        thread.add_chunk_hook(nats_chunk_processor)
        thread.add_step_message_hook(nats_step_processor)

        await self.attach_hooks(
            chat_id, process_chunk, process_step_message, wait=False
        )
        await thread.run()

        # Generate or update chat name after conversation
        try:
            from .special_agents import get_chat_name_generator

            chat_name_generator = get_chat_name_generator()
            memory.name = await chat_name_generator.generate_or_update_name(memory)
        except Exception as e:
            logger.error(f"Failed to generate/update chat name: {e}")

        memory.extra_data["running"] = False
        memory.extra_data["last_activity_date"] = datetime.now().isoformat()
        await run_func(self.memory_manager.save)
        del self.threads[chat_id]
        return thread.response

    @tool
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

    @tool
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

    @tool
    async def get_suggestions(self, chat_id: str) -> dict:
        """Get suggestion questions for a chat."""
        return await self._handle_suggestions(chat_id, force_refresh=False)

    @tool
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
            suggestion_generator = get_suggestion_generator()
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

            logger.debug(f"Generated {len(suggestions)} suggestions for chat {chat_id}")

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

    @tool
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
                        "sub_agents": team_template.get("sub_agents", []),
                        "required_toolsets": team_template.get("required_toolsets", []),
                        "required_mcp_servers": team_template.get(
                            "required_mcp_servers", []
                        ),
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

    @tool
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
                sub_agents=template.get("sub_agents", []),
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

            # Services will be automatically started via _ensure_services when template is loaded
            return {
                "success": True,
                "compatible": True,
                "required_toolsets": template_obj.required_toolsets,
                "required_mcp_servers": template_obj.required_mcp_servers,
                "template": template_obj.to_dict(),
            }

        except Exception as e:
            logger.error(f"Error validating template compatibility: {e}")
            return {"success": False, "message": str(e)}

    # Template and Agent Management (CRUD operations)

    @tool
    async def manage_template(
        self,
        operation: str,
        template_id: str | None = None,
        template_data: dict | None = None,
    ) -> dict:
        """
        Unified template management interface for CRUD operations.

        Delegates to template_manager for all template CRUD operations.

        Args:
            operation: "list", "read", "create", "update", "delete", or "clone"
            template_id: Template ID (required for read, update, delete, clone)
            template_data: Template data dict (required for create, update, clone)

        Returns:
            Response dict with operation results
        """
        try:
            template_manager = get_template_manager()

            if operation == "list":
                templates = template_manager.list_templates()
                return {
                    "success": True,
                    "operation": "list",
                    "templates": [t.to_dict() for t in templates],
                    "total": len(templates),
                }

            elif operation == "read":
                if not template_id:
                    return {
                        "success": False,
                        "operation": "read",
                        "error": "template_id is required",
                    }
                template = template_manager.get_template(template_id)
                if not template:
                    return {
                        "success": False,
                        "operation": "read",
                        "error": f"Template '{template_id}' not found",
                    }
                return {
                    "success": True,
                    "operation": "read",
                    "template": template.to_dict(),
                }

            elif operation == "create":
                if not template_data:
                    return {
                        "success": False,
                        "operation": "create",
                        "error": "template_data is required",
                    }
                success, msg, template = template_manager.create_template(template_data)
                if not success:
                    return {"success": False, "operation": "create", "error": msg}
                return {
                    "success": True,
                    "operation": "create",
                    "template_id": msg,  # msg contains the template_id on success
                    "template": template.to_dict(),
                }

            elif operation == "update":
                if not template_id or not template_data:
                    return {
                        "success": False,
                        "operation": "update",
                        "error": "template_id and template_data are required",
                    }
                success, msg, template = template_manager.update_template(
                    template_id, template_data
                )
                if not success:
                    return {"success": False, "operation": "update", "error": msg}
                return {
                    "success": True,
                    "operation": "update",
                    "template": template.to_dict(),
                }

            elif operation == "delete":
                if not template_id:
                    return {
                        "success": False,
                        "operation": "delete",
                        "error": "template_id is required",
                    }
                success, msg = template_manager.delete_template(template_id)
                if not success:
                    return {"success": False, "operation": "delete", "error": msg}
                return {"success": True, "operation": "delete"}

            else:
                return {
                    "success": False,
                    "operation": operation,
                    "error": f"Unknown operation: {operation}",
                }

        except Exception as e:
            logger.error(f"Error in manage_template (op={operation}): {e}")
            return {"success": False, "operation": operation, "error": str(e)}

    @tool
    async def manage_agents(
        self,
        operation: str,
        agent_id: str | None = None,
        agent_data: dict | None = None,
    ) -> dict:
        """
        Unified agent management interface for CRUD operations.

        Delegates to template_manager for all agent CRUD operations.

        Args:
            operation: "list", "read", "create", "update", or "delete"
            agent_id: Agent ID (required for read, update, delete)
            agent_data: Agent data dict (required for create, update)

        Returns:
            Response dict with operation results
        """
        try:
            template_manager = get_template_manager()

            if operation == "list":
                agents_list = template_manager.get_all_agents()
                return {
                    "success": True,
                    "operation": "list",
                    "agents": agents_list,
                    "total": len(agents_list),
                }

            elif operation == "read":
                if not agent_id:
                    return {
                        "success": False,
                        "operation": "read",
                        "error": "agent_id is required",
                    }
                agent_config = template_manager.agents_manager.get_agent_config(
                    agent_id
                )
                if not agent_config:
                    return {
                        "success": False,
                        "operation": "read",
                        "error": f"Agent '{agent_id}' not found",
                    }
                return {
                    "success": True,
                    "operation": "read",
                    "agent": {"id": agent_id, **agent_config},
                }

            elif operation == "create":
                if not agent_data:
                    return {
                        "success": False,
                        "operation": "create",
                        "error": "agent_data is required",
                    }
                success, msg, agent_config = template_manager.create_agent(agent_data)
                if not success:
                    return {"success": False, "operation": "create", "error": msg}
                return {
                    "success": True,
                    "operation": "create",
                    "agent_id": msg,  # msg contains the agent_id on success
                    "agent": {"id": msg, **agent_config},
                }

            elif operation == "update":
                if not agent_id or not agent_data:
                    return {
                        "success": False,
                        "operation": "update",
                        "error": "agent_id and agent_data are required",
                    }
                success, msg, agent_config = template_manager.update_agent(
                    agent_id, agent_data
                )
                if not success:
                    return {"success": False, "operation": "update", "error": msg}
                return {
                    "success": True,
                    "operation": "update",
                    "agent": {"id": agent_id, **agent_config},
                }

            elif operation == "delete":
                if not agent_id:
                    return {
                        "success": False,
                        "operation": "delete",
                        "error": "agent_id is required",
                    }
                success, msg = template_manager.delete_agent(agent_id)
                if not success:
                    return {"success": False, "operation": "delete", "error": msg}
                return {"success": True, "operation": "delete"}

            else:
                return {
                    "success": False,
                    "operation": operation,
                    "error": f"Unknown operation: {operation}",
                }

        except Exception as e:
            logger.error(f"Error in manage_agents (op={operation}): {e}")
            return {"success": False, "operation": operation, "error": str(e)}
