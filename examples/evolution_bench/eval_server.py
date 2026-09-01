"""One dedicated machine for every measurement in a wave.

    modal deploy eval_server.py          # prints the endpoint URL once

Exists because AHC-class scores are wall-clock-limited work: a measurement is program x machine
speed at that moment, and wave5 ran each arm's measurements on whatever container it happened to
get -- identical code re-read on one box spread ~30 points/case, and cross-arm absolute scores
inherited the hardware lottery. This server removes the lottery: `max_containers=1` makes it
literally one machine for the whole campaign, the default one-input-at-a-time concurrency
serializes every measurement (no two 2-second-limit runs stealing CPU from each other), and the
method arms shrink to thin LLM-orchestration nodes that POST files here and get metrics back.

What it does NOT fix: this one machine still drifts over the campaign's hours. All arms ride the
same drift, and their 20-minute seed probes -- all landing here -- merge into one dense drift
series. Every response carries `boot_id`; if the container ever recycles mid-campaign (idle gap
longer than `scaledown_window`), the id changes and the analysis can see the seam instead of
averaging across it.

Auth: a token derived from the shared OpenRouter secret both sides already hold -- proves the
caller is ours without minting a new secret or sending the key itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import modal

# Deployed, this module runs from /root while the repo (and modal_exp) live in the image
# mount -- the sibling import needs the mount on the path there, and the local dir here.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/repo/examples/evolution_bench")
from modal_exp import image  # noqa: E402

app = modal.App("evolve-eval-server")

CASE_WORKERS = "24"
"""On 64 dedicated cores: 24 concurrent cases x (1 solution thread + tester) stays well under
the core count, so cases do not contend -- one 150-case eval in ~7 rounds x 2s ~= 18s against
42s on the arms' old 8-core boxes."""


def _token() -> str:
    import hashlib
    import os

    key = os.environ.get("OPENROUTER_API_KEY", "")
    return hashlib.sha256(f"evolve-eval:{key}".encode()).hexdigest()[:32]


@app.cls(image=image, secrets=[modal.Secret.from_name("evolve-exp-openrouter")],
         cpu=64.0, memory=32768, timeout=24 * 3600, max_containers=1,
         scaledown_window=1200)
class EvalServer:

    @modal.enter()
    def boot(self):
        import os
        import time
        import uuid

        self.boot_id = uuid.uuid4().hex[:12]
        self.boot_at = time.time()
        self.evals_served = 0
        self._mods: dict = {}
        os.environ["AHC_CASE_WORKERS"] = CASE_WORKERS
        # the machine's own warm-up: one discarded read so the first REAL measurement of a
        # campaign lands on a warm box, and the cold-vs-warm delta is logged once, here
        try:
            t0 = time.time()
            seed = open("/repo/examples/evolution_bench/tasks/ahc039/solution.cpp").read()
            m = self._run("ahc039", {"solution.cpp": seed}, "full")
            self.warmup = {"score": m.get("combined_score"), "seconds": time.time() - t0}
            print(f"[boot {self.boot_id}] warm-up: {self.warmup}")
        except Exception as e:  # noqa: BLE001
            self.warmup = {"error": str(e)[:200]}

    def _evaluate_fn(self, task: str):
        if task not in self._mods:
            import importlib.util
            import json
            import os

            task_dir = f"/repo/examples/evolution_bench/tasks/{task}"
            os.environ["AHC_TASK_DIR"] = task_dir
            os.environ["TASK_DIR"] = task_dir
            # the task's own env (AHC_EVAL_RUNS=1, timeouts) -- run_bench applies these on the
            # arms, and skipping them here silently ran full fidelity at the evaluator's 3-repeat
            # default: 50s per eval instead of ~17, and means-of-3 nobody else measures with
            cfg_path = f"{task_dir}/task.json"
            if os.path.exists(cfg_path):
                for k, v in (json.load(open(cfg_path)).get("env") or {}).items():
                    if k != "AHC_CASE_WORKERS":         # ours is sized for this box
                        os.environ.setdefault(k, str(v))
            spec = importlib.util.spec_from_file_location(
                f"eval_{task}", f"{task_dir}/evaluator.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._mods[task] = mod.evaluate
        return self._mods[task]

    def _run(self, task: str, files: dict, fidelity: str) -> dict:
        import shutil
        import tempfile
        from pathlib import Path

        ws = tempfile.mkdtemp(prefix="eval_")
        try:
            for rel, content in files.items():
                p = Path(ws) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
            return self._evaluate_fn(task)(ws, fidelity)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    @modal.fastapi_endpoint(method="POST")
    def evaluate(self, req: dict) -> dict:
        import time

        if req.get("token") != _token():
            return {"success": False, "error": "bad token", "metrics": {}}
        t0 = time.time()
        try:
            metrics = self._run(req["task"], req["files"], req.get("fidelity", "full"))
            ok = True
            err = None
        except Exception as e:  # noqa: BLE001
            metrics, ok, err = {}, False, f"{type(e).__name__}: {str(e)[:300]}"
        self.evals_served += 1
        return {"success": ok, "metrics": metrics, "error": err,
                "boot_id": self.boot_id, "evals_served": self.evals_served,
                "server_seconds": round(time.time() - t0, 2), "warmup": self.warmup}
