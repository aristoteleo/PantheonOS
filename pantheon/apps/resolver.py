"""AppInstanceResolver — a toolset name resolved to a live App instance (P3).

The endpoint-free path: given the service names templates already use
('shell', 'file_manager'), ensure an instance is running on this user's node
via the fleet supervisor and hand back the service_id to dial directly
(ToolsetProxy.from_toolset — the mode that has existed all along).

Gated by environment, additive by construction: nothing uses this unless
PANTHEON_APPS_VIA_FLEET is truthy and the fleet coordinates are present.

    PANTHEON_APPS_VIA_FLEET=1
    PANTHEON_FLEET_ID=<fleet id>          (the user's fleet)
    PANTHEON_FLEET_NODE_ID=<node id>      (the node to place on — own sandbox)
    PANTHEON_USER_SEED=<id_hash>          (instance service-id seeds)
"""

from __future__ import annotations

import os

from pantheon.utils.log import logger


def apps_via_fleet_enabled() -> bool:
    return os.environ.get("PANTHEON_APPS_VIA_FLEET", "").strip().lower() in (
        "1", "true", "on", "yes",
    )


class AppInstanceResolver:
    """Ensure-and-dial App instances for this user on one node."""

    def __init__(self, fleet_id: str, node_id: str, user_seed: str, workdir: str):
        self._fleet = fleet_id
        self._node = node_id
        self._seed = user_seed
        self._workdir = workdir
        self._nc = None
        self._client = None
        self._started: dict[str, str] = {}  # service_type -> service_id

    @classmethod
    def from_env(cls, workdir: str | None = None) -> "AppInstanceResolver | None":
        """The environment-configured resolver, or None when not wired."""
        if not apps_via_fleet_enabled():
            return None
        fleet_id = os.environ.get("PANTHEON_FLEET_ID", "")
        node_id = os.environ.get("PANTHEON_FLEET_NODE_ID", "")
        seed = os.environ.get("PANTHEON_USER_SEED") or os.environ.get("ID_HASH", "")
        if not (fleet_id and node_id and seed):
            logger.warning(
                "[apps] PANTHEON_APPS_VIA_FLEET set but fleet coordinates missing "
                "(PANTHEON_FLEET_ID / PANTHEON_FLEET_NODE_ID / PANTHEON_USER_SEED)"
            )
            return None
        return cls(fleet_id, node_id, seed, workdir or os.getcwd())

    def resolves(self, service_type: str) -> bool:
        """Whether this resolver can serve the named toolset as an App."""
        from pantheon.apps.catalog import by_service_type

        return service_type in by_service_type()

    async def _ensure_client(self):
        if self._client is None:
            import nats

            from pantheon.apps.client import AppClient

            servers = os.environ.get("NATS_SERVERS", "nats://localhost:4222").split("|")
            self._nc = await nats.connect(servers=servers)
            self._client = AppClient(self._nc, self._fleet)
        return self._client

    async def ensure_instance(self, service_type: str) -> str:
        """Start (idempotently) and return the instance's service_id."""
        if service_type in self._started:
            return self._started[service_type]
        from pantheon.apps.catalog import by_service_type
        from pantheon.apps.spec import apphost_spec

        entry = by_service_type()[service_type]
        client = await self._ensure_client()
        spec = apphost_spec(
            entry.app_id,
            user_seed=self._seed,
            workdir=self._workdir,
            env={k: v for k, v in os.environ.items()
                 if k.startswith("NATS_") or k in ("PYTHONPATH", "PATH")},
        )
        resp = await client.start(self._node, spec)
        if not resp.get("ok"):
            raise RuntimeError(f"app_start {entry.app_id} on {self._node}: {resp}")
        self._started[service_type] = spec["service_id"]
        logger.info(
            f"[apps] {service_type} -> app {entry.app_id} instance "
            f"{spec['service_id'][:12]}… on node {self._node}"
        )
        return spec["service_id"]

    async def close(self):
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._client = None
