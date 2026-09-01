"""Operators and evaluation for the idea level.

Three pieces, because an idea is not code and cannot borrow code's machinery:

  `IdeaVariator`   writes approaches as prose. One completion, k proposals, no execution -- there
                   is nothing to execute yet.
  `IdeaCodeVariator` routes a work item to the operator for its kind, so one object can serve a
                   method that evolves two populations.
  `IdeaJudge`      scores a proposal before anything is built.

`IdeaJudge` deserves the suspicion it will get. It is a model grading prose, which is the cheapest
kind of number to produce and the easiest to fool, and if the search trusted it the whole method
would be an elaborate way of optimising plausibility. It does not: the judge only orders ideas that
have never been implemented, and is superseded by measured code the moment any exists. What it buys
is the ordering of the *first* implementation attempts, which is worth something when implementing
is expensive and worth nothing if the judge is noise -- which is why the method records both
numbers and can report whether they agree.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from pantheon.utils.log import logger

from ..core.genome import CodeGenome, TextGenome
from ..core.individual import Individual
from ..core.method import EvolveContext
from ..core.work import Create, Measurement, Produced
from .completion import CompletionVariator, extract_code

IDEA_SYSTEM = (
    "You are a research strategist proposing approaches to an optimisation problem. "
    "Propose ONE concrete approach: what mechanism to exploit and why it should help, in 3-6 "
    "sentences. Be specific enough that a competent programmer could implement it without asking "
    "you a question, and specific enough to be WRONG -- an approach that cannot fail to be true "
    "is not an approach. Do not write code. Do not hedge with several options; commit to one."
)

JUDGE_SYSTEM = (
    "You are judging proposed approaches to an optimisation problem, before any of them are "
    "implemented. Score the proposal from 0 to 1 on whether implementing it is likely to beat the "
    "current best result: specificity, whether the stated mechanism plausibly bears on the "
    "objective, and whether it differs from what has already been tried. Reply with JSON only: "
    '{"score": <0-1>, "reason": "<one sentence>"}'
)


class IdeaVariator:
    """One prompt, k proposed approaches, as text."""

    def __init__(self, *, model: str = "high", system_prompt: Optional[str] = None,
                 timeout: float = 300, temperature: float = 1.0,
                 reasoning_max_tokens: Optional[int] = None):
        self.model = model
        self.system_prompt = system_prompt or IDEA_SYSTEM
        self.timeout = timeout
        self.temperature = temperature
        self.reasoning_max_tokens = reasoning_max_tokens

    def build_prompt(self, ctx: EvolveContext, item: Create) -> str:
        c = item.context
        parts = [c.instruction or ctx.objective]
        parent = c.parents[0] if c.parents else None
        if parent is not None:
            parts.append("\n## The approach you are refining\n" + parent.genome.render())
        if c.history:
            parts.append(
                "\n## Approaches tried so far, and what their implementations measured\n"
                + c.history
                + "\n\nAn approach that was implemented and scored badly is settled -- do not "
                  "repropose it. An approach never implemented is untested, not refuted."
            )
        parts.append("\nPropose one approach. Prose only, no code.")
        return "\n".join(parts)

    async def _call(self, prompt: str, k: int) -> List[str]:
        from openai import AsyncOpenAI

        from pantheon.utils.llm_providers import detect_provider

        cfg = detect_provider(self.model, False)
        client = AsyncOpenAI(api_key=cfg.api_key or os.environ.get("OPENAI_API_KEY"),
                             base_url=cfg.base_url or os.environ.get("OPENAI_API_BASE") or None,
                             timeout=self.timeout)
        kwargs: Dict[str, Any] = {
            "model": cfg.model_name,
            "messages": [{"role": "system", "content": self.system_prompt},
                         {"role": "user", "content": prompt}],
        }
        if k > 1:
            kwargs["n"] = k
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if getattr(self, "reasoning_max_tokens", None):
            kwargs["extra_body"] = {"reasoning": {"max_tokens": self.reasoning_max_tokens}}
        resp = await client.chat.completions.create(**kwargs)
        from .usage import add_response
        add_response(resp)
        return [(ch.message.content or "").strip() for ch in (resp.choices or [])]

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        import asyncio

        prompt = self.build_prompt(ctx, item)
        t0 = time.time()
        try:
            texts = await self._call(prompt, item.k)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{item.id}] idea generation failed: {type(e).__name__}: {e}")
            return []
        # Many gateways ignore `n` and return one choice regardless -- OpenRouter does. Without
        # this top-up every k>1 batch reports k-1 phantom failures, and the method throttles
        # itself against a shortfall that never happened.
        if len(texts) < item.k:
            extra = await asyncio.gather(
                *(self._call(prompt, 1) for _ in range(item.k - len(texts))),
                return_exceptions=True)
            for r in extra:
                if isinstance(r, list):
                    texts.extend(r)

        out: List[Produced] = []
        for i, text in enumerate(texts[: item.k]):
            if len(text) < 40:          # a sentence fragment is not an approach
                logger.warning(f"[{item.id}#{i}] idea too short to be an approach")
                continue
            out.append(Produced(
                genome=TextGenome(text=text, kind="idea"),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids),
                meta={"summary": text.splitlines()[0][:160], "candidate": i,
                      "mutation_seconds": time.time() - t0},
            ))
        return out


class IdeaCodeVariator:
    """Routes by `item.kind`: prose for ideas, code for implementations."""

    def __init__(self, *, model: str = "high", evaluator: Any = None,
                 timeout: float = 600, target_file: Optional[str] = None,
                 code_variator: Any = None, idea_variator: Any = None, **kw):
        self.idea = idea_variator or IdeaVariator(model=model, timeout=min(timeout, 300))
        self.code = code_variator or CompletionVariator(
            model=model, timeout=timeout, target_file=target_file,
            system_prompt=(
                "You are an expert algorithm designer implementing a specified approach. "
                "Reply with ONE complete, runnable replacement for the program, in a single "
                "fenced code block. Implement the approach you were given -- not a different "
                "improvement you happen to prefer. No commentary outside the block."
            ),
        )

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        if item.kind == "idea":
            return await self.idea.create(ctx, item)
        return await self.code.create(ctx, item)


class IdeaJudge:
    """Scores a proposal before it is built. A prior, and treated as one."""

    kind = "idea"

    def __init__(self, *, model: str = "high", objective: str = "",
                 system_prompt: Optional[str] = None, timeout: float = 120):
        self.model = model
        self.objective = objective
        self.system_prompt = system_prompt or JUDGE_SYSTEM
        self.timeout = timeout

    async def measure(self, ctx: EvolveContext, ind: Individual,
                      fidelity: str = "full") -> Measurement:
        from openai import AsyncOpenAI

        from pantheon.utils.llm_providers import detect_provider

        t0 = time.time()
        cfg = detect_provider(self.model, False)
        client = AsyncOpenAI(api_key=cfg.api_key or os.environ.get("OPENAI_API_KEY"),
                             base_url=cfg.base_url or os.environ.get("OPENAI_API_BASE") or None,
                             timeout=self.timeout)
        prompt = (f"## Objective\n{self.objective or ctx.objective}\n\n"
                  f"## Proposed approach\n{ind.genome.render()}\n\nScore it.")
        try:
            resp = await client.chat.completions.create(
                model=cfg.model_name,
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": prompt}],
            )
            from .usage import add_response
            add_response(resp)
            text = (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"idea judge failed: {type(e).__name__}: {e}")
            # a failed judge must not veto an idea: score it neutrally and let the code decide
            return Measurement(individual_id=ind.id, fidelity=fidelity, ok=True,
                               metrics={"idea_score": 0.5},
                               artifacts={"error": str(e)[:200]},
                               duration=time.time() - t0)

        score, reason = _parse_judgement(text)
        return Measurement(
            individual_id=ind.id, fidelity=fidelity, ok=True,
            metrics={"idea_score": score},
            artifacts={"judgement": reason, "raw": text[:500]},
            duration=time.time() - t0,
        )


def _parse_judgement(text: str) -> tuple:
    """Pull a score out of the reply, however it was wrapped.

    Models fence their JSON, prepend prose, or answer with a bare number. A judge that fails to
    parse would silently zero an idea and remove it from the search, so every fallback lands on a
    neutral 0.5 rather than a verdict.
    """
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            s = float(d.get("score", 0.5))
            return max(0.0, min(1.0, s)), str(d.get("reason", ""))[:300]
        except Exception:  # noqa: BLE001
            pass
    m = re.search(r"([01](?:\.\d+)?)", text)
    if m:
        try:
            return max(0.0, min(1.0, float(m.group(1)))), text[:300]
        except ValueError:
            pass
    return 0.5, text[:300]


class NullJudge:
    """A judge that carries no information, for ablating the real one.

    Two nulls, and they are not the same:

      `random`   scores uniformly at random, so which unimplemented idea gets built first is a
                 coin toss
      `constant` gives every idea the same score, so ties break by proposal order and the search
                 implements ideas first-come-first-served

    Both remove the judge's signal; only `random` also removes the ordering. Running against both
    separates "the judge knows something" from "any consistent ordering beats a shuffled one",
    which a single null would conflate.
    """

    kind = "idea"

    def __init__(self, mode: str = "random", value: float = 0.5, seed: int = 0):
        import random as _r

        if mode not in ("random", "constant"):
            raise ValueError(f"mode must be 'random' or 'constant', got {mode!r}")
        self.mode = mode
        self.value = value
        self.rng = _r.Random(seed)

    async def measure(self, ctx: EvolveContext, ind: Individual,
                      fidelity: str = "full") -> Measurement:
        score = self.rng.random() if self.mode == "random" else self.value
        return Measurement(individual_id=ind.id, fidelity=fidelity, ok=True,
                           metrics={"idea_score": score},
                           artifacts={"judge": f"null:{self.mode}"})
