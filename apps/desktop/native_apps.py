"""Dispatch native windows to App-owned drivers from the official node catalog.

Like module-form service bindings, user-controlled module paths never execute
in the display daemon. App frontends, icons and releases are independent.
"""
from importlib import import_module


class NativeAppManager:
    def __init__(self, engine):
        self.engine = engine
        self.drivers = {}
        self.owners = {}

    def _driver(self, app_id):
        if app_id not in self.drivers:
            from pantheon.apps.registry import default_scope_roots
            from pantheon.settings import get_settings
            from pathlib import Path
            from .store_manager import AppStoreManager
            manager = AppStoreManager(default_scope_roots(Path(get_settings().workspace)))
            manager.versions.migrate_bundled()
            installed = manager.inventory(match_id=app_id, with_defaults=False, with_git=False)['apps']
            # Native drivers remain trusted OS integration code. Installing an
            # App never grants its manifest permission to import arbitrary code
            # into the Desktop daemon.
            trusted = {'qupath': 'pantheon.apps.builtin.qupath.native:NativeAppManager'}
            entry = trusted.get(app_id)
            if not installed or not entry:
                raise ValueError(f"No installed native driver for App: {app_id}")
            module, name = entry.rsplit(":", 1)
            self.drivers[app_id] = getattr(import_module(module), name)(self.engine)
        return self.drivers[app_id]

    async def launch(self, app_id, session_id, path="", width=1200, height=800):
        if session_id in self.owners and self.owners[session_id] != app_id:
            raise ValueError("This native session belongs to another App")
        driver = self._driver(app_id)
        # Reserve before awaiting startup; another App cannot race into this id.
        self.owners[session_id] = app_id
        return await driver.launch(app_id, session_id, path, width, height)

    def _owner(self, session_id):
        if session_id not in self.owners:
            raise ValueError("This native desktop session is not running")
        return self.drivers[self.owners[session_id]]

    async def read(self, session_id, **kwargs):
        return await self._owner(session_id).read(session_id, **kwargs)

    async def call(self, session_id, action, args=None):
        return await self._owner(session_id).call(session_id, action, args)

    async def status(self, session_id):
        if session_id not in self.owners:
            return {"session_id": session_id, "running": False}
        return await self._owner(session_id).status(session_id)

    async def close(self, session_id):
        if session_id not in self.owners:
            return {"session_id": session_id, "running": False, "close_requested": False}
        return await self._owner(session_id).close(session_id)
