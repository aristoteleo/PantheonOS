"""LabNotebook -- understanding-guided trajectory search.

Two levels. The INNER level is SimpleTES: a short chain of best-of-K completions, no tools, no
self-scoring -- the operator that won on cost in every campaign so far. The OUTER level is a
scientist's loop whose state is a lab notebook:

    understanding  a maintained prose account of the problem, the seed, what limits the score,
                   what has worked and what has not -- rewritten by the model after every cycle
    history        every idea tried, with the NUMBERS next to the prose: parent score, best
                   score its trajectory reached, delta, predicted gain, and a one-paragraph digest
    references     the seed plus each trajectory's best program, tagged with the idea it came
                   from, so the next cycle can start from a different lineage than the champion

One cycle: pick a parent from the references, ask for K distinct ideas conditioned on the
notebook + parent + history, run ONE SHORT SimpleTES TRAJECTORY PER IDEA (in parallel -- each is
its own chain and the loop's concurrency is the only limit), digest each finished trajectory
against its idea, update the understanding, repeat.

The structural bet, stated so the ablation can test it: **an idea is scored by the best of a
local search, not by one implementation.** HypothesisBandit judged a claim on one or two edits
at SNR~1 and retired the claim that produced its best program. A trajectory averages over both
implementation variance and read noise before the outer loop learns anything from it.

Three guards, each answering a failure the earlier methods paid for:

  * The idea travels in EVERY step's instruction, not just the first. A prefix on step one
    drifts to generic improvement by step two, and "best of this idea's trajectory" becomes
    "best of a random trajectory" wearing the idea's name.
  * Numbers travel with the prose. `history` carries parent/best/delta/predicted per idea and
    the update prompt is told to cite them. The E1 ablation showed model opinion drifting from
    evidence when nothing anchored it; the predicted-vs-measured gap is also the cheapest
    calibration signal the run produces.
  * The notebook has fixed sections and a length cap. Free rewriting every cycle either grows
    without bound or forgets; a bounded structure forces it to consolidate.

Model calls the method makes itself (understanding, ideas, digest, update) are booked in the
same ledger as the operator's, so the LLM-call and token budgets see all of them.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import math
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from pantheon.utils.log import logger

from ..core.genome import CodeGenome, TextGenome
from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext, Remeasure
from .fidelity import FidelityController
from .simpletes import Selector, UpstreamCompletionVariator, score_of, SELECTORS

IDEA = "idea"
CODE = "code"

NOTEBOOK_SECTIONS = ("Problem structure", "Current best approach and its performance",
                     "What limits the score", "What has worked (with numbers)",
                     "What has not worked (with numbers)", "Open directions")

UNDERSTAND_SYSTEM = (
    "You are a research scientist keeping a lab notebook on an optimisation problem. Write a "
    "compact, factual account under EXACTLY these markdown headings, in this order: "
    + " / ".join(f"## {s}" for s in NOTEBOOK_SECTIONS) +
    ". Be specific about mechanisms and quantities. No code. Stay under 700 words."
)

IDEAS_SYSTEM = (
    "You are a research scientist proposing the next experiments on an optimisation problem, "
    "using your lab notebook and the record of what has already been tried. Propose exactly K "
    "ideas that target DIFFERENT mechanisms from each other. Each idea must be concrete enough "
    "that a programmer can implement it without asking a question, and specific enough to be "
    "wrong. Do not repropose something the record shows was tried and failed unless you say "
    "what would be done differently and why. Reply with JSON only: a list of objects "
    '{"title": "<6 words>", "idea": "<3-6 sentences>", "predicted_gain": <number: expected '
    "change in combined_score, may be small or negative>}."
)

DIGEST_SYSTEM = (
    "You are a research scientist writing up one experiment in a lab notebook. You are given "
    "the idea that was tested, every candidate program's score along its search trajectory, "
    "the best program's diff against its parent and against the original seed, and the "
    "predicted gain. Reply with JSON only: "
    '{"outcome": "<one sentence: what happened, with the numbers>", '
    '"why": "<2-4 sentences: the mechanism you infer for the result>", '
    '"adherence": <0-1: how faithfully the trajectory actually pursued the idea>, '
    '"lesson": "<one sentence a future proposer should know>"}'
)

UPDATE_SYSTEM = (
    "You are a research scientist revising your lab notebook after a batch of experiments. "
    "Rewrite the notebook under EXACTLY the same headings, integrating the new results. Cite "
    "the measured numbers; where a result contradicts an earlier belief, change the belief and "
    "say what changed it. Keep what is still true, drop what is superseded. No code. Stay "
    "under 700 words."
)


GENERIC_IDEAS = [
    ("tune the main parameters", "Adjust the program's key numeric parameters (step sizes, iteration counts, thresholds) toward better scores."),
    ("add a local refinement pass", "After the main procedure, add a local search or polishing pass on the current best solution."),
    ("change the initialisation", "Start from a different or better-constructed initial state instead of the current one."),
    ("restart from several starts", "Run the core procedure from several random starts and keep the best."),
    ("simplify the objective handling", "Recompute the objective more precisely or more cheaply so more iterations fit the budget."),
    ("exploit problem structure", "Use a symmetry, decomposition or invariant of the problem to constrain the search."),
    ("perturb and re-optimise", "Perturb the best solution and re-optimise to escape a local optimum."),
    ("reallocate the time budget", "Shift compute from the cheap phase to the phase that produces the score gains."),
]


@dataclass
class _Trajectory:
    """One idea's short SimpleTES chain, from one parent."""

    idea_id: str
    parent_id: str
    chain: List[str]
    steps_done: int = 0
    open_batch: Optional[str] = None
    finished: bool = False
    failures: Dict[str, float] = field(default_factory=dict)


@dataclass
class _Batch:
    traj_idx: int
    parent_ids: List[str]
    k: int
    done: int = 0
    children: List[str] = field(default_factory=list)
    # cascade: "screen" while the k candidates are read at the cheap fidelity, then "promote"
    # while the best of them are measured in full; plain batches stay "full"
    stage: str = "full"
    screened: List[str] = field(default_factory=list)


def _extract_json(text: str, want: str = "list") -> Any:
    """First parseable JSON list/object in a reply, fences and prose tolerated."""
    if not text:
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    cands = [fence.group(1)] if fence else []
    cands.append(t)
    opener, closer = ("[", "]") if want == "list" else ("{", "}")
    for c in cands:
        s, e = c.find(opener), c.rfind(closer)
        if s == -1 or e <= s:
            continue
        try:
            return json.loads(c[s:e + 1])
        except Exception:  # noqa: BLE001
            continue
    return None


def _unified_diff(a: str, b: str, name: str, cap: int = 6000) -> str:
    d = "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                     fromfile=f"a/{name}", tofile=f"b/{name}", n=2))
    if len(d) > cap:
        d = d[:cap] + f"\n... (diff truncated at {cap} chars)\n"
    return d or "(no textual change)"


class LabNotebook(BaseMethod):
    """K ideas per cycle, one short parallel SimpleTES trajectory per idea, a notebook between
    cycles."""

    name = "lab_notebook"

    def __init__(
        self,
        ideas_per_cycle: int = 4,
        steps_per_trajectory: int = 2,
        k_candidates: int = 2,
        num_inspirations: int = 2,
        p_best_parent: float = 0.7,
        score_key: str = "combined_score",
        valid_key: str = "validity",
        history_window: int = 24,
        seed: int = 0,
        ideas: str = "model",
        notebook: bool = True,
        digest: bool = True,
        history: bool = True,
        selector: str = "balance",
        research: bool = False,
        research_secs: float = 1200.0,
        research_turns: int = 120,
        research_tool_chars: int = 9000,
        research_tool_secs: float = 20.0,
        research_block: Sequence[str] = (),
        prior_file: Optional[str] = None,
        screen_fidelity: Optional[str] = None,
        k_screen: int = 6,
        promote_top: int = 1,
        fidelity_policy: str = "fixed",
        fidelity_opts: Optional[Dict[str, Any]] = None,
        fidelity_planning: bool = False,
    ):
        # Ablation switches. `ideas`: "model" (the Propose call), "none" (K parallel short
        # trajectories with no idea injected -- the structure without the steering), or
        # "generic" (a fixed list sampled per cycle: diversity without the model's judgement).
        # `notebook=False` drops Understand/Update (Propose sees only the history table);
        # `digest=False` drops the Digest call (history rows keep the numbers, no prose);
        # `history=False` hides the history table from Propose. All three off together is the
        # NO-MEMORY arm: ideas come from the problem and the parent program alone, nothing the
        # run has learned reaches the proposer -- the arm that tests whether memory matters at
        # all, which notebook=False and digest=False cannot (both still show the record).
        if ideas not in ("model", "none", "generic"):
            raise ValueError(f"ideas must be model|none|generic, got {ideas!r}")
        self.ideas_mode = ideas
        self.use_notebook = notebook
        self.use_digest = digest
        self.use_history = history
        # Knowledge for the first notebook. `research`: a PantheonOS agent searches the web and the
        # literature before Understand and its cited notes go into the Understand prompt
        # (lab_notebook_research.py); bounded by research_secs (wall clock, including the write-up),
        # research_turns (agent messages) and per-tool limits; any failure means no notes.
        # `prior_file` (or LABNOTEBOOK_PRIOR_FILE): a text written beforehand, shown to Understand
        # the same way. Both reach later cycles only through the notebook.
        self.use_research = research
        self.research_secs = float(research_secs)
        self.research_turns = int(research_turns)
        self.research_tool_chars = int(research_tool_chars)
        self.research_tool_secs = float(research_tool_secs)
        self.research_block = [b for b in (research_block or ()) if b]
        self.prior_file = prior_file
        self.research_notes: str = ""
        self.research_log: Dict[str, Any] = {}
        # the research brief's candidate approaches, tested side by side as the first cycle's
        # ideas (the loop, on the real evaluator, picks among them rather than the brief's writer)
        self.research_ideas: List[Dict[str, str]] = []
        self.K = ideas_per_cycle
        self.T = steps_per_trajectory
        self.k = k_candidates
        # Fidelity cascade (off by default). With `screen_fidelity` set (e.g. "low"), each inner
        # step writes `k_screen` candidates and reads them at that cheaper fidelity; the best
        # `promote_top` feasible ones are then measured in full, and only full measurements count
        # (`_score` reads full only: the chain, the notebook, the history and `rank` never see a
        # cheap reading). Where a cheap reading costs a fraction of a full one, a step screens
        # several times more candidates for the clock one full step used to take.
        if screen_fidelity is not None and (k_screen < 1 or promote_top < 1):
            raise ValueError("cascade needs k_screen >= 1 and promote_top >= 1")
        self.screen_fidelity = screen_fidelity or None
        self.k_screen = int(k_screen)
        self.promote_top = int(promote_top)
        self.pending_promote: List[Remeasure] = []
        self.promote_of: Dict[str, str] = {}
        """remeasure batch id -> the inner step's batch id it promotes for"""
        # `fidelity_policy="adaptive"`: a FidelityController learns, from this run's own (cheap,
        # full) pairs and evaluation times, how wide to screen and which candidates to promote
        # (fidelity.py); "fixed" keeps k_screen / promote_top.
        if fidelity_policy not in ("fixed", "adaptive"):
            raise ValueError(f"fidelity_policy must be fixed|adaptive, got {fidelity_policy!r}")
        self.fidelity_policy = fidelity_policy
        # `fidelity_planning`: the proposer sees how the cheap evaluator has ranked candidates so
        # far and marks each idea "screen" (write several, rank them cheaply, measure the best in
        # full) or "full" (measure every candidate in full: refinements smaller than the cheap
        # reading's error); the digest is told what was planned and how the cheap reading did.
        self.fidelity_planning = bool(fidelity_planning) and bool(self.screen_fidelity)
        self.fidelity_ctl: Optional[FidelityController] = None
        if self.screen_fidelity and fidelity_policy == "adaptive":
            self.fidelity_ctl = FidelityController(cheap=self.screen_fidelity, base_k=self.k,
                                                   seed=seed, **(fidelity_opts or {}))
        self.num_inspirations = num_inspirations
        self.p_best_parent = p_best_parent
        """How often a cycle starts from the champion. The rest of the time it starts from a
        reference of a DIFFERENT idea lineage -- the warm-start ablation showed a search that
        always builds on its champion collapses to hill-climbing on multimodal problems."""
        self.score_key, self.valid_key = score_key, valid_key
        self.history_window = history_window
        self.rng = random.Random(seed)
        # SimpleTES's inspiration selector for the inner steps. It only gets to choose when a
        # trajectory's chain outgrows num_inspirations (T >= 3 with two inspirations); the tree
        # policies (puct, rpucg) also need observe() per ask and on_commit() per resolved batch.
        if selector not in SELECTORS:
            raise ValueError(f"selector must be one of {sorted(SELECTORS)}, got {selector!r}")
        self.selector = SELECTORS[selector]()

        self.understanding: str = ""
        self.history: List[Dict[str, Any]] = []
        self.references: List[str] = []
        self.ideas: Dict[str, Dict[str, Any]] = {}
        """idea id -> {title, text, predicted, cycle}"""
        self.trajectories: List[_Trajectory] = []
        self.open: Dict[str, _Batch] = {}
        self.cycle = 0
        self.cycle_parent: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self.seed_id: Optional[str] = None
        self.evolve_file: Optional[str] = None

        # the operator's model settings, captured in default_variator so the method's own
        # calls speak to the same model the trajectories do
        self.model = "high"
        self.timeout = 600.0
        self.max_tokens: Optional[int] = None
        self.reasoning_max_tokens: Optional[int] = None

    # ---- model calls the method makes itself -------------------------------

    async def _llm(self, system: str, prompt: str) -> str:
        from openai import AsyncOpenAI

        from pantheon.utils.llm_providers import detect_provider
        from ..variators.usage import add_response

        cfg = detect_provider(self.model, False)
        client = AsyncOpenAI(api_key=cfg.api_key or os.environ.get("OPENAI_API_KEY"),
                             base_url=cfg.base_url or os.environ.get("OPENAI_API_BASE") or None,
                             timeout=self.timeout)
        kwargs: Dict[str, Any] = {"model": cfg.model_name,
                                  "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": prompt}]}
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        extra: Dict[str, Any] = {"usage": {"include": True}}
        if self.reasoning_max_tokens:
            extra["reasoning"] = {"max_tokens": self.reasoning_max_tokens}
        if getattr(self, "reasoning_off", False):
            extra["reasoning"] = {"enabled": False}
        if getattr(self, "providers", None):
            extra["provider"] = {"order": self.providers, "allow_fallbacks": False}
        kwargs["extra_body"] = extra
        resp = await client.chat.completions.create(**kwargs)
        add_response(resp)
        return (resp.choices[0].message.content or "") if resp.choices else ""

    async def _llm_safe(self, system: str, prompt: str, what: str) -> str:
        try:
            return await self._llm(system, prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"lab_notebook {what} call failed: {type(e).__name__}: {e}")
            return ""

    # ---- helpers -------------------------------------------------------------

    def _score(self, ind: Optional[Individual]) -> Optional[float]:
        if ind is None:
            return None
        m = ind.measured("full")
        if m is None or (m.metrics.get(self.valid_key, 1.0) or 0) <= 0:
            return None
        v = m.metrics.get(self.score_key)
        return float(v) if isinstance(v, (int, float)) else None

    def _code(self, ind: Individual) -> str:
        g = ind.genome
        if isinstance(g, CodeGenome):
            if self.evolve_file and self.evolve_file in g.files:
                return g.files[self.evolve_file]
            return next(iter(g.files.values()), "")
        return g.render()

    def _metrics_text(self, ind: Individual) -> str:
        m = ind.metrics("full") or {}
        rows = []
        for k, v in m.items():
            if k in ("fitness_weights",) or isinstance(v, (dict, list)):
                continue
            rows.append(f"{k}={v:.6f}" if isinstance(v, float) else f"{k}={v}")
        return ", ".join(rows)

    def _history_text(self) -> str:
        if not self.history:
            return "(nothing tried yet)"
        rows = ["| cycle | idea | parent | best | delta | predicted | adherence | outcome |",
                "|---|---|---|---|---|---|---|---|"]
        for h in self.history[-self.history_window:]:
            if h.get("evaluation"):
                h = {**h, "title": f"{h['title']} [{h['evaluation']}]"}
            rows.append(
                f"| {h['cycle']} | {h['title']} | {h['parent_score']:.5f} | "
                f"{h['best_score'] if h['best_score'] is not None else 'invalid'} | "
                f"{h['delta']:+.5f} | {h['predicted']:+.5f} | {h.get('adherence', '?')} | "
                f"{h.get('outcome', '')} |"
                if h['best_score'] is not None else
                f"| {h['cycle']} | {h['title']} | {h['parent_score']:.5f} | invalid | -- | "
                f"{h['predicted']:+.5f} | {h.get('adherence', '?')} | {h.get('outcome', '')} |")
        lessons = [h["lesson"] for h in self.history[-self.history_window:] if h.get("lesson")]
        out = "\n".join(rows)
        if lessons:
            out += "\n\nLessons recorded:\n" + "\n".join(f"- {x}" for x in lessons[-12:])
        return out

    # ---- lifecycle -----------------------------------------------------------

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        code = [s for s in seeds if s.kind == CODE]
        if not code:
            return
        self.seed_id = code[0].id
        self.references = [s.id for s in code]
        if isinstance(code[0].genome, CodeGenome) and not self.evolve_file:
            self.evolve_file = next(iter(code[0].genome.files), None)
        if self.use_notebook:
            await self._initial_understanding(ctx, code[0])
        else:
            self.understanding = "(notebook disabled)"

    def _prior_text(self) -> str:
        path = self.prior_file or os.environ.get("LABNOTEBOOK_PRIOR_FILE")
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def _agent_model_params(self) -> Dict[str, Any]:
        """The research agent's request settings, matching the method's own calls."""
        mp: Dict[str, Any] = {}
        if self.max_tokens:
            mp["max_tokens"] = self.max_tokens
        extra: Dict[str, Any] = {"usage": {"include": True}}
        if self.reasoning_max_tokens:
            extra["reasoning"] = {"max_tokens": self.reasoning_max_tokens}
        if getattr(self, "reasoning_off", False):
            extra["reasoning"] = {"enabled": False}
        if getattr(self, "providers", None):
            extra["provider"] = {"order": self.providers, "allow_fallbacks": False}
        mp["extra_body"] = extra
        return mp

    async def _research(self, ctx: EvolveContext, seed: Individual) -> str:
        from ..variators.usage import add as add_usage
        from .lab_notebook_research import parse_candidates, run_research

        notes, log = await run_research(
            model=self.model, objective=ctx.objective, seed_text=self._code(seed),
            model_params=self._agent_model_params(), secs=self.research_secs,
            turns=self.research_turns, tool_chars=self.research_tool_chars,
            tool_secs=self.research_tool_secs, block=self.research_block,
            fallback_llm=self._llm)
        # the agent's calls in the method's usage ledger (the fallback write-up books itself)
        if log.get("calls"):
            add_usage(calls=int(log["calls"]), prompt_tokens=int(log.get("tokens", 0)),
                      cost_usd=float(log.get("usd_est", 0.0)))
        self.research_notes = notes
        self.research_log = log
        self.research_ideas = parse_candidates(notes, limit=self.K)
        log["candidates"] = len(self.research_ideas)
        self.events.append({"kind": "research", "cycle": 0,
                            **{k: v for k, v in log.items() if not isinstance(v, (list, dict))},
                            "web_queries": len(log.get("web_queries", [])),
                            "paper_queries": len(log.get("paper_queries", [])),
                            "fetched": len(log.get("fetched", [])),
                            "blocked": len(log.get("blocked", []))})
        logger.info(f"lab_notebook research: {len(notes)} chars of notes in {log.get('secs')} s "
                    f"({log.get('stopped')}; finish={log.get('finish') or '-'})")
        return notes

    async def _initial_understanding(self, ctx: EvolveContext, seed: Individual) -> None:
        prior = self._prior_text()
        notes = await self._research(ctx, seed) if self.use_research else ""
        prompt = (f"# Problem\n{ctx.objective}\n\n"
                  f"# Seed program (score {self._score(seed)}; metrics: "
                  f"{self._metrics_text(seed)})\n```\n{self._code(seed)}\n```\n\n"
                  + (f"# Prior knowledge about this problem (from the literature and the metric "
                     f"definitions; treat as hypotheses to be checked against measurements)\n"
                     f"{prior}\n\n" if prior else "")
                  + (f"# Literature notes (gathered by a research agent before any experiment; "
                     f"treat as hypotheses to be checked against measurements)\n{notes}\n\n"
                     if notes else "")
                  + "Write the initial notebook.")
        text = await self._llm_safe(UNDERSTAND_SYSTEM, prompt, "initial understanding")
        self.understanding = text.strip() or "(no understanding yet)"
        self.events.append({"kind": "understand", "cycle": 0, "chars": len(self.understanding),
                            "prior_chars": len(prior), "research_chars": len(notes)})

    # ---- the outer loop ------------------------------------------------------

    def _live(self) -> List[_Trajectory]:
        return [t for t in self.trajectories if not t.finished]

    def _select_parent(self, ctx: EvolveContext) -> Optional[Individual]:
        refs = [(self._score(ctx.store.get(i)), i) for i in self.references]
        refs = [(s, i) for s, i in refs if s is not None]
        if not refs:
            return None
        refs.sort(reverse=True)
        best_s, best_id = refs[0]
        if len(refs) == 1 or self.rng.random() < self.p_best_parent:
            return ctx.store.get(best_id)
        best_lineage = (ctx.store.get(best_id).anchor_id if ctx.store.get(best_id) else None)
        others = [(s, i) for s, i in refs[1:]
                  if (ctx.store.get(i).anchor_id if ctx.store.get(i) else None) != best_lineage]
        pool = others[: max(1, len(others) // 2)] or refs[1:]
        return ctx.store.get(self.rng.choice(pool)[1])

    async def _open_cycle(self, ctx: EvolveContext) -> bool:
        """Close the previous cycle (digests + notebook update), pick a parent, propose K ideas,
        and start one trajectory per idea. Returns False when nothing could be started."""
        if self.trajectories:
            await self._close_cycle(ctx)
        parent = self._select_parent(ctx)
        if parent is None:
            return False
        self.cycle += 1
        self.cycle_parent = parent.id
        ideas = await self._gen_ideas(ctx, parent)
        if not ideas:
            logger.warning("lab_notebook: no ideas this cycle; falling back to one blank idea")
            ideas = [{"title": "free improvement", "idea":
                      "Improve the program in whatever way the evidence suggests.",
                      "predicted_gain": 0.0}]
        self.trajectories = []
        for d in ideas[: self.K]:
            ind = ctx.store.add(Individual(
                genome=TextGenome(text=f"{d['title']}\n\n{d['idea']}", kind=IDEA),
                kind=IDEA, parent_ids=[parent.id],
                meta={"cycle": self.cycle, "title": d["title"],
                      "predicted_gain": d["predicted_gain"], "source": d.get("source", "model")}))
            ctx.store.record(Measurement(individual_id=ind.id, ok=True, metrics={},
                                         fidelity="full"))
            self.ideas[ind.id] = {"title": d["title"], "text": d["idea"],
                                  "predicted": float(d["predicted_gain"]),
                                  "cycle": self.cycle, "source": d.get("source", "model")}
            if self.fidelity_planning:
                self.ideas[ind.id].update({"evaluation": d.get("evaluation", "screen"),
                                           "evaluation_reason": d.get("evaluation_reason", "")})
            self.trajectories.append(_Trajectory(idea_id=ind.id, parent_id=parent.id,
                                                 chain=[parent.id]))
        self.events.append({"kind": "cycle", "cycle": self.cycle, "parent": parent.id,
                            "parent_score": self._score(parent),
                            "ideas": [t.idea_id for t in self.trajectories]})
        return True

    async def _gen_ideas(self, ctx: EvolveContext, parent: Individual) -> List[Dict[str, Any]]:
        if self.cycle == 1 and self.research_ideas and self.ideas_mode == "model":
            seeded = [{"title": d["title"], "idea": d["idea"], "predicted_gain": 0.0,
                       "source": "research"} for d in self.research_ideas[: self.K]]
            if len(seeded) < self.K:
                more = await self._model_ideas(ctx, parent)
                seen = {d["title"].lower() for d in seeded}
                seeded += [d for d in more if d["title"].lower() not in seen][: self.K - len(seeded)]
            self.events.append({"kind": "research_ideas", "cycle": 1,
                                "titles": [d["title"] for d in seeded]})
            return seeded
        return await self._model_ideas(ctx, parent)

    def _fidelity_text(self, ctx: EvolveContext) -> str:
        """What the proposer is told about the cheap evaluator, and what it is asked to plan."""
        pairs = []
        for c in ctx.store.of_kind(CODE):
            a, b = self._screen_score(c), self._score(c)
            if a is not None and b is not None:
                pairs.append((a, b))
        lines = ["# Evaluation",
                 f"Each candidate can be read cheaply ('{self.screen_fidelity}') or in full. Only full "
                 "readings count. A 'screen' trajectory writes several candidates per step, reads "
                 "them cheaply and measures the best in full; a 'full' trajectory measures every "
                 "candidate in full."]
        if self.fidelity_ctl is not None:
            cal = self.fidelity_ctl.calibration()
            lines.append(f"So far: {cal.get('n', 0)} candidates read both ways; rank agreement "
                         f"(Spearman) {cal['rho']:.2f}; typical cheap-vs-full error {cal['sigma']:.4f}; "
                         f"a full reading costs {cal.get('cost_ratio', 0):.1f}x a cheap one."
                         if "rho" in cal else
                         f"So far: {cal.get('n', 0)} candidates read both ways (too few to judge).")
        elif len(pairs) >= 3:
            from .fidelity import _spearman
            err = sum(abs(a - b) for a, b in pairs) / len(pairs)
            lines.append(f"So far: {len(pairs)} candidates read both ways; rank agreement "
                         f"(Spearman) {_spearman([p[0] for p in pairs], [p[1] for p in pairs]):.2f}; "
                         f"mean cheap-vs-full difference {err:.4f}.")
        lines.append("For each idea add \"evaluation\": \"screen\" (large or uncertain effects: "
                     "worth trying many candidates) or \"full\" (refinements whose effect is "
                     "smaller than the cheap reading's error), and \"evaluation_reason\": one "
                     "sentence.")
        return "\n".join(lines) + "\n\n"

    async def _model_ideas(self, ctx: EvolveContext, parent: Individual) -> List[Dict[str, Any]]:
        if self.ideas_mode == "none":
            return [{"title": f"trajectory {i + 1}", "idea": "", "predicted_gain": 0.0}
                    for i in range(self.K)]
        if self.ideas_mode == "generic":
            picks = self.rng.sample(GENERIC_IDEAS, min(self.K, len(GENERIC_IDEAS)))
            return [{"title": t, "idea": text, "predicted_gain": 0.0} for t, text in picks]
        prompt = (f"# Problem\n{ctx.objective}\n\n"
                  + (f"# Lab notebook\n{self.understanding}\n\n" if self.use_notebook else "")
                  + (f"# Record of ideas tried\n{self._history_text()}\n\n"
                     if self.use_history else "")
                  +
                  f"# Parent program for this cycle (score {self._score(parent)}; metrics: "
                  f"{self._metrics_text(parent)})\n```\n{self._code(parent)}\n```\n\n"
                  + (self._fidelity_text(ctx) if self.fidelity_planning else "")
                  + f"Propose exactly K={self.K} ideas as JSON.")
        for attempt in range(2):
            text = await self._llm_safe(IDEAS_SYSTEM, prompt, "ideas")
            raw = _extract_json(text, "list")
            out = []
            for d in (raw or []):
                if not isinstance(d, dict) or not d.get("idea"):
                    continue
                try:
                    pg = float(d.get("predicted_gain", 0.0) or 0.0)
                except (TypeError, ValueError):
                    pg = 0.0
                if not math.isfinite(pg):
                    pg = 0.0
                item = {"title": str(d.get("title") or d["idea"][:40]).strip()[:80],
                        "idea": str(d["idea"]).strip(), "predicted_gain": pg}
                if self.fidelity_planning:
                    ev = str(d.get("evaluation", "")).strip().lower()
                    item["evaluation"] = ev if ev in ("screen", "full") else "screen"
                    item["evaluation_reason"] = str(d.get("evaluation_reason", "")).strip()[:300]
                out.append(item)
            if out:
                return out
            logger.warning(f"lab_notebook: ideas reply unparseable (attempt {attempt + 1})")
        return []

    async def _close_cycle(self, ctx: EvolveContext) -> None:
        """Digest every trajectory of the cycle in parallel, then rewrite the notebook."""
        parent = ctx.store.get(self.cycle_parent) if self.cycle_parent else None
        parent_score = self._score(parent) if parent else None
        digests = await asyncio.gather(
            *(self._digest(ctx, t, parent, parent_score) for t in self.trajectories))
        for t, d in zip(self.trajectories, digests):
            self.history.append(d)
        for t in self.trajectories:
            t.finished = True
        new = "\n\n".join(
            f"### {d['title']} (predicted {d['predicted']:+.5f}, measured "
            f"{d['delta']:+.5f}, adherence {d.get('adherence', '?')})\n"
            f"outcome: {d.get('outcome', '')}\nwhy: {d.get('why', '')}\n"
            f"lesson: {d.get('lesson', '')}" for d in digests)
        best_ref = max(((self._score(ctx.store.get(i)) if self._score(ctx.store.get(i))
                         is not None else -math.inf), i) for i in self.references)
        prompt = (f"# Problem\n{ctx.objective}\n\n"
                  f"# Notebook before this cycle\n{self.understanding}\n\n"
                  f"# This cycle's experiments (cycle {self.cycle}, parent score "
                  f"{parent_score})\n{new}\n\n"
                  f"# Best score so far: {best_ref[0]}\n\n"
                  "Rewrite the notebook.")
        text = await self._llm_safe(UPDATE_SYSTEM, prompt, "update") if self.use_notebook else ""
        if text.strip():
            self.understanding = text.strip()
        self.events.append({"kind": "update", "cycle": self.cycle,
                            "chars": len(self.understanding)})

    async def _digest(self, ctx: EvolveContext, t: _Trajectory, parent: Optional[Individual],
                      parent_score: Optional[float]) -> Dict[str, Any]:
        idea = self.ideas.get(t.idea_id, {})
        kids = [ctx.store.get(i) for i in t.chain[1:]]
        kids = [k for k in kids if k is not None]
        # every candidate the trajectory measured, not only the committed ones
        all_kids = [c for c in ctx.store.of_kind(CODE) if c.anchor_id == t.idea_id]
        scored = [(self._score(c), c) for c in all_kids]
        # key-based, never a tuple max: equal scores are routine (two candidates from one
        # prompt, or two invalid ones at 0) and a tuple tie falls through to comparing the
        # Individuals themselves, which raises
        best_s, best = (max(scored, key=lambda sc: sc[0] if sc[0] is not None else -math.inf)
                        if scored else (None, None))
        best_s = best_s if best_s is not None else -math.inf
        if best is not None and best_s > -math.inf:
            if best.id not in self.references:
                self.references.append(best.id)
        best_score = best_s if best_s > -math.inf else None
        delta = (best_score - parent_score) if (best_score is not None
                                                and parent_score is not None) else 0.0
        row = {"cycle": self.cycle, "idea_id": t.idea_id, "title": idea.get("title", "?"),
               "parent_id": t.parent_id, "parent_score": parent_score or 0.0,
               "best_id": best.id if best is not None else None,
               "best_score": best_score, "delta": delta,
               "predicted": float(idea.get("predicted", 0.0)),
               "n_candidates": len(all_kids)}
        traj_lines = []
        for s, c in sorted(scored, key=lambda x: (x[1].order)):
            m = c.measured()
            err = (m.artifacts.get("error") if m else None) or ""
            cheap = self._screen_score(c) if self.screen_fidelity else None
            if s is None and cheap is not None:
                # screened and not promoted: a cheap reading, not a failure
                traj_lines.append(f"- candidate {c.id}: screened only, cheap reading {cheap} "
                                  "(not promoted to a full measurement)")
                continue
            traj_lines.append(f"- candidate {c.id}: score={s if s is not None else 'invalid'}"
                              f"{f' (cheap reading {cheap})' if cheap is not None and s is not None else ''}"
                              f"{'  error: ' + str(err)[:120] if err else ''}")
        name = self.evolve_file or "program"
        seed = ctx.store.get(self.seed_id) if self.seed_id else None
        d_parent = _unified_diff(self._code(parent), self._code(best), name) \
            if (parent is not None and best is not None) else "(no valid program)"
        d_seed = _unified_diff(self._code(seed), self._code(best), name) \
            if (seed is not None and best is not None and parent is not None
                and seed.id != parent.id) else "(same as parent diff)"
        plan = ""
        if self.fidelity_planning:
            row["evaluation"] = idea.get("evaluation")
            pairs = [(self._screen_score(c), self._score(c)) for c in all_kids]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            plan = (f"# Evaluation plan\n{idea.get('evaluation', 'screen')}"
                    f"{': ' + idea['evaluation_reason'] if idea.get('evaluation_reason') else ''}\n"
                    + ("cheap vs full readings of the promoted candidates: "
                       + ", ".join(f"{a:.4f} -> {b:.4f}" for a, b in pairs) + "\n" if pairs else "")
                    + "If the cheap evaluator misranked candidates here, or the plan was the wrong "
                      "choice, say so in the lesson.\n\n")
        prompt = (f"# Problem\n{ctx.objective}\n\n"
                  f"# Idea tested\n{idea.get('title', '')}\n{idea.get('text', '')}\n"
                  f"predicted gain: {row['predicted']:+.5f}\n\n" + plan +
                  f"# Trajectory (parent score {parent_score})\n" + "\n".join(traj_lines) +
                  f"\n\nbest: {best_score}  delta vs parent: {delta:+.5f}\n\n"
                  f"# Best program's diff vs parent\n```diff\n{d_parent}\n```\n\n"
                  f"# Best program's diff vs original seed\n```diff\n{d_seed}\n```\n\n"
                  + ("# Failures during the trajectory\n"
                     + "\n".join(f"- {k} (x{int(v)})" for k, v in t.failures.items()) + "\n\n"
                     if t.failures else "")
                  + "Write the digest as JSON.")
        parsed: Dict[str, Any] = {}
        for _attempt in range(2 if self.use_digest else 0):
            # one retry: the digest is the outer loop's evidence, and in the smoke run one of
            # four came back as prose the parser could not use
            text = await self._llm_safe(DIGEST_SYSTEM, prompt, "digest")
            parsed = _extract_json(text, "object") or {}
            if parsed.get("outcome"):
                break
        try:
            adh = float(parsed.get("adherence", 0.0) or 0.0)
        except (TypeError, ValueError):
            adh = 0.0
        row.update({"outcome": str(parsed.get("outcome", "")).strip()[:300],
                    "why": str(parsed.get("why", "")).strip()[:600],
                    "adherence": round(max(0.0, min(1.0, adh)), 2),
                    "lesson": str(parsed.get("lesson", "")).strip()[:300]})
        self.events.append({"kind": "digest", "cycle": self.cycle, "idea": t.idea_id,
                            "best": row["best_id"], "delta": delta})
        return row

    # ---- the inner loop: one SimpleTES step per trajectory ---------------------

    async def ask(self, ctx: EvolveContext, n: int) -> List[Create]:
        items: List[Create] = []
        if not self.seed_id:
            return items
        self.selector.observe(ctx, self.score_key)
        while self.pending_promote and len(items) < n:
            items.append(self.pending_promote.pop(0))
        if not self._live() and not self.open:
            if not await self._open_cycle(ctx):
                return items
        for t_idx, t in enumerate(self.trajectories):
            if len(items) >= n:
                break
            if t.finished or t.open_batch is not None:
                continue
            if t.steps_done >= self.T:
                t.finished = True
                continue
            nodes = [ctx.store.get(i) for i in t.chain]
            nodes = [x for x in nodes if x is not None]
            nodes.sort(key=lambda x: score_of(x, self.score_key) or -math.inf, reverse=True)
            picked = self.selector.pick(nodes, min(self.num_inspirations, len(nodes)),
                                        self.rng, self.score_key, t_idx)
            if not picked:
                continue
            idea = self.ideas.get(t.idea_id, {})
            if idea.get("text"):
                instruction = (
                    f"{ctx.objective}\n\n"
                    f"## The ONE idea this trajectory tests: {idea.get('title', '')}\n"
                    f"{idea.get('text', '')}\n\n"
                    "Every candidate you write must implement or advance THIS idea on the reference "
                    "program(s) below. Keep everything the idea does not touch intact; do not "
                    "switch to an unrelated improvement."
                )
            else:   # ideas="none": a plain SimpleTES step
                instruction = ctx.objective
            cascade = self.screen_fidelity is not None
            if cascade and self.fidelity_planning and \
                    self.ideas.get(t.idea_id, {}).get("evaluation") == "full":
                cascade = False                  # the proposer planned full measurements here
            k = self.k_screen if cascade else self.k
            if cascade and self.fidelity_ctl is not None:
                ks = self.fidelity_ctl.k_screen()
                if ks is None:                   # the cheap reading stopped ranking like the full one
                    cascade, k = False, self.k
                else:
                    k = ks
            item = Create(
                kind=CODE, parent_ids=[p.id for p in picked], anchor_id=t.idea_id, k=k,
                fidelity=self.screen_fidelity if cascade else "full",
                context=PromptContext(instruction=instruction, parents=list(picked),
                                      failures=dict(t.failures)),
                meta={"cycle": self.cycle, "trajectory": t_idx, "step": t.steps_done,
                      "idea": t.idea_id},
            )
            self.open[item.batch_id] = _Batch(traj_idx=t_idx,
                                              parent_ids=[p.id for p in picked], k=k,
                                              stage="screen" if cascade else "full")
            t.open_batch = item.batch_id
            items.append(item)
        return items

    def _eval_secs(self, m: Measurement) -> Optional[float]:
        v = m.metrics.get("eval_secs")
        return float(v) if isinstance(v, (int, float)) and v > 0 else (m.duration or None)

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement) -> None:
        batch_id = self.promote_of.pop(m.batch_id, m.batch_id)
        b = self.open.get(batch_id)
        if b is None:
            return
        if self.fidelity_ctl is not None:
            self.fidelity_ctl.observe_cost(m.fidelity, self._eval_secs(m))
            if m.fidelity == "full" and b.stage == "promote":
                cheap, full = self._screen_score(ind), self._score(ind)
                if cheap is not None and full is not None:
                    self.fidelity_ctl.observe_pair(cheap, full)
        if b.stage == "screen":
            b.screened.append(ind.id)
            b.done += 1
            if b.done >= b.k:
                self._promote(ctx, batch_id)
            return
        b.children.append(ind.id)
        b.done += 1
        if b.done >= b.k:
            self._commit(ctx, batch_id)

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        batch_id = self.promote_of.pop(f.batch_id, f.batch_id)
        b = self.open.get(batch_id)
        if b is None:
            return
        b.done += 1
        if f.reason and 0 <= b.traj_idx < len(self.trajectories):
            book = self.trajectories[b.traj_idx].failures
            book[f.reason] = book.get(f.reason, 0.0) + 1.0
        if b.done >= b.k:
            if b.stage == "screen":
                self._promote(ctx, batch_id)
            else:
                self._commit(ctx, batch_id)

    def _screen_score(self, ind: Optional[Individual]) -> Optional[float]:
        m = ind.measured(self.screen_fidelity) if ind is not None and self.screen_fidelity else None
        if m is None or (m.metrics.get(self.valid_key, 1.0) or 0) <= 0:
            return None
        v = m.metrics.get(self.score_key)
        return float(v) if isinstance(v, (int, float)) else None

    def _promote(self, ctx: EvolveContext, batch_id: str) -> None:
        """A screened step: measure its best `promote_top` feasible candidates in full. With none
        feasible the step ends as a step whose candidates all failed."""
        b = self.open[batch_id]
        ranked = [(self._screen_score(ctx.store.get(c)), c) for c in b.screened]
        ranked = sorted(((s, c) for s, c in ranked if s is not None), reverse=True)
        ev: Dict[str, Any] = {"kind": "screen", "cycle": self.cycle, "trajectory": b.traj_idx,
                              "screened": len(b.screened), "feasible": len(ranked),
                              "screen_scores": [round(s, 6) for s, _ in ranked]}
        if self.fidelity_ctl is not None:
            t = self.trajectories[b.traj_idx]
            chain = [self._score(ctx.store.get(i)) for i in t.chain]
            incumbent = max((x for x in chain if x is not None), default=None)
            d = self.fidelity_ctl.promote([(c, s) for s, c in ranked], incumbent)
            top = d["promote"]
            cal = self.fidelity_ctl.calibration()
            ev.update({"rule": d["rule"], "explored": d["explored"], "incumbent": incumbent,
                       "calibration": {k: (round(v, 4) if isinstance(v, float) else v)
                                       for k, v in cal.items()}})
        else:
            top = [c for _, c in ranked[: self.promote_top]]
        ev["promoted"] = top
        self.events.append(ev)
        if not top:
            self._commit(ctx, batch_id)
            return
        b.stage, b.k, b.done = "promote", len(top), 0
        for c in top:
            rm = Remeasure(individual_id=c, fidelity="full",
                           meta={"cycle": self.cycle, "trajectory": b.traj_idx, "promote": True})
            self.promote_of[rm.batch_id] = batch_id
            self.pending_promote.append(rm)

    def _commit(self, ctx: EvolveContext, batch_id: str) -> None:
        b = self.open.pop(batch_id, None)
        if b is None or not (0 <= b.traj_idx < len(self.trajectories)):
            return
        t = self.trajectories[b.traj_idx]
        t.open_batch = None
        t.steps_done += 1
        scored = [(self._score(ctx.store.get(c)), c) for c in b.children]
        scored = [(s, c) for s, c in scored if s is not None]
        if scored:
            scored.sort(reverse=True)
            t.chain.append(scored[0][1])
            self.selector.on_commit(b.traj_idx, b.parent_ids, scored[0][0])
        if t.steps_done >= self.T:
            t.finished = True

    # ---- reporting -------------------------------------------------------------

    def rank(self, ctx: EvolveContext, kind: str = CODE) -> Ranking:
        scores = {}
        for ind in ctx.store.of_kind(kind or CODE):
            s = self._score(ind)
            if s is not None:
                scores[ind.id] = s
        return Ranking.by_score(scores)

    # ---- persistence -----------------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {"selector": self.selector.name, "selector_state": self.selector.state_dict(),
            "understanding": self.understanding, "history": list(self.history),
            "references": list(self.references), "ideas": dict(self.ideas),
            "cycle": self.cycle, "cycle_parent": self.cycle_parent,
            "seed_id": self.seed_id, "evolve_file": self.evolve_file,
            "trajectories": [{"idea_id": t.idea_id, "parent_id": t.parent_id,
                              "chain": list(t.chain), "steps_done": t.steps_done,
                              "finished": t.finished, "failures": dict(t.failures)}
                             for t in self.trajectories],
            "events": list(self.events)[-400:],
            "research_notes": self.research_notes, "research_log": dict(self.research_log),
            "research_ideas": list(self.research_ideas),
            "fidelity_ctl": self.fidelity_ctl.state_dict() if self.fidelity_ctl else None,
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.selector.load_state_dict(state.get("selector_state", {}))
        self.understanding = state.get("understanding", "")
        self.history = list(state.get("history", []))
        self.references = list(state.get("references", []))
        self.ideas = dict(state.get("ideas", {}))
        self.cycle = int(state.get("cycle", 0))
        self.cycle_parent = state.get("cycle_parent")
        self.seed_id = state.get("seed_id")
        self.evolve_file = state.get("evolve_file")
        self.trajectories = [_Trajectory(idea_id=d["idea_id"], parent_id=d["parent_id"],
                                         chain=list(d.get("chain", [])),
                                         steps_done=int(d.get("steps_done", 0)),
                                         finished=bool(d.get("finished", False)),
                                         failures=dict(d.get("failures", {})))
                             for d in state.get("trajectories", [])]
        self.events = list(state.get("events", []))
        self.research_notes = state.get("research_notes", "")
        self.research_log = dict(state.get("research_log", {}))
        self.research_ideas = list(state.get("research_ideas", []))
        if self.fidelity_ctl is not None and state.get("fidelity_ctl"):
            self.fidelity_ctl.load_state_dict(state["fidelity_ctl"])

    def reconcile(self, ctx: EvolveContext) -> None:
        """Batches in flight at checkpoint time never report back; their trajectories resume
        at the step they had completed."""
        self.open.clear()
        self.pending_promote.clear()
        self.promote_of.clear()
        for t in self.trajectories:
            t.open_batch = None
            t.chain = [i for i in t.chain if i in ctx.store]
        self.references = [i for i in self.references if i in ctx.store]
        if not self.seed_id or self.seed_id not in ctx.store:
            seed = next((i for i in sorted(ctx.store, key=lambda x: x.order)
                         if i.kind == CODE and not i.parent_ids), None)
            self.seed_id = seed.id if seed else None

    # ---- the operator this algorithm is defined with ----------------------------

    def default_variator(self, *, model: str = "high", timeout: float = 600,
                         target_file: Optional[str] = None, **kw):
        """SimpleTES's own operator, verbatim -- the inner loop IS SimpleTES, so the comparison
        against plain SimpleTES isolates the outer loop and nothing else."""
        self.model, self.timeout = model, timeout
        self.max_tokens = kw.get("max_output_tokens") or 32768
        self.reasoning_max_tokens = kw.get("reasoning_max_tokens")
        self.reply_retries = int(kw.get("reply_retries") or 1)
        self.reasoning_off = bool(kw.get("reasoning_off"))
        self.providers = list(kw.get("providers") or [])
        if target_file:
            self.evolve_file = target_file
        return UpstreamCompletionVariator(model=model, timeout=timeout,
                                          target_file=target_file,
                                          score_key=self.score_key,
                                          max_tokens=self.max_tokens,
                                          reasoning_max_tokens=self.reasoning_max_tokens,
                                          reply_retries=self.reply_retries,
                                          reasoning_off=self.reasoning_off,
                                          providers=self.providers)
