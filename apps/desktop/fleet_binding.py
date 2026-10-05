"""Desktop-owned Fleet control connection, supplied by its composition owner.

The connection carries the Desktop owner's Fleet authority. Consuming Agents
receive only their ordinary Desktop tool grant, never these credentials. A
closed or disconnected binding cannot discover a different ambient identity.
The binder must supply a separate connection, not one borrowed from a sibling.
"""
import asyncio
from pathlib import Path

from pantheon.apps.resolver import AppInstanceResolver


class DesktopFleetBinding:
    def __init__(self, *, fleet_id: str, node_id: str, user_seed: str,
                 workspace: Path, connection):
        root = Path(workspace)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError('Desktop Fleet workspace must be an existing absolute directory')
        if not isinstance(user_seed, str) or not user_seed:
            raise ValueError('Desktop Fleet requires an explicit user identity')
        if connection is None:
            raise ValueError('Desktop Fleet requires an owned connection')
        self._resolver = AppInstanceResolver(fleet_id, node_id, user_seed,
            str(root.resolve()), connection=connection)
        self._retired = False
        self._close_lock = asyncio.Lock()

    @property
    def resolver(self):
        if self._retired:
            raise RuntimeError('Desktop Fleet binding is closed')
        return self._resolver

    async def close(self):
        # Refuse new work immediately, but retain ownership if cleanup fails or
        # is cancelled. A subsequent close retries the resolver's own cleanup.
        self._retired = True
        async with self._close_lock:
            await self._resolver.close()
