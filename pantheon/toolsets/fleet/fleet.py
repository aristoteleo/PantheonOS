"""Fleet toolset — the Agent's interface to a Pantheon-Fleet.

It speaks the Fleet control plane (NATS): it reads the Registry (JetStream KV)
to *see* every Node, runs code on a chosen Node, and schedules Node->Node bulk
Transfers over the libp2p data plane. The Go Runner (`fleet up`) serves the
other side; see ``fleet/`` and ``docs/pantheon-fleet.md``.

Wiring: a Fleet is selected by the user's API key via the Controller, or — for
local/dev — by an explicit NATS url + fleet id. Config resolves from constructor
args first, then environment:

    FLEET_NATS_URL / FLEET_ID            (dev: connect directly)
    FLEET_CONTROLLER_URL / FLEET_KEY     (prod: key -> fleet via the Controller)

The toolset never hard-fails at setup when no Fleet is configured; its tools
just return ``{"success": False, "error": ...}`` until one is.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger


def _registry_bucket(fleet: str) -> str:
    return f"FLEET_{fleet}_NODES"


def _subj_cmd(fleet: str, node: str) -> str:
    return f"fleet.{fleet}.node.{node}.cmd"


def _subj_transfer_progress(fleet: str, tid: str) -> str:
    return f"fleet.{fleet}.transfer.{tid}.progress"


class FleetToolSet(ToolSet):
    """Let the Agent see and drive a Pantheon-Fleet of compute Nodes.

    Args:
        name: Toolset name (default "fleet").
        nats_url: Fleet NATS url (dev: bypass the Controller).
        fleet_id: Fleet id (dev: bypass the Controller).
        controller_url: Controller url that maps an API key to a Fleet.
        key: API key selecting the user's Fleet (via the Controller).
        **kwargs: Forwarded to ToolSet.
    """

    def __init__(
        self,
        name: str = "fleet",
        *,
        nats_url: str | None = None,
        fleet_id: str | None = None,
        controller_url: str | None = None,
        key: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self._nats_url = nats_url or os.environ.get("FLEET_NATS_URL")
        self._fleet_id = fleet_id or os.environ.get("FLEET_ID")
        self._controller_url = controller_url or os.environ.get("FLEET_CONTROLLER_URL")
        self._key = key or os.environ.get("FLEET_KEY") or os.environ.get("PANTHEON_API_KEY")
        self._nc = None
        self._js = None
        self._connect_lock = asyncio.Lock()
        # transfer_id -> latest TransferProgress dict (for transfer_status)
        self._transfers: dict[str, dict] = {}
        self._transfer_tasks: dict[str, asyncio.Task] = {}

    # ---- lifecycle ----------------------------------------------------------

    async def run_setup(self):
        # Pre-warm the connection when a Fleet is configured; never hard-fail so
        # the toolset can be attached even before a Fleet exists.
        try:
            await self._ensure_connected()
            logger.info(f"[fleet] connected to {self._nats_url} fleet={self._fleet_id}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[fleet] not connected at setup: {e}")

    async def cleanup(self):
        for t in list(self._transfer_tasks.values()):
            t.cancel()
        self._transfer_tasks.clear()
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:  # noqa: BLE001
                pass
        self._nc = None
        self._js = None

    # ---- connection ---------------------------------------------------------

    async def _resolve_via_controller(self):
        import httpx

        url = self._controller_url.rstrip("/") + "/join"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"key": self._key})
            r.raise_for_status()
            data = r.json()
        self._nats_url = data["nats_url"]
        self._fleet_id = data["fleet_id"]

    async def _ensure_connected(self):
        if self._nc is not None and self._js is not None:
            return
        async with self._connect_lock:
            if self._nc is not None and self._js is not None:
                return
            if (not self._nats_url or not self._fleet_id) and self._controller_url and self._key:
                await self._resolve_via_controller()
            if not self._nats_url or not self._fleet_id:
                raise RuntimeError(
                    "Fleet not configured: pass nats_url+fleet_id or controller_url+key "
                    "(env FLEET_NATS_URL/FLEET_ID or FLEET_CONTROLLER_URL/FLEET_KEY)."
                )
            import nats

            self._nc = await nats.connect(self._nats_url, name="pantheon-fleet-toolset")
            self._js = self._nc.jetstream()

    # ---- registry helpers ---------------------------------------------------

    async def _read_nodes(self) -> list[dict]:
        await self._ensure_connected()
        try:
            kv = await self._js.key_value(_registry_bucket(self._fleet_id))
            keys = await kv.keys()
        except Exception:  # noqa: BLE001 — empty/absent bucket => no nodes
            return []
        out: list[dict] = []
        for k in keys:
            try:
                entry = await kv.get(k)
                out.append(json.loads(entry.value))
            except Exception:  # noqa: BLE001
                continue
        return out

    @staticmethod
    def _summarize(n: dict) -> dict:
        cap = n.get("capability", {})
        st = n.get("state", {})
        net = n.get("net", {})
        return {
            "node_id": n.get("node_id"),
            "name": n.get("name"),
            "labels": n.get("labels", []),
            "status": st.get("status"),
            "os": cap.get("os"),
            "arch": cap.get("arch"),
            "cpu_cores": cap.get("cpu_cores"),
            "gpu": cap.get("gpu") or "",
            "ram_gb": cap.get("ram_gb"),
            "disk_free_gb": cap.get("disk_free_gb"),
            "load": st.get("load", {}),
            "reachability": net.get("reachability"),
            "last_seen": n.get("last_seen"),
        }

    # ---- Observe ------------------------------------------------------------

    @tool
    async def fleet_list_nodes(self) -> dict:
        """List every Node currently in your Fleet (from the Registry).

        Returns a compact summary per Node — id, name, labels, online status,
        OS/arch, CPU/GPU/RAM/free-disk, live load, and data-plane reachability —
        so you can decide where to run code or move data.

        Returns:
            dict: {"success": bool, "count": int, "nodes": list[dict]}
        """
        try:
            nodes = [self._summarize(n) for n in await self._read_nodes()]
            return {"success": True, "count": len(nodes), "nodes": nodes}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    @tool
    async def fleet_node_info(self, node_id: str) -> dict:
        """Get the full record for one Node: capability, live state, network.

        Args:
            node_id: The Node's id (from fleet_list_nodes).

        Returns:
            dict: {"success": bool, "node": dict} or {"success": False, "error": str}.
        """
        try:
            await self._ensure_connected()
            kv = await self._js.key_value(_registry_bucket(self._fleet_id))
            entry = await kv.get(node_id)
            return {"success": True, "node": json.loads(entry.value)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"node {node_id} not found: {e}"}

    @tool
    async def fleet_status(self) -> dict:
        """Summarize the whole Fleet: node counts, aggregate capacity, transfers.

        Returns:
            dict: totals (nodes, online, cpu cores, RAM), gpu node ids, and the
            number of in-flight transfers this toolset has started.
        """
        try:
            nodes = await self._read_nodes()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        online = [n for n in nodes if n.get("state", {}).get("status") == "online"]
        total_cores = sum(int(n.get("capability", {}).get("cpu_cores", 0) or 0) for n in nodes)
        total_ram = sum(float(n.get("capability", {}).get("ram_gb", 0) or 0) for n in nodes)
        gpu_nodes = [n.get("node_id") for n in nodes if n.get("capability", {}).get("gpu")]
        active = [t for t, p in self._transfers.items() if p.get("state") not in ("done", "failed")]
        return {
            "success": True,
            "nodes_total": len(nodes),
            "nodes_online": len(online),
            "total_cpu_cores": total_cores,
            "total_ram_gb": round(total_ram, 1),
            "gpu_nodes": gpu_nodes,
            "active_transfers": len(active),
        }

    # ---- Execute ------------------------------------------------------------

    @tool
    async def run_on_node(
        self, node_id: str, code: str, kind: str = "shell", timeout: int = 60
    ) -> dict:
        """Run code on a specific Node and return its result.

        Args:
            node_id: Target Node id (from fleet_list_nodes).
            code: The shell command or Python source to run on the Node.
            kind: "shell" (default) or "python".
            timeout: Seconds before the Node aborts the task. Default 60.

        Returns:
            dict: {"success", "exit_code", "stdout", "stderr", "error"} as
            reported by the Node.
        """
        try:
            await self._ensure_connected()
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
                _subj_cmd(self._fleet_id, node_id), json.dumps(cmd).encode(), timeout=timeout + 5
            )
            res = json.loads(msg.data)
            res.setdefault("success", res.get("exit_code", 1) == 0 and not res.get("error"))
            return res
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    # ---- Transfer -----------------------------------------------------------

    @tool
    async def transfer(
        self,
        src_node: str,
        src_path: str,
        dst_node: str,
        dst_path: str,
        verify: str = "sha256",
        wait: bool = False,
        timeout: int = 600,
    ) -> dict:
        """Move a file directly Node->Node over the data plane (libp2p).

        Non-blocking by default: returns a transfer_id immediately; poll
        transfer_status(transfer_id) for progress. The source Node streams the
        file to the destination over a direct (hole-punched) connection, falling
        back to a relay when the two can't connect directly. sha256 is verified
        end-to-end.

        Args:
            src_node: Node that has the file.
            src_path: Path of the file on src_node.
            dst_node: Node that should receive the file.
            dst_path: Destination path on dst_node.
            verify: "sha256" (default) or "none".
            wait: If True, block until the transfer finishes and return the final
                record (incl. sha256 and whether it went "direct" or via "relay").
            timeout: Max seconds for completion. Default 600.

        Returns:
            dict: {"success", "transfer_id", "state", ...}; the final record when
            wait=True, otherwise an immediate {"state": "started"}.
        """
        try:
            await self._ensure_connected()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        tid = "x_" + uuid.uuid4().hex[:8]
        self._transfers[tid] = {"transfer_id": tid, "state": "pending"}
        task = asyncio.create_task(
            self._run_transfer(tid, src_node, src_path, dst_node, dst_path, verify, timeout)
        )
        self._transfer_tasks[tid] = task
        if wait:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout + 10)
            except Exception as e:  # noqa: BLE001
                return {"success": False, "transfer_id": tid, "error": str(e), **self._transfers.get(tid, {})}
            final = self._transfers.get(tid, {})
            return {"success": final.get("state") == "done", "transfer_id": tid, **final}
        return {
            "success": True,
            "transfer_id": tid,
            "state": "started",
            "note": "poll transfer_status(transfer_id) for progress",
        }

    async def _run_transfer(self, tid, src_node, src_path, dst_node, dst_path, verify, timeout):
        progress_subj = _subj_transfer_progress(self._fleet_id, tid)

        async def _on_prog(m):
            try:
                self._transfers[tid] = json.loads(m.data)
            except Exception:  # noqa: BLE001
                pass

        sub = await self._nc.subscribe(progress_subj, cb=_on_prog)
        try:
            cmd = {
                "type": "transfer",
                "transfer": {
                    "transfer_id": tid,
                    "src_node": src_node,
                    "dst_node": dst_node,
                    "src_path": src_path,
                    "dst_path": dst_path,
                    "options": {"verify": verify},
                },
            }
            msg = await self._nc.request(
                _subj_cmd(self._fleet_id, src_node), json.dumps(cmd).encode(), timeout=timeout
            )
            self._transfers[tid] = json.loads(msg.data)
        except Exception as e:  # noqa: BLE001
            self._transfers[tid] = {"transfer_id": tid, "state": "failed", "error": str(e)}
        finally:
            try:
                await sub.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
            self._transfer_tasks.pop(tid, None)

    @tool
    async def transfer_status(self, transfer_id: str) -> dict:
        """Get the latest status of a transfer started by `transfer`.

        Args:
            transfer_id: The id returned by transfer().

        Returns:
            dict: latest progress — state (pending|connecting|transferring|done|
            failed), bytes_done/total, rate_bps, path (direct|relay), sha256.
        """
        p = self._transfers.get(transfer_id)
        if p is None:
            return {"success": False, "error": f"unknown transfer_id {transfer_id}"}
        return {"success": True, **p}
