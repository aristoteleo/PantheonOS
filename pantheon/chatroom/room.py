"""Legacy combined service, retained while desktop callers migrate.

New Agent packages use runtime.AgentRuntime and supply scoped integrations.
Platform management, host telemetry and global mutations remain only in this
compatibility facade; importing the Agent core never imports this module.
"""
import asyncio
import os
from pathlib import Path
from typing import Callable

from pantheon.platform.apps_api import AppServicesAPI
from pantheon.platform.fleet_api import FleetAPI
from pantheon.platform.health import PlatformHealth
from pantheon.platform.models_api import ModelServicesAPI
from pantheon.platform.projects_api import ProjectsAPI
from pantheon.platform.store_api import StoreAPI
from pantheon.platform.model_directory import ModelDirectoryAPI
from pantheon.platform.oauth_api import OAuthAPI
from pantheon.apps.builtin.llm_playground.service import PlaygroundAPI
from pantheon.apps.host_lifecycle import AppShutdownError
from pantheon.factory import create_agents_from_template, get_template_manager
from pantheon.settings import get_settings
from pantheon.team import PantheonTeam
from pantheon.toolset import tool
from pantheon.utils.log import logger
from .projects import ProjectManager
from .routed_memory import project_memory_dir
from .environment import AgentEnvironment
from .runtime import AgentRuntime


class ChatRoom(AgentRuntime, PlaygroundAPI, OAuthAPI, ModelDirectoryAPI, StoreAPI,
               PlatformHealth, AppServicesAPI, FleetAPI, ModelServicesAPI, ProjectsAPI):
    def __init__(
        self,
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
        environment = AgentEnvironment(
            projects=ProjectManager(active_path=workspace_path or str(get_settings().workspace)),
            templates=get_template_manager(), settings=self._settings,
            ensure_services=self._ensure_services, create_agents=self._create_agents,
            validate_model=self._validate_model_provider,
        )
        super().__init__(memory_dir=memory_dir, name=name,
            description=description, speech_to_text_model=speech_to_text_model,
            check_before_chat=check_before_chat, enable_nats_streaming=enable_nats_streaming,
            default_team=default_team, enable_auto_chat_name=enable_auto_chat_name,
            environment=environment, **kwargs)

    def _settings(self):
        return get_settings()

    async def _create_agents(self, agent_configs):
        return await create_agents_from_template(agent_configs)

    async def run_setup(self):
        await super().run_setup()
        # Only the combined compatibility host serves the model directory.
        self._track_background(asyncio.create_task(self._warm_model_catalog()))

    def _get_activity_status(self):
        metrics = super()._get_activity_status()
        transfer_handles = self._transfer_handles_cached()
        playground_tasks = len(getattr(getattr(self, "_llm_playground", None), "tasks", {}))
        metrics.update(transfer_handles=transfer_handles, playground_tasks=playground_tasks,
            has_active_tasks=metrics["has_active_tasks"] or transfer_handles > 0 or playground_tasks > 0)
        metrics.pop("activity_scope", None)  # Legacy Hub expects aggregate activity.
        metrics.update(self._get_host_metrics())
        return metrics

    async def begin_shutdown(self):
        await super().begin_shutdown()
        # Release the combined host's long-poll RPCs before generic host drain.
        await self._stop_oauth()

    async def _stop_auxiliary_services(self):
        errors = []
        for stop in (self._stop_playground, self._stop_oauth,
                     self._stop_model_directory, self._stop_health_refresh):
            try:
                await stop()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise AppShutdownError(errors) from errors[0]

    async def _warm_model_catalog(self) -> None:
        """Preload the OpenRouter catalog so the picker's first open is served from cache.
        Unconditional (not gated on platform mode): the catalog's created/cost data drives
        picker ordering in BOTH modes. Never raises — a failed warmup just means
        list_available_models fetches on demand, with the fallback layers behind it."""
        try:
            from pantheon.utils import openrouter_catalog

            await openrouter_catalog.ensure_fresh()
            st = openrouter_catalog.catalog_status()
            logger.info(
                f"ChatRoom: model catalog warm ({st['model_count']} models, "
                f"source={st['source'] or 'none'}, live={st['live']})"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ChatRoom: model catalog warmup skipped: {e}")


    def _transfer_handles_cached(self) -> int:
        """Fresh-handle count from the file-transfer instance, cached.

        The _ping path is synchronous and must not block, so this returns
        the last sample and — at most every 30s — kicks an async refresh in
        the background. No instance started yet means no transfer can be
        running: the probe reads the resolver's cache and never boots one.
        """
        import time as _t

        probe = getattr(self, "_transfer_probe", None)
        if probe is None:
            probe = self._transfer_probe = {"at": 0.0, "handles": 0, "task": None}
        now = _t.monotonic()
        task = probe["task"]
        if now - probe["at"] > 30 and (task is None or task.done()):
            probe["at"] = now  # stamp first: a failing probe also backs off
            try:
                loop = asyncio.get_running_loop()
                if not getattr(self, "_agent_stopping", False):
                    probe["task"] = self._track_background(loop.create_task(self._refresh_transfer_handles()))
            except RuntimeError:
                pass  # no loop on this thread — keep the last value
        return int(probe["handles"])


    async def _refresh_transfer_handles(self) -> None:
        probe = self._transfer_probe
        try:
            from pantheon.apps.proxy import ToolsetProxy
            from pantheon.apps.resolver import get_shared_resolver

            resolver = get_shared_resolver()
            if resolver is None:
                probe["handles"] = 0
                return
            total = 0
            for sid in resolver.started_instances("file_transfer"):
                try:
                    res = await asyncio.wait_for(
                        ToolsetProxy(sid).invoke("transfer_activity", {}), 5
                    )
                    total += int((res or {}).get("fresh_handles") or 0)
                except Exception:
                    continue  # a dead instance holds nothing open
            probe["handles"] = total
        except Exception:
            probe["handles"] = 0


    async def _ensure_services(
        self,
        service_type: str,
        required_services: list[str],
    ):
        """Ensure required services are available in the App model.

        Toolsets are ensured lazily at bind time on their own instances, so
        only MCP servers need an explicit start — on the mcp-gateway App.
        Unknown toolset names are reported, not started: there is nothing
        left that could start them.
        """
        if not required_services:
            return

        from pantheon.apps.proxy import ToolsetProxy
        from pantheon.apps.resolver import get_shared_resolver

        resolver = get_shared_resolver()
        if resolver is None:
            logger.warning(
                f"[apps] resolver not wired; cannot ensure {service_type}: "
                f"{required_services}"
            )
            return
        if service_type == "mcp":
            try:
                sid = await resolver.ensure_instance("mcp_gateway")
                result = await ToolsetProxy.from_toolset(sid).invoke(
                    "start_servers", {"names": required_services}
                )
                logger.info(f"MCP servers ensured: {result.get('started', result)}")
            except Exception as e:
                logger.warning(f"Error ensuring MCP servers: {e}")
            return

        unknown = [t for t in required_services if not resolver.resolves(t)]
        if unknown:
            logger.warning(f"[apps] not in the App catalog, unavailable: {unknown}")


    @tool
    async def get_endpoint(self, session_id: str | None = None) -> dict:
        """The data-channel service for a session's files.

        Post-endpoint form: returns the file-manager App instance that serves
        this session (project-scoped when the chat lives in a project). The
        instance answers file tools (read_file, glob, …) directly on its own
        service — there is no endpoint indirection anymore.
        """
        try:
            from pantheon.platform.apps_api import resolve_app_service

            sid_arg = None if session_id in (None, "", "__global__") else session_id
            proj_dir = await self._project_dir_for_chat(sid_arg)
            return await resolve_app_service("file_manager", workdir=proj_dir)
        except Exception as e:
            logger.error(f"Error getting file service info: {e}")
            return {"success": False, "message": str(e)}


    @tool
    async def set_endpoint(self, endpoint_service_id: str) -> dict:
        """Retired: services are App instances placed by the resolver."""
        return {
            "success": False,
            "message": "set_endpoint is retired; services are App instances "
                       "managed by the fleet runner",
        }


    @tool
    async def proxy_toolset(
        self,
        method_name: str,
        args: dict | None = None,
        toolset_name: str | None = None,
    ) -> dict:
        """Legacy chat-aware App call; platform callers use call_app_service."""
        from pantheon.platform.apps_api import invoke_app_tool

        try:
            args = dict(args or {})
            if args.get('_node_id') is not None:
                return await invoke_app_tool(method_name, args, toolset_name)
            if toolset_name in ('file_manager', 'desktop'):
                from pantheon.internal.memory_system.file_routing import is_memory_file_request, route_memory_file
                if is_memory_file_request(method_name, args):
                    session_id = args.get('session_id')
                    workdir = await self._project_dir_for_chat(None if session_id == '__global__' else session_id)
                    local = await route_memory_file(method_name, args, workdir=workdir)
                    if local is not None:
                        return local
            session_id = args.get('session_id') or getattr(self, '_current_chat_id', None)
            workdir = await self._project_dir_for_chat(None if session_id == '__global__' else session_id)
            return await invoke_app_tool(method_name, args, toolset_name, workdir=workdir)
        except Exception as exc:
            logger.error(f'Error calling toolset method {method_name} on {toolset_name}: {exc}')
            return {'success': False, 'error': str(exc)}


    @tool
    async def set_active_project(self, path: str) -> dict:
        """Record the active project WITHOUT a heavy context switch — no chdir,
        no settings/memory/template singleton reset.

        For multi-project view-switching: each chat runs in its own per-project
        endpoint, so switching the *viewed* project must NOT interrupt running
        chats or reset the default endpoint. Use this from the UI project
        switcher; use switch_project only when the default endpoint must follow.
        """
        result = await super().set_active_project(path)
        if not result["success"]:
            return result
        resolved = result["project"]["path"]
        # Route list/new chats to this project's own memory store (entering a
        # project shows its own chats). Per-chat ops still follow each chat to its
        # own store, so running chats in other projects are unaffected.
        self.memory_manager.set_active_dir(project_memory_dir(resolved))
        self.memory_manager.set_search_dirs(
            [project_memory_dir(p["path"]) for p in self.project_manager.list_projects()]
        )
        return result


    @tool
    async def set_active_project_for_chat(self, chat_id: str) -> dict:
        """Make the active project FOLLOW a chat.

        Restoring/opening a chat should put the file panel, project selector, and
        chat list into THAT chat's workspace. On a fresh page load the UI restores
        the chat named in the URL, but the backend's active project has reset to
        the default — so a chat that lives in another workspace is shown while the
        file panel + selector stay stranded on the default project (the mismatch
        this fixes). Resolves cross-project chats too: a chat you're viewing may
        not appear in the active project's own chat list, so the frontend can't
        resolve its workspace itself — the backend does it here via the same
        routing that already sends the chat's tools/files to the right project.

        A home/legacy chat (no project of its own) — or one already in the active
        project — leaves the active project unchanged (``switched: False``).
        Mirrors ``set_active_project`` but keyed by chat id instead of a path.
        """
        if not chat_id:
            return {"success": False, "switched": False}
        try:
            pdir = await self._project_dir_for_chat(chat_id)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[multi-project] set_active_project_for_chat resolve {chat_id}: {e}")
            return {"success": False, "switched": False, "message": str(e)}
        # No own project, or it resolves to home → the default project is already
        # the sensible place to be; don't disturb it.
        if not pdir or self._is_home_dir(pdir):
            return {"success": True, "switched": False}
        # Already active → skip the memory-store reroute (a no-op switch).
        try:
            active = self.project_manager.active_project
            if active and active.path and Path(active.path).resolve() == Path(pdir).resolve():
                return {"success": True, "switched": False, "project": active.to_dict()}
        except Exception:  # noqa: BLE001
            pass
        res = await self.set_active_project(pdir)
        res["switched"] = bool(res.get("success"))
        return res


    @tool
    async def switch_project(self, path: str) -> dict:
        """Switch the active project to a different directory.

        This performs a full runtime context switch:
        - Changes working directory
        - Reloads settings from new .pantheon/
        - Switches memory manager to new project's chats
        - Reloads templates, agents, skills from new project

        Args:
            path: Path of the registered project to switch to.
        """
        resolved = str(Path(path).resolve())
        info = self.project_manager.set_active(resolved)
        if not info:
            return {"success": False, "message": f"Project not registered: {resolved}"}

        if not Path(resolved).is_dir():
            return {"success": False, "message": f"Directory does not exist: {resolved}"}

        try:
            # 1. Change process working directory
            import os
            os.chdir(resolved)
            logger.info(f"[switch_project] chdir → {resolved}")

            # 2. Reset Settings singleton and replace with new work_dir
            import pantheon.settings as _settings_mod
            _settings_mod._settings = None
            _settings_mod._settings = _settings_mod.Settings(Path(resolved))
            _settings_mod._settings.reload()

            # 3. Ensure .pantheon directory exists
            pantheon_dir = Path(resolved) / ".pantheon"
            pantheon_dir.mkdir(parents=True, exist_ok=True)
            (pantheon_dir / "memory").mkdir(parents=True, exist_ok=True)

            # 4. Point memory routing at the new project (keep the router so
            # per-chat routing + other projects' stores stay intact).
            new_memory_dir = pantheon_dir / "memory"
            self.memory_dir = new_memory_dir
            self.memory_manager.set_active_dir(new_memory_dir)
            self.memory_manager.set_search_dirs(
                [project_memory_dir(p["path"]) for p in self.project_manager.list_projects()]
            )
            logger.info(f"[switch_project] memory → {new_memory_dir}")

            # 5. Reload TemplateManager (reads dirs from settings)
            self.template_manager = get_template_manager(work_dir=Path(resolved))
            logger.info(f"[switch_project] templates reloaded")

            # 6. Toolset workspace roots: nothing to mutate here anymore —
            # instances are project-scoped by the resolver, so the switched
            # project's chats land on instances rooted in the new directory.

            # 7. Reset memory + learning system singletons
            try:
                import pantheon.internal.memory_system.plugin as _mem_plugin
                _mem_plugin._memory_runtime = None
            except Exception:
                pass
            try:
                import pantheon.internal.learning_system.plugin as _learn_plugin
                _learn_plugin._learning_runtime = None
            except Exception:
                pass
            # Recreate plugins with new settings so MemoryRuntime
            # initializes from the new project's .pantheon/memory-store
            try:
                from pantheon.team.plugin_registry import create_plugins
                self._plugins = create_plugins(_settings_mod._settings)
                from pantheon.internal.memory_system.plugin import MemorySystemPlugin
                self._memory_plugin = None
                for p in self._plugins:
                    if isinstance(p, MemorySystemPlugin):
                        self._memory_plugin = p
                        break
            except Exception as e:
                logger.warning(f"[switch_project] plugin reset failed: {e}")
            logger.info("[switch_project] memory system + plugins reset")

            # 8. Clear per-chat team cache (stale references)
            self.chat_teams.clear()

            return {
                "success": True,
                "project": info.to_dict(),
                "message": f"Switched to {info.name}",
            }
        except Exception as e:
            logger.error(f"[switch_project] Failed: {e}")
            return {"success": False, "message": str(e)}


    def _provider_settings(self):
        # Legacy Agent settings remain process-scoped until runtime extraction.
        return get_settings()


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

        if model.startswith(('fleet-model://', 'fleet-route://')):
            from pantheon.models.client import parse_ref, parse_route_ref
            try:
                (parse_route_ref if model.startswith('fleet-route://') else parse_ref)(model)
                return True, ''  # ownership/readiness is checked again at invocation
            except ValueError as error:
                return False, str(error)
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


    @tool
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
    async def set_llm_proxy(
        self, enabled: bool, base_url: str = "", api_key: str = ""
    ) -> dict:
        """Toggle 'platform budget' mode for THIS local backend (frontend-only).

        enabled=True: route every LLM call through the platform LiteLLM proxy
        (base_url) using the user's per-user virtual key (api_key) — usage spends
        against the user's platform budget — bypassing (NOT deleting) the user's own
        provider keys. enabled=False: go back to the user's own keys.

        Sets the env in this process so it takes effect immediately. The frontend
        re-pushes this on every connect, so it survives backend restarts without
        persisting the virtual key to disk.
        """
        import os

        try:
            if enabled:
                if not base_url or not api_key:
                    return {
                        "success": False,
                        "message": "base_url and api_key are required when enabling platform budget",
                    }
                # Dedicated env so the user's own LLM_API_BASE/KEY are never touched.
                os.environ["PANTHEON_PLATFORM_PROXY_BASE"] = base_url
                os.environ["PANTHEON_PLATFORM_PROXY_KEY"] = api_key
                os.environ["LLM_FORCE_PROXY"] = "true"
            else:
                os.environ.pop("LLM_FORCE_PROXY", None)
                os.environ.pop("PANTHEON_PLATFORM_PROXY_BASE", None)
                os.environ.pop("PANTHEON_PLATFORM_PROXY_KEY", None)
            # Rebuild the model selector so quality-tier chains (high/normal/low)
            # re-resolve under the new mode — under budget they must use only the
            # platform's providers, not a cached local one.
            try:
                from pantheon.utils.model_selector import reset_model_selector
                reset_model_selector()
            except Exception as e:
                logger.warning(f"set_llm_proxy: model selector reset failed: {e}")
            return {
                "success": True,
                "enabled": bool(enabled),
                "message": (
                    "Platform budget enabled — LLM calls now use the platform proxy."
                    if enabled
                    else "Platform budget disabled — using your own API keys."
                ),
            }
        except Exception as e:
            logger.error(f"set_llm_proxy failed: {e}")
            return {"success": False, "message": str(e)}
