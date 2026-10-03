"""Authenticated per-user platform RPC host, separate from Agent execution."""

import asyncio
import threading
from pathlib import Path

from pantheon.toolset import ToolSet, tool

from .apps_api import AppServicesAPI
from .fleet_api import FleetAPI
from .models_api import ModelServicesAPI
from .projects_api import ProjectsAPI


class PlatformService(AppServicesAPI, FleetAPI, ModelServicesAPI, ProjectsAPI, ToolSet):
    """Serve platform operations on the existing user-scoped service bus.

    Deployment supplies the NATS credentials and Fleet coordinates, just as it
    does for other service workers. This host neither issues a broader identity
    nor proxies through ChatRoom. Desktop and Hub routing migrate separately.
    """

    def __init__(self, name: str = "pantheon-platform", workspace_path: str | None = None, **kwargs):
        self.workspace_path = str(Path(workspace_path or Path.cwd()).resolve())
        self._project_manager = None
        self._project_manager_lock = threading.Lock()
        # The legacy worker's re-exec bypasses snapshot shutdown and assumes it
        # owns Agent/browser processes. Platform restarts use its supervisor.
        kwargs["allow_in_place_restart"] = False
        super().__init__(name=name, **kwargs)

    def _projects(self):
        # Avoid scanning a network-backed workspace on the readiness path.
        with self._project_manager_lock:
            if self._project_manager is None:
                from .projects import ProjectManager
                self._project_manager = ProjectManager(
                    active_path=self.workspace_path, activate_on_start=False)
            return self._project_manager

    @tool(exclude=True)
    async def platform_info(self) -> dict:
        """Advertise implemented platform endpoints without launching Apps."""
        return {
            "service": "pantheon-platform",
            "api_version": 1,
            "methods": sorted(self.functions),
        }

    async def cleanup(self):
        # Stop accepting platform mutations before shutdown's final snapshot.
        worker = getattr(self, "worker", None)
        if worker is not None:
            await getattr(worker, "drain", worker.stop)()
        backend = getattr(self, "_backend", None)
        connection = getattr(backend, "_nc", None)
        if connection is not None:
            await connection.close()
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
