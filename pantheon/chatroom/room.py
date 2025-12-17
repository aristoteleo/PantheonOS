import asyncio
import dataclasses
import io
from datetime import datetime
from pathlib import Path
from typing import Callable
from typing import TYPE_CHECKING

from pantheon.agent import Agent
from pantheon.factory import (
    create_agents_from_template,
    get_template_manager,
    TeamConfig,
)
from pantheon.memory import MemoryManager, _ALL_CONTEXTS
from pantheon.settings import get_settings
from pantheon.team import PantheonTeam
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
from pantheon.utils.misc import run_func
from .special_agents import get_suggestion_generator
from .thread import Thread

if TYPE_CHECKING:
    from pantheon.endpoint import Endpoint

import openai


DEFAULT_TOOLSETS = []


class ChatRoom(ToolSet):
    """
    ChatRoom is a service that allows user to interact with a team of agents.

    Args:
        endpoint: Endpoint instance (embed mode), service_id string (remote mode),
                  or None (auto-create Endpoint in embed mode).
        memory_dir: The directory to store the memory.
        workspace_path: Workspace path for auto-created Endpoint (only used when endpoint=None).
        name: The name of the chatroom.
        description: The description of the chatroom.
        speech_to_text_model: The model to use for speech to text.
        check_before_chat: The function to check before chat.
        enable_nats_streaming: Enable NATS streaming for real-time message publishing.
                               Default: False.
        default_team: A fixed PantheonTeam to use for all chats (bypasses template system).
                      Useful for REPL or embedded usage. Default: None.
        **kwargs: Additional parameters passed to ToolSet (e.g., id_hash).
    """

    def __init__(
        self,
        endpoint: "Endpoint | str | None" = None,
        memory_dir: str = "./.pantheon/memory",
        workspace_path: str | None = None,
        name: str = "pantheon-chatroom",
        description: str = "Chatroom for Pantheon agents",
        speech_to_text_model: str = "gpt-4o-mini-transcribe",
        check_before_chat: Callable | None = None,
        enable_nats_streaming: bool = False,
        default_team: "PantheonTeam | None" = None,
        enable_learning: bool | None = None,
        learning_config: dict | None = None,
        **kwargs,
    ):
        # Initialize ToolSet (will handle worker creation in run())
        super().__init__(name=name, **kwargs)

        # ChatRoom specific initialization (before endpoint setup for workspace_path default)
        # Convert to absolute path BEFORE Endpoint creation (Endpoint does os.chdir)
        self.memory_dir = Path(memory_dir).resolve()
        self.memory_manager = MemoryManager(self.memory_dir)

        # Determine endpoint connection mode based on type
        if isinstance(endpoint, str):
            # Remote mode: endpoint is a service_id string
            self._endpoint_embed = False
            self._endpoint = None
            self.endpoint_service_id = endpoint
            self._auto_created_endpoint = False
        elif endpoint is None:
            # Auto-create mode: create Endpoint instance automatically
            from pantheon.endpoint import Endpoint

            # Use workspace_path or default to .pantheon dir (where settings.json lives)
            if workspace_path is None:
                # settings is already loaded in __init__ via get_settings() call if needed,
                # but better to get it fresh or via kwargs if possible.
                # Actually, ChatRoom doesn't hold settings instance directly in __init__ args,
                # but we can get it from the factory or create a new one.
                # Since we want consistency, let's use the global settings instance.
                from pantheon.settings import get_settings
                settings = get_settings()
                workspace_path = str(settings.workspace)

            self._endpoint = Endpoint(
                config=None,
                workspace_path=workspace_path,
            )
            self._endpoint_embed = True
            self.endpoint_service_id = None
            self._auto_created_endpoint = True
            logger.info(f"ChatRoom: auto-created Endpoint at {workspace_path}")
        else:
            # Embed mode: endpoint is an Endpoint instance
            self._endpoint_embed = True
            self._endpoint = endpoint
            self.endpoint_service_id = None
            self._auto_created_endpoint = False

        self._endpoint_service = None

        # NATS streaming (optional)
        self._nats_adapter = None
        if enable_nats_streaming:
            from .stream import NATSStreamAdapter

            self._nats_adapter = NATSStreamAdapter()

        # Initialize template manager (supports old and new formats, manages agents.yaml library)
        self.template_manager = get_template_manager()

        self.description = description

        # Per-chat team management
        self.chat_teams: dict[str, PantheonTeam] = {}  # Per-chat teams cache

        self.speech_to_text_model = speech_to_text_model
        self.threads: dict[str, Thread] = {}
        self.check_before_chat = check_before_chat

        # Default team (bypasses template system when set)
        self._default_team = default_team

        # Background tasks management (for non-blocking operations like chat renaming)
        self._background_tasks: set[asyncio.Task] = set()

        # ACE (Agentic Context Engineering) long-term memory
        self._init_learning(enable_learning, learning_config)

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

    def _init_learning(
        self, enable_learning: bool | None = None, learning_config: dict | None = None
    ) -> None:
        """Initialize Long-term memory resources."""
        from pantheon.internal.learning import create_learning_resources
        
        self._skillbook, self._learning_pipeline = create_learning_resources(
            enable=enable_learning, config=learning_config
        )

    async def run(self, log_level: str | None = None, remote: bool = True):
        return await super().run(log_level=log_level, remote=remote)

    async def run_setup(self):
        """Setup the chatroom (ToolSet hook called before run)."""
        # Start auto-created Endpoint if needed
        if self._auto_created_endpoint and self._endpoint is not None:
            logger.info("ChatRoom: starting auto-created Endpoint...")
            asyncio.create_task(self._endpoint.run(remote=False))
            # Wait for endpoint to be ready
            max_retries = 30
            for i in range(max_retries):
                if self._endpoint._setup_completed:
                    logger.info(
                        f"ChatRoom: auto-created Endpoint ready (service_id={self._endpoint.service_id})"
                    )
                    break
                await asyncio.sleep(0.1)
            else:
                logger.warning("ChatRoom: Endpoint startup timeout, continuing anyway")

        if self._endpoint_embed:
            logger.info(
                f"ChatRoom: endpoint_mode=embed, endpoint_id={self._endpoint.service_id}"
            )
        else:
            logger.info(
                f"ChatRoom: endpoint_mode=process, endpoint_id={self.endpoint_service_id}"
            )

        # Log NATS streaming status
        if self._nats_adapter is not None:
            logger.info("ChatRoom: NATS streaming enabled")
        else:
            logger.info("ChatRoom: NATS streaming disabled")

        # Start learning pipeline
        if self._learning_pipeline is not None:
            await self._learning_pipeline.start()
            logger.info("ChatRoom: learning pipeline started")

    def _save_team_template_to_memory(self, memory, template_obj: dict) -> None:
        """Save TeamConfig to memory for persistence (new format)."""
        extra_data = getattr(memory, "extra_data", None)
        if extra_data is None:
            memory.extra_data = extra_data = {}

        if isinstance(template_obj, TeamConfig):
            team_config = template_obj
        else:
            team_config = self.template_manager.dict_to_team_config(template_obj)

        extra_data["team_template"] = dataclasses.asdict(team_config)

    async def get_team_for_chat(self, chat_id: str) -> PantheonTeam:
        """Get the team for a specific chat, creating from memory if needed."""
        # 0. If default_team is set, always use it (bypass template system)
        if self._default_team is not None:
            return self._default_team

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
        extra_data = getattr(memory, "extra_data", None)
        if extra_data is None:
            memory.extra_data = extra_data = {}

        team_template_dict = extra_data.get("team_template")

        # If no template found, use default template
        if not team_template_dict:
            logger.info(
                f"No team template in memory, creating default team for chat {chat_id}"
            )
            default_template = self.template_manager.get_template("default")
            if not default_template:
                raise RuntimeError("Default template not found in template manager")

            # template_manager returns TeamConfig, convert to dict and save
            team_template_dict = dataclasses.asdict(default_template)

            # Save default template to memory for this chat
            extra_data["team_template"] = team_template_dict
            await run_func(self.memory_manager.save)
            logger.info(f"Saved default template to memory for chat {chat_id}")
        else:
            logger.info(
                f"Loading team from stored template '{team_template_dict.get('name', 'unknown')}' for chat {chat_id}"
            )

        # Convert dict to TeamConfig
        team_config = self.template_manager.dict_to_team_config(team_template_dict)

        # Create team with per-chat toolsets
        return await self._create_team_from_template(team_config, chat_id=chat_id)

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
        self, team_config: TeamConfig, chat_id: str = None
    ) -> PantheonTeam:
        """Create a team from TeamConfig object."""
        template_name = team_config.name or "unknown"

        logger.info(f"🏗️ Creating team from template '{template_name}'")

        # Connect to endpoint service
        endpoint_service = await self._get_endpoint_service()

        (
            agent_configs,
            required_toolsets,
            required_mcp_servers,
        ) = self.template_manager.prepare_team(team_config)

        # ===== STEP 2: Compute and ensure all required services =====
        await self._ensure_services("mcp", list(required_mcp_servers))
        await self._ensure_services("toolset", list(required_toolsets))

        logger.debug(
            f"Ensured services: {len(required_mcp_servers)} MCP servers, "
            f"{len(required_toolsets)} toolsets"
        )

        # ===== STEP 3: Create agents =====
        all_agents = await create_agents_from_template(endpoint_service, agent_configs)
        logger.info(f"Created {len(all_agents)} agents")

        # ===== STEP 4: Create and setup team =====
        team = PantheonTeam(
            agents=all_agents,
            skillbook=self._skillbook,
            learning_pipeline=self._learning_pipeline,
        )
        await team.async_setup()

        num_agents = len(team.team_agents)
        features = f"{num_agents} agents" if num_agents > 1 else "single agent"

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
            if not endpoint_service_id:
                return {
                    "success": False,
                    "message": "endpoint_service_id is required",
                }

            # Switch to process/remote mode whenever endpoint ID is provided.
            self._endpoint_embed = False
            self._endpoint = None
            self.endpoint_service_id = endpoint_service_id

            # Force reconnection on next use and drop cached teams bound to the old endpoint.
            self._endpoint_service = None
            self._backend = None
            self.chat_teams.clear()

            # Sanity-check connectivity immediately to fail fast.
            await self._get_endpoint_service()

            return {
                "success": True,
                "message": f"Endpoint service set to '{endpoint_service_id}'",
            }
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
        """Get the team agents info for a specific chat."""

        def get_agent_info(agent: Agent):
            if hasattr(agent, "not_loaded_toolsets"):
                not_loaded_toolsets = agent.not_loaded_toolsets
            else:
                not_loaded_toolsets = []
            return {
                "name": agent.name,
                "instructions": agent.instructions,
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
                "has_transfer": False,
            }

        # Get the appropriate team for this chat
        team = await self.get_team_for_chat(chat_id)

        # Only expose primary agents (not sub-agents)
        # Sub-agents are internal implementation, managed by primary agents
        agents_to_expose = team.team_agents
        logger.debug(f"Team has {len(team.team_agents)} agents")

        return {
            "success": True,
            "agents": [get_agent_info(a) for a in agents_to_expose],
            "can_switch_agents": len(team.team_agents) > 1,
            "has_transfer": len(team.team_agents) > 1,
        }

    @tool
    async def set_active_agent(self, chat_name: str, agent_name: str):
        """Set the active agent for a chat."""
        # Get the team for this specific chat
        team = await self.get_team_for_chat(chat_name)

        # Verify the requested agent is part of the primary team (not a sub-agent)
        target_agent = next(
            (agent for agent in team.team_agents if agent.name == agent_name),
            None,
        )
        if target_agent is None:
            return {
                "success": False,
                "message": f"'{agent_name}' is not a primary team agent.",
            }

        memory = await run_func(self.memory_manager.get_memory, chat_name)

        # Set active agent
        team.set_active_agent(memory, agent_name)
        logger.debug(f"Set active agent to '{agent_name}' for chat '{chat_name}'")
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
            # Get full raw history for UI
            messages = await run_func(memory.get_messages, _ALL_CONTEXTS, False)
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

    async def _background_rename_chat(self, memory):
        """Background task to rename chat without blocking main flow.

        This runs asynchronously after chat() returns, so the user doesn't
        experience any delay from the LLM call for name generation.
        """
        try:
            from .special_agents import get_chat_name_generator

            chat_name_generator = get_chat_name_generator()
            new_name = await chat_name_generator.generate_or_update_name(memory)
            if new_name and new_name != memory.name:
                memory.name = new_name
                # Save memory with updated name
                await run_func(self.memory_manager.save)
                logger.debug(f"Chat renamed in background to: {new_name}")
        except Exception as e:
            logger.error(f"Background chat rename failed: {e}")

    @tool
    async def chat(
        self,
        chat_id: str,
        message: list[dict],
        context_variables: dict | None = None,
        process_chunk=None,
        process_step_message=None,
    ):
        """Start a chat, send a message to the chat.

        Args:
            chat_id: The ID of the chat.
            message: The messages to send to the chat.
                Messages can include `_llm_content` field for LLM-specific content
                (assembled by frontend) while `content` is used for display.
            context_variables: Optional context variables to pass to the agent.
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
            context_variables=context_variables,
        )

        self.threads[chat_id] = thread

        # Add NATS streaming hooks if enabled
        if self._nats_adapter is not None:
            chunk_hook, step_hook = self._nats_adapter.create_hooks(chat_id)
            thread.add_chunk_hook(chunk_hook)
            thread.add_step_message_hook(step_hook)

        await self.attach_hooks(
            chat_id, process_chunk, process_step_message, wait=False
        )

        try:
            await thread.run()

            # Generate or update chat name in background (non-blocking)
            # This prevents UI lag from the LLM call for name generation
            task = asyncio.create_task(self._background_rename_chat(memory))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

            # Publish chat finished message if NATS streaming enabled
            if self._nats_adapter is not None:
                await self._nats_adapter.publish_chat_finished(chat_id)

            return thread.response
        except asyncio.CancelledError:
            logger.info(f"Chat {chat_id} was cancelled/interrupted")
            raise  # Re-raise to propagate cancellation
        finally:
            # Always clean up the thread from the registry, whether successful or interrupted
            memory.extra_data["running"] = False
            memory.extra_data["last_activity_date"] = datetime.now().isoformat()
            try:
                await run_func(self.memory_manager.save)
            except Exception as e:
                logger.error(f"Failed to save memory on cleanup: {e}")
            if chat_id in self.threads:
                del self.threads[chat_id]

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
        # Note: Thread cleanup from self.threads happens in chat()'s finally block
        # But if called externally, we ensure cleanup here as well
        if chat_id in self.threads:
            del self.threads[chat_id]
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
            messages = memory.get_messages(None)

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
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)

            # Check if chat has a stored template
            if hasattr(memory, "extra_data") and memory.extra_data:
                team_template_dict = memory.extra_data.get("team_template")
                if team_template_dict:
                    # Return the stored template information (new format)
                    return {
                        "success": True,
                        "template": team_template_dict,
                    }

            # No template found, return default template info
            template_manager = get_template_manager()
            default_template = template_manager.get_template("default")
            if default_template:
                return {
                    "success": True,
                    "template": dataclasses.asdict(default_template),
                    "is_default": True,
                }

            # Fallback if no default template found
            return {
                "success": False,
                "message": "No template found and no default template available",
            }
        except Exception as e:
            logger.error(f"Error getting chat template: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def validate_template(self, template: dict) -> dict:
        """Validate if a template is compatible with current endpoint."""
        try:
            template_manager = get_template_manager()
            return template_manager.validate_template_dict(template)
        except Exception as e:
            logger.error(f"Error validating template compatibility: {e}")
            return {"success": False, "message": str(e)}

    # File-Based Template Management (delegates to template_manager)

    @tool
    async def list_template_files(self, file_type: str = "teams") -> dict:
        """
        List available template files.
        """
        logger.debug(f"Listing template files... {file_type}")
        template_manager = get_template_manager()
        return template_manager.list_template_files(file_type)

    @tool
    async def read_template_file(
        self, file_path: str, resolve_refs: bool = False
    ) -> dict:
        """
        Read a template markdown file.

        Args:
            file_path: Path to template file (e.g., "teams/default.md")
            resolve_refs: If True, resolve agent references to full configs.
                         Use False for editing, True for applying template to chat.
        """
        template_manager = get_template_manager()
        return template_manager.read_template_file(file_path, resolve_refs=resolve_refs)

    @tool
    async def write_template_file(self, file_path: str, content: dict) -> dict:
        """
        Write/update a template markdown file.
        """
        template_manager = get_template_manager()
        return template_manager.write_template_file(file_path, content)

    @tool
    async def delete_template_file(self, file_path: str) -> dict:
        """
        Delete a template markdown file.
        """
        template_manager = get_template_manager()
        return template_manager.delete_template_file(file_path)
