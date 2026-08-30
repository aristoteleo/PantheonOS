"""AppClient — the control plane's hand on a node's App supervisor (P3).

Speaks the fleet cmd protocol (proto.Command over the node's cmd subject) to
start/stop/list App instances. The brain builds a spec with
`pantheon.apps.spec.apphost_spec` and hands it here; the runner does the
supervising. Deliberately a thin, dependency-light NATS client — it reuses
whatever connection the caller already holds.
"""

from __future__ import annotations

import json
from typing import Any


def _subj_node_cmd(fleet_id: str, node_id: str) -> str:
    # Mirror of fleet's proto.SubjNodeCmd — the wire contract, not shared code.
    return f"fleet.{fleet_id}.node.{node_id}.cmd"


class AppClient:
    """Drive App instances on fleet nodes over an existing NATS connection."""

    def __init__(self, nc, fleet_id: str):
        self._nc = nc
        self._fleet = fleet_id

    async def _cmd(self, node_id: str, payload: dict, timeout: float) -> dict[str, Any]:
        msg = await self._nc.request(
            _subj_node_cmd(self._fleet, node_id),
            json.dumps(payload).encode(),
            timeout=timeout,
        )
        return json.loads(msg.data.decode())

    async def start(self, node_id: str, spec: dict, timeout: float = 15.0) -> dict:
        """app_start: spec is an apphost_spec()/proto.AppCommand payload."""
        return await self._cmd(node_id, {"type": "app_start", "app": spec}, timeout)

    async def stop(self, node_id: str, app_id: str, scope: str = "app",
                   timeout: float = 10.0) -> dict:
        return await self._cmd(
            node_id, {"type": "app_stop", "app": {"app_id": app_id, "scope": scope}}, timeout
        )

    async def list(self, node_id: str, timeout: float = 10.0) -> list[dict]:
        resp = await self._cmd(node_id, {"type": "app_list"}, timeout)
        return resp.get("instances") or []

    async def ping(self, node_id: str, timeout: float = 5.0) -> bool:
        try:
            resp = await self._cmd(node_id, {"type": "ping"}, timeout)
            return "pong" in resp
        except Exception:
            return False
