"""The mutation operator that runs in a Modal sandbox instead of on this machine.

Same job as `AgentVariator` -- turn a parent into a child -- with one property that changes what
the loop is allowed to do with the result: **the sandbox evaluates the child itself**. Evolved code
never executes on the host, not while the agent is exploring and not afterwards to score it.

That is why `Produced.measurement` exists. If the loop measured the child locally the way it does
for every other operator, the isolation would be pointless: the whole point is that nothing the
model wrote runs here. The sandbox worker returns metrics alongside the files and this variator
hands both back, so the loop records the measurement rather than making one.

Because there is no host workspace, none of `AgentVariator`'s per-call session state exists here.
The isolation is the sandbox.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from pantheon.utils.log import logger

from ..core.genome import CodeGenome
from ..core.method import EvolveContext
from ..core.work import Create, Measurement, Produced
from .agent import MUTATION_AGENT_SYSTEM_PROMPT

PROVIDER_KEYS = (
    "OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
)


def provider_env() -> Dict[str, str]:
    """LLM credentials to forward into the sandbox -- only the ones actually set here.

    `OPENAI_API_BASE` is included deliberately: it lets an OpenAI-compatible provider be pointed at
    OpenRouter, which is how Anthropic models are reached as `openai/anthropic/claude-*` without
    the image's native Anthropic adapter mis-routing OpenRouter ids.
    """
    return {k: os.environ[k] for k in PROVIDER_KEYS if os.environ.get(k)}


class SandboxVariator:
    """One mutation per isolated Modal sandbox, evaluated there too."""

    def __init__(
        self,
        evaluator_code: str,
        *,
        model: str = "high",
        system_prompt: Optional[str] = None,
        image_ref: str = "nanguage/pantheon-agents:latest",
        app_name: Optional[str] = None,
        timeout: int = 1800,
        send_inspirations: bool = False,
        score_key: str = "combined_score",
        cpu: Any = (1.0, 4.0),
        memory: Any = (4096, 16384),
    ):
        self.evaluator_code = evaluator_code
        self.model = model
        self.system_prompt = system_prompt or MUTATION_AGENT_SYSTEM_PROMPT
        self.image_ref = image_ref
        self.app_name = app_name
        self.timeout = timeout
        self.send_inspirations = send_inspirations
        self.score_key = score_key
        self.cpu = cpu
        self.memory = memory

    # ---- prompt ----------------------------------------------------------

    def build_objective(self, ctx: EvolveContext, item: Create) -> str:
        """The objective plus whatever lineage context the method chose.

        The sandbox worker gets one string, so the method's `PromptContext` is flattened here
        rather than sent as structure. What goes in is still the method's decision -- this only
        decides the wording.
        """
        c = item.context
        parts = [c.instruction or ctx.objective]
        if c.history:
            parts += ["", "What earlier attempts in this lineage tried "
                          "(learn from these, then improve or do something different):",
                      c.history]
        if c.failures:
            worst = sorted(c.failures.items(), key=lambda kv: -kv[1])[:5]
            parts += ["", "Recurring failures to avoid:"] + [
                f"- {k} (x{int(v)})" for k, v in worst]
        return "\n".join(parts)

    def build_inspirations(self, item: Create) -> Optional[List[dict]]:
        """Elites from other niches, as read-only reference files for the agent."""
        if not self.send_inspirations:
            return None
        payload = []
        for ins in item.context.inspirations[:4]:
            files = getattr(ins.genome, "files", None)
            if not files:
                continue
            payload.append({
                "files": dict(files),
                "score": round(float(ins.metrics().get(self.score_key, 0.0) or 0.0), 4),
                "summary": ins.meta.get("summary", ""),
            })
        return payload or None

    # ---- the Variator protocol ------------------------------------------

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        import asyncio

        parent = item.context.parents[0] if item.context.parents else (
            ctx.store.get(item.parent_ids[0]) if item.parent_ids else None)
        if parent is None or not isinstance(parent.genome, CodeGenome):
            logger.warning("SandboxVariator needs a CodeGenome parent; got %r",
                           type(getattr(parent, "genome", None)))
            return []

        results = await asyncio.gather(
            *(self._one(ctx, item, parent, c) for c in range(max(1, item.k))),
            return_exceptions=True,
        )
        out: List[Produced] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"[{item.id}] sandbox candidate raised: {type(r).__name__}: {r}")
            elif r is not None:
                out.append(r)
        return out

    async def _one(self, ctx: EvolveContext, item: Create, parent,
                   candidate: int) -> Optional[Produced]:
        from pantheon.evolution.sandbox import run_mutation_in_sandbox

        kwargs = dict(
            model=self.model,
            provider_env=provider_env(),
            inspirations=self.build_inspirations(item),
            image_ref=self.image_ref,
            timeout=int(self.timeout),
            cpu=self.cpu,
            memory=self.memory,
            tags={"evo_item": item.id, "candidate": str(candidate)},
        )
        if self.app_name:
            kwargs["app_name"] = self.app_name

        t0 = time.time()
        try:
            res = await run_mutation_in_sandbox(
                dict(parent.genome.files),
                self.evaluator_code,
                self.build_objective(ctx, item),
                self.system_prompt,
                **kwargs,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{item.id}#{candidate}] sandbox mutation failed: {e}")
            return None

        if not res.get("ok") or not res.get("submitted"):
            reason = res.get("error") or ("no_submit" if res.get("ok") else "sandbox_error")
            logger.warning(f"[{item.id}#{candidate}] no child from sandbox ({reason})")
            return None

        metrics = res.get("metrics") or {}
        return Produced(
            genome=CodeGenome(files=dict(res["child_files"])),
            item_id=item.id,
            batch_id=item.batch_id,
            parent_ids=list(item.parent_ids),
            anchor_id=item.anchor_id,
            meta={"summary": res.get("summary", ""), "sandbox": res.get("sandbox"),
                  "mutation_seconds": time.time() - t0, "cost": float(res.get("cost", 0.0) or 0.0),
                  "candidate": candidate},
            # measured in the sandbox: the loop must not re-run this on the host
            measurement=Measurement(
                individual_id="", metrics=metrics, fidelity=item.fidelity,
                ok=bool(metrics), duration=time.time() - t0,
                cost=float(res.get("cost", 0.0) or 0.0),
                artifacts={"sandbox": res.get("sandbox")} if res.get("sandbox") else {},
            ),
        )
