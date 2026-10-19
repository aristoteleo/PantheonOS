"""Python client for Pantheon-Fleet — the core of the Agent's Fleet toolset.

It speaks the same NATS wire protocol as the Go Runner: it reads the Registry
(JetStream KV) to *see* the Fleet, and uses request/reply to run code and
schedule Transfers. The PantheonOS ToolSet wrapper is a thin layer on top of
this.

    fc = await FleetClient.connect("nats://localhost:4222", fleet="f_...")
    nodes = await fc.list_nodes()
    res   = await fc.run_on_node(nodes[0]["node_id"], "uname -sm")
    await fc.transfer(src, dst, "/data/x.h5ad", "/work/x.h5ad")
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

import nats


def _subj_cmd(fleet: str, node: str) -> str:
    return f"fleet.{fleet}.node.{node}.cmd"


def _subj_transfer_progress(fleet: str, tid: str) -> str:
    return f"fleet.{fleet}.transfer.{tid}.progress"


def _registry_bucket(fleet: str) -> str:
    return f"FLEET_{fleet}_NODES"


class FleetClient:
    """A thin async client over one Fleet."""

    def __init__(self, nc: "nats.NATS", fleet: str):
        self._nc = nc
        self._fleet = fleet
        self._js = nc.jetstream()

    @classmethod
    async def connect(cls, nats_url: str, fleet: str) -> "FleetClient":
        nc = await nats.connect(nats_url)
        return cls(nc, fleet)

    async def list_nodes(self) -> list[dict[str, Any]]:
        """Return every Node record currently in the Registry."""
        try:
            kv = await self._js.key_value(_registry_bucket(self._fleet))
            keys = await kv.keys()
        except Exception:
            return []
        out = []
        for k in keys:
            entry = await kv.get(k)
            out.append(json.loads(entry.value))
        return out

    async def run_on_node(
        self, node: str, code: str, kind: str = "shell", timeout: int = 60
    ) -> dict[str, Any]:
        """Run code on a Node and return its TaskResult."""
        cmd = {
            "type": "run_task",
            "task": {
                "task_id": "t_" + uuid.uuid4().hex[:8],
                "kind": kind,
                "code": code,
                "timeout_s": timeout,
            },
        }
        msg = await self._nc.request(
            _subj_cmd(self._fleet, node), json.dumps(cmd).encode(), timeout=timeout + 5
        )
        return json.loads(msg.data)

    async def transfer(
        self,
        src_node: str,
        dst_node: str,
        src_path: str,
        dst_path: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Schedule a Node->Node Transfer; returns the final progress record."""
        tid = "x_" + uuid.uuid4().hex[:8]

        sub = None
        if on_progress is not None:
            async def _cb(m):  # noqa: ANN001
                on_progress(json.loads(m.data))

            sub = await self._nc.subscribe(
                _subj_transfer_progress(self._fleet, tid), cb=_cb
            )

        cmd = {
            "type": "transfer",
            "transfer": {
                "transfer_id": tid,
                "src_node": src_node,
                "dst_node": dst_node,
                "src_path": src_path,
                "dst_path": dst_path,
                "options": {"verify": "sha256"},
            },
        }
        try:
            msg = await self._nc.request(
                _subj_cmd(self._fleet, src_node),
                json.dumps(cmd).encode(),
                timeout=timeout,
            )
            return json.loads(msg.data)
        finally:
            if sub is not None:
                await sub.unsubscribe()

    async def ping(self, node: str) -> dict[str, Any]:
        msg = await self._nc.request(
            _subj_cmd(self._fleet, node), json.dumps({"type": "ping"}).encode(), timeout=3
        )
        return json.loads(msg.data)

    async def close(self) -> None:
        await self._nc.drain()
