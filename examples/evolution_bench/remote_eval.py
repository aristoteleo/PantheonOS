"""Client for the shared evaluation server -- same adapter surface, measurements elsewhere.

A drop-in for `CodeEvaluator`: the loop, the agents' inner probes, the warm-up and the drift
probes all call `evaluate_files` exactly as before; the work happens on the one dedicated
machine `eval_server.py` runs, so every measurement in a wave shares hardware and queues behind
a single-input server instead of contending locally. Ledger booking (with the search/harness
source split) stays on the arm -- spend belongs to whoever caused it, not to the machine that
served it.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any, Dict

from pantheon.evolution.variators.adapters import ProgramEvaluatorAdapter


def server_token() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return hashlib.sha256(f"evolve-eval:{key}".encode()).hexdigest()[:32]


class RemoteEvalAdapter(ProgramEvaluatorAdapter):

    def __init__(self, url: str, task: str, *, kind: str = "code", timeout: float = 900):
        super().__init__(inner=None, kind=kind, serialize=False)   # the server serializes
        self.url = url
        self.task = task
        self.timeout = timeout
        self.boot_ids: list = []
        """Every distinct server boot_id seen, in order. One entry = one machine all wave;
        more means the server recycled mid-run and absolute scores have a seam."""

    async def _evaluate_files_now(self, files: Dict[str, str], fidelity: str = "full",
                                  book_as: str = "search") -> Dict[str, Any]:
        import httpx

        payload = {"token": server_token(), "task": self.task, "files": files,
                   "fidelity": fidelity}
        last_err: Any = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout,
                                             follow_redirects=True) as client:
                    r = await client.post(self.url, json=payload)
                r.raise_for_status()
                body = r.json()
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                await asyncio.sleep(5 * (attempt + 1))
        else:
            body = {"success": False, "metrics": {},
                    "error": f"eval server unreachable: {type(last_err).__name__}: "
                             f"{str(last_err)[:200]}"}
        bid = body.get("boot_id")
        if bid and bid not in self.boot_ids:
            self.boot_ids.append(bid)
        metrics = dict(body.get("metrics") or {})
        from pantheon.evolution.variators.usage import add_eval
        add_eval(fidelity, metrics, source=book_as)
        return {"success": bool(body.get("success")), "metrics": metrics,
                "artifacts": {}, "error": body.get("error")}
