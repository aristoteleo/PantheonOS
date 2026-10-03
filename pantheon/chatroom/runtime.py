"""Agent domain service; legacy platform composition lives in room.py.

Execution/model factories still have process-scoped internals to migrate. An
explicit environment separates service composition without claiming sandboxing
or completing independent distribution/data migration.
"""

import asyncio
import copy
import dataclasses
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from typing import TYPE_CHECKING

from pantheon.agent import Agent
from pantheon.factory.models import TeamConfig
from .environment import AgentEnvironment
from pantheon.internal.memory import MemoryManager, _ALL_CONTEXTS
from pantheon.chatroom.routed_memory import ProjectRoutedMemoryManager, project_memory_dir
from pantheon.team import PantheonTeam
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import log_startup_profile, logger
from pantheon.utils.misc import generate_service_id, run_func
from .special_agents import get_suggestion_generator
from .thread import Thread
from .lifecycle import AgentLifetime, admitted_chat

if TYPE_CHECKING:
    from pantheon.team import PantheonTeam


def _activity_date(value):
    """Serialize legacy server-local timestamps with an explicit offset.

    Reading metadata must not make a conversation look active again. Invalid
    dates stay unknown instead of becoming today or breaking the entire list.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


DEFAULT_TOOLSETS = []

# Marker for internal auto-chat notifications (background-task completion, etc.)
# that must NOT be treated as user steer messages by the message queue.
_BG_NOTIFICATION_MARKER = "<bg_task_notification>"


def _is_internal_notification(message: list[dict]) -> bool:
    """True if a chat() message is an internal auto-chat notification rather
    than a real user message (so the message queue can skip enqueuing it)."""
    if not isinstance(message, list):
        return False
    for m in message:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str) and _BG_NOTIFICATION_MARKER in content:
            return True
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and isinstance(block.get("text"), str)
                    and _BG_NOTIFICATION_MARKER in block["text"]
                ):
                    return True
    return False


class AgentRuntime(AgentLifetime, ToolSet):
    """
    Agent execution and conversation service with explicitly supplied integrations.

    No platform management APIs are inherited here. The legacy ChatRoom facade
    supplies process-global integrations; an App composition root must supply its
    own bindings. This core alone is not the final deployable Agent package.

    Args:
        memory_dir: The directory to store the memory.
        environment: Explicit project view, templates, settings and service factories.
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
        memory_dir: str = "./.pantheon/memory",
        name: str = "pantheon-chatroom",
        description: str = "Chatroom for Pantheon agents",
        speech_to_text_model: str = "gpt-4o-mini-transcribe",
        check_before_chat: Callable | None = None,
        enable_nats_streaming: bool = False,
        default_team: "PantheonTeam | None" = None,
        enable_auto_chat_name: bool = False,
        *,
        environment: AgentEnvironment,
        **kwargs,
    ):
        self._environment = environment
        # Initialize ToolSet (will handle worker creation in run())
        super().__init__(name=name, **kwargs)

        # The compatibility host also uses this for startup telemetry.
        import time as _t_boot

        self._started_monotonic = _t_boot.monotonic()

        self.memory_dir = Path(memory_dir).resolve()
        # Explicit App routing is resolved before opening any conversation store.
        # An invalid binding must fail startup, not write to the legacy workspace
        # or silently put project conversations in the home store.
        app_routes = None
        if environment.project_memory_dir is not None:
            app_routes = [self._project_memory_dir(p["path"])
                          for p in environment.projects.list_projects()]
            active = environment.projects.active_project
            app_active = self._project_memory_dir(active.path) if active else str(self.memory_dir)
        # Per-project list/new routing and per-chat lookup share the same policy.
        # Legacy clients retain project-local storage; Apps provide their own.
        self.memory_manager = ProjectRoutedMemoryManager(self.memory_dir)

        # NATS streaming (optional)
        self._nats_adapter = None
        if enable_nats_streaming:
            from .stream import NATSStreamAdapter

            self._nats_adapter = NATSStreamAdapter()

        # These are supplied by the composition root. Importing/constructing
        # Agent never opens or mutates the platform's project registry.
        self.template_manager = environment.templates
        self.project_manager = environment.projects
        # Point memory routing at the registered projects + the active one so
        # list_chats shows the active project's own chats and per-chat ops can
        # find any chat in any project.
        #
        # In the BACKGROUND: every set_* here resolves and mkdirs on the
        # network-backed volume (measured ~2s of the boot's critical path on a
        # real workspace), and none of it is needed before the NATS worker can
        # subscribe. Readers that route by directory block on the manager's
        # routing event instead of seeing a half-initialized dir set; a failure
        # degrades to home-only routing, exactly what the old inline try/except
        # produced.
        self.memory_manager.begin_deferred_routing()

        def _init_memory_routing():
            import time as _t
            _t0 = _t.perf_counter()
            try:
                self.memory_manager.set_search_dirs(
                    [self._project_memory_dir(p["path"]) for p in self.project_manager.list_projects()]
                )
                _active = self.project_manager.active_project
                if _active:
                    self.memory_manager.set_active_dir(self._project_memory_dir(_active.path))
            except Exception as _e:
                logger.warning(f"[memory routing] init failed: {_e}")
            finally:
                self.memory_manager.finish_deferred_routing()
                log_startup_profile(
                    f"ChatRoom memory routing initialized in background in {_t.perf_counter() - _t0:.3f}s"
                )

        if app_routes is not None:
            # The App data mount is its own readiness dependency. Unlike the
            # legacy background initializer, errors must not degrade routing.
            self.memory_manager.set_search_dirs(app_routes)
            self.memory_manager.set_active_dir(app_active)
            self.memory_manager.finish_deferred_routing()
        else:
            import threading as _threading
            self._memory_routing_thread = _threading.Thread(
                target=_init_memory_routing, name="memory-routing-init", daemon=True
            )
            self._memory_routing_thread.start()

        self.description = description

        # Per-chat team management
        self.chat_teams: dict[str, PantheonTeam] = {}  # Per-chat teams cache
        # Per-chat single-flight locks: serialize concurrent first-time team
        # creation for the same chat so the team — and its endpoint + MCP
        # gateway — is built exactly once. Mirrors _project_endpoint_locks.
        self._team_init_locks: dict[str, asyncio.Lock] = {}

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

    def _project_memory_dir(self, path: str) -> str:
        resolver = self._environment.project_memory_dir or project_memory_dir
        result = resolver(path)
        if not isinstance(result, str) or not result or not Path(result).is_absolute():
            raise ValueError("Project conversation storage must be an absolute path")
        return result

    def _is_home_dir(self, p) -> bool:
        """Is `p` the home (work_dir) project? Home uses the default endpoint."""
        try:
            home = self.project_manager.default_project
            return bool(home) and Path(home.path).resolve() == Path(p).resolve()
        except Exception:
            return False

    async def _project_dir_for_chat(self, session_id: str | None) -> str | None:
        """Resolve a chat's PROJECT ROOT directory (workspace root) — the dir its
        files, task brain, and image outputs should all live under.

        Resolves, in priority order, and — for home chats — falls through to the
        home dir too, so the brain/prompt are ALWAYS anchored to a concrete project
        instead of silently falling back to the global default brain dir.

        Priority: (1) the chat's explicit workspace_path, else (2) the project that
        owns the chat's memory, else (3) the active project, else (4) home."""
        if session_id:
            try:
                memory = await run_func(self.memory_manager.get_memory, session_id)
                project = memory.extra_data.get("project", {})
                if isinstance(project, dict):
                    wpath = project.get("workspace_path")
                    if wpath and Path(wpath).is_dir():
                        return str(Path(wpath).resolve())
            except Exception as e:
                logger.debug(f"[multi-project] project dir (workspace_path) for {session_id}: {e}")
            try:
                mdir = self.memory_manager.mgr_for_chat(session_id).path
                if self._environment.project_memory_dir is not None:
                    # App memories live under a data mount, not <workspace>/
                    # .pantheon/memory. Reverse the explicit binding instead of
                    # treating its parent directory as a project workspace.
                    for project in self.project_manager.list_projects():
                        if Path(self._project_memory_dir(project["path"])).resolve() == Path(mdir).resolve():
                            return project["path"]
                    if Path(mdir).resolve() != self.memory_dir:
                        return None
                    selected = self.project_manager.active_project or self.project_manager.default_project
                    return selected.path if selected else None
                pdir = Path(mdir).parent.parent
                if pdir.is_dir():
                    return str(pdir.resolve())
            except Exception as e:
                logger.debug(f"[multi-project] project dir (memory) for {session_id}: {e}")
                if self._environment.project_memory_dir is not None:
                    # Do not route an unavailable App project to a different
                    # active project's filesystem just because lookup failed.
                    return None
        try:
            active = self.project_manager.active_project
            if active and active.path and Path(active.path).is_dir():
                return str(Path(active.path).resolve())
        except Exception:
            pass
        try:
            home = self.project_manager.default_project
            if home and home.path and Path(home.path).is_dir():
                return str(Path(home.path).resolve())
        except Exception:
            pass
        return None

    def _init_plugins(self) -> None:
        """Initialize plugin config (lazy creation).

        Actual plugin instances are created during run_setup() before readiness.
        """
        self._compression_plugin = None
        self._memory_plugin = None
        self._plugins = []  # List of initialized plugins
        self._plugin_initialization = None

    async def run_setup(self):
        """Initialize Agent plugins and activity; dependencies bind separately."""

        # Log NATS streaming status
        if self._nats_adapter is not None:
            logger.info("ChatRoom: NATS streaming enabled")
        else:
            logger.info("ChatRoom: NATS streaming disabled")

        # An enabled plugin is part of this App's capabilities. Do not advertise
        # readiness while silently missing memory, learning or another plugin.
        await self._ensure_plugins()

        # Report this Agent's activity; the platform aggregates App/node health.
        if hasattr(self, 'worker') and self.worker and hasattr(self.worker, 'set_activity_callback'):
            self.worker.set_activity_callback(self._get_activity_status)




    def _get_activity_status(self) -> dict:
        """Return current activity status for _ping responses.

        Called synchronously from NATSRemoteWorker._ping().  Must not block.
        This reports only Agent work, never host/Fleet activity.
        """
        active_threads = len(self.threads)
        bg_task_count = 0
        teams = list(self.chat_teams.values())
        if default := getattr(self, "_default_team", None):
            teams.append(default)
        seen = set()
        for team in teams:
            for agent in team.agents.values():
                manager = getattr(agent, "_bg_manager", None)
                if manager is not None and id(manager) not in seen:
                    seen.add(id(manager))
                    bg_task_count += sum(t.status == "running" for t in manager.list_tasks())

        return {
            "active_threads": active_threads,
            "bg_tasks": bg_task_count,
            "has_active_tasks": active_threads > 0 or bg_task_count > 0,
            "activity_scope": "agent",
        }

    def _settings(self):
        return self._environment.settings()

    async def _ensure_services(self, service_type, required_services):
        return await self._environment.ensure_services(service_type, required_services)

    async def _create_agents(self, agent_configs, *, conversation_id=None):
        return await self._environment.create_agents(agent_configs, conversation_id=conversation_id)

    def _validate_model_provider(self, model):
        return self._environment.validate_model(model)

    async def _ensure_plugins(self) -> list:
        """Share one owned initialization, including empty results and failures."""
        if self._plugins:
            return self._plugins
        task = getattr(self, '_plugin_initialization', None)
        if task is None:
            if (getattr(self, '_agent_stopping', False)
                    and asyncio.current_task() not in getattr(self, '_agent_calls', {})):
                raise RuntimeError('Agent is stopping')
            task = self._plugin_initialization = asyncio.create_task(self._initialize_plugins())
        # One cancelled RPC/warmup observer cannot cancel construction or cleanup
        # needed by other calls. AgentLifetime joins this task on shutdown.
        return await asyncio.shield(task)

    async def _initialize_plugins(self) -> list:
        from pantheon.team.plugin_registry import create_owned_plugins
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin

        factory = getattr(self._environment, "create_plugins", None)
        plugins = await factory() if factory is not None else await create_owned_plugins(self._settings())
        self._plugins = plugins
        self._memory_plugin = next((p for p in plugins if isinstance(p, MemorySystemPlugin)), None)
        logger.info(f'Agent: {len(plugins)} owned plugins initialized')
        return plugins

    def _save_team_template_to_memory(
        self,
        memory,
        template_obj: dict,
        *,
        persist: bool = False,
    ) -> dict:
        """Save TeamConfig to memory and optionally persist it immediately."""
        extra_data = getattr(memory, "extra_data", None)
        if extra_data is None:
            memory.extra_data = extra_data = {}

        if isinstance(template_obj, TeamConfig):
            team_config = template_obj
        else:
            team_config = self.template_manager.dict_to_team_config(template_obj)

        template_dict = dataclasses.asdict(team_config)
        if persist:
            memory.set_metadata("team_template", template_dict)
        else:
            memory.set_metadata_in_memory("team_template", template_dict)
        return template_dict

    def _build_template_summary(self, template_obj: dict | None) -> dict | None:
        """Build a lightweight template summary for chat listings."""
        if not isinstance(template_obj, dict):
            return None

        agents = template_obj.get("agents")
        agent_count = len(agents) if isinstance(agents, list) else None

        return {
            "id": template_obj.get("id"),
            "name": template_obj.get("name"),
            "icon": template_obj.get("icon"),
            "category": template_obj.get("category"),
            "version": template_obj.get("version"),
            "source_path": template_obj.get("source_path"),
            "agent_count": agent_count,
        }

    def _apply_model_to_template(
        self,
        template_obj: dict,
        model: str | None,
        *,
        validate_model: bool = True,
    ) -> dict:
        """Return a normalized team template with every agent using model."""
        if model is None:
            return copy.deepcopy(template_obj)

        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string when provided")

        model = model.strip()
        if validate_model:
            from pantheon.agent import _parse_thinking_suffix

            clean_model, _thinking = _parse_thinking_suffix(model)
            is_valid, error_msg = self._validate_model_provider(clean_model)
            if not is_valid:
                raise ValueError(error_msg)

        team_config = self.template_manager.dict_to_team_config(template_obj)
        for agent_config in team_config.agents:
            agent_config.model = model
        return team_config.to_dict()

    async def get_team_for_chat(self, chat_id: str, save_to_memory: bool = True) -> PantheonTeam:
        """Get the team for a specific chat, creating from memory if needed."""
        # 0. If default_team is set, always use it (bypass template system)
        if self._default_team is not None:
            return self._default_team

        # FIX for performance, history chat will get team even not needed.
        # 1. Check if team already exists in cache
        if chat_id in self.chat_teams:
            return self.chat_teams[chat_id]

        # 2. Serialize concurrent first-time creation for this chat. Without
        # this single-flight lock, parallel requests (e.g. the first message
        # racing a session restore) both miss the cache, both build the team,
        # and the duplicate endpoint / MCP gateway fight over port 3100 —
        # either crashing the sandbox ("bind: address already in use") or
        # mis-registering MCP tools so ve_curator drops out of the agent's
        # active toolset.
        lock = self._team_init_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._team_init_locks[chat_id] = lock
        async with lock:
            # Double-check: another caller may have built it while we waited.
            if chat_id in self.chat_teams:
                return self.chat_teams[chat_id]

            # 3. Try to load (or create) the team from persistent memory.
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


    async def _create_team_from_template(
        self, team_config: TeamConfig, chat_id: str = None
    ) -> PantheonTeam:
        """Create a team from TeamConfig object."""
        team_t0 = time.perf_counter()
        template_name = team_config.name or "unknown"

        logger.info(f"🏗️ Creating team from template '{template_name}'")

        prepare_t0 = time.perf_counter()
        (
            agent_configs,
            required_toolsets,
            required_mcp_servers,
        ) = self.template_manager.prepare_team(team_config)
        log_startup_profile(
            "ChatRoom team prepare_team finished in "
            f"{time.perf_counter() - prepare_t0:.3f}s "
            f"(template={template_name}, agents={len(agent_configs)}, "
            f"toolsets={list(required_toolsets)}, mcp_servers={list(required_mcp_servers)})"
        )

        # ===== STEP 2: Compute and ensure all required services =====
        ensure_mcp_t0 = time.perf_counter()
        await self._ensure_services("mcp", list(required_mcp_servers))
        log_startup_profile(
            "ChatRoom team ensure MCP finished in "
            f"{time.perf_counter() - ensure_mcp_t0:.3f}s "
            f"(template={template_name}, mcp_servers={list(required_mcp_servers)})"
        )
        ensure_toolset_t0 = time.perf_counter()
        await self._ensure_services("toolset", list(required_toolsets))
        log_startup_profile(
            "ChatRoom team ensure ToolSets finished in "
            f"{time.perf_counter() - ensure_toolset_t0:.3f}s "
            f"(template={template_name}, toolsets={list(required_toolsets)})"
        )

        logger.debug(
            f"Ensured services: {len(required_mcp_servers)} MCP servers, "
            f"{len(required_toolsets)} toolsets"
        )

        # ===== STEP 3: Create agents =====
        create_agents_t0 = time.perf_counter()
        all_agents = await self._create_agents(agent_configs, conversation_id=chat_id)
        log_startup_profile(
            "ChatRoom team create_agents finished in "
            f"{time.perf_counter() - create_agents_t0:.3f}s "
            f"(template={template_name}, agents={len(all_agents)})"
        )
        logger.info(f"Created {len(all_agents)} agents")

        # ===== STEP 4: Ensure plugins are ready (and init learning team) =====
        plugins_t0 = time.perf_counter()
        plugins = await self._ensure_plugins()
        log_startup_profile(
            "ChatRoom team ensure plugins finished in "
            f"{time.perf_counter() - plugins_t0:.3f}s "
            f"(template={template_name}, plugins={len(plugins)})"
        )

        # ===== STEP 5: Create and setup team with plugins =====
        team = PantheonTeam(
            agents=all_agents,
            plugins=plugins,
        )
        # Anchor the task/brain prompt to this chat's project root (TaskSystemPlugin
        # reads team._project_dir in on_team_created, which runs inside async_setup).
        try:
            team._project_dir = await self._project_dir_for_chat(chat_id)
        except Exception as e:
            logger.debug(f"[multi-project] team project dir for {chat_id}: {e}")
        setup_t0 = time.perf_counter()
        await team.async_setup()
        log_startup_profile(
            "ChatRoom team async_setup finished in "
            f"{time.perf_counter() - setup_t0:.3f}s "
            f"(template={template_name})"
        )

        # Store source path for template persistence
        team._source_path = team_config.source_path

        num_agents = len(team.team_agents)
        features = f"{num_agents} agents" if num_agents > 1 else "single agent"

        logger.info(f"✅ Team '{template_name}' created (Features: {features})")
        log_startup_profile(
            "ChatRoom team created in "
            f"{time.perf_counter() - team_t0:.3f}s "
            f"(template={template_name}, chat_id={chat_id}, features={features})"
        )
        return team

    @tool
    async def setup_team_for_chat(
        self,
        chat_id: str,
        template_obj: dict | None = None,
        save_to_memory: bool = True,
        model: str | None = None,
        validate_model: bool = True,
        template_id: str | None = None,
    ):
        """Setup/update team for a chat using a template object and/or model override."""
        try:
            if template_id and template_obj:
                return {
                    "success": False,
                    "message": "template_id and template_obj are mutually exclusive",
                }

            # Read-only: storing template/model config, no need to fix
            memory = await run_func(self.memory_manager.get_memory, chat_id)

            if template_obj is None:
                if template_id:
                    selected_template = self.template_manager.get_template(template_id)
                    if not selected_template:
                        return {
                            "success": False,
                            "message": f"Team template '{template_id}' not found",
                        }
                    template_obj = dataclasses.asdict(selected_template)
                else:
                    if model is None:
                        return {
                            "success": False,
                            "message": "template_obj, template_id, or model is required",
                        }

                    extra_data = getattr(memory, "extra_data", None) or {}
                    template_obj = copy.deepcopy(extra_data.get("team_template"))
                    if not template_obj:
                        default_template = self.template_manager.get_template("default")
                        if not default_template:
                            return {
                                "success": False,
                                "message": "No existing team template and default template not found",
                            }
                        template_obj = dataclasses.asdict(default_template)

            if model is not None:
                template_obj = self._apply_model_to_template(
                    template_obj,
                    model,
                    validate_model=validate_model,
                )

            logger.info(
                f"Setting up team for chat {chat_id} with template: {template_obj.get('name', 'unknown')}"
            )

            # Store full template in memory using consolidated method
            template_dict = self._save_team_template_to_memory(
                memory,
                template_obj,
                persist=save_to_memory,
            )

            memory.delete_metadata("active_agent")

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
    async def get_agents(self, chat_id: str = None) -> dict:
        """Get the team agents info for a specific chat."""

        def get_agent_info(agent: Agent):
            if hasattr(agent, "not_loaded_toolsets"):
                not_loaded_toolsets = agent.not_loaded_toolsets
            else:
                not_loaded_toolsets = []
            identity = getattr(agent, '_instance_identity', None)
            return {
                "name": agent.name,
                **({'instance': dict(identity)} if identity is not None else {}),
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
        template_id: str | None = None,
        template_obj: dict | None = None,
        chat_config: dict | None = None,
        project_metadata: dict | None = None,
        model: str | None = None,
        validate_model: bool = True,
    ) -> dict:
        """Create a new chat.

        Args:
            chat_name: The name of the chat.
            project_name: Optional project name for grouping.
            workspace_path: Optional workspace directory path.
            workspace_mode: Workspace mode - "project" (shared, default) or "isolated" (per-chat).
            template_id: Optional existing team template ID to bind at creation time.
            template_obj: Optional full team template object to bind at creation time.
            chat_config: Optional per-chat UI/runtime configuration blob.
            project_metadata: Optional extra project metadata to persist with the chat.
            model: Optional model name or tag to apply to every agent in the selected template.
            validate_model: If True, verify the model provider has valid credentials.
        """
        if template_id and template_obj:
            return {
                "success": False,
                "message": "template_id and template_obj are mutually exclusive",
            }

        if project_metadata is not None and not isinstance(project_metadata, dict):
            return {
                "success": False,
                "message": "project_metadata must be a dict when provided",
            }

        if chat_config is not None and not isinstance(chat_config, dict):
            return {
                "success": False,
                "message": "chat_config must be a dict when provided",
            }

        initial_template_dict = None
        if template_id:
            initial_template = self.template_manager.get_template(template_id)
            if not initial_template:
                return {
                    "success": False,
                    "message": f"Team template '{template_id}' not found",
                }
            initial_template_dict = dataclasses.asdict(initial_template)
        elif template_obj is not None:
            validation = self.template_manager.validate_template_dict(template_obj)
            if not validation.get("success"):
                return {
                    "success": False,
                    "message": validation.get("message", "Template validation failed"),
                    "validation_errors": validation.get("validation_errors", []),
                }
            initial_template_dict = copy.deepcopy(
                validation.get("template") or template_obj
            )

        if model is not None:
            if initial_template_dict is None:
                default_template = self.template_manager.get_template("default")
                if not default_template:
                    return {
                        "success": False,
                        "message": "Default template not found in template manager",
                    }
                initial_template_dict = dataclasses.asdict(default_template)
            try:
                initial_template_dict = self._apply_model_to_template(
                    initial_template_dict,
                    model,
                    validate_model=validate_model,
                )
            except ValueError as exc:
                return {"success": False, "message": str(exc)}

        # Create the chat's memory in the NAMED project's own store (not the global
        # active project) — so a desktop window scoped to project P puts its new
        # chats in P regardless of which project another window made active.
        _target_dir = None
        if project_name and hasattr(self.memory_manager, "new_memory_in"):
            for _p in self.project_manager.list_projects():
                if _p.get("name") == project_name and _p.get("path"):
                    _target_dir = self._project_memory_dir(_p["path"])
                    break
        if _target_dir:
            memory = await run_func(self.memory_manager.new_memory_in, _target_dir, chat_name)
        else:
            memory = await run_func(self.memory_manager.new_memory, chat_name)
        memory.set_metadata("last_activity_date", datetime.now(timezone.utc).isoformat())

        project = copy.deepcopy(project_metadata) if project_metadata else {}
        if project_name is not None:
            project["name"] = project_name

        if workspace_path is None and isinstance(project, dict):
            project_workspace_path = project.get("workspace_path")
            if isinstance(project_workspace_path, str) and project_workspace_path:
                workspace_path = project_workspace_path

        if workspace_mode == "project" and isinstance(project, dict):
            project_workspace_mode = project.get("workspace_mode")
            if isinstance(project_workspace_mode, str) and project_workspace_mode:
                workspace_mode = project_workspace_mode

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
            settings = self._settings()
            session_workspace_dir = settings.pantheon_dir / "workspaces" / memory.id
            try:
                session_workspace_dir.mkdir(parents=True, exist_ok=True)
                workspace_path = str(session_workspace_dir)
                logger.info(f"Created session workspace directory: {workspace_path}")
            except Exception as e:
                logger.warning(f"Failed to create session workspace directory: {e}")
                workspace_mode = "project"  # Fallback to project mode

        # Set project metadata
        project["workspace_mode"] = workspace_mode
        if workspace_path:
            project["workspace_path"] = workspace_path
            project.setdefault("original_cwd", str(self._settings().workspace))
        if project:
            memory.set_metadata("project", project)
        if chat_config is not None:
            memory.set_metadata("chat_config", copy.deepcopy(chat_config))
        if initial_template_dict is not None:
            initial_template_dict = self._save_team_template_to_memory(
                memory,
                initial_template_dict,
                persist=True,
            )

        return {
            "success": True,
            "message": "Chat created successfully",
            "chat_name": memory.name,
            "chat_id": memory.id,
            "workspace_mode": workspace_mode,
            "workspace_path": workspace_path,
            "project": copy.deepcopy(project),
            "chat_config": copy.deepcopy(chat_config) if chat_config is not None else None,
            "template": self._build_template_summary(initial_template_dict),
        }

    @tool
    async def fork_chat(
        self,
        source_chat_id: str,
        fork_at_message_id: str,
        new_chat_name: str | None = None,
    ) -> dict:
        """Fork a chat at a message node into a new, independent chat.

        The new chat (new id) is a copy of source_chat_id's history up to
        fork_at_message_id. If that message is a USER message the copy stops BEFORE
        it and the message is returned (the caller puts its text in the input box, so
        the user re-asks in the new branch); otherwise the copy INCLUDES it (continue
        from there). Project / team template / chat config are copied so the fork
        opens in the same context.

        Args:
            source_chat_id: The chat to fork from.
            fork_at_message_id: The message id to fork at.
            new_chat_name: Optional name (defaults to "<source> (fork)").

        Returns:
            {success, chat_id, chat_name, is_user_node, reverted_message?}
        """
        import copy as _copy
        import uuid as _uuid
        try:
            source = await run_func(self.memory_manager.get_memory, source_chat_id)

            index = None
            is_user_node = False
            reverted_message = None
            if fork_at_message_id:
                for i, msg in enumerate(source._messages):
                    if msg.get("id") == fork_at_message_id:
                        index = i
                        is_user_node = msg.get("role") == "user"
                        if is_user_node:
                            reverted_message = _copy.deepcopy(msg)
                        break
                if index is None:
                    return {"success": False, "message": f"Message '{fork_at_message_id}' not found"}
            elif source._messages:
                # No message id → fork at the LAST message (sidebar "fork this chat").
                index = len(source._messages) - 1
                is_user_node = source._messages[index].get("role") == "user"
                if is_user_node:
                    reverted_message = _copy.deepcopy(source._messages[index])
            else:
                return {"success": False, "message": "Chat has no messages to fork"}

            # user node → copy up to BEFORE it (re-ask); other → include it (continue).
            end = index if is_user_node else index + 1

            # A fork must never end on an assistant message that still has unresolved
            # tool_calls. Forking from a task/execution card lands on the last
            # tool-call assistant message, whose tool RESULTS are the following
            # role="tool" messages. Slicing right after the tool_calls would leave a
            # dangling call and the next LLM turn in the branch would error on the
            # unmatched tool_call. Extend past any immediately-following tool-result
            # messages so the tool_call/result pairing stays intact.
            if not is_user_node and 0 < end <= len(source._messages):
                last = source._messages[end - 1]
                if last.get("role") == "assistant" and last.get("tool_calls"):
                    while end < len(source._messages) and source._messages[end].get("role") == "tool":
                        end += 1

            sliced = _copy.deepcopy(source._messages[:end])
            for m in sliced:
                m["id"] = str(_uuid.uuid4())  # fresh message ids; only content is shared

            name = new_chat_name or f"{source.name} (fork)"

            # Create the fork in the SAME store as the source so it lands in the same
            # project, then copy the context metadata over.
            memory = None
            if hasattr(self.memory_manager, "new_memory_in"):
                try:
                    src_dir = self.memory_manager.mgr_for_chat(source_chat_id).path
                    memory = await run_func(self.memory_manager.new_memory_in, src_dir, name)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[fork] new_memory_in failed ({e}); using default store")
            if memory is None:
                memory = await run_func(self.memory_manager.new_memory, name)

            for key in ("project", "chat_config", "team_template"):
                if key in source.extra_data:
                    memory.set_metadata(key, _copy.deepcopy(source.extra_data[key]))
            memory.set_metadata("last_activity_date", datetime.now(timezone.utc).isoformat())

            if sliced:
                memory.add_messages(sliced)
            await run_func(self.memory_manager.save_one, memory.id)

            logger.info(
                f"Forked chat {source_chat_id} @ {fork_at_message_id} -> {memory.id} "
                f"({len(sliced)} msgs, user_node={is_user_node})"
            )
            result = {
                "success": True,
                "message": "Chat forked successfully",
                "chat_id": memory.id,
                "chat_name": memory.name,
                "is_user_node": is_user_node,
            }
            if reverted_message is not None:
                result["reverted_message"] = reverted_message
            return result
        except Exception as e:
            logger.error(f"Error forking chat {source_chat_id}: {e}")
            return {"success": False, "message": str(e)}

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
                        settings = self._settings()
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

    def _chat_has_running_bg_tasks(self, chat_id: str) -> bool:
        """Whether a chat has at least one in-flight background task.

        Background tasks are asyncio tasks owned by the chat's agents and can
        outlive the agent turn that spawned them — the main loop returns (and
        the persisted "running" flag flips False) while the task keeps running.
        The team is retained in ``self.chat_teams``, so the UI busy indicator can
        reflect this post-loop work by consulting it. Cheap: O(1) miss for chats
        with no cached team (the common case).
        """
        team = self.chat_teams.get(chat_id)
        if team is None:
            return False
        try:
            for agent in team.agents.values():
                mgr = getattr(agent, "_bg_manager", None)
                if mgr is None:
                    continue
                if any(t.status == "running" for t in mgr.list_tasks()):
                    return True
        except Exception as e:
            logger.debug(f"bg-task check failed for {chat_id}: {e}")
        return False

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
            # Scoping a chat list to a project (a desktop window).
            _filter_by_name = False
            if project_name is not None:
                # If project_name is a REGISTERED project (desktop window), list
                # ITS store directly. Its chats physically live there but are NOT
                # reliably tagged with project.name (pre-existing chats carry only
                # workspace_mode), so a name-tag filter would wrongly hide them.
                _proj = next(
                    (p for p in self.project_manager.list_projects()
                     if p.get("name") == project_name),
                    None,
                )
                if _proj is not None and hasattr(self.memory_manager, "list_memory_metadata_in"):
                    metadata_items = await run_func(
                        self.memory_manager.list_memory_metadata_in,
                        self._project_memory_dir(_proj["path"]), True,
                    )
                elif hasattr(self.memory_manager, "list_all_memory_metadata"):
                    # Not a registered project (e.g. hub test-user isolation keyed
                    # by user id): aggregate across stores and filter by name tag.
                    _dirs = [self._project_memory_dir(p["path"]) for p in self.project_manager.list_projects()]
                    metadata_items = await run_func(
                        self.memory_manager.list_all_memory_metadata, _dirs, True
                    )
                    _filter_by_name = True
                else:
                    metadata_items = await run_func(self.memory_manager.list_memory_metadata, True)
                    _filter_by_name = True
            else:
                # Single-window / web / home-window default: active store only.
                metadata_items = await run_func(self.memory_manager.list_memory_metadata, True)
            chats = []
            skipped_chats = []
            for item in metadata_items:
                id = item["id"]
                if item.get("metadata_error"):
                    logger.warning(f"Skipping unreadable chat {id}: {item['metadata_error']}")
                    skipped_chats.append({"id": id, "message": "metadata unreadable"})
                    continue
                extra_data = item.get("extra_data", {})
                if not isinstance(extra_data, dict):
                    logger.warning(f"Skipping chat with invalid metadata {id}: extra_data is not a dict")
                    skipped_chats.append({"id": id, "message": "extra_data is not a dict"})
                    continue
                project = extra_data.get("project", None)

                # Filter by project_name only on the aggregate-by-name-tag path
                # (hub test-user isolation). The registered-project path already
                # listed exactly that project's store, so no name filter there.
                if project_name is not None and _filter_by_name:
                    chat_project_name = project.get("name") if isinstance(project, dict) else None
                    if chat_project_name != project_name:
                        continue

                workspace_mode = None
                workspace_path = None
                if isinstance(project, dict):
                    workspace_mode = project.get(
                        "workspace_mode",
                        "isolated" if project.get("workspace_path") else "project",
                    )
                    workspace_path = project.get("workspace_path")

                chat_config = extra_data.get("chat_config", None)
                team_template = extra_data.get("team_template", None)

                # UI busy flag = main agent loop active OR an in-flight background
                # task (which outlives the loop). The persisted "running" flag is
                # only trusted when the chat actually has a LIVE thread — otherwise a
                # run that was killed mid-flight (e.g. a backend restart, which never
                # runs the completion reset) leaves "running": true on disk and the
                # sidebar shows a phantom spinner on a cold start. self.threads is
                # empty right after startup, so a stale flag resolves to not-running
                # immediately instead of waiting for the list_running_chats poll.
                bg_running = self._chat_has_running_bg_tasks(id)
                main_running = bool(extra_data.get("running", False)) and id in self.threads
                chats.append(
                    {
                        "id": id,
                        "name": item["name"],
                        "running": main_running or bg_running,
                        "has_background_tasks": bg_running,
                        "last_activity_date": _activity_date(extra_data.get("last_activity_date")),
                        "project": project,
                        "workspace_mode": workspace_mode,
                        "workspace_path": workspace_path,
                        "chat_config": copy.deepcopy(chat_config)
                        if isinstance(chat_config, dict)
                        else chat_config,
                        "template": self._build_template_summary(team_template),
                        "memory_path": str(item.get("memory_path")) if item.get("memory_path") else None,
                    }
                )

            chats.sort(
                key=lambda x: datetime.fromisoformat(x["last_activity_date"]).timestamp()
                if x["last_activity_date"]
                else float("-inf"),
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

    async def _get_sanitized_messages(self, chat_id: str, filter_out_images: bool = False) -> list:
        """Load + sanitize a chat's FULL message history (shared by
        get_chat_messages and stream_chat_messages).

        Bounds per-message field sizes so the JSON stays reasonable: drops
        raw_content > 50 KB, caps stdout/stderr to 10 KB, caps content +
        reasoning_content to 200 KB.
        """
        import json as _json
        memory = await run_func(self.memory_manager.get_memory, chat_id)
        # Sync _current_chat_id to keep backend state aligned with UI.
        self._current_chat_id = chat_id
        messages = await run_func(memory.get_messages, _ALL_CONTEXTS, False)
        if messages is None:
            messages = []

        MAX_RAW_CONTENT_SIZE = 50_000
        MAX_FIELD_LENGTH = 10_000
        MAX_CONTENT_FIELD_SIZE = 200_000

        def _truncate_str_field(msg: dict, key: str) -> None:
            v = msg.get(key)
            if isinstance(v, str) and len(v) > MAX_CONTENT_FIELD_SIZE:
                msg[key] = (
                    v[:MAX_CONTENT_FIELD_SIZE]
                    + f"\n\n[...truncated {len(v) - MAX_CONTENT_FIELD_SIZE} bytes]"
                )

        for message in messages:
            if "raw_content" in message:
                if isinstance(message["raw_content"], dict):
                    if filter_out_images and "base64_uri" in message["raw_content"]:
                        del message["raw_content"]["base64_uri"]
                    for _k in ("stdout", "stderr"):
                        if _k in message["raw_content"]:
                            message["raw_content"][_k] = message["raw_content"][_k][:MAX_FIELD_LENGTH]
                try:
                    rc_size = len(_json.dumps(message["raw_content"], ensure_ascii=False))
                    if rc_size > MAX_RAW_CONTENT_SIZE:
                        del message["raw_content"]
                except (TypeError, ValueError):
                    pass
            _truncate_str_field(message, "content")
            _truncate_str_field(message, "reasoning_content")
        return messages

    @tool
    async def stream_chat_messages(
        self,
        chat_id: str,
        reply_to: str,
        ack_subject: str,
        filter_out_images: bool = False,
        chunk_size: int = 64 * 1024,
        window: int = 192,
    ):
        """Stream a chat's full history to the caller's inbox as binary chunks.

        Faster counterpart of get_chat_messages: instead of the client looping
        byte-budgeted ~700 KB pages over the slow request/reply path
        (~0.7 MB/s, and 700 KB messages sit in the NATS jam zone), serialize
        the whole sanitized history once and PUSH it in 64 KiB chunks
        (~3.5 MB/s, jam-free). Same wire format as file_transfer stream_read.

        Returns {"success": True, "total_size": int} immediately; the JSON
        ({"messages": [...], "total": N}) flows over reply_to.
        """
        try:
            messages = await self._get_sanitized_messages(chat_id, filter_out_images)
        except KeyError:
            return {"success": False, "error": f"Chat '{chat_id}' not found"}
        except Exception as e:
            logger.error(f"[stream_chat_messages] error loading {chat_id}: {e}")
            return {"success": False, "error": str(e)}

        import json as _json
        payload = _json.dumps(
            {"messages": messages, "total": len(messages)}, ensure_ascii=False
        ).encode("utf-8")

        nc = self.worker.nc if getattr(self, "worker", None) else None
        if nc is None:
            return {"success": False, "error": "No NATS connection available for streaming"}

        from pantheon.utils.stream_push import push_bytes_stream
        self._track_background(asyncio.create_task(
            push_bytes_stream(nc, payload, reply_to, ack_subject, int(chunk_size), int(window))
        ))
        return {"success": True, "total_size": len(payload)}


    @tool
    async def get_chat_outputs(self, chat_id: str) -> dict:
        """Read output registrations from this conversation's Agent-owned state."""
        import json
        import re
        memory = await run_func(self.memory_manager.get_memory, chat_id)
        if not memory:
            return {'success': False, 'error': 'Conversation not found'}
        root = await self._project_dir_for_chat(chat_id)
        if not root or not re.fullmatch(r'[A-Za-z0-9_-]+', chat_id):
            return {'success': False, 'error': 'Invalid conversation workspace'}
        path = Path(root) / '.pantheon' / 'brain' / chat_id / 'task_state.json'
        state = json.loads(path.read_text()).get('state', {}) if path.exists() else {}
        return {'success': True, 'outputs': state.get('outputs', []), 'task_dirs': state.get('task_dirs', {})}

    @tool
    async def get_chat_messages(
        self,
        chat_id: str,
        filter_out_images: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ):
        """Get messages of a chat, byte-budgeted page by page.

        The full response of a long chat (lots of tool calls, embedded
        images, long reasoning) easily blows past the NATS 1 MiB max
        payload. Clients loop calls with ``offset`` advancing by the
        returned ``next_offset`` until ``next_offset >= total``.

        Args:
            chat_id: The ID of the chat.
            filter_out_images: Whether to filter out the images.
            offset: 0-based index of the first message to return.
            limit: Hard cap on messages per page. ``None`` means the
                method auto-stops when its byte budget is reached;
                callers normally don't need to set this.
        """
        try:
            import json as _json
            messages = await self._get_sanitized_messages(chat_id, filter_out_images)
            total = len(messages)
            if offset < 0:
                offset = 0
            if offset >= total:
                return {
                    "success": True,
                    "messages": [],
                    "total": total,
                    "next_offset": total,
                }

            # Byte-budgeted slice: target ~700 KB per page so a single
            # NATS frame (1 MiB) reliably accommodates the JSON-RPC
            # envelope on top.
            PAGE_BYTE_BUDGET = 700_000
            page: list = []
            size = 0
            for msg in messages[offset:]:
                if limit is not None and len(page) >= limit:
                    break
                try:
                    msg_size = len(_json.dumps(msg, ensure_ascii=False))
                except (TypeError, ValueError):
                    msg_size = MAX_CONTENT_FIELD_SIZE  # defensive estimate
                # Always include at least one message per page so a single
                # oversized message still makes forward progress.
                if page and (size + msg_size) > PAGE_BYTE_BUDGET:
                    break
                page.append(msg)
                size += msg_size

            return {
                "success": True,
                "messages": page,
                "total": total,
                "next_offset": offset + len(page),
            }
        except KeyError:
            return {
                "success": False,
                "message": f"Chat '{chat_id}' not found",
                "messages": [],
                "total": 0,
                "next_offset": 0,
            }
        except Exception as e:
            logger.error(f"Error getting chat messages: {e}")
            return {
                "success": False,
                "message": str(e),
                "messages": [],
                "total": 0,
                "next_offset": 0,
            }

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
    async def touch_chat(self, chat_id: str):
        """Bump ``last_activity_date`` to the current time without modifying
        chat content.

        Used by the UI so that re-entering the single empty New Chat moves
        it to the top of the sidebar's by-activity sort, matching the
        intuition that "the chat you just opened is the most recent one."
        """
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id, True)
            memory.set_metadata("last_activity_date", datetime.now(timezone.utc).isoformat())
            await run_func(self.memory_manager.save_one, chat_id)
            return {"success": True, "message": "Chat activity refreshed"}
        except KeyError:
            return {"success": False, "message": f"Chat '{chat_id}' not found"}
        except Exception as e:
            logger.error(f"Error touching chat: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def export_chat(
        self, chat_id: str, output_path: str = "", compress: bool = True
    ) -> dict:
        """Export a chat and its referenced files into a portable bundle.

        Args:
            chat_id: The chat ID to export.
            output_path: Destination directory. Defaults to .pantheon/exports/<chat_id>.
            compress: If True, also create a .tar.gz archive.
        """
        try:
            from .export import export_chat_bundle

            if not output_path:
                output_path = str(
                    Path(self.memory_manager.path).parent / "exports" / chat_id
                )
            result = export_chat_bundle(
                memory_dir=self.memory_manager.path,
                chat_id=chat_id,
                output_dir=output_path,
                compress=compress,
            )
            return result
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return {"success": False, "message": str(e)}

    @tool
    async def import_chat(
        self, bundle_path: str, target_workspace: str = ""
    ) -> dict:
        """Import a chat from an exported bundle.

        Args:
            bundle_path: Path to a bundle directory or .tar.gz file.
            target_workspace: Workspace root for file placement. Defaults to current workspace.
        """
        try:
            from .export import import_chat_bundle

            if not target_workspace:
                target_workspace = str(
                    Path(self.memory_manager.path).parent.parent
                )
            result = import_chat_bundle(
                memory_dir=self.memory_manager.path,
                bundle_path=bundle_path,
                target_root=target_workspace,
            )
            return result
        except Exception as e:
            logger.error(f"Import failed: {e}")
            return {"success": False, "message": str(e)}

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
                settings = self._settings()
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
                    project.setdefault("original_cwd", str(self._settings().workspace))
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

    # ── Project Management ──────────────────────────────────────────


    @tool
    async def list_running_chats(self) -> dict:
        """Running chats across ALL projects, with each chat's project.

        The chat list (list_chats) is scoped to the active project's own memory,
        so the UI project switcher needs this separate, cross-project view to show
        which OTHER projects currently have work in flight (running indicator).
        """
        running = []
        for chat_id in list(self.threads.keys()):
            project = None
            # Derive the project from WHERE the chat's memory lives (per-project
            # memory is authoritative). The chat's own metadata may lack a
            # name/workspace_path (e.g. created in a project without being tagged),
            # so the memory dir is the reliable signal for the running indicator.
            try:
                mdir = self.memory_manager.mgr_for_chat(chat_id).path
                pdir = Path(mdir).parent.parent  # <proj>/.pantheon/memory → <proj>
                info = self.project_manager.get_project(str(pdir))
                if info:
                    project = {"name": info.name, "workspace_path": info.path}
                elif Path(pdir).is_dir():
                    project = {"name": Path(pdir).name, "workspace_path": str(pdir)}
            except Exception as e:
                logger.debug(f"[list_running_chats] project for {chat_id}: {e}")
            running.append({"chat_id": chat_id, "project": project})
        return {"running": running}






    @tool
    async def get_chat_workspace(self, chat_id: str) -> dict:
        """Read-only: resolve the workspace (project) a chat belongs to WITHOUT
        touching any global state.

        Web per-tab scoping: each browser tab scopes its OWN view (file panel +
        chat list + selector) to the chat it shows, via the frontend's per-tab
        windowProject — never the shared global active project — so multiple tabs
        can work in different workspaces at once. The frontend calls this to learn
        which workspace to scope to. Cross-project aware (a viewed chat need not be
        in the active project's own chat list). Deliberately does NOT fall back to
        the global active project (that would make the result depend on other tabs)
        — a home/legacy chat with no project of its own returns ``{path: None}`` and
        the tab keeps the default view.

        Returns ``{"path": <project root> | None, "name": <display name> | None}``.
        """
        if not chat_id:
            return {"path": None, "name": None}
        pdir = None
        # (1) the chat's explicit workspace_path.
        try:
            memory = await run_func(self.memory_manager.get_memory, chat_id)
            project = memory.extra_data.get("project", {})
            if isinstance(project, dict):
                wpath = project.get("workspace_path")
                if wpath and Path(wpath).is_dir() and not self._is_home_dir(wpath):
                    pdir = str(Path(wpath).resolve())
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[multi-project] get_chat_workspace resolve {chat_id}: {e}")
        # (2) else the project whose memory store owns the chat.
        if pdir is None:
            try:
                mdir = self.memory_manager.mgr_for_chat(chat_id).path
                cand = Path(mdir).parent.parent
                if cand.is_dir() and not self._is_home_dir(cand):
                    pdir = str(cand.resolve())
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[multi-project] get_chat_workspace by memory dir {chat_id}: {e}")
        if not pdir:
            return {"path": None, "name": None}
        name = None
        try:
            info = self.project_manager.get_project(pdir)
            if info:
                name = info.name
        except Exception:  # noqa: BLE001
            pass
        return {"path": pdir, "name": name or Path(pdir).name}


    def _find_teams_using_agent(self, agent_id: str) -> list[str]:
        """Find all teams that reference a given agent by ID."""
        teams_using = []
        try:
            all_teams = self.template_manager.file_manager.list_teams(resolve_refs=False)
            for team in all_teams:
                for agent in team.agents:
                    if agent.id == agent_id:
                        teams_using.append(team.name)
                        break
        except Exception:
            pass
        return teams_using

    @tool
    async def change_template_scope(
        self,
        kind: str,
        source_path: str,
        target_scope: str,
        overwrite: bool = False,
    ) -> dict:
        """Move a template/agent/skill between project and global scope.

        Args:
            kind: "agents", "teams", or "skills"
            source_path: Absolute path to the source file or directory
            target_scope: "global" or "project"
            overwrite: If True, overwrite existing at target
        """
        import shutil
        settings = self._settings()
        user_home = Path.home() / ".pantheon"

        kind_dirs = {
            "agents": (settings.agents_dir, user_home / "agents"),
            "teams": (settings.teams_dir, user_home / "teams"),
            "skills": (settings.skills_dir, user_home / "skills"),
        }
        if kind not in kind_dirs:
            return {"success": False, "message": f"Unknown kind: {kind}. Use agents/teams/skills"}

        project_dir, global_dir = kind_dirs[kind]
        src = Path(source_path)

        # If relative path, resolve against project/global dirs
        if not src.is_absolute():
            rel_name = source_path
            # Strip .pantheon/<kind>/ prefix if present
            for strip_prefix in [f'.pantheon/{kind}/', f'.pantheon/']:
                if rel_name.startswith(strip_prefix):
                    rel_name = rel_name[len(strip_prefix):]
                    break
            # Strip bare kind prefix (e.g. "teams/test.md" → "test.md")
            if '/' in rel_name:
                first = rel_name.split('/')[0]
                if first in ('agents', 'teams', 'skills'):
                    rel_name = rel_name.split('/', 1)[1]

            # Try project first, then global
            for base in [project_dir, global_dir]:
                candidate = base / rel_name
                if candidate.exists():
                    src = candidate
                    break

        if not src.exists():
            return {"success": False, "message": f"Source not found: {source_path} (resolved: {src})"}

        # Determine which base this source belongs to
        if str(src).startswith(str(project_dir)):
            src_base = project_dir
        elif str(src).startswith(str(global_dir)):
            src_base = global_dir
        else:
            return {"success": False, "message": f"Source path not in project or global dir: {src}"}

        if target_scope == "global":
            dst_base = global_dir
        elif target_scope == "project":
            dst_base = project_dir
        else:
            return {"success": False, "message": f"Unknown target_scope: {target_scope}"}

        if project_dir.resolve() == global_dir.resolve():
            return {"success": False, "message": "Project and global are the same directory"}

        try:
            warnings = []

            if kind == "skills":
                skill_dir = src if src.is_dir() else src.parent
                rel = skill_dir.relative_to(src_base)
                dst = dst_base / rel
                if dst.exists() and not overwrite:
                    return {"success": False, "message": f"Already exists at {dst}.", "conflict": True}
                dst_base.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(skill_dir, dst)
                shutil.rmtree(skill_dir)
            elif kind == "teams":
                # Check for path-referenced agents that won't move with the team
                try:
                    team = self.template_manager.file_manager._read_team_from_path(src)
                    for agent in team.agents:
                        sp = agent.source_path or ''
                        if sp and ('/' in sp or sp.endswith('.md')):
                            warnings.append(f"Agent '{agent.id}' uses path reference '{sp}' which may break after move")
                except Exception:
                    pass
                rel = src.relative_to(src_base)
                dst = dst_base / rel
                if dst.exists() and not overwrite:
                    return {"success": False, "message": f"Already exists at {dst}.", "conflict": True}
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                src.unlink()
            else:
                rel = src.relative_to(src_base)
                dst = dst_base / rel
                if dst.exists() and not overwrite:
                    return {"success": False, "message": f"Already exists at {dst}.", "conflict": True}
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                src.unlink()

            logger.info(f"[change_scope] Moved {kind}/{rel} → {target_scope}")
            result: dict = {"success": True, "message": f"Moved to {target_scope}"}
            if warnings:
                result["warnings"] = warnings
            return result
        except Exception as e:
            logger.error(f"[change_scope] Failed: {e}")
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
            # If a turn is still running for this chat, stop it and WAIT for it to
            # fully finish (all its memory writes included) before trimming. Otherwise
            # the run races the delete and its late output (e.g. a "Thinking" step)
            # re-appears above the user's next message. The stop is near-instant — the
            # in-flight LLM call is aborted (see _run_stream) — so this wait is short.
            thread = self.threads.get(chat_id)
            if thread is not None:
                await thread.stop()
                try:
                    await asyncio.wait_for(thread._done.wait(), timeout=20.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[revert] chat {chat_id} did not stop within 20s; trimming anyway"
                    )

            # Read-only: reverting message, no need to fix (run has stopped above)
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

    async def _apply_chat_rename(self, memory, new_name, chat_name_generator, messages=None):
        if not new_name or new_name == memory.name:
            return False
        if not chat_name_generator._is_default_name(memory.name):
            return False

        message_count = len(messages) if messages is not None else len(memory.get_messages(None))
        chat_name_generator._update_metadata(memory, message_count)
        memory.name = new_name
        # Sync customTitle to keep session_storage in step with memory.name,
        # otherwise restoreSessionMetadata would clobber memory.name back
        # on the next turn.
        session_storage = memory.extra_data.get("session_storage")
        if isinstance(session_storage, dict):
            metadata = session_storage.get("metadata")
            if isinstance(metadata, dict) and metadata.get("customTitle") != new_name:
                metadata["customTitle"] = new_name
                memory.mark_dirty()
        # Save only this chat's memory
        await run_func(self.memory_manager.save_one, memory.id)
        # Notify frontend via NATS
        if self._nats_adapter is not None:
            await self._nats_adapter.publish(
                memory.id, "chat_renamed",
                {"type": "chat_renamed", "chat_id": memory.id, "name": new_name},
            )
        return True

    async def _background_rename_chat(self, memory, messages=None, preferred_model=None, candidate_task=None):
        """Background task to rename chat without blocking main flow.

        This runs asynchronously so the user doesn't experience any delay from
        the LLM call for name generation.
        """
        try:
            from .special_agents import get_chat_name_generator

            chat_name_generator = get_chat_name_generator()
            if not chat_name_generator._is_default_name(memory.name):
                return

            if candidate_task is not None:
                try:
                    new_name = await candidate_task
                except Exception:
                    new_name = None
            else:
                new_name = None

            if not new_name:
                agent_messages = messages if messages is not None else memory.get_messages(None)
                new_name = await chat_name_generator.generate_name_candidate(
                    agent_messages,
                    preferred_model=preferred_model,
                )

            await self._apply_chat_rename(
                memory,
                new_name,
                chat_name_generator,
                messages=messages,
            )
        except Exception as e:
            logger.error(f"Background chat rename failed: {e}", exc_info=True)

    async def _resolve_chat_name_preferred_model(self, memory):
        try:
            team = await self.get_team_for_chat(memory.id)
            active_agent = team.get_active_agent(memory)
            return active_agent.models[0] if getattr(active_agent, "models", None) else None
        except Exception:
            return None

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
            if getattr(chatroom_self, "_agent_stopping", False):
                return
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
                self._track_background(loop.create_task(_auto_chat()))
            except RuntimeError:
                pass

        for agent in team.agents.values():
            if hasattr(agent, "_bg_manager"):
                # Only set if no external consumer (REPL, SDK) has already wired it
                if agent._bg_manager.on_complete is None:
                    agent_name = agent.name

                    def _on_bg_complete_with_notify(bg_task, _agent_name=agent_name):
                        if getattr(chatroom_self, "_agent_stopping", False):
                            return
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
                                self._track_background(loop.create_task(_publish()))
                            except RuntimeError:
                                pass

                    agent._bg_manager.on_complete = _on_bg_complete_with_notify

    @tool
    async def get_pending_messages(self, chat_id: str) -> dict:
        """Return user messages currently queued for a running chat.

        These are messages the user sent while the agent was busy (message
        queue feature) that have not yet been drained by the agent loop. Used
        by the frontend to re-render the pending queue after a page refresh.

        Args:
            chat_id: The ID of the chat.
        """
        thread = self.threads.get(chat_id, None)
        if thread is None or not hasattr(thread, "steer_queue"):
            return {"success": True, "messages": []}
        # Peek without draining — the agent loop is the only consumer. Filter
        # out any internal notifications defensively (should not be enqueued).
        pending = [
            m for m in thread.steer_queue.peek()
            if not _is_internal_notification([m])
        ]
        return {"success": True, "messages": pending}

    @tool
    async def remove_pending_message(self, chat_id: str, message_id: str) -> dict:
        """Remove a queued (not-yet-processed) user message from the chat queue.

        Used by the frontend to "recall" a message the user sent while the agent
        was busy, before the agent drains it.

        Args:
            chat_id: The ID of the chat.
            message_id: The ID of the queued message to remove.
        """
        thread = self.threads.get(chat_id, None)
        if thread is None or not hasattr(thread, "steer_queue"):
            return {"success": False, "removed": False, "reason": "no_active_run"}
        removed = thread.steer_queue.remove(message_id)
        return {"success": True, "removed": removed}

    @tool
    async def edit_pending_message(
        self, chat_id: str, message_id: str, text: str
    ) -> dict:
        """Edit the text of a queued (not-yet-processed) user message in place.

        Args:
            chat_id: The ID of the chat.
            message_id: The ID of the queued message to edit.
            text: The new text content.
        """
        thread = self.threads.get(chat_id, None)
        if thread is None or not hasattr(thread, "steer_queue"):
            return {"success": False, "updated": False, "reason": "no_active_run"}
        updated = thread.steer_queue.update(message_id, text)
        return {"success": True, "updated": updated}

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
    @admitted_chat
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
            # Internal auto-chat notifications (e.g. background-task completion)
            # are already handled by the ephemeral drain_notifications path in
            # Agent._run_stream while the agent is busy. Don't enqueue them as
            # user steer messages — that double-handles them and pollutes the
            # pending-queue UI. Reject like before so the ephemeral path wins.
            if _is_internal_notification(message):
                return {"success": False, "message": "Chat is already running"}
            # Agent is busy: instead of rejecting a real user message, queue it
            # so the running agent loop can pick it up mid-run (message queue).
            running_thread = self.threads[chat_id]
            running_thread.inject_user_messages(message)
            logger.info(
                f"Chat {chat_id} busy; queued {len(message)} steer message(s)"
            )
            return {"success": True, "queued": True}
        try:
            # CRITICAL: Agent execution - MUST fix messages for LLM API
            memory = await run_func(self.memory_manager.get_memory, chat_id, True)
        except KeyError:
            return {"success": False, "message": f"Chat '{chat_id}' not found"}
        memory.update_metadata({
            "running": True,
            "last_activity_date": datetime.now(timezone.utc).isoformat(),
        })

        async def team_getter():
            return await self.get_team_for_chat(chat_id)

        # Wire bg task auto-notification for this chat
        # Resolve team early so we can set on_complete hooks before agent runs
        team = await self.get_team_for_chat(chat_id)
        self._setup_bg_auto_notify(chat_id, team)

        rename_candidate_task = None
        rename_apply_task = None
        rename_preferred_model = None
        if self._enable_auto_chat_name:
            from .special_agents import get_chat_name_generator

            rename_preferred_model = await self._resolve_chat_name_preferred_model(memory)
            chat_name_generator = get_chat_name_generator()
            rename_candidate_task = asyncio.create_task(
                chat_name_generator.generate_name_candidate(
                    message,
                    preferred_model=rename_preferred_model,
                )
            )
            self._background_tasks.add(rename_candidate_task)
            rename_candidate_task.add_done_callback(self._background_tasks.discard)
            rename_apply_task = asyncio.create_task(
                self._background_rename_chat(
                    memory,
                    messages=message,
                    preferred_model=rename_preferred_model,
                    candidate_task=rename_candidate_task,
                )
            )
            self._background_tasks.add(rename_apply_task)
            rename_apply_task.add_done_callback(self._background_tasks.discard)

        # Anchor the run to THIS chat's project root (workspace root) so the task
        # brain, image output, and file tools all live under it — never the global
        # home. Previously workdir was set ONLY for "isolated" chats with an
        # explicit workspace_path; a "project"-mode chat (no workspace_path) left
        # workdir empty, so the task brain fell back to the global brain dir and the
        # agent saw home paths and wrote files into the wrong project.
        project = memory.extra_data.get("project", {})
        workspace_path = project.get("workspace_path") if isinstance(project, dict) else None
        project_dir = await self._project_dir_for_chat(chat_id)
        if project_dir:
            context_variables = context_variables or {}
            context_variables["workdir"] = project_dir
            # `project_root` is the IMMUTABLE anchor for this chat's project. Unlike
            # `workdir` — which proxy_toolset pops/overwrites per endpoint-toolset
            # call to steer the endpoint's cwd — `project_root` is never mutated, so
            # LOCAL toolsets (task brain, register_output) always resolve to the
            # right project instead of silently falling back to the global home.
            context_variables["project_root"] = project_dir

        # Set up a designated image output directory so agents save images
        # to a known location and claw channels can detect them cheaply.
        from pantheon.utils.image_detection import (
            IMAGE_OUTPUT_DIR, snapshot_images, diff_snapshots, encode_images_to_uris,
        )
        image_output_path: str | None = None
        img_root = project_dir or workspace_path
        if img_root:
            import os
            image_output_path = os.path.join(img_root, IMAGE_OUTPUT_DIR)
            os.makedirs(image_output_path, exist_ok=True)
            context_variables = context_variables or {}
            context_variables["image_output_dir"] = image_output_path

        # Pre-snapshot: only scan the dedicated quick-preview dir (.pantheon/images).
        # Deliverable figures are surfaced via the Output panel — register_output plus
        # the live preview of the task's declared output_dir — NOT this inline channel,
        # which exists mainly for claw channels that have no Output panel.
        pre_image_snapshot = snapshot_images(image_output_path) if image_output_path else {}

        # Expose the chat id to tools. `client_id` in the tool context is the
        # UI connection id (stable across chats), not the chat id — toolsets
        # that need the chat id (e.g. live_view, to publish on the chat's
        # NATS stream) must read `chat_id` instead.
        context_variables = context_variables or {}
        context_variables["chat_id"] = chat_id

        thread = Thread(
            team_getter,  # Pass team getter
            memory,
            message,
            context_variables=context_variables,
        )

        self.threads[chat_id] = thread

        # Wire the steer queue so the running agent loop can pull in user
        # messages that arrive mid-run (message queue feature). Attach to every
        # team agent, mirroring _setup_bg_auto_notify, so whichever agent is
        # active after a transfer drains the same queue.
        for agent in team.agents.values():
            if hasattr(agent, "_steer_queue"):
                agent._steer_queue = thread.steer_queue

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

            # Flush messages that arrived in the race window between the agent
            # loop's final drain and the thread being deregistered. Schedule a
            # fresh chat() (mirrors _auto_chat); it runs after this chat()
            # returns, by which point the thread is removed from self.threads.
            # If the user STOPPED the run, discard the queue instead of flushing
            # — Stop should halt everything, not continue with queued messages.
            leftover = thread.steer_queue.drain()
            if leftover and thread._stop_flag:
                logger.info(
                    f"Chat {chat_id} stopped; discarding {len(leftover)} queued message(s)"
                )
                leftover = []
            if leftover:
                self._continue_accepted_chat(thread, chat_id, leftover)

            if self._enable_auto_chat_name:
                if rename_apply_task is None or rename_apply_task.done():
                    task = asyncio.create_task(
                        self._background_rename_chat(
                            memory,
                            messages=message,
                            preferred_model=rename_preferred_model,
                        )
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

            # Post-execution image detection: scan the quick-preview dir for images
            # created during this run and push them inline (mainly for claw channels).
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
                try:
                    memory.update_metadata({
                        "running": False,
                        "last_activity_date": datetime.now(timezone.utc).isoformat(),
                    })
                    await run_func(self.memory_manager.save_one, chat_id)
                except Exception as e:
                    logger.error(f"Failed to save memory on cleanup: {e}")
                    # The RPC reports its failure, and App stop must not report
                    # a successful data drain after this final write failed.
                    self._agent_save_error = e
                    raise

            saving = asyncio.create_task(_cleanup_persistent_state())
            cancelled = None
            try:
                # Shield alone leaves a detached write when the caller is
                # cancelled. Keep owning the save until it has actually ended.
                while not saving.done():
                    try:
                        await asyncio.shield(saving)
                    except asyncio.CancelledError as exc:
                        cancelled = exc
                saving.result()
            finally:
                # Revert/shutdown must never pass an unfinished persistent write.
                thread._done.set()
            if cancelled is not None:
                raise cancelled

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
            from pantheon.utils.llm_providers import get_openai_effective_config

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
            api_base, api_key = get_openai_effective_config()
            adapter = get_adapter("openai")
            response = await asyncio.wait_for(
                adapter.atranscription(
                    model=self.speech_to_text_model,
                    file=audio_file,
                    base_url=api_base or None,
                    api_key=api_key or None,
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
            template_manager = self.template_manager
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
            template_manager = self.template_manager
            return template_manager.validate_template_dict(template)
        except Exception as e:
            logger.error(f"Error validating template compatibility: {e}")
            return {"success": False, "message": str(e)}

    # File-Based Template Management (delegates to template_manager)

    @tool
    async def list_template_files(self, file_type: str = "teams", view: str = "files") -> dict:
        """
        List available template files.
        """
        logger.debug(f"Listing template files... {file_type} view={view}")
        return self.template_manager.list_template_files(file_type, view=view)

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
        template_manager = self.template_manager
        return template_manager.read_template_file(file_path, resolve_refs=resolve_refs)

    @tool
    async def write_template_file(self, file_path: str, content: dict) -> dict:
        """
        Write/update a template markdown file.
        """
        template_manager = self.template_manager
        return template_manager.write_template_file(file_path, content)

    @tool
    async def delete_template_file(self, file_path: str, force: bool = False) -> dict:
        """
        Delete a template markdown file.

        Args:
            file_path: Path to template file (e.g. "agents/researcher.md")
            force: If True, delete even if referenced by teams
        """
        # Check if deleting an agent that's referenced by teams
        if file_path.startswith("agents/") and not force:
            agent_id = Path(file_path).stem
            teams_using = self._find_teams_using_agent(agent_id)
            if teams_using:
                return {
                    "success": False,
                    "message": f"Agent '{agent_id}' is used by: {', '.join(teams_using)}. Set force=True to delete anyway.",
                    "referenced_by": teams_using,
                }
        template_manager = self.template_manager
        return template_manager.delete_template_file(file_path)


    # Model Management Methods





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
            model: Model name (e.g., "openai/gpt-5.4") or tag (e.g., "high", "normal,vision").
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
            from .token_stats import get_detailed_token_stats

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





    # ============ OAuth Management ============
