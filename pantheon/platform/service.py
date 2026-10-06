"""Authenticated per-user platform RPC host, separate from Agent execution."""

import asyncio
import threading
import time
from pathlib import Path

from pantheon.toolset import ToolSet, tool

from .apps_api import AppServicesAPI
from .fleet_api import FleetAPI
from .models_api import ModelServicesAPI
from .projects_api import ProjectsAPI
from .health import PlatformHealth
from .store_api import StoreAPI
from .model_directory import ModelDirectoryAPI
from .oauth_api import OAuthAPI


class PlatformService(OAuthAPI, ModelDirectoryAPI, StoreAPI, PlatformHealth, AppServicesAPI, FleetAPI, ModelServicesAPI, ProjectsAPI, ToolSet):
    """Serve platform operations on the existing user-scoped service bus.

    Deployment supplies the NATS credentials and Fleet coordinates, just as it
    does for other service workers. This host neither issues a broader identity
    nor proxies through ChatRoom. Desktop and Hub routing migrate separately.
    """

    def __init__(self, name: str = "pantheon-platform", workspace_path: str | None = None,
                 app_preset=None, app_preset_source=None, model_credential_preparer=None,
                 owner_state_directory=None, **kwargs):
        from .owner_state import configured_directory
        self._owner_state_directory = configured_directory(owner_state_directory)
        self.workspace_path = str(Path(workspace_path or Path.cwd()).resolve())
        self._project_manager = None
        self._project_manager_lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        self._model_credential_preparer = model_credential_preparer
        # The legacy worker's re-exec bypasses snapshot shutdown and assumes it
        # owns Agent/browser processes. Platform restarts use its supervisor.
        kwargs["allow_in_place_restart"] = False
        super().__init__(name=name, **kwargs)
        from .app_preset import AppPreset
        self._app_preset = AppPreset(app_preset, advance=self._advance_app_preset, load=app_preset_source)

    async def _advance_app_preset(self, **spec):
        if spec.get('kind') == 'model-services':
            return await self.model_services_bootstrap(**{k: v for k, v in spec.items() if k != 'kind'})
        return await self.fleet_app_deploy(**spec)

    async def run_setup(self):
        from .owner_state import prepare_directory
        # Prepare before any maintenance/startup writer. Merely constructing a
        # service or previewing a recipe does not create state directories.
        prepare_directory(self._owner_state_directory)
        if self.worker is not None and hasattr(self.worker, "set_activity_callback"):
            self.worker.set_activity_callback(self._get_platform_status)
        self._start_dependency_maintenance()
        self._app_preset.start()

    def _get_platform_status(self):
        # A platform ping is not a statement that all hosted Apps are idle.
        return {**self._get_host_metrics(), "activity_scope": "platform"}

    def _projects(self):
        # Avoid scanning a network-backed workspace on the readiness path.
        with self._project_manager_lock:
            if self._project_manager is None:
                from .projects import ProjectManager
                self._project_manager = ProjectManager(
                    active_path=self.workspace_path, activate_on_start=False)
            return self._project_manager

    def _provider_settings(self):
        from pantheon.settings import Settings
        return Settings(self._store_workdir(), isolated_env=True)

    @tool(exclude=True)
    async def platform_info(self) -> dict:
        """Advertise implemented platform endpoints without launching Apps."""
        return {
            "service": "pantheon-platform",
            "api_version": 1,
            "methods": sorted(self.functions),
        }

    @tool(exclude=True)
    async def platform_app_preset_status(self) -> dict:
        """Startup progress only; never substitutes for live App/node health."""
        return self._app_preset.status()

    async def cleanup(self):
        await self._app_preset.stop()
        # Release login waiters before draining accepted RPCs.
        await self._stop_oauth()
        # Stop accepting platform mutations before shutdown's final snapshot.
        worker = getattr(self, "worker", None)
        if worker is not None:
            await getattr(worker, "drain", worker.stop)()
        await self._stop_dependency_maintenance()
        backend = getattr(self, "_backend", None)
        connection = getattr(backend, "_nc", None)
        if connection is not None:
            await connection.close()
        await self._stop_model_directory()
        await self._stop_health_refresh()
        task = getattr(self, "_fleet_session_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._fleet_session_task = None
        fleet = getattr(self, "_fleet_ts", None)
        if fleet is not None:
            await fleet.cleanup()
            self._fleet_ts = None
        manager = getattr(self, "_model_services", None)
        if manager is not None:
            try:
                await manager.client.aclose()
            finally:
                if manager.resolver is not None:
                    await manager.resolver.close()
                del self._model_services
        self._fleet_session_started = False
