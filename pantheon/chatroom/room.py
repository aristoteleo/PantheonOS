import asyncio
import copy
import dataclasses
import io
import json as _json
try:
    import psutil as _psutil
    _psutil_process = _psutil.Process()
except ImportError:
    _psutil = None  # type: ignore
    _psutil_process = None
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
from pantheon.internal.memory import MemoryManager, _ALL_CONTEXTS
from pantheon.settings import get_settings
from pantheon.team import PantheonTeam
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
from pantheon.utils.misc import run_func
from .special_agents import get_suggestion_generator
from .thread import Thread

if TYPE_CHECKING:
    from pantheon.endpoint import Endpoint


DEFAULT_TOOLSETS = []


def _custom_models_path() -> Path:
    """Path to the custom models config file."""
    from pantheon.settings import get_settings
    return get_settings().pantheon_dir / "custom_models.json"


def _load_custom_models() -> dict:
    """Load user-defined custom models from custom_models.json."""
    p = _custom_models_path()
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_custom_models(models: dict) -> None:
    """Save user-defined custom models to custom_models.json."""
    p = _custom_models_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(models, indent=2, ensure_ascii=False), encoding="utf-8")


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
        enable_auto_chat_name: bool = False,
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

        # Auto chat name generation (disabled by default, enable for UI mode)
        self._enable_auto_chat_name = enable_auto_chat_name

        # PantheonClaw gateway manager (lazy init; tied to chatroom event loop)
        self._gateway_channel_manager = None

        # Plugin system (memory, learning, compression)
        self._init_plugins()

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

    def _init_plugins(self) -> None:
        """Initialize plugin config (lazy creation).

        Actual plugin instances are created in background during run_setup().
        """
        self._compression_plugin = None
        self._memory_plugin = None
        self._plugins = []  # List of initialized plugins

    async def run(self, log_level: str | None = None, remote: bool = True):
        return await super().run(log_level=log_level, remote=remote)

    async def run_setup(self):
        """Setup the chatroom (ToolSet hook called before run).
        
        This method is idempotent - Endpoint startup is guarded by _auto_created_endpoint flag.
        """
        # Start auto-created Endpoint if needed (one-time)
        if self._auto_created_endpoint and self._endpoint is not None:
            # Clear flag to prevent re-entry
            self._auto_created_endpoint = False
            
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

        # Log endpoint mode (always log if endpoint exists)
        if self._endpoint is not None:
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

        # Start plugin initialization in background (non-blocking warmup)
        task = asyncio.create_task(self._ensure_plugins())
        self._background_tasks.add(task)

        # Register activity callback for _ping responses (used by Hub idle cleanup)
        if hasattr(self, 'worker') and self.worker and hasattr(self.worker, 'set_activity_callback'):
            self.worker.set_activity_callback(self._get_activity_status)

    def _get_activity_status(self) -> dict:
        """Return current activity status for _ping responses.

        Called synchronously from NATSRemoteWorker._ping().  Must not block.
        psutil.cpu_percent(interval=None) is non-blocking; first call returns 0.0.
        """
        active_threads = len(self.threads)
        bg_task_count = 0
        for team in self.chat_teams.values():
            for agent in team.agents.values():
                if hasattr(agent, '_bg_manager'):
                    bg_task_count += sum(
                        1 for t in agent._bg_manager.list_tasks()
                        if t.status == "running"
                    )
        has_active_tasks = active_threads > 0 or bg_task_count > 0

        metrics: dict = {
            "active_threads": active_threads,
            "bg_tasks": bg_task_count,
            "has_active_tasks": has_active_tasks,
        }

        if _psutil is not None and _psutil_process is not None:
            try:
                metrics["cpu_percent"] = round(_psutil_process.cpu_percent(interval=None), 1)
                rss = _psutil_process.memory_info().rss
                total = _psutil.virtual_memory().total
                metrics["mem_used_mb"] = round(rss / 1024 / 1024, 1)
                metrics["mem_percent"] = round(rss / total * 100, 1) if total > 0 else 0.0
            except Exception:
                pass  # process may have exited or psutil failed — omit silently

        return metrics

    async def _ensure_plugins(self, endpoint_service: object = None) -> list:
        """Lazily initialize plugins via centralized registry (idempotent)."""
        if self._plugins:
            return self._plugins

        try:
            from pantheon.team.plugin_registry import create_plugins
            from pantheon.settings import get_settings

            self._plugins = create_plugins(get_settings())
            # Track memory plugin reference for direct access
            from pantheon.internal.memory_system.plugin import MemorySystemPlugin
            for p in self._plugins:
                if isinstance(p, MemorySystemPlugin):
                    self._memory_plugin = p
                    break

            logger.info(f"ChatRoom: {len(self._plugins)} plugins initialized via registry")
        except Exception as e:
            logger.error(f"ChatRoom: Failed to initialize plugins: {e}")
            import traceback
            traceback.print_exc()

        return self._plugins

    async def cleanup(self) -> None:
        """Clean up ChatRoom resources before exit.
        
        Stops plugins, cancels background tasks, and cleans up the endpoint.
        """
        # Shutdown plugins
        self._plugins.clear()
        
        # Clean up endpoint if it exists
        if hasattr(self, "_endpoint") and self._endpoint:
            try:
                await self._endpoint.cleanup()
            except Exception:
                pass

        # Cancel any pending background tasks
        for task in self._background_tasks:
            if not task.done():
                task.cancel()


    def _save_team_template_to_memory(self, memory, template_obj: dict) -> None:
        """Save TeamConfig to memory for persistence (new format)."""
        extra_data = getattr(memory, "extra_data", None)
        if extra_data is None:
            memory.extra_data = extra_data = {}

        if isinstance(template_obj, TeamConfig):
            team_config = template_obj
        else:
            team_config = self.template_manager.dict_to_team_config(template_obj)

        memory.set_metadata_in_memory("team_template", dataclasses.asdict(team_config))

    async def get_team_for_chat(self, chat_id: str, save_to_memory: bool = True) -> PantheonTeam:
        """Get the team for a specific chat, creating from memory if needed."""
        # 0. If default_team is set, always use it (bypass template system)
        if self._default_team is not None:
            return self._default_team

        # FIX for performance, history chat will get team even not needed.
        # 1. Check if team already exists in cache
        if chat_id in self.chat_teams:
            return self.chat_teams[chat_id]

        # 2. Try to load team from persistent memory
        team = await self._load_team_from_memory(chat_id, save_to_memory=save_to_memory)
        self.chat_teams[chat_id] = team  # Cache it

        return team

    async def _load_team_from_memory(self, chat_id: str, save_to_memory: bool = True) -> PantheonTeam:
        """Load team from chat's persistent memory.

        If no team template is found in memory, create a new team from default template
        and save it to memory for this chat.
        """
        # Read-only: loading team config, no need to fix
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
            if save_to_memory:
                memory.set_metadata("team_template", team_template_dict)
                logger.info(f"Saved default template to memory for chat {chat_id}")
            else:
                memory.set_metadata_in_memory("team_template", team_template_dict)
        else:
            logger.info(
                f"Loading team from stored template '{team_template_dict.get('name', 'unknown')}' for chat {chat_id}"
            )

        # Convert dict to TeamConfig
        team_config = self.template_manager.dict_to_team_config(team_template_dict)

        # Ensure source_path is set (may be missing from old memory data)
        if not team_config.source_path and team_config.id:
            try:
                # Look up the actual template file path
                original_template = self.template_manager.get_template(team_config.id)
                if original_template and original_template.source_path:
                    team_config.source_path = original_template.source_path
                    # Update memory with source_path for future loads
                    updated_team_template = copy.deepcopy(team_template_dict)
                    updated_team_template["source_path"] = original_template.source_path
                    memory.set_metadata("team_template", updated_team_template)
                    team_template_dict = updated_team_template
                    logger.info(f"Updated memory with source_path: {original_template.source_path}")
            except Exception as e:
                logger.debug(f"Could not look up source_path for template {team_config.id}: {e}")

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

        # ===== STEP 4: Ensure plugins are ready (and init learning team) =====
        plugins = await self._ensure_plugins(endpoint_service=endpoint_service)
        
        # ===== STEP 5: Create and setup team with plugins =====
        team = PantheonTeam(
            agents=all_agents,
            plugins=plugins,
        )
        await team.async_setup()

        # Store source path for template persistence
        team._source_path = team_config.source_path

        num_agents = len(team.team_agents)
        features = f"{num_agents} agents" if num_agents > 1 else "single agent"

        logger.info(f"✅ Team '{template_name}' created (Features: {features})")
        return team

    @tool
    async def setup_team_for_chat(self, chat_id: str, template_obj: dict, save_to_memory: bool = True):
        """Setup/update team for a specific chat using full template object."""
        try:
            logger.info(
                f"Setting up team for chat {chat_id} with template: {template_obj.get('name', 'unknown')}"
            )

            # Store full template in memory using consolidated method
            # Read-only: storing template, no need to fix
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            self._save_team_template_to_memory(memory, template_obj)

            memory.delete_metadata("active_agent")
            
            if save_to_memory:
                # Optionally: use save_one for immediate persistence of just this chat
                # await run_func(self.memory_manager.save_one, chat_id)
                pass

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

    def _get_gateway_manager(self):
        if self._gateway_channel_manager is None:
            from pantheon.claw import GatewayChannelManager

            self._gateway_channel_manager = GatewayChannelManager(
                chatroom=self,
                loop=asyncio.get_running_loop(),
            )
        return self._gateway_channel_manager

    @tool
    async def get_gateway_channel_config(self) -> dict:
        manager = self._get_gateway_manager()
        return {
            "success": True,
            "config": manager.get_config(masked=True),
            "channels": manager.list_states(),
        }

    @tool
    async def save_gateway_channel_config(self, config: dict) -> dict:
        manager = self._get_gateway_manager()
        manager.save_config(config)
        return {
            "success": True,
            "config": manager.get_config(masked=True),
            "channels": manager.list_states(),
        }

    @tool
    async def list_gateway_channels(self) -> dict:
        manager = self._get_gateway_manager()
        return {
            "success": True,
            "channels": manager.list_states(),
        }

    @tool
    async def start_gateway_channel(self, channel: str) -> dict:
        manager = self._get_gateway_manager()
        result = manager.start_channel(channel)
        return {
            "success": bool(result.get("ok")),
            **result,
            "channels": manager.list_states(),
        }

    @tool
    async def stop_gateway_channel(self, channel: str) -> dict:
        manager = self._get_gateway_manager()
        result = manager.stop_channel(channel)
        return {
            "success": bool(result.get("ok")),
            **result,
            "channels": manager.list_states(),
        }

    @tool
    async def get_gateway_channel_logs(self, channel: str) -> dict:
        manager = self._get_gateway_manager()
        return {
            "success": True,
            "channel": channel,
            "logs": manager.get_logs(channel),
        }

    @tool
    async def wechat_login_qr(self) -> dict:
        manager = self._get_gateway_manager()
        try:
            result = await asyncio.to_thread(manager.wechat_get_login_qr)
            return {"success": True, **result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @tool
    async def wechat_login_status(self, qrcode_id: str) -> dict:
        manager = self._get_gateway_manager()
        try:
            result = await asyncio.to_thread(manager.wechat_poll_login_status, qrcode_id)
            return {"success": True, **result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @tool
    async def list_gateway_sessions(self) -> dict:
        manager = self._get_gateway_manager()
        return {
            "success": True,
            "sessions": await manager.list_sessions(),
        }

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

            # Inject workdir from project metadata if session is in isolated mode
            session_id = (args or {}).get("session_id") or getattr(self, '_current_chat_id', None)
            # Special '__global__' session_id: explicitly use project root (clear any workdir)
            if session_id == '__global__':
                from pantheon.toolset import get_current_context_variables
                ctx = get_current_context_variables()
                if ctx is not None:
                    ctx.pop("workdir", None)
                session_id = None
            if session_id:
                try:
                    memory = await run_func(self.memory_manager.get_memory, session_id)
                    project = memory.extra_data.get("project", {})
                    if isinstance(project, dict):
                        workspace_mode = project.get("workspace_mode",
                            "isolated" if project.get("workspace_path") else "project")
                        workspace_path = project.get("workspace_path")
                        from pantheon.toolset import get_current_context_variables
                        ctx = get_current_context_variables()
                        if ctx is not None:
                            if workspace_mode == "isolated" and workspace_path:
                                ctx["workdir"] = workspace_path
                            else:
                                # Clear workdir so toolset falls back to project root
                                ctx.pop("workdir", None)
                except Exception as e:
                    logger.debug(f"Could not inject workdir for session {session_id}: {e}")

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
                "model": agent.models[0] if agent.models else None,
                "models": agent.models,
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

        try:
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
        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_id}' not found",
            }

    @tool
    async def set_active_agent(self, chat_name: str, agent_name: str):
        """Set the active agent for a chat."""
        try:
            # Get the team for this specific chat
            team = await self.get_team_for_chat(chat_name)
        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_name}' not found",
            }

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

        # Read-only: setting active agent, no need to fix
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
        try:
            # Get the team for this specific chat
            team = await self.get_team_for_chat(chat_name)
            # Read-only: getting active agent, no need to fix
            memory = await run_func(self.memory_manager.get_memory, chat_name)
            active_agent = team.get_active_agent(memory)
            return {
                "success": True,
                "agent": active_agent.name,
            }
        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_name}' not found",
            }

    @tool
    async def create_chat(
        self,
        chat_name: str | None = None,
        project_name: str | None = None,
        workspace_path: str | None = None,
        workspace_mode: str = "project",
    ) -> dict:
        """Create a new chat.

        Args:
            chat_name: The name of the chat.
            project_name: Optional project name for grouping.
            workspace_path: Optional workspace directory path.
            workspace_mode: Workspace mode - "project" (shared, default) or "isolated" (per-chat).
        """
        memory = await run_func(self.memory_manager.new_memory, chat_name)
        memory.set_metadata("last_activity_date", datetime.now().isoformat())

        if workspace_path:
            # Explicit path provided — always isolated
            workspace_mode = "isolated"
            import os
            try:
                os.makedirs(workspace_path, exist_ok=True)
                logger.info(f"Ensured workspace directory exists: {workspace_path}")
            except Exception as e:
                logger.warning(f"Failed to create workspace directory {workspace_path}: {e}")
        elif workspace_mode == "isolated":
            # Create per-session workspace
            settings = get_settings()
            session_workspace_dir = settings.pantheon_dir / "workspaces" / memory.id
            try:
                session_workspace_dir.mkdir(parents=True, exist_ok=True)
                workspace_path = str(session_workspace_dir)
                logger.info(f"Created session workspace directory: {workspace_path}")
            except Exception as e:
                logger.warning(f"Failed to create session workspace directory: {e}")
                workspace_mode = "project"  # Fallback to project mode

        # Set project metadata
        project = {}
        if project_name:
            project["name"] = project_name
        project["workspace_mode"] = workspace_mode
        if workspace_path:
            project["workspace_path"] = workspace_path
            project["original_cwd"] = str(get_settings().workspace)
        if project:
            memory.set_metadata("project", project)

        return {
            "success": True,
            "message": "Chat created successfully",
            "chat_name": memory.name,
            "chat_id": memory.id,
            "workspace_mode": workspace_mode,
            "workspace_path": workspace_path,
        }

    @tool
    async def delete_chat(self, chat_id: str):
        """Delete a chat.

        Args:
            chat_id: The ID of the chat.
        """
        import shutil

        try:
            # Check if chat has an isolated workspace to clean up
            workspace_path_to_delete = None
            try:
                memory = await run_func(self.memory_manager.get_memory, chat_id)
                project = memory.extra_data.get("project", {})
                if isinstance(project, dict):
                    workspace_mode = project.get("workspace_mode",
                        "isolated" if project.get("workspace_path") else "project")
                    workspace_path = project.get("workspace_path")
                    if workspace_mode == "isolated" and workspace_path:
                        settings = get_settings()
                        workspaces_dir = settings.pantheon_dir / "workspaces"
                        workspace_path_obj = Path(workspace_path)
                        try:
                            workspace_path_obj.relative_to(workspaces_dir)
                            workspace_path_to_delete = workspace_path_obj
                        except ValueError:
                            pass  # Not under .pantheon/workspaces/, don't delete
            except Exception as e:
                logger.debug(f"Could not get workspace path for chat {chat_id}: {e}")

            await run_func(self.memory_manager.delete_memory, chat_id)

            # Clean up isolated workspace directory
            if workspace_path_to_delete and workspace_path_to_delete.exists():
                try:
                    shutil.rmtree(workspace_path_to_delete)
                    logger.info(f"Deleted session workspace: {workspace_path_to_delete}")
                except Exception as e:
                    logger.warning(f"Failed to delete workspace folder {workspace_path_to_delete}: {e}")

            return {"success": True, "message": "Chat deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting chat: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def list_chats(self, project_name: str | None = None) -> dict:
        """List all the chats, optionally filtered by project.

        Args:
            project_name: Optional project name to filter chats.
                          When provided, only chats belonging to this project are returned.

        Returns:
            A dictionary with the following keys:
            - success: Whether the operation was successful.
            - chats: A list of dictionaries, each containing the info of a chat.
        """
        try:
            ids = await run_func(self.memory_manager.list_memories)
            chats = []
            skipped_chats = []
            for id in ids:
                # Read-only: listing chats, no need to fix.
                # Skip corrupted entries so one broken metadata file doesn't hide all chats.
                try:
                    memory = await run_func(self.memory_manager.get_memory, id)
                except KeyError as e:
                    logger.warning(f"Skipping unreadable chat {id}: {e}")
                    skipped_chats.append({"id": id, "message": str(e)})
                    continue
                project = memory.extra_data.get("project", None)

                # Filter by project_name if specified
                if project_name is not None:
                    chat_project_name = project.get("name") if isinstance(project, dict) else None
                    if chat_project_name != project_name:
                        continue

                chats.append(
                    {
                        "id": id,
                        "name": memory.name,
                        "running": memory.extra_data.get("running", False),
                        "last_activity_date": memory.extra_data.get(
                            "last_activity_date", None
                        ),
                        "project": project,
                        "memory_path": memory.file_path,
                    }
                )

            chats.sort(
                key=lambda x: datetime.fromisoformat(x["last_activity_date"])
                if x["last_activity_date"]
                else datetime.min,
                reverse=True,
            )

            result = {
                "success": True,
                "chats": chats,
            }
            if skipped_chats:
                result["skipped_chats"] = skipped_chats
            return result
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
            # Frontend query: skip auto-fix for better performance (5-10x faster)
            # Messages will be fixed automatically when agent execution starts
            memory = await run_func(self.memory_manager.get_memory, chat_id)

            # Sync _current_chat_id to keep backend state aligned with UI
            self._current_chat_id = chat_id

            # Get full raw history for UI
            messages = await run_func(memory.get_messages, _ALL_CONTEXTS, False)

            # Defensive check: ensure messages is a list
            if messages is None:
                messages = []

            # Always sanitize messages for transport (NATS payload limit)
            import json as _json
            MAX_RAW_CONTENT_SIZE = 50000  # 50KB per raw_content
            MAX_FIELD_LENGTH = 10000
            for message in messages:
                if "raw_content" in message:
                    if isinstance(message["raw_content"], dict):
                        if filter_out_images and "base64_uri" in message["raw_content"]:
                            del message["raw_content"]["base64_uri"]
                        for _k in ("stdout", "stderr"):
                            if _k in message["raw_content"]:
                                message["raw_content"][_k] = message["raw_content"][_k][:MAX_FIELD_LENGTH]
                    # Drop raw_content entirely if still too large
                    try:
                        rc_size = len(_json.dumps(message["raw_content"], ensure_ascii=False))
                        if rc_size > MAX_RAW_CONTENT_SIZE:
                            del message["raw_content"]
                    except (TypeError, ValueError):
                        pass
            return {"success": True, "messages": messages}
        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_id}' not found",
                "messages": []  # Always include messages field
            }
        except Exception as e:
            logger.error(f"Error getting chat messages: {e}")
            return {"success": False, "message": str(e), "messages": []}  # Always include messages field

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
    async def set_chat_workspace_mode(
        self,
        chat_id: str,
        workspace_mode: str,
    ) -> dict:
        """Toggle workspace mode for a chat.

        Args:
            chat_id: The chat ID.
            workspace_mode: "project" (shared) or "isolated" (per-chat).

        Returns:
            A dictionary with success status, workspace_mode, and workspace_path.
        """
        if workspace_mode not in ("project", "isolated"):
            return {"success": False, "message": "workspace_mode must be 'project' or 'isolated'"}

        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            project = copy.deepcopy(memory.extra_data.get("project", {}))
            if not isinstance(project, dict):
                project = {}

            workspace_path = project.get("workspace_path")

            if workspace_mode == "isolated" and not workspace_path:
                # Create per-session workspace if switching to isolated
                settings = get_settings()
                session_workspace_dir = settings.pantheon_dir / "workspaces" / chat_id
                try:
                    session_workspace_dir.mkdir(parents=True, exist_ok=True)
                    workspace_path = str(session_workspace_dir)
                    project["workspace_path"] = workspace_path
                    project["original_cwd"] = str(settings.workspace)
                    logger.info(f"Created workspace for chat {chat_id}: {workspace_path}")
                except Exception as e:
                    logger.warning(f"Failed to create workspace for chat {chat_id}: {e}")
                    return {"success": False, "message": f"Failed to create workspace: {e}"}

            project["workspace_mode"] = workspace_mode
            memory.set_metadata("project", project)

            return {
                "success": True,
                "message": f"Workspace mode set to '{workspace_mode}'",
                "workspace_mode": workspace_mode,
                "workspace_path": workspace_path if workspace_mode == "isolated" else None,
            }
        except Exception as e:
            logger.error(f"Error setting workspace mode: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def set_chat_project(
        self,
        chat_id: str,
        project_name: str | None = None,
        workspace_path: str | None = None,
        workspace_mode: str | None = None,
        **kwargs,
    ) -> dict:
        """Set or update project metadata for a chat.

        Args:
            chat_id: The ID of the chat.
            project_name: Project name (None to remove project).
            workspace_path: Optional workspace directory path.
            workspace_mode: Optional workspace mode ("project" or "isolated").
            **kwargs: Additional project metadata (color, icon, etc.)

        Returns:
            A dictionary with success status and message.
        """
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)

            if project_name is None and workspace_path is None and workspace_mode is None and not kwargs:
                # Remove project metadata
                memory.delete_metadata("project")
                message = "Project metadata removed"
            else:
                # Create or update project object
                project = copy.deepcopy(memory.extra_data.get("project", {}))
                if not isinstance(project, dict):
                    project = {}

                if project_name is not None:
                    project["name"] = project_name
                if workspace_path is not None:
                    project["workspace_path"] = workspace_path
                    project.setdefault("original_cwd", str(get_settings().workspace))
                if workspace_mode is not None:
                    project["workspace_mode"] = workspace_mode

                for key, value in kwargs.items():
                    if value is not None:
                        project[key] = value

                memory.set_metadata("project", project)
                message = f"Project metadata updated for chat"
            return {"success": True, "message": message}
        except Exception as e:
            logger.error(f"Error setting chat project: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def revert_to_message(self, chat_id: str, message_id: str) -> dict:
        """Revert chat memory to a specific message by ID.
        
        This will delete the message with the given ID and all subsequent messages.
        The revert operation only affects conversation memory and does NOT revert
        file changes or other external states.
        
        Args:
            chat_id: The ID of the chat.
            message_id: The ID of the message to revert to (inclusive deletion).
            
        Returns:
            A dictionary with:
            - success: Whether the operation was successful
            - message: Status message
            - reverted_content: Content of the deleted user message (if applicable)
        """
        try:
            # Read-only: reverting message, no need to fix
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            
            # Find the index of the message with the given ID
            message_index = None
            reverted_message = None
            
            for idx, msg in enumerate(memory._messages):
                if msg.get("id") == message_id:
                    message_index = idx
                    # Store the full message for frontend to parse
                    if msg.get("role") == "user":
                        reverted_message = msg
                    break
            
            if message_index is None:
                return {
                    "success": False,
                    "message": f"Message with ID '{message_id}' not found in chat history"
                }
            
            # Perform the revert
            await run_func(memory.revert_to_message, message_index)
            
            logger.info(f"Reverted chat {chat_id} to state before message {message_id} (index {message_index})")
            
            return {
                "success": True,
                "message": f"Successfully reverted to state before message {message_id}",
                "reverted_message": reverted_message
            }
        except Exception as e:
            logger.error(f"Error reverting chat {chat_id}: {e}")
            return {"success": False, "message": str(e)}

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

            preferred_model = None
            try:
                team = await self.get_team_for_chat(memory.id)
                active_agent = team.get_active_agent(memory)
                preferred_model = (
                    active_agent.models[0] if getattr(active_agent, "models", None) else None
                )
            except Exception:
                preferred_model = None

            chat_name_generator = get_chat_name_generator()
            new_name = await chat_name_generator.generate_or_update_name(
                memory,
                preferred_model=preferred_model,
            )
            if new_name and new_name != memory.name:
                memory.name = new_name
                # Save only this chat's memory
                await run_func(self.memory_manager.save_one, memory.id)
                logger.debug(f"Chat renamed in background to: {new_name}")
                # Notify frontend via NATS
                if self._nats_adapter is not None:
                    await self._nats_adapter.publish(
                        memory.id, "chat_renamed",
                        {"type": "chat_renamed", "chat_id": memory.id, "name": new_name},
                    )
        except Exception as e:
            logger.error(f"Background chat rename failed: {e}")

    def _setup_bg_auto_notify(self, chat_id: str, team):
        """Wire bg task completion to auto-trigger a new chat turn.

        When a background task completes after chat() has returned (agent idle),
        this schedules a new chat() call with a notification message so the
        agent automatically reports results to the user/frontend.

        If chat() is still running (agent busy), the notification is handled
        by the existing ephemeral injection in Agent._run_stream instead.
        """
        chatroom_self = self

        def _on_bg_complete(bg_task):
            status = bg_task.status
            result_preview = ""
            if bg_task.result is not None:
                result_preview = str(bg_task.result)[:200]
            elif bg_task.error:
                result_preview = bg_task.error[:200]

            notif_text = (
                f"<bg_task_notification>"
                f"[Background task '{bg_task.task_id}' ({bg_task.tool_name}) "
                f"{status}. Result: {result_preview}]"
                f"</bg_task_notification>"
            )

            async def _auto_chat():
                try:
                    await chatroom_self.chat(
                        chat_id=chat_id,
                        message=[{"role": "user", "content": notif_text}],
                    )
                except Exception as e:
                    logger.warning(f"Auto bg notification chat failed: {e}")

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_auto_chat())
            except RuntimeError:
                pass

        for agent in team.agents.values():
            if hasattr(agent, "_bg_manager"):
                # Only set if no external consumer (REPL, SDK) has already wired it
                if agent._bg_manager.on_complete is None:
                    agent_name = agent.name

                    def _on_bg_complete_with_notify(bg_task, _agent_name=agent_name):
                        _on_bg_complete(bg_task)
                        # Publish NATS stream event for UI real-time updates
                        if chatroom_self._nats_adapter is not None:
                            async def _publish():
                                await chatroom_self._nats_adapter.publish(
                                    chat_id, "bg_task_update",
                                    {
                                        "type": "bg_task_update",
                                        "task_id": bg_task.task_id,
                                        "tool_name": bg_task.tool_name,
                                        "status": bg_task.status,
                                        "agent_name": _agent_name,
                                    },
                                )
                            try:
                                loop = asyncio.get_running_loop()
                                loop.create_task(_publish())
                            except RuntimeError:
                                pass

                    agent._bg_manager.on_complete = _on_bg_complete_with_notify

    @tool
    async def list_background_tasks(self, chat_id: str) -> dict:
        """List all background tasks across all agents for a chat.

        Args:
            chat_id: The ID of the chat.
        """
        try:
            team = await self.get_team_for_chat(chat_id, save_to_memory=False)
            tasks = []
            for agent in team.agents.values():
                if hasattr(agent, "_bg_manager"):
                    for t in agent._bg_manager.list_tasks():
                        summary = agent._bg_manager.to_summary(t)
                        summary["agent_name"] = agent.name
                        tasks.append(summary)
            return {"success": True, "tasks": tasks}
        except Exception as e:
            logger.error(f"Error listing background tasks: {e}")
            return {"success": False, "message": str(e), "tasks": []}

    @tool
    async def get_background_task_detail(self, chat_id: str, task_id: str) -> dict:
        """Get detailed info for a specific background task.

        Args:
            chat_id: The ID of the chat.
            task_id: The ID of the background task.
        """
        try:
            team = await self.get_team_for_chat(chat_id, save_to_memory=False)
            for agent in team.agents.values():
                if hasattr(agent, "_bg_manager"):
                    t = agent._bg_manager.get(task_id)
                    if t is not None:
                        summary = agent._bg_manager.to_summary(t)
                        summary["agent_name"] = agent.name
                        summary["output_lines"] = t.output_lines
                        summary["args"] = t.args
                        return {"success": True, "task": summary}
            return {"success": False, "message": f"Task '{task_id}' not found"}
        except Exception as e:
            logger.error(f"Error getting background task detail: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def cancel_background_task(self, chat_id: str, task_id: str) -> dict:
        """Cancel a running background task.

        Args:
            chat_id: The ID of the chat.
            task_id: The ID of the background task to cancel.
        """
        try:
            team = await self.get_team_for_chat(chat_id, save_to_memory=False)
            for agent in team.agents.values():
                if hasattr(agent, "_bg_manager"):
                    t = agent._bg_manager.get(task_id)
                    if t is not None:
                        result = agent._bg_manager.cancel(task_id)
                        if result:
                            return {"success": True, "message": f"Task '{task_id}' cancelled"}
                        else:
                            return {"success": False, "message": f"Task '{task_id}' could not be cancelled (already finished?)"}
            return {"success": False, "message": f"Task '{task_id}' not found"}
        except Exception as e:
            logger.error(f"Error cancelling background task: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def remove_background_task(self, chat_id: str, task_id: str) -> dict:
        """Remove a background task from the manager.

        Args:
            chat_id: The ID of the chat.
            task_id: The ID of the background task to remove.
        """
        try:
            team = await self.get_team_for_chat(chat_id, save_to_memory=False)
            for agent in team.agents.values():
                if hasattr(agent, "_bg_manager"):
                    t = agent._bg_manager.get(task_id)
                    if t is not None:
                        result = agent._bg_manager.remove(task_id)
                        if result:
                            return {"success": True, "message": f"Task '{task_id}' removed"}
                        else:
                            return {"success": False, "message": f"Task '{task_id}' could not be removed"}
            return {"success": False, "message": f"Task '{task_id}' not found"}
        except Exception as e:
            logger.error(f"Error removing background task: {e}")
            return {"success": False, "message": str(e)}

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
        try:
            # CRITICAL: Agent execution - MUST fix messages for LLM API
            memory = await run_func(self.memory_manager.get_memory, chat_id, True)
        except KeyError:
            return {"success": False, "message": f"Chat '{chat_id}' not found"}
        memory.update_metadata({
            "running": True,
            "last_activity_date": datetime.now().isoformat(),
        })

        async def team_getter():
            return await self.get_team_for_chat(chat_id)

        # Wire bg task auto-notification for this chat
        # Resolve team early so we can set on_complete hooks before agent runs
        team = await self.get_team_for_chat(chat_id)
        self._setup_bg_auto_notify(chat_id, team)

        # Inject workdir from project metadata if in isolated mode
        project = memory.extra_data.get("project", {})
        workspace_path = None
        if isinstance(project, dict):
            workspace_mode = project.get("workspace_mode",
                "isolated" if project.get("workspace_path") else "project")
            workspace_path = project.get("workspace_path")
            if workspace_mode == "isolated" and workspace_path:
                context_variables = context_variables or {}
                context_variables["workdir"] = workspace_path

        # Set up a designated image output directory so agents save images
        # to a known location and claw channels can detect them cheaply.
        from pantheon.utils.image_detection import (
            IMAGE_OUTPUT_DIR, snapshot_images, diff_snapshots, encode_images_to_uris,
        )
        image_output_path: str | None = None
        if workspace_path:
            import os
            image_output_path = os.path.join(workspace_path, IMAGE_OUTPUT_DIR)
            os.makedirs(image_output_path, exist_ok=True)
            context_variables = context_variables or {}
            context_variables["image_output_dir"] = image_output_path

        # Pre-snapshot: only scan the designated image output directory
        pre_image_snapshot = snapshot_images(image_output_path) if image_output_path else {}

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

        # Generate chat name as soon as user message arrives (non-blocking).
        # No need to wait for agent response — user message is enough context.
        # Only fires if the chat still has a default name (e.g. "New Chat").
        if self._enable_auto_chat_name:
            task = asyncio.create_task(self._background_rename_chat(memory))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        try:
            await thread.run()

            # Post-execution image detection: scan the designated image
            # output directory for any newly created images.
            if image_output_path and pre_image_snapshot is not None:
                post_image_snapshot = snapshot_images(image_output_path)
                new_image_paths = diff_snapshots(pre_image_snapshot, post_image_snapshot)
                if new_image_paths:
                    uris = encode_images_to_uris(new_image_paths)
                    if uris:
                        await thread.process_step_message({
                            "role": "tool",
                            "raw_content": {"base64_uri": uris},
                        })

            # Publish chat finished message if NATS streaming enabled
            if self._nats_adapter is not None:
                resp = thread.response or {}
                if resp.get("success") is False:
                    # Send error to frontend so it can display to user
                    error_msg = resp.get("message", "Unknown error")
                    await self._nats_adapter.publish(
                        chat_id, "chat_finished",
                        {
                            "type": "chat_finished",
                            "status": "error",
                            "metadata": {"message": error_msg},
                        },
                    )
                else:
                    await self._nats_adapter.publish_chat_finished(chat_id)

            return thread.response
        except asyncio.CancelledError:
            logger.info(f"Chat {chat_id} was cancelled/interrupted")
            raise  # Re-raise to propagate cancellation
        finally:
            # Always clean up the thread from the registry FIRST
            # This ensures subsequent chat attempts can proceed even if cleanup is interrupted
            if chat_id in self.threads:
                del self.threads[chat_id]

            # Protect persistent state updates from cancellation
            async def _cleanup_persistent_state():
                memory.update_metadata({
                    "running": False,
                    "last_activity_date": datetime.now().isoformat(),
                })
                try:
                    await run_func(self.memory_manager.save_one, chat_id)
                except Exception as e:
                    logger.error(f"Failed to save memory on cleanup: {e}")

            await asyncio.shield(_cleanup_persistent_state())

    @tool
    async def stop_chat(self, chat_id: str):
        """Stop a chat.

        Args:
            chat_id: The ID of the chat.
        """
        thread = self.threads.get(chat_id, None)
        if thread is None:
            return {"success": True, "message": "Chat already stopped"}
        await thread.stop()
        # Note: Thread cleanup from self.threads happens in chat()'s finally block
        # But if called externally, we ensure cleanup here as well
        if chat_id in self.threads:
            del self.threads[chat_id]
        return {"success": True, "message": "Chat stopped successfully"}

    @tool
    async def speech_to_text(self, bytes_data):
        """Convert speech to text.

        Args:
            bytes_data: The bytes data of the audio (bytes, base64 string, or list).
        """
        try:
            import base64
            from pantheon.utils.adapters import get_adapter
            from pantheon.utils.llm_providers import get_llm_proxy_config

            logger.info(f"[STT] Received bytes_data type={type(bytes_data).__name__}, "
                        f"len={len(bytes_data) if hasattr(bytes_data, '__len__') else 'N/A'}")

            # Normalize bytes_data: JSON transport may encode bytes as list/dict/base64
            if isinstance(bytes_data, str):
                bytes_data = base64.b64decode(bytes_data)
            elif isinstance(bytes_data, list):
                bytes_data = bytes(bytes_data)
            elif isinstance(bytes_data, dict):
                if "data" in bytes_data:
                    data = bytes_data["data"]
                    if isinstance(data, list):
                        bytes_data = bytes(data)
                    elif isinstance(data, str):
                        bytes_data = base64.b64decode(data)
                    else:
                        bytes_data = bytes(data)
                else:
                    bytes_data = bytes(bytes_data[str(i)] for i in range(len(bytes_data)))

            logger.info(f"[STT] Audio bytes size: {len(bytes_data)}, "
                        f"model: {self.speech_to_text_model}")

            if len(bytes_data) == 0:
                return {"success": False, "text": "Empty audio data"}

            # Create a BytesIO object with webm format (browser MediaRecorder default)
            audio_file = io.BytesIO(bytes_data)
            audio_file.name = "audio.webm"

            logger.info("[STT] Calling transcription adapter...")
            _proxy_base, _proxy_key = get_llm_proxy_config()
            adapter = get_adapter("openai")
            response = await asyncio.wait_for(
                adapter.atranscription(
                    model=self.speech_to_text_model,
                    file=audio_file,
                    base_url=_proxy_base or None,
                    api_key=_proxy_key or None,
                ),
                timeout=30,
            )
            logger.info(f"[STT] Transcription result: {response.text[:100] if response.text else '(empty)'}")

            return {
                "success": True,
                "text": response.text,
            }

        except asyncio.TimeoutError:
            logger.error("[STT] Transcription timed out (30s)")
            return {"success": False, "text": "Transcription timed out"}
        except Exception as e:
            logger.error(f"[STT] Error transcribing speech: {e}")
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
            # Read-only: getting suggestions, no need to fix
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            # Use for_llm=False to skip unnecessary LLM processing (compression truncation, etc.)
            messages = memory.get_messages(None, for_llm=False)

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
            preferred_model = None
            try:
                team = await self.get_team_for_chat(chat_id)
                active_agent = team.get_active_agent(memory)
                preferred_model = (
                    active_agent.models[0] if getattr(active_agent, "models", None) else None
                )
            except Exception:
                preferred_model = None
            suggestions_objects = await suggestion_generator.generate_suggestions(
                formatted_messages,
                preferred_model=preferred_model,
            )

            # Convert to dict format
            suggestions = [
                {"text": s.text, "category": s.category} for s in suggestions_objects
            ]

            # Cache suggestions in memory
            if suggestions:
                memory.update_metadata({
                    "cached_suggestions": suggestions,
                    "last_suggestion_message_count": len(messages),
                    "suggestions_generated_at": datetime.now().isoformat(),
                })

            logger.debug(f"Generated {len(suggestions)} suggestions for chat {chat_id}")

            return {
                "success": True,
                "suggestions": suggestions,
                "chat_id": chat_id,
                "from_cache": False,
            }

        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_id}' not found",
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
            # Read-only: getting template, no need to fix
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
        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_id}' not found",
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

    # Model Management Methods

    @tool
    async def list_available_models(self) -> dict:
        """List all available models based on configured API keys.

        Returns models grouped by provider. Only providers with valid API keys
        are included.

        Returns:
            {
                "success": True,
                "available_providers": ["openai", "anthropic"],
                "current_provider": "openai",
                "models_by_provider": {
                    "openai": ["openai/gpt-5.4", "openai/gpt-5.2", ...],
                    "anthropic": ["anthropic/claude-opus-4-5-20251101", ...]
                },
                "supported_tags": ["high", "normal", "low", "vision", ...]
            }
        """
        try:
            from pantheon.utils.model_selector import get_model_selector

            selector = get_model_selector()
            # Clear provider cache so dynamic providers (Ollama, OAuth) are re-detected
            selector._available_providers = None
            selector._detected_provider = None
            return selector.list_available_models()
        except Exception as e:
            logger.error(f"Error listing available models: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def set_agent_model(
        self,
        chat_id: str,
        agent_name: str,
        model: str,
        validate: bool = True,
    ) -> dict:
        """Set the model for an agent in a specific chat.

        Args:
            chat_id: The chat ID.
            agent_name: The name of the agent to update.
            model: Model name (e.g., "openai/gpt-4o") or tag (e.g., "high", "normal,vision").
            validate: If True, verify that the provider has a valid API key.

        Returns:
            {
                "success": True,
                "agent": "assistant",
                "model": "high",
                "resolved_models": ["openai/gpt-5.4", "openai/gpt-5.2", ...]
            }
        """
        try:
            from pantheon.agent import _is_model_tag, _resolve_model_tag, _parse_thinking_suffix
            from pantheon.utils.model_selector import get_model_selector

            # 1. Get team and find target agent
            team = await self.get_team_for_chat(chat_id)
            target_agent = next(
                (a for a in team.team_agents if a.name == agent_name),
                None,
            )
            if target_agent is None:
                return {
                    "success": False,
                    "message": f"Agent '{agent_name}' not found in chat '{chat_id}'",
                }

            # 2. Parse +think suffix (e.g. "high+think:medium" → thinking="medium")
            clean_model, thinking = _parse_thinking_suffix(model)

            # 3. Validate provider if requested
            if validate:
                is_valid, error_msg = self._validate_model_provider(clean_model)
                if not is_valid:
                    return {"success": False, "message": error_msg}

            # 4. Resolve model to list
            if _is_model_tag(clean_model):
                resolved_models = _resolve_model_tag(clean_model)
            else:
                resolved_models = [clean_model]

            # 5. Update runtime agent
            target_agent.models = resolved_models
            if thinking:
                target_agent.model_params["thinking"] = thinking
            else:
                target_agent.model_params.pop("thinking", None)

            # 5. Persist to template file (if source_path exists)
            source_path = getattr(team, "_source_path", None)
            if not source_path:
                # Fallback: look up source_path from template manager
                team_id = getattr(team, "_team_id", None) or "default"
                try:
                    original = self.template_manager.get_template(team_id)
                    if original and original.source_path:
                        source_path = original.source_path
                        team._source_path = source_path
                except Exception:
                    pass
            if source_path:
                from pathlib import Path

                template_path = Path(source_path)
                if template_path.exists():
                    try:
                        # Read original template (without resolving refs to preserve structure)
                        file_manager = self.template_manager.file_manager
                        original_team = file_manager._read_team_from_path(template_path)

                        # Update the agent's model in template
                        # Compare case-insensitively: runtime agent name may differ
                        # in casing from the template id (e.g. "Leader" vs "leader")
                        agent_name_lower = agent_name.lower()
                        for agent_cfg in original_team.agents:
                            if (agent_cfg.name or "").lower() == agent_name_lower or (agent_cfg.id or "").lower() == agent_name_lower:
                                agent_cfg.model = model  # Store original input (tag or model name)
                                break

                        # Write back to template file
                        file_manager._write_team_file(
                            original_team, template_path, overwrite=True
                        )
                        logger.info(f"Persisted model to template file: {source_path}")
                    except Exception as e:
                        logger.warning(f"Failed to persist model to template file: {e}")

            # Also update memory template for current session
            # Read-only: updating model config, no need to fix
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            team_template = copy.deepcopy(memory.extra_data.get("team_template", {}))

            # Update the agent's model in template (case-insensitive match)
            for agent_config in team_template.get("agents", []):
                if (agent_config.get("name") or "").lower() == agent_name_lower or (agent_config.get("id") or "").lower() == agent_name_lower:
                    agent_config["model"] = (
                        model  # Store original input (tag or model name)
                    )
                    break

            memory.set_metadata("team_template", team_template)

            logger.info(
                f"Set model for agent '{agent_name}' in chat '{chat_id}': {model} -> {resolved_models}"
            )

            return {
                "success": True,
                "agent": agent_name,
                "model": model,
                "resolved_models": resolved_models,
            }

        except Exception as e:
            logger.error(f"Error setting agent model: {e}")
            return {"success": False, "message": str(e)}


    @tool
    async def get_token_stats(self, chat_id: str, model: str | None = None) -> dict:
        """Get detailed token usage statistics for a chat.

        Returns token breakdown by role (system/user/assistant/tool),
        usage percentage, cost, model info, and context window utilization.

        Args:
            chat_id: The chat to get token stats for
            model: Optional model override (e.g. the model currently selected in the UI).
                   When provided, used for catalog lookup instead of agent.models[0].

        Returns:
            dict with success status and token statistics
        """
        try:
            team = await self.get_team_for_chat(chat_id)
            from pantheon.repl.utils import get_detailed_token_stats

            token_info = await get_detailed_token_stats(
                chatroom=self,
                chat_id=chat_id,
                team=team,
                fallback={},
                model_override=model,
            )
            return {"success": True, **token_info}
        except Exception as e:
            logger.error(f"Error getting token stats: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def compress_chat(self, chat_id: str) -> dict:
        """Trigger context compression for a chat.
        
        Args:
            chat_id: The chat to compress
            
        Returns:
            dict with success status and compression details
        """
        try:
            team = await self.get_team_for_chat(chat_id)
            # CRITICAL: Compression may need valid messages for LLM API
            memory = await run_func(self.memory_manager.get_memory, chat_id, True)
            
            if not hasattr(team, 'force_compress'):
                return {"success": False, "message": "Team does not support compression"}
            
            result = await team.force_compress(memory)
            
            # Save memory to persist compression changes
            if result.get("success"):
                await run_func(self.memory_manager.save_one, chat_id)
                logger.info(f"Manual compression completed for chat {chat_id}")
            
            return result
        except Exception as e:
            logger.error(f"Error compressing chat: {e}")
            return {"success": False, "message": str(e)}

    def _validate_model_provider(self, model: str) -> tuple[bool, str]:
        """Validate that the provider for a model has a valid API key.

        Args:
            model: Model name or tag.

        Returns:
            (is_valid, error_message)
        """
        from pantheon.agent import _is_model_tag
        from pantheon.utils.model_selector import get_model_selector

        # Tags are always valid (they resolve based on available providers)
        if _is_model_tag(model):
            return True, ""

        selector = get_model_selector()
        available = selector._get_available_providers()

        # Extract provider from model name
        if "/" in model:
            provider = model.split("/")[0]
            # Handle provider aliases
            provider_aliases = {
                "google": "gemini",
                "vertex_ai": "gemini",
            }
            provider = provider_aliases.get(provider, provider)

            if provider not in available:
                return False, f"Provider '{provider}' not available (missing credentials)"

        return True, ""

    @staticmethod
    def _get_store_installs_path():
        """Get path to local store installs manifest."""
        from pathlib import Path
        return Path.home() / ".pantheon" / "store_installs.json"

    def _load_store_installs(self) -> dict:
        """Load local store installs manifest."""
        import json
        path = self._get_store_installs_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_store_installs(self, data: dict):
        """Save local store installs manifest."""
        import json
        path = self._get_store_installs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @tool
    async def install_store_package(self, package_id: str, version: str = None) -> dict:
        """Install a package from the Pantheon Store.

        Args:
            package_id: The ID of the package to install.
            version: Optional specific version to install.
        """
        from pantheon.store.client import StoreClient
        from pantheon.store.installer import PackageInstaller

        try:
            client = StoreClient()
            dl = await client.download(package_id, version)
            installer = PackageInstaller()
            written = installer.install(
                dl["type"], dl["name"], dl["content"], dl.get("files")
            )
            # Record in local manifest
            try:
                installs = self._load_store_installs()
                installs[package_id] = {
                    "name": dl["name"],
                    "type": dl["type"],
                    "version": dl["version"],
                    "installed_at": datetime.now().isoformat(),
                }
                self._save_store_installs(installs)
            except Exception as e:
                logger.warning(f"Failed to save local install manifest: {e}")

            # Record install on Hub if logged in
            try:
                from pantheon.store.auth import StoreAuth
                auth = StoreAuth()
                if auth.is_logged_in:
                    await client.record_install(package_id, dl["version"])
            except Exception:
                pass
            return {
                "success": True,
                "name": dl["name"],
                "type": dl["type"],
                "version": dl["version"],
                "installed_files": [str(p) for p in written],
            }
        except Exception as e:
            logger.error(f"Error installing store package: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def get_installed_store_packages(self) -> dict:
        """Get locally installed store packages with their versions.

        Returns:
            dict with package_id -> {name, type, version, installed_at}
        """
        try:
            installs = self._load_store_installs()
            return {"success": True, "installs": installs}
        except Exception as e:
            logger.error(f"Error reading store installs: {e}")
            return {"success": False, "installs": {}, "error": str(e)}

    @tool
    async def get_custom_models(self) -> dict:
        """Get all user-defined custom models."""
        models = _load_custom_models()
        return {"success": True, "models": models}

    @tool
    async def save_custom_models(self, models: dict) -> dict:
        """Save user-defined custom models.

        Args:
            models: Dict of model_name -> {api_base, api_key, provider_type}
        """
        try:
            _save_custom_models(models)
            # Reset model selector cache so new models appear
            from pantheon.utils.model_selector import reset_model_selector
            reset_model_selector()
            return {"success": True, "message": "Custom models saved"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def reload_settings(self) -> dict:
        """Reload configuration settings from .env file and settings.json.

        This allows users to update their API keys and other settings
        without restarting the Pod.

        Reloads:
        - .env file (user environment variables, overrides existing values)
        - ~/.pantheon/settings.json (user global config)
        - .pantheon/settings.json (project config)
        - mcp.json (MCP server configuration)

        Does NOT reload:
        - System environment variables (Pod-injected by Hub, requires Pod restart)

        Returns:
            dict with success status and message
        """
        try:
            from pantheon.settings import get_settings

            settings = get_settings()
            settings.reload()

            return {
                "success": True,
                "message": "Settings reloaded successfully. New API keys and configuration are now active."
            }
        except Exception as e:
            logger.error(f"Error reloading settings: {e}")
            return {
                "success": False,
                "message": f"Failed to reload settings: {str(e)}"
            }

    @tool(exclude=True)
    async def check_api_keys(self) -> dict:
        """Check the configuration status of LLM API keys.

        Returns a dict with each key's status (configured, source, masked value)
        and whether any key is configured at all.
        """
        import os
        from pantheon.settings import get_settings

        settings = get_settings()
        key_names = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
        ]

        keys = {}
        for key in key_names:
            value = settings.get_api_key(key)
            if value:
                # Determine source
                source = "env" if os.environ.get(key) else "settings"
                masked = value[:6] + "***" if len(value) > 6 else "***"
                keys[key] = {"configured": True, "source": source, "masked": masked}
            else:
                keys[key] = {"configured": False, "source": None, "masked": None}

        has_any_key = any(v["configured"] for v in keys.values())
        return {"keys": keys, "has_any_key": has_any_key}

    # ============ OAuth Management ============

    @tool
    async def oauth_status(self) -> dict:
        """Get OAuth authentication status for all supported providers.

        Returns:
            Dict with provider statuses including authentication state and account info.
        """
        from pantheon.utils.oauth import CodexOAuthManager, GeminiCliOAuthManager
        from pantheon.utils.oauth.codex import CODEX_CLI_AUTH
        from pantheon.utils.oauth.gemini import GEMINI_CLI_AUTH

        codex = CodexOAuthManager()
        # Actually verify the token works (auto_refresh=True will try to refresh if expired)
        access_token = codex.get_access_token(auto_refresh=True)
        codex_authenticated = access_token is not None
        codex_account_id = codex.get_account_id() if codex_authenticated else None
        cli_available = CODEX_CLI_AUTH.exists()
        gemini = GeminiCliOAuthManager()
        gemini_access = gemini.get_access_token(refresh_if_needed=True)
        gemini_authenticated = gemini_access is not None
        gemini_cli_available = GEMINI_CLI_AUTH.exists()
        gemini_project_id = gemini.get_project_id()
        gemini_runtime_ready = gemini_authenticated and bool(gemini_project_id)
        gemini_runtime_error = None
        if gemini_authenticated and not gemini_runtime_ready:
            gemini_runtime_error = (
                "Gemini CLI OAuth is signed in, but no Code Assist project is available yet. "
                "Pantheon will try to resolve one automatically at runtime; if it still fails, "
                "set GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_PROJECT_ID."
            )

        return {
            "providers": {
                "codex": {
                    "authenticated": codex_authenticated,
                    "account_id": codex_account_id,
                    "description": "OpenAI Codex (ChatGPT backend-api, free with ChatGPT Plus)",
                    "supports_browser_login": True,
                    "supports_import": cli_available,
                },
                "gemini": {
                    "authenticated": gemini_authenticated,
                    "email": gemini.get_email() if gemini_authenticated else None,
                    "project_id": gemini_project_id if gemini_authenticated else None,
                    "runtime_ready": gemini_runtime_ready,
                    "runtime_error": gemini_runtime_error,
                    "description": "Gemini CLI OAuth (Gemini CLI auth with Code Assist project resolution)",
                    "supports_browser_login": True,
                    "supports_import": gemini_cli_available,
                },
            },
        }

    @tool
    async def oauth_login(self, provider: str = "codex") -> dict:
        """Start browser-based OAuth login flow.

        Opens the system browser for the user to authenticate.
        Token is saved automatically after successful login.

        Args:
            provider: OAuth provider name ('codex' or 'gemini')

        Returns:
            Dict with success status and account info.
        """
        if provider == "codex":
            from pantheon.utils.oauth import CodexOAuthError, CodexOAuthManager

            try:
                mgr = CodexOAuthManager()
                mgr.login(open_browser=True, timeout_seconds=300)

                from pantheon.utils.model_selector import reset_model_selector

                reset_model_selector()
                return {
                    "success": True,
                    "provider": "codex",
                    "account_id": mgr.get_account_id(),
                    "message": "Codex OAuth login successful. You can now use codex/ models.",
                }
            except CodexOAuthError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                logger.error(f"OAuth login failed: {e}")
                return {"success": False, "error": str(e)}

        if provider == "gemini":
            from pantheon.utils.oauth import GeminiCliOAuthError, GeminiCliOAuthManager

            try:
                mgr = GeminiCliOAuthManager()
                mgr.login(open_browser=True, timeout_seconds=300)

                from pantheon.utils.model_selector import reset_model_selector

                reset_model_selector()
                return {
                    "success": True,
                    "provider": "gemini",
                    "email": mgr.get_email(),
                    "project_id": mgr.get_project_id(),
                    "runtime_ready": bool(mgr.get_project_id()),
                    "message": "Gemini OAuth login successful. You can now use gemini-cli/ models.",
                }
            except GeminiCliOAuthError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                logger.error(f"OAuth login failed: {e}")
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"Unsupported OAuth provider: {provider}"}

    @tool
    async def oauth_import(self, provider: str = "codex") -> dict:
        """Import OAuth tokens from native CLI tools.

        For Codex: imports from ~/.codex/auth.json (Codex CLI).
        For Gemini: imports from ~/.gemini/oauth_creds.json (Gemini CLI).

        Args:
            provider: OAuth provider name ('codex' or 'gemini')

        Returns:
            Dict with success status.
        """
        try:
            if provider == "codex":
                from pantheon.utils.oauth import CodexOAuthManager

                mgr = CodexOAuthManager()
                result = mgr.import_from_codex_cli()
                success_payload = {
                    "success": True,
                    "provider": "codex",
                    "account_id": mgr.get_account_id(),
                    "message": "Imported Codex CLI tokens successfully.",
                }
                error_payload = {
                    "success": False,
                    "error": "No Codex CLI auth found (~/.codex/auth.json). Install Codex CLI or use browser login.",
                }
            elif provider == "gemini":
                from pantheon.utils.oauth import GeminiCliOAuthManager

                mgr = GeminiCliOAuthManager()
                result = mgr.import_from_gemini_cli()
                success_payload = {
                    "success": True,
                    "provider": "gemini",
                    "email": mgr.get_email(),
                    "project_id": mgr.get_project_id(),
                    "runtime_ready": bool(mgr.get_project_id()),
                    "message": "Imported Gemini CLI auth successfully.",
                }
                error_payload = {
                    "success": False,
                    "error": "No Gemini CLI auth found (~/.gemini/oauth_creds.json). Install Gemini CLI or use browser login.",
                }
            else:
                return {"success": False, "error": f"Unsupported OAuth provider: {provider}"}

            if result:
                from pantheon.utils.model_selector import reset_model_selector
                reset_model_selector()
                return success_payload
            else:
                return error_payload
        except Exception as e:
            logger.error(f"OAuth import failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def ollama_status(self, url: str = "http://localhost:11434") -> dict:
        """Check Ollama server status and list available models.

        Args:
            url: Ollama server URL (default: http://localhost:11434)

        Returns:
            Dict with running status, model list, and URL.
        """
        try:
            from pantheon.utils.model_selector import _detect_ollama, _list_ollama_models
            running = _detect_ollama(url)
            models = _list_ollama_models(url) if running else []
            return {"running": running, "models": models, "url": url}
        except Exception as e:
            return {"running": False, "models": [], "url": url}
