"""Installed Apps in the Agent's App directory (docs/agent-app-directory.md).

The deployment's Apps are the Agent's toolsets. The other Apps the owner has
installed are listed by the Desktop with their declared tools and window
actions; each becomes a provider here, so describe_app and call_app_tool reach
it like any toolset. Calls go through the Desktop's agent_app_call.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import time

from .agent import ToolInfo, ToolProvider
from .utils.log import logger

# The Apps of the Agent's deployment (pantheon.apps.general_agent_preset's
# PROVIDERS): its toolsets already, so not listed again.
DEPLOYMENT_APPS = frozenset({'file-manager', 'shell', 'integrated-notebook', 'web', 'evolution',
                             'desktop', 'fleet', 'model-services-management'})

# A failed listing (the Desktop's computer still starting) is tried again
# after this long, not on every model call.
RETRY_SECONDS = 60
LIST_TIMEOUT_SECONDS = 20


def provider_name(app_id: str) -> str | None:
    """The App's name in call_app_tool: its id with separators as underscores."""
    name = re.sub(r'_+', '_', re.sub(r'[^A-Za-z0-9]', '_', app_id or '')).strip('_')
    return name if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name) else None


class LibraryAppProvider(ToolProvider):
    """One installed App: its declared tools and window actions."""

    def __init__(self, directory: "AppDirectory", entry: dict, name: str):
        self.toolset_name = name
        self.app_id = entry['app_id']
        self._directory = directory
        self._functions = {f['name']: f for f in entry['tools'] + entry['actions']}
        description = entry.get('description') or entry.get('name') or ''
        self.description = description.strip().splitlines()[0][:200] if description.strip() else ''

    async def list_tools(self) -> list[ToolInfo]:
        return [ToolInfo(name=name, description=f.get('description', ''), inputSchema=dict(f))
                for name, f in self._functions.items()]

    async def call_tool(self, name: str, args: dict):
        if name not in self._functions:
            raise ValueError(f"{self.app_id} declares no tool or action '{name}'")
        return await self._directory.desktop.call_tool('agent_app_call', {
            'app_id': self.app_id, 'name': name, 'args': args or {}, 'holder': self._directory.holder})


class AppDirectory:
    """The installed Apps one Agent (one conversation) reaches."""

    def __init__(self, desktop: ToolProvider, exclude=()):
        self.desktop = desktop
        self.exclude = sorted(set(exclude))
        # The Agent's lease on the Apps it uses (agent_apps.AgentLeases).
        self.holder = secrets.token_hex(8)
        self._failed_at = 0.0
        self._lock = asyncio.Lock()

    async def providers(self, *, force: bool = False) -> dict[str, LibraryAppProvider] | None:
        """The installed Apps as providers, or None when the Desktop could
        not list them (tried again after RETRY_SECONDS, or when forced)."""
        async with self._lock:
            if not force and time.monotonic() - self._failed_at < RETRY_SECONDS:
                return None
            try:
                reply = await asyncio.wait_for(
                    self.desktop.call_tool('agent_app_directory', {'exclude': self.exclude}), LIST_TIMEOUT_SECONDS)
                if not isinstance(reply, dict) or not reply.get('success'):
                    raise RuntimeError((reply or {}).get('error') if isinstance(reply, dict) else 'invalid reply')
            except Exception as exc:
                self._failed_at = time.monotonic()
                logger.info(f"Installed Apps are not listed yet: {exc}")
                return None
            self._failed_at = 0.0
            out = {}
            for entry in reply.get('apps') or []:
                name = provider_name(entry.get('app_id', ''))
                if name and name not in out:
                    out[name] = LibraryAppProvider(self, entry, name)
            return out
