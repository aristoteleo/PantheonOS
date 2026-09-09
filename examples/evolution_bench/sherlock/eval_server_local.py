"""The eval server of eval_server.py without Modal: one SLURM job = one machine = one ruler.

    python eval_server_local.py --port 8001 --url-file /path/wave1.url

Same contract as the Modal endpoint (POST {token, task, files, fidelity} -> {success, metrics,
boot_id, evals_served, server_seconds, warmup}), same serialization (one measurement at a time),
same warm-up read at boot. `boot_id` is fresh per process, so a restarted server is visible in
the arms' boot logs exactly as a recycled Modal container was.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import uuid
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
from remote_eval import server_token  # noqa: E402

CASE_WORKERS = os.environ.get("AHC_CASE_WORKERS", "24")


class EvalServer:
    def __init__(self):
        self.boot_id = uuid.uuid4().hex[:12]
        self.boot_at = time.time()
        self.evals_served = 0
        self._mods: dict = {}
        self._lock = asyncio.Lock()
        os.environ["AHC_CASE_WORKERS"] = CASE_WORKERS
        try:
            t0 = time.time()
            seed = (BENCH / "tasks/ahc039/solution.cpp").read_text()
            m = self._run("ahc039", {"solution.cpp": seed}, "full")
            self.warmup = {"score": m.get("combined_score"), "seconds": round(time.time() - t0, 1),
                           "host": socket.gethostname()}
        except Exception as e:  # noqa: BLE001
            self.warmup = {"error": str(e)[:200]}
        print(f"[boot {self.boot_id}] warm-up: {self.warmup}", flush=True)

    def _evaluate_fn(self, task: str):
        if task not in self._mods:
            task_dir = str(BENCH / "tasks" / task)
            os.environ["AHC_TASK_DIR"] = task_dir
            os.environ["TASK_DIR"] = task_dir
            cfg_path = f"{task_dir}/task.json"
            if os.path.exists(cfg_path):
                for k, v in (json.load(open(cfg_path)).get("env") or {}).items():
                    if k != "AHC_CASE_WORKERS":
                        os.environ.setdefault(k, str(v))
            spec = importlib.util.spec_from_file_location(f"eval_{task}", f"{task_dir}/evaluator.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._mods[task] = mod.evaluate
        return self._mods[task]

    def _run(self, task: str, files: dict, fidelity: str) -> dict:
        ws = tempfile.mkdtemp(prefix="eval_", dir=os.environ.get("EVAL_TMP") or None)
        try:
            for rel, content in files.items():
                p = Path(ws) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
            return self._evaluate_fn(task)(ws, fidelity)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def evaluate(self, req: dict) -> dict:
        if req.get("token") != server_token():
            return {"success": False, "error": "bad token", "metrics": {}}
        t0 = time.time()
        async with self._lock:                       # one measurement at a time, as on Modal
            try:
                metrics = await asyncio.to_thread(self._run, req["task"], req["files"],
                                                  req.get("fidelity", "full"))
                ok, err = True, None
            except Exception as e:  # noqa: BLE001
                metrics, ok, err = {}, False, f"{type(e).__name__}: {str(e)[:300]}"
        self.evals_served += 1
        return {"success": ok, "metrics": metrics, "error": err,
                "boot_id": self.boot_id, "evals_served": self.evals_served,
                "server_seconds": round(time.time() - t0, 2), "warmup": self.warmup}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--url-file", required=True, help="written once the server listens; arms read it")
    a = ap.parse_args()
    from fastapi import FastAPI
    import uvicorn

    srv = EvalServer()
    app = FastAPI()

    @app.post("/evaluate")
    async def evaluate(req: dict):
        return await srv.evaluate(req)

    @app.get("/health")
    async def health():
        return {"boot_id": srv.boot_id, "evals_served": srv.evals_served, "warmup": srv.warmup}

    url = f"http://{socket.gethostname()}:{a.port}/evaluate"
    Path(a.url_file).write_text(url + "\n")
    print(f"[boot {srv.boot_id}] serving {url}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
