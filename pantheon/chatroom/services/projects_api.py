"""Project selection and registry APIs, independent of Agent context."""

import asyncio
from pathlib import Path

from pantheon.toolset import tool


class ProjectsAPI:
    def _projects(self):
        return self.project_manager

    @tool
    async def get_project_settings(self) -> dict:
        """Get settings with scope info (global vs project)."""
        def read():
            manager = self._projects()
            project = manager.active_project
            if not project:
                return {"success": False, "message": "No active project"}
            return manager.get_config_scope(project.path)
        return await asyncio.to_thread(read)

    @tool
    async def list_projects(self) -> dict:
        """List all registered projects."""
        projects = await asyncio.to_thread(lambda: self._projects().list_projects())
        return {"projects": projects}

    @tool
    async def get_project_snapshot(self) -> dict:
        """Export stable project IDs and selection; persist missing legacy IDs."""
        return await asyncio.to_thread(lambda: self._projects().snapshot())

    @tool
    async def get_active_project(self) -> dict:
        """Get the selected project and the deployment's home project."""
        def read():
            manager = self._projects()
            project = manager.active_project
            home = manager.default_project
            active = project.to_dict() if project else None
            if active is not None:
                active["is_active"] = True
            return {"active": active, "home": home.to_dict() if home else None}
        return await asyncio.to_thread(read)

    @tool
    async def register_project(self, path: str, name: str = "") -> dict:
        """Register an existing directory without creating or moving its data."""
        def register():
            try:
                resolved = str(Path(path).resolve())
                if not Path(resolved).is_dir():
                    return {"success": False, "message": f"Directory not found: {resolved}"}
                info = self._projects().register(resolved, name)
                return {"success": True, "project": info.to_dict()}
            except Exception as e:
                return {"success": False, "message": str(e)}
        return await asyncio.to_thread(register)

    @tool
    async def remove_project(self, path: str) -> dict:
        """Remove a project from the registry; never delete its files."""
        ok = await asyncio.to_thread(lambda: self._projects().remove(path))
        return {"success": ok, "message": "Removed" if ok else "Not found"}

    @tool
    async def set_active_project(self, path: str) -> dict:
        """Select a project without changing process cwd or any App context."""
        def select():
            resolved = str(Path(path).resolve())
            if not Path(resolved).is_dir():
                return {"success": False, "message": f"Directory does not exist: {resolved}"}
            info = self._projects().select(resolved)
            return {"success": True, "project": info.to_dict()}
        return await asyncio.to_thread(select)
