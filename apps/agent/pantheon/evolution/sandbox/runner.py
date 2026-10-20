"""Controller side: run one mutation in an isolated Modal sandbox and read back the child.

This runs OUTSIDE the sandbox (on the host / controller). It never executes evolved code
locally — it only orchestrates: create a sandbox, inject the worker + inputs, run it, parse
the child from the worker's stdout, tear the sandbox down.

Auth: Modal picks up credentials from MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or ~/.modal.toml.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Dict, List, Optional

_WORKER_SRC = (Path(__file__).parent / "mutation_worker.py").read_text()

DEFAULT_IMAGE = "nanguage/pantheon-agents:latest"
DEFAULT_APP = "pantheon-agents"


def _enc(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


async def run_mutation_in_sandbox(
    parent_files: Dict[str, str],
    evaluator_code: str,
    objective: str,
    system_prompt: str,
    model: str,
    provider_env: Optional[Dict[str, str]] = None,
    inspirations: Optional[List[dict]] = None,
    *,
    app_name: str = DEFAULT_APP,
    image_ref: str = DEFAULT_IMAGE,
    timeout: int = 600,
    cpu=(1.0, 4.0),
    memory=(4096, 16384),
    tags: Optional[Dict[str, str]] = None,
    log_path: Optional[str] = None,
) -> dict:
    """Run one mutation. Returns {ok, submitted, summary, child_files, metrics, error, ...}.

    ``system_prompt`` is injected at runtime — change it freely without rebuilding the image.
    ``provider_env`` carries the LLM key(s) the model needs, e.g. {"OPENROUTER_API_KEY": "..."}.
    ``inspirations`` are MAP-Elites elites from other niches — a list of
    {"files": {relpath: content}, "score": float, "summary": str} that the worker drops into a
    read-only ``inspirations/`` folder for the agent to borrow ideas from.
    """
    import modal

    app = await modal.App.lookup.aio(app_name, create_if_missing=True)
    image = modal.Image.from_registry(image_ref).dockerfile_commands(["ENTRYPOINT []"])
    env = {
        "EVO_PARENT": _enc(json.dumps(parent_files)),
        "EVO_EVALUATOR": _enc(evaluator_code),
        "EVO_OBJECTIVE": _enc(objective),
        "EVO_PROMPT": _enc(system_prompt),
        "EVO_MODEL": model,
        "EVO_TIMEOUT": str(timeout),
        "EVO_WORKER": _enc(_WORKER_SRC),
        **({"EVO_INSPIRATIONS": _enc(json.dumps(inspirations))} if inspirations else {}),
        **(provider_env or {}),
    }
    # Run the worker AS the sandbox's main process (batch pattern): decode the worker source
    # from env, then run it. The sandbox lives exactly for this one mutation and exits when done
    # — avoids fragile long-lived exec-stdio streaming.
    sb = await modal.Sandbox.create.aio(
        "sh", "-c",
        "python -c \"import base64,os;open('/tmp/evo_worker.py','w').write("
        "base64.b64decode(os.environ['EVO_WORKER']).decode())\" && python /tmp/evo_worker.py",
        app=app, image=image, env=env, timeout=timeout + 300, cpu=cpu, memory=memory)
    try:
        if tags:
            try:
                await sb.set_tags.aio(tags)
            except Exception:
                pass

        async def _drain(stream):
            return "".join([chunk async for chunk in stream])

        out, err = await asyncio.gather(_drain(sb.stdout), _drain(sb.stderr))
        rc = await sb.wait.aio()

        if log_path:  # full agent trajectory for offline analysis
            try:
                Path(log_path).write_text(
                    f"=== STDOUT ===\n{out}\n\n=== STDERR ===\n{err}\n")
            except Exception:
                pass

        # pantheon logs to stdout; count LLM turns so we can tell "active but slow" from "stuck".
        # (submitted/metrics are the reliable action signals; tool-call log format varies.)
        activity = {"llm_calls": out.count("[TTAF]")}
        for line in out.splitlines():
            if line.startswith("EVO_RESULT_JSON:"):
                result = json.loads(line[len("EVO_RESULT_JSON:"):])
                return {"ok": True, "sandbox": sb.object_id, "returncode": rc,
                        "activity": activity, "stderr_tail": err[-1500:], **result}
        return {"ok": False, "sandbox": sb.object_id, "returncode": rc,
                "error": "no result marker in worker stdout",
                "stdout_tail": out[-2500:], "stderr_tail": err[-2500:]}
    finally:
        try:
            await sb.terminate.aio()
        except Exception:
            pass


def run_mutation_in_sandbox_sync(*args, **kwargs) -> dict:
    return asyncio.run(run_mutation_in_sandbox(*args, **kwargs))
