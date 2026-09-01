"""Bridges to the evaluation machinery that already exists.

`HybridEvaluator` and `FunctionEvaluator` take a `Program` and return an `EvaluationResult`. The
new loop deals in `Individual` and `Measurement`. Rather than rewrite working evaluation code --
subprocess isolation, timeouts, cascades, LLM feedback -- these adapters translate at the edge.

Two shapes are needed, not one:
  - `measure(ind)`         for the loop, which measures individuals
  - `evaluate_files(files)` for the variator's `run_evaluator` tool, which measures a directory
                            mid-mutation for something that is not yet an individual
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from ..core.genome import CodeGenome
from ..core.individual import Individual
from ..core.method import EvolveContext
from ..core.work import Measurement


class ProgramEvaluatorAdapter:
    """Wraps a `HybridEvaluator` / `FunctionEvaluator` so it can serve both callers."""

    kind = "code"

    def __init__(self, inner: Any, kind: str = "code", serialize: bool = False):
        self.inner = inner
        self.kind = kind
        self.serialize = serialize
        self._eval_lock = None
        """Created lazily on first use so the adapter can be built outside an event loop."""
        self.last_state: Any = None
        """The evaluator's produced solution, when it returns one. Read by the variator to persist
        a warm-start file into the child's genome."""

    FIDELITY_MARKER = ".fidelity"
    """How a fidelity request reaches an evaluator that only takes a workspace path.

    Written into the workspace for this call and read back by evaluators that offer more than one
    fidelity. A marker file rather than an environment variable because several mutations evaluate
    concurrently in one process, and a shared env var would let one agent's cheap reading be
    recorded as another's authoritative score. An evaluator that ignores the file gets its normal
    behaviour, which is what every existing task does.
    """

    async def evaluate_files(self, files: Dict[str, str],
                             fidelity: str = "full") -> Dict[str, Any]:
        # On wall-clock-scored tasks two concurrent measurements steal CPU from each other and
        # both readings drop -- `serialize=True` runs them one at a time instead. LLM waits
        # still overlap, so worker concurrency keeps paying where it is safe to.
        if self.serialize:
            import asyncio

            if self._eval_lock is None:
                self._eval_lock = asyncio.Lock()
            async with self._eval_lock:
                return await self._evaluate_files_now(files, fidelity)
        return await self._evaluate_files_now(files, fidelity)

    async def _evaluate_files_now(self, files: Dict[str, str],
                                  fidelity: str = "full") -> Dict[str, Any]:
        from ..program import CodebaseSnapshot, Program

        payload = dict(files)
        if fidelity and fidelity != "full":
            payload[self.FIDELITY_MARKER] = fidelity
        res = await self.inner.evaluate(Program(
            id=f"_probe{uuid.uuid4().hex[:6]}",
            snapshot=CodebaseSnapshot(files=payload),
            generation=0,
        ))
        state = getattr(res, "state", None)
        if state is not None:
            self.last_state = state
        metrics = dict(getattr(res, "metrics", {}) or {})
        from .usage import add_eval
        add_eval(fidelity, metrics)
        return {
            "success": bool(getattr(res, "success", False)),
            "metrics": metrics,
            "artifacts": dict(getattr(res, "artifacts", {}) or {}),
            "error": getattr(res, "error", None),
        }

    async def measure(self, ctx: EvolveContext, ind: Individual,
                      fidelity: str = "full") -> Measurement:
        t0 = time.time()
        genome = ind.genome
        files = genome.files if isinstance(genome, CodeGenome) else {"main.py": genome.render()}
        # fidelity MUST reach evaluate_files or it is only a label: this call once dropped it,
        # and every "low-fidelity screen" issued by a method ran -- and billed -- at full price
        # while its Measurement claimed otherwise (wave4's HypothesisBandit screens did).
        out = await self.evaluate_files(files, fidelity)
        return Measurement(
            individual_id=ind.id,
            metrics=out["metrics"],
            artifacts=out["artifacts"],
            fidelity=fidelity,
            ok=out["success"],
            duration=time.time() - t0,
        )


class CodeEvaluator(ProgramEvaluatorAdapter):
    """The common case: an `evaluate(workspace_path) -> dict` script, run in a subprocess.

    `llm_weight` defaults to 0 here, unlike `HybridEvaluator`'s own default. The LLM feedback pass
    is a second model call per evaluation, and under the new design the method owns ranking, so a
    caller who wants an LLM opinion asks for it explicitly rather than paying for it by accident.
    """

    def __init__(self, evaluator_code: str, *, timeout: int = 600,
                 workspace_path: Optional[str] = None, kind: str = "code",
                 llm_weight: float = 0.0, max_parallel: int = 4, serialize: bool = False):
        from ..evaluator import HybridEvaluator

        super().__init__(HybridEvaluator(
            evaluator_code=evaluator_code,
            function_weight=1.0 - llm_weight,
            llm_weight=llm_weight,
            max_parallel=max_parallel,
            timeout=timeout,
            workspace_base=workspace_path,
        ), kind=kind, serialize=serialize)
