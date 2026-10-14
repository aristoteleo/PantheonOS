"""Library Apps offered to the Agent (docs/agent-app-directory.md).

The Agent reaches an installed App's declared tools (backend methods) and
window actions through the Desktop, which holds the packages and the windows.
Only what a manifest declares is listed or callable, with only its declared
parameters. The Agent holds a lease on an App it used until ten minutes after
its last call, so the node's idle stop leaves the App running meanwhile.
"""
from __future__ import annotations

import asyncio
import re
import time

from pantheon.apps.schema import parse_manifest
from pantheon.apps.tool_profiles import tool_function
from pantheon.utils.log import logger

POLICIES = ('on_demand', 'resident', 'disabled')
# The Agent keeps an App in use this long after its last call to it.
AGENT_LEASE_SECONDS = 600
# A node lease lasts five minutes; renew well before it lapses.
RENEW_SECONDS = 240
_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}')
_HOLDER = re.compile(r'[a-z0-9]{8,32}')


def action_function(action: dict) -> dict | None:
    """A manifest window action as a model function, or None if malformed.
    Its parameters are those it declares, each optional."""
    name = action.get('name') if isinstance(action, dict) else None
    params = action.get('params') or {}
    if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(params, dict):
        return None
    properties = {}
    for key, doc in params.items():
        if not isinstance(key, str) or not _NAME.fullmatch(key):
            return None
        properties[key] = {'description': doc} if isinstance(doc, str) and doc else {}
    description = action.get('description') if isinstance(action.get('description'), str) else ''
    return {'name': name,
            'description': (description + '\n\n' if description else '')
                           + "Window action: runs in this App's window on the owner's open desktop.",
            'parameters': {'type': 'object', 'properties': properties, 'additionalProperties': False}}


def offer(manifest: dict) -> dict | None:
    """What an installed App offers the Agent: its declared tools and actions,
    and whether it has a window and a backend. None when it offers nothing."""
    try:
        app = parse_manifest(manifest)
        tools = [tool_function(tool) for tool in app.provides.tools if not tool.hidden]
    except Exception:
        return None
    actions = [f for f in (action_function(a) for a in manifest.get('actions') or []) if f is not None]
    names = [f['name'] for f in tools + actions]
    if len(set(names)) != len(names):
        # A tool and an action of the same name could not be told apart.
        return None
    window = bool(app.entry.frontend)
    if not names and not window:
        return None
    return {'app_id': app.id, 'name': app.name or app.id, 'description': app.description or '',
            'tools': tools, 'actions': actions, 'window': window,
            'backend': bool(app.entry.backend or app.entry.fleetBackend or manifest.get('execution'))}


def check_args(function: dict, args) -> dict:
    """The arguments if they are exactly the function's declared parameters."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError('Arguments must be an object')
    parameters = function['parameters']
    unknown = set(args) - set(parameters.get('properties', {}))
    if unknown:
        raise ValueError(f"Unknown arguments for {function['name']}: {', '.join(sorted(unknown))}")
    missing = set(parameters.get('required', [])) - set(args)
    if missing:
        raise ValueError(f"Missing arguments for {function['name']}: {', '.join(sorted(missing))}")
    return args


class AgentLeases:
    """The Agent's leases on App instances, one per holder (conversation).

    use() takes or renews the holder's lease on each call. A background task
    renews leases used in the last AGENT_LEASE_SECONDS and releases the rest.
    """

    def __init__(self, usage):
        # usage(binding, lease_id, release) -> awaitable; the node lease call.
        self._usage = usage
        self._leases: dict[tuple, dict] = {}
        self._task: asyncio.Task | None = None

    @staticmethod
    def lease_id(holder: str) -> str:
        if not isinstance(holder, str) or not _HOLDER.fullmatch(holder):
            raise ValueError('Invalid Agent lease holder')
        return 'agent-' + holder

    async def use(self, binding: dict, holder: str) -> None:
        lease = self.lease_id(holder)
        key = (binding['node_id'], binding['instance_id'], binding['revision'], binding['generation'], lease)
        now = time.monotonic()
        entry = self._leases.get(key)
        if entry is None or now - entry['renewed'] >= RENEW_SECONDS:
            await self._usage(binding, lease, False)
            entry = self._leases[key] = {'binding': binding, 'renewed': now, 'used': now}
        entry['used'] = now
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._maintain())

    async def release(self, binding: dict, holder: str) -> None:
        """Give up the holder's lease on an instance now (the Agent stops it)."""
        lease = self.lease_id(holder)
        key = (binding['node_id'], binding['instance_id'], binding['revision'], binding['generation'], lease)
        if self._leases.pop(key, None) is not None:
            try:
                await self._usage(binding, lease, True)
            except Exception as exc:
                logger.debug(f'Agent lease {lease} on {key[1]} was not released: {exc}')

    async def tick(self) -> None:
        """Renew leases still in use; release those past their time."""
        now = time.monotonic()
        for key, entry in list(self._leases.items()):
            lease = key[-1]
            try:
                if now - entry['used'] >= AGENT_LEASE_SECONDS:
                    self._leases.pop(key, None)
                    await self._usage(entry['binding'], lease, True)
                elif now - entry['renewed'] >= RENEW_SECONDS:
                    await self._usage(entry['binding'], lease, False)
                    entry['renewed'] = now
            except Exception as exc:
                # A stopped or replaced instance has no lease to keep.
                self._leases.pop(key, None)
                logger.debug(f'Agent lease {lease} on {key[1]} ended: {exc}')

    async def _maintain(self) -> None:
        while self._leases:
            await asyncio.sleep(30)
            await self.tick()

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
