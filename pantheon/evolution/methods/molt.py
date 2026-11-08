"""Molt: SimpleTES that sheds a stalled design and grows a new one in its own chain.

Plain SimpleTES polishes one design until the global best stops moving. Its chains pick parents
best-first, so a program built on a different algorithm -- which usually starts below the polished
incumbent -- is never picked again and the run stays on the old design. Molt adds three things:

  * a stall rule: `stall_evals` resolved children without the global best rising by more than
    `progress_rel` (relative) triggers a planning round.
  * planning: `planners` independent calls read the search state (live programs, a different random
    sample of recent attempts each, the code of the strongest programs, and the designs earlier
    rounds already tried) and each proposes `directions_per_planner` directions, at least
    `min_redesign` of them a `redesign`: a different overall algorithm written as a complete new
    program. A listwise call removes obvious duplicates; every kept direction runs once.
  * protected chains with a probation: a redesign's program opens a NEW chain (a sprout), so the
    selector never compares it with the old design while it is young. A sprout gets
    `probation_turns` turns (its first program plus ordinary SimpleTES batches); then it graduates if
    its best is within `probation_margin` (relative) of the champion it was planned against, and is
    retired otherwise. At most `max_established` graduated or original chains stay active; when a
    graduate would exceed that, the active chain with the lowest best is retired (the molt: the old
    shell is shed), never the chain holding the global best.
  * planning goes on while sprouts are young: a stall plans again even when sprouts are on
    probation, as long as fewer than `max_sprouts` are.

Integration and exploration directions (edits of a named live program, with up to two donors) run
once and their child joins the target's chain, as in RSI-top's archive insertion.

Where the numbers come from. The stall rule, the planner prompt and the dedup pass are the
RSI-top-style intervention that was ablated on AHC058/024/046 (42 runs, 160 evaluations each,
pantheonos evolution_bench, Nov 2026): the redesign type plus a protected chain beat plain SimpleTES,
more chains, and RSI-top's own directions; guided multi-step trajectories added nothing, so each
direction runs once. Without a probation those runs ended with 15-29 chains sharing turns
round-robin. Over their 771 redesign chains, every chain that ended up holding the run's best was
within 1 % of the champion it was planned against by its 5th program, while 71 % of all chains never
got there -- hence `probation_turns=5`, `probation_margin=0.01`. `max_established` is a budget
guard, not a measured optimum. Planning during probation came from the ablations (AHC058/046/024,
5 seeds, 400 evaluations, Nov 2026): when a young sprout blocked planning, Molt intervened ~6 times
per run; planning on (at most 4 sprouts on probation) raised it to ~8, at the same cost, scored
higher on 10 of 15 (task, seed) pairs on held-out cases, and broke out of AHC024's local optimum in
4 of 5 runs.

Only programs count. The stall window and a sprout's turns advance on attempts that produced a
program (scored, or crashed when run); a model call that returned nothing (stage "generate": a
gateway outage, an empty reply) tells nothing about the search and moves neither. Otherwise an
outage reads as a stall: planning fires again and again, and sprouts burn their probation without a
child and are retired. A redesign the model never wrote is not recorded as a tried design either.

A redesign that falls back. A model asked for a new design often guards it with the reference's
own algorithm; when the new code fails on every case the fallback answers instead, and the program
scores exactly like the reference (AHC046, Nov 2026: 26 of 121 redesigns, 14 passed probation on
the fallback's score alone). Such an echo is told so in its chain's failure patterns and graduates
only once its chain beats the echoed score; the worker prompt also asks for no silent fallback,
since a redesign that fails visibly is fixed by its next turns (first programs scoring 0 grew into
the run's best 7 times).

Prompts. Ordinary chain steps use SimpleTES's generation prompt unchanged. A planned direction is
written with Molt's own (DIRECTION): every program shown carries its branch id and role (target,
donor, or -- for a redesign -- an input/output reference, the shortest program that scored, so the
incumbent is neither copied nor available as a fallback), the attempts the planner cites are
quoted with their rejection reasons, and nothing asks for "a higher score". A chain a redesign
opened gets its design, its probation status and its recent results (with rejection reasons) on
top of SimpleTES's prompt, whose "prioritize novel approaches" closing is replaced by "keep
developing this design".

Every intervention, sprout and decision carries the attempt count it happened at, and `lineage()`
says which intervention the run's best descends from (run_bench writes it into summary.json).

Budget accounting is SimpleTES's: planner and dedup calls are booked like every other model call;
direction children are evaluations inside the same budget. Chains have no per-chain prompt cap here
(sprouts appear mid-run); the run budget stops the run and retirement keeps the active set small.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence

from ..core.genome import CodeGenome
from ..core.individual import Individual
from ..core.method import EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext
from .simpletes import SimpleTES, UpstreamCompletionVariator, score_of

logger = logging.getLogger(__name__)

PLANNER = """# Dynamic branch intervention portfolio

## Intervention objective

Ordinary search has stalled in its current neighborhood. This bounded intervention must redirect
search toward global progress. Use the {type_names} proposal types defined
below to build a portfolio that both seeks credible routes beyond the current frontier and opens
causally distinct, reusable measured states for future search. Routine novelty or a weak-parent gain
is insufficient.

## The task

{objective}

## Scope

You do not edit code in this planning call. Select exact live target branches and propose a small
portfolio of bounded source-code experiments. Select donor branches only when integrating a
mechanism already observed in history. Only the candidate program may change; do not propose
anything that bypasses or games the evaluator.

## Branch catalog (every program still LIVE in the search; higher score is better)

{catalog}

`target_branch_id` and every `donor_branch_id` MUST be ids from this catalog. Compare the live best
branches before choosing targets. Do not default every direction to the global best; also consider
live branches created by strong measured parent gains or carrying mechanisms absent from the best.

## Recent attempts (child <- parents, child score, gain over its best parent; errors abridged)

{attempts}

## Code of the strongest live branches

{code}

## Designs tried at earlier stalls (do not propose these again)

{tried}

## Proposal types

- `integration`: reuse measured knowledge from the history above -- transfer a mechanism that a
  donor branch demonstrably carries, or apply a lesson the attempts establish. Cite the attempt ids.
- `exploration`: test a genuinely new representation, algorithmic or search hypothesis. Positive
  evidence is not required; never invent it. State the new axis and what result would falsify it.
{redesign_type}
## Portfolio selection

- Return exactly {num_directions} directions.{redesign_rule}
- Every direction must target global progress, not merely improve a weak parent: give the target's
  score, the frontier score, the gain required to beat the frontier, and why the mechanism could
  plausibly clear that gap.

## Verbalized proposal distribution

Generate the directions as one joint portfolio sampled across the distribution of credible
interventions, not as variations of the single most typical answer. Include credible
lower-probability modes as well as common modes. Add a numeric `verbalized_probability` between 0
and 1 to every direction: how likely an ordinary planner would be to independently propose it
(generation typicality, not probability of success; values need not sum to 1). Do not weaken a
direction merely to give it a lower probability. Directions are substantively distinct only when they
test materially different bottleneck -> mechanism -> expected-effect hypotheses; changing only a
target, parameter, or wording is insufficient.

## Output

Return exactly one JSON object as your entire final answer -- no prose, no Markdown fence:

{{"diagnosis": "concise evidence-backed diagnosis of the current search state",
  "exhausted_patterns": ["up to three evidence-backed patterns not to repeat"],
  "directions": [
    {{"type": {type_enum},
      "target_branch_id": "live catalog id the worker will edit",
      "mechanism_hypothesis": "falsifiable target-local reason the experiment may raise the score",
      "task": "one concrete bounded source-code experiment (not code, not a list of alternatives)",
      "frontier_case": {{"target_score": 0.0, "frontier_score": 0.0, "required_gain": 0.0, "why_plausible": "..."}},
      "constraints": ["only conditions material to this experiment"],
      "donor_branch_ids": ["at most two live donors, integration only"],
      "evidence_attempt_ids": ["attempt ids from the log"],
      "novel_axis": "exploration only, else null",
      "falsifier": "exploration only, else null",
      "verbalized_probability": 0.0}}
  ]}}
"""

DEDUP = """You are removing only obvious semantic duplicates from a frozen pool of intervention directions.

Remove a direction only when a kept direction proposes substantially the same mechanism, concrete
action, and expected causal effect. Sharing a broad topic, bottleneck, target, or direction family is
not enough. Keep variants whose implementation, scope, composition, target state, or falsifiable
effect differs materially. When uncertain, keep both.

Do not judge whether a unique direction is promising, exhausted, well evidenced, or likely to
succeed. Within an obvious duplicate group, keep the clearest and most executable representative.
The number kept is variable.

Return exactly one JSON object:

{{"kept_direction_ids": ["supplied direction id", "..."],
  "removed_directions": [{{"direction_id": "removed id", "duplicate_of": "kept id", "reason": "brief"}}]}}

Every supplied direction ID must appear exactly once: either in `kept_direction_ids` or as one
`removed_directions[].direction_id`. Copy every ID exactly as supplied. Do not invent IDs.

Candidate directions:

{candidates}
"""

# The prompt a planned direction is written with. Not SimpleTES's generation template: that one
# shows unlabeled "inspirations" to learn from and asks for "an improved solution with higher
# score", which contradicts a redesign (write a different algorithm, whose first version usually
# scores lower) and leaves the branch ids the planner's task names (91 % of directions) pointing at
# nothing. Here every program carries its id and its role, the attempts the planner cites are
# quoted, and a redesign sees an input/output reference instead of the incumbent to copy.
DIRECTION = """Task: {objective}

=== YOUR ASSIGNMENT: one planned experiment ({type}) ===
{assignment}
Task: {task}
Mechanism hypothesis: {hypothesis}{extra}
Constraints: {constraints}

=== PROGRAMS (each labeled with its branch id and its role) ===
{programs}{evidence}
=== OUTPUT RULES (must follow exactly) ===
1) Only the code between `{start}` and `{end}` is extracted.
2) The final program is reconstructed as EXACT_PREFIX + your block + EXACT_SUFFIX.
3) Keep the marker lines exactly as written.
4) Return one {block_word} that includes both EVOLVE-BLOCK markers.

EXACT_PREFIX (kept unchanged):
```{tag}
{prefix}
```

EXACT_SUFFIX (kept unchanged):
```{tag}
{suffix}
```

{closing}
"""

REDESIGN_ASSIGNMENT = (
    "Write a COMPLETE NEW PROGRAM implementing the design below, from scratch. It replaces the "
    "current program {target} (score {target_score}), whose code is deliberately not shown: the "
    "point is a different algorithm, not an edit of that one. The program shown below is only an "
    "input/output reference -- reuse its parsing, output and helpers, not its method.")
REDESIGN_CLOSING = (
    "Write the new design end to end. Its first version may score below the current best -- it "
    "gets its own chain and several refinement steps to grow, and it is judged after them. Do NOT "
    "fall back to another method (the reference's or the current program's) when your design "
    "fails on an input: the fallback would answer in its place, the program would score exactly "
    "like that method, and the failure of the new design would be hidden. Let a failure show, so "
    "the next steps can fix it. Respect the time limit on every input.")
EDIT_ASSIGNMENT = (
    "Edit the TARGET program below. DONOR programs, if any, carry the mechanism to take over.")
EDIT_CLOSING = "Implement exactly this experiment on the TARGET program."

# Added to SimpleTES's prompt for a chain a redesign opened: without it the chain's next steps see
# only code and scores, and a repair can quietly swap the new design back for an old one.
DESIGN_BRIEF = """=== THIS CHAIN'S DESIGN ===
This chain was opened to develop one specific design (a redesign planned at a stall):
Design: {task}
Why it could win: {hypothesis}
{status}
Keep developing THIS design: fix what fails (read the rejection reasons below), then improve it. Do
not replace it with a different algorithm and do not add a fallback to another method.
Recent results in this chain:
{recent}
"""

# replaces SimpleTES's closing strategy ("prioritize NOVEL approaches ... higher score") on a chain
# a redesign opened, which would otherwise pull every step away from the design it is developing
DESIGN_STRATEGY = """
=== GENERATION STRATEGY ===
- Keep this chain's design (see THIS CHAIN'S DESIGN above): fix its failures first, then improve it
- Use the reference solutions for this design's details, not to switch to another algorithm
- Avoid the listed failure patterns

Generate the next version of this design:
"""

REDESIGN_TYPE = """- `redesign`: replace the target's overall algorithm with a different one -- a different
  representation of a solution, a different search paradigm, or a different decomposition of
  the problem -- written as a complete new program, not an edit of the target. The target is
  only a reference for the input/output format and reusable helpers. A new design usually
  starts below the polished incumbent; it is judged after a few refinement steps, so argue
  for the design's ceiling, not its first version.
"""

BASE, SPROUT, ESTABLISHED, RETIRED = "base", "sprout", "established", "retired"


ECHO_NOTE = ("NEW DESIGN NEVER RAN: the redesign scored exactly like {what}, so it fell back to "
             "that program's method on every input. Fix the new design itself and drop the "
             "fallback")


def _differs(a: float, b: float) -> bool:
    return abs(a - b) > 1e-9 * max(1.0, abs(a), abs(b))


def _json(text: str) -> Optional[Any]:
    """The first JSON object in a reply, fenced or not; tolerant of trailing commas."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth, end = 0, None
    in_str = esc = False
    for i, ch in enumerate(t[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    blob = t[start:end] if end else t[start:]
    for cand in (blob, re.sub(r",\s*([}\]])", r"\1", blob)):
        try:
            return json.loads(cand)
        except Exception:  # noqa: BLE001
            pass
    return None


class Molt(SimpleTES):
    """SimpleTES + stall-triggered redesigns that grow in protected chains under a probation."""

    name = "molt"
    variator_cls = None    # set below MoltVariator: planned directions and sprout chains

    def __init__(self, *, planners: int = 4, directions_per_planner: int = 2,
                 min_redesign: int = 1, stall_evals: int = 12, progress_rel: float = 0.002,
                 probation_turns: int = 5, probation_margin: float = 0.01,
                 max_established: int = 4, planner_reasoning: bool = True,
                 catalog_size: int = 24, attempts_shown: int = 40, code_shown: int = 3,
                 tried_shown: int = 20, redesign: bool = True, protect: bool = True,
                 probation: bool = True, trigger: str = "stall", period: int = 65,
                 period_first: int = 20, plan_during_probation: bool = True,
                 max_sprouts: int = 4, **kw):
        """The last six are ablations; their defaults are Molt. `redesign=False`: planners get
        RSI-top's two types only. `protect=False`: a redesign's program joins its target's
        chains like an edit instead of opening a sprout. `probation=False`: every sprout is kept
        at once, never judged, and no chain is shed. `trigger="periodic"`: plan at attempt
        `period_first` and then every `period` attempts, stalled or not (the defaults match
        Molt's measured spacing on AHC058/024/046: first at ~20, then every ~65).
        `plan_during_probation=False` (Molt before Nov 2026, "molt-gated"): any sprout on
        probation blocks planning; with it on (the default) a stall plans as long as fewer than
        `max_sprouts` sprouts are on probation."""
        super().__init__(**kw)
        self.planners = planners
        self.directions_per_planner = directions_per_planner
        self.min_redesign = min_redesign
        self.stall_evals = stall_evals
        self.progress_rel = progress_rel
        self.probation_turns = probation_turns
        self.probation_margin = probation_margin
        self.max_established = max(1, max_established)
        self.planner_reasoning = planner_reasoning
        self.catalog_size = catalog_size
        self.attempts_shown = attempts_shown
        self.code_shown = code_shown
        self.tried_shown = tried_shown
        if trigger not in ("stall", "periodic"):
            raise ValueError(f"trigger must be 'stall' or 'periodic', not {trigger!r}")
        self.use_redesign, self.protect, self.use_probation = redesign, protect, probation
        self.trigger, self.period, self.period_first = trigger, period, period_first
        self.plan_during_probation, self.max_sprouts = plan_during_probation, max(1, max_sprouts)
        off = [n for n, on in (("noredesign", redesign), ("noprotect", protect),
                               ("noprobation", probation), ("periodic", trigger == "stall"))
               if not on]
        if not plan_during_probation:
            off.append("gated")
        if off:
            self.name = "molt-" + "-".join(off)

        self.role: Dict[int, str] = {c: BASE for c in range(self.num_chains)}
        self.sprouts: Dict[int, Dict[str, Any]] = {}
        """Chain index -> {intervention, direction, task, old (champion when planned), fate}."""
        self.anchor: Optional[float] = None
        """Global best at the last meaningful progress."""
        self.since: Optional[int] = None
        """Resolved children since then; None until computed from the store."""
        self.pending: List[Dict[str, Any]] = []
        self.open_dirs: Dict[str, Dict[str, Any]] = {}
        self.interventions: List[Dict[str, Any]] = []
        self.tried: List[Dict[str, Any]] = []
        """Every redesign run so far, with its outcome -- shown to later planners."""
        self.attempts: Optional[int] = None
        """Attempts that produced a program so far (the clock every record is stamped with)."""
        self.sheds: List[Dict[str, Any]] = []
        self.turns: Dict[int, int] = {}
        """Chain index -> batches that produced at least one program (a probation's clock)."""
        self._turned: set = set()
        """Open batches already counted in `turns`."""
        self._planning = False
        self.model, self.timeout, self.providers = "high", 900.0, []
        self.max_tokens: Optional[int] = 32768
        self.evolve_file: Optional[str] = None

    def default_variator(self, *, model: str = "high", timeout: float = 600,
                         target_file: Optional[str] = None, **kw):
        self.model, self.timeout = model, max(timeout, 900.0)
        self.max_tokens = kw.get("max_output_tokens") or 32768
        self.providers = list(kw.get("providers") or [])
        self.evolve_file = target_file
        return super().default_variator(model=model, timeout=timeout, target_file=target_file, **kw)

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        await super().start(ctx, seeds)
        # chains appear mid-run, so an equal per-chain share fixed at the start would be wrong;
        # the run budget stops the run and retirement keeps the number of active chains small
        self.prompt_budget = {}

    # ---- model calls the method makes itself ----------------------------------------

    async def _llm(self, prompt: str) -> str:
        from openai import AsyncOpenAI

        from pantheon.utils.llm_providers import detect_provider
        from ..variators.usage import add_response

        cfg = detect_provider(self.model, False)
        client = AsyncOpenAI(api_key=cfg.api_key or os.environ.get("OPENAI_API_KEY"),
                             base_url=cfg.base_url or os.environ.get("OPENAI_API_BASE") or None,
                             timeout=self.timeout)
        extra: Dict[str, Any] = {"usage": {"include": True}}
        extra["reasoning"] = {"effort": "high"} if self.planner_reasoning else {"enabled": False}
        if self.providers:
            extra["provider"] = {"order": self.providers, "allow_fallbacks": False}
        kwargs: Dict[str, Any] = {"model": cfg.model_name, "extra_body": extra,
                                  "messages": [{"role": "user", "content": prompt}]}
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"molt planning call failed: {type(e).__name__}: {e}")
            return ""
        add_response(resp)
        return (resp.choices[0].message.content or "") if resp.choices else ""

    # ---- chains -----------------------------------------------------------------------

    def _retired(self, c: int) -> bool:
        return self.role.get(c) == RETIRED

    def _chain_best(self, ctx: EvolveContext, c: int) -> Optional[Individual]:
        nodes = self._chain_nodes(ctx, c)
        return nodes[0] if nodes and score_of(nodes[0], self.score_key) is not None else None

    def _new_chain(self) -> int:
        c = len(self.chains)
        self.chains.append([])
        self.num_chains = len(self.chains)
        self.failures.append({})
        self.prompt_count[c] = 0
        self.turns[c] = 0
        self.role[c] = SPROUT
        return c

    def _turns(self, c: int) -> int:
        """A sprout's first program counts as its first turn; a later turn is a batch that
        produced a program, not a batch asked for."""
        return 1 + self.turns.get(c, 0)

    def _count_turn(self, batch_id: str) -> None:
        b = self.open.get(batch_id)
        if b is not None and batch_id not in self._turned:
            self._turned.add(batch_id)
            self.turns[b.chain_idx] = self.turns.get(b.chain_idx, 0) + 1

    def _forget_closed(self, batch_id: str) -> None:
        if batch_id not in self.open:
            self._turned.discard(batch_id)

    def _active(self, roles=(BASE, ESTABLISHED)) -> List[int]:
        return [c for c in range(len(self.chains)) if self.role.get(c) in roles]

    def _review_sprouts(self, ctx: EvolveContext) -> None:
        busy = {b.chain_idx for b in self.open.values()}
        for c in self._active((SPROUT,)):
            if c in busy or self._turns(c) < self.probation_turns:
                continue
            sp = self.sprouts[c]
            best = self._chain_best(ctx, c)
            bs = score_of(best, self.score_key) if best is not None else None
            old = sp.get("old")
            ok = bs is not None and (old is None or bs >= old - self.probation_margin * abs(old))
            echo = sp.get("echo")
            stuck = ok and echo is not None and bs is not None and not _differs(bs, echo)
            sp["best"], sp["decided_at"] = bs, self.attempts
            if ok and not stuck:
                self.role[c] = ESTABLISHED
                sp["fate"] = "graduated"
                self._shed(ctx)
            else:
                self.role[c] = RETIRED
                sp["fate"] = "retired (never beat its fallback)" if stuck else "retired"
            self._record_tried(c)
            logger.info(f"molt: sprout chain {c} {sp['fate']} (best {bs}, planned against {old})")

    def _shed(self, ctx: EvolveContext) -> None:
        """Keep at most `max_established` active chains: retire the weakest, never the champion's."""
        active = self._active()
        if not self.use_probation or len(active) <= self.max_established:
            return
        bests = {}
        for c in active:
            b = self._chain_best(ctx, c)
            bests[c] = score_of(b, self.score_key) if b is not None else float("-inf")
        g = max(bests.values())
        champ = next(c for c in active if bests[c] == g)
        for c in sorted(active, key=lambda c: bests[c]):
            if len(self._active()) <= self.max_established:
                break
            if c == champ:
                continue
            self.role[c] = RETIRED
            self.sheds.append({"chain": c, "best": bests[c], "at": self.attempts})
            if c in self.sprouts:
                self.sprouts[c]["shed_at"] = self.attempts
            logger.info(f"molt: shed chain {c} (best {bests[c]})")

    def _echo_of(self, ctx: EvolveContext, s: float, target_id: str,
                 reference_id: Optional[str] = None) -> Optional[str]:
        """What a redesign's score is an exact copy of: the program it replaces, the I/O
        reference it was shown, or a seed."""
        for pid, what in ((target_id, "the program it replaces"),
                          (reference_id, "its input/output reference")):
            ind = ctx.store.get(pid) if pid else None
            v = score_of(ind, self.score_key) if ind is not None else None
            if v is not None and not _differs(s, v):
                return f"{what} {pid}"
        for i in ctx.store.of_kind(self.kind):
            if not i.parent_ids:
                ss = score_of(i, self.score_key)
                if ss is not None and not _differs(s, ss):
                    return "the seed program"
        return None

    def lineage(self, store) -> Dict[str, Any]:
        """Where the run's best came from: its chains, each chain's origin, and per intervention
        what it opened and how that ended. Plain data, for summary.json."""
        member = {i for ch in self.chains for i in ch}
        best, bs = None, None
        for i in store.of_kind(self.kind):
            v = score_of(i, self.score_key)
            # on a tie prefer a chain member: best-of-K leaves an equal sibling unattached
            if v is not None and (bs is None or v > bs or (v == bs and i.id in member)):
                best, bs = i, v
        # a best that joined no chain (an unattached sibling) belongs where its parents grew
        node, seen = best, set()
        while node is not None and node.id not in member and node.id not in seen:
            seen.add(node.id)
            node = store.get(node.parent_ids[0]) if node.parent_ids else None
        holding = [c for c, ch in enumerate(self.chains) if node is not None and node.id in ch]

        def origin(c: int) -> Dict[str, Any]:
            sp = self.sprouts.get(c)
            if sp is None:
                return {"chain": c, "role": self.role.get(c), "origin": "initial chain"}
            return {"chain": c, "role": self.role.get(c), "origin": "redesign",
                    "intervention": sp.get("intervention"), "direction": sp.get("direction"),
                    "task": str(sp.get("task") or "")[:300], "opened_at": sp.get("opened_at"),
                    "decided_at": sp.get("decided_at"), "fate": sp.get("fate"),
                    "echo": sp.get("echo")}

        return {"best_id": best.id if best is not None else None, "best_score": bs,
                "best_chains": [origin(c) for c in holding],
                "attempts": self.attempts,
                "interventions": [
                    {"n": iv["n"], "at": iv.get("at"), "frontier": iv.get("frontier"),
                     "kept": iv.get("kept"),
                     "sprouts": [{"chain": c["chain"], "first_score": c.get("score"),
                                  "echo": c.get("echo"),
                                  "fate": self.sprouts.get(c["chain"], {}).get("fate"),
                                  "decided_at": self.sprouts.get(c["chain"], {}).get("decided_at")}
                                 for c in iv.get("children", []) if "chain" in c]}
                    for iv in self.interventions],
                "sheds": self.sheds}

    def _record_tried(self, c: int) -> None:
        sp = self.sprouts[c]
        self.tried.append({"task": str(sp.get("task") or "")[:400], "best": sp.get("best"),
                           "planned_against": sp.get("old"), "fate": sp.get("fate"),
                           "chain": c, "intervention": sp.get("intervention"),
                           "opened_at": sp.get("opened_at"), "decided_at": sp.get("decided_at"),
                           "echo": sp.get("echo")})

    # ---- stall ------------------------------------------------------------------------

    def _measured(self, ctx: EvolveContext) -> List[Individual]:
        inds = [i for i in ctx.store.of_kind(self.kind) if i.measured() is not None]
        return sorted(inds, key=lambda i: i.created_at)

    def _sync_stall(self, ctx: EvolveContext) -> None:
        """First call (fresh or resumed): set the anchor and the attempts since it from the store."""
        if self.attempts is None:
            self.attempts = len([i for i in self._measured(ctx) if i.parent_ids])
        if self.since is not None:
            return
        meas = self._measured(ctx)
        best, idx = None, 0
        for k, ind in enumerate(meas):
            s = score_of(ind, self.score_key)
            if s is not None and (best is None or s > best + self.progress_rel * abs(best)):
                best, idx = s, k
        self.anchor = best
        self.since = max(len(meas) - 1 - idx, 0)

    def _note(self, ind: Optional[Individual]) -> None:
        s = score_of(ind, self.score_key) if ind is not None else None
        if s is not None and (self.anchor is None
                              or s > self.anchor + self.progress_rel * abs(self.anchor)):
            self.anchor, self.since = s, 0
        else:
            self.since = (self.since or 0) + 1

    def _global_best(self, ctx: EvolveContext) -> Optional[float]:
        s = [score_of(i, self.score_key) for i in ctx.store.of_kind(self.kind)]
        s = [x for x in s if x is not None]
        return max(s) if s else None

    # ---- what a planner reads ---------------------------------------------------------

    def _code(self, ind: Individual) -> str:
        g = ind.genome
        if isinstance(g, CodeGenome):
            if self.evolve_file and self.evolve_file in g.files:
                return g.files[self.evolve_file]
            return next(iter(g.files.values()), "")
        return g.render()

    def _live(self, ctx: EvolveContext) -> Dict[str, Dict[str, Any]]:
        live: Dict[str, Dict[str, Any]] = {}
        for c, chain in enumerate(self.chains):
            if self._retired(c):
                continue
            for i in chain:
                ind = ctx.store.get(i)
                if ind is None:
                    continue
                e = live.setdefault(i, {"id": i, "chains": [], "parents": list(ind.parent_ids),
                                        "score": score_of(ind, self.score_key),
                                        "created": ind.created_at})
                e["chains"].append(c)
        return live

    def _attempt_line(self, ctx: EvolveContext, ind: Individual, chars: int = 200) -> str:
        """One attempt as planners and workers read it. A rejected program scores 0 with the
        reason in `invalid_reason` (e.g. "7/150 cases rejected -- case 4: TLE"); reading only
        `error` showed every rejection as a bare "invalid"."""
        s = score_of(ind, self.score_key)
        ps = [score_of(ctx.store.get(p), self.score_key) if ctx.store.get(p) else None
              for p in ind.parent_ids]
        ps = [p for p in ps if p is not None]
        gain = (s - max(ps)) if (s is not None and ps) else None
        m = ind.metrics() or {}
        why = m.get("invalid_reason") or m.get("error") or ("invalid" if s is None else "")
        err = f" rejected: {str(why)[:chars]}".replace("\n", " ") if why else ""
        return (f"- attempt {ind.id} <- {list(ind.parent_ids)[:3]}: score="
                f"{'fail' if s is None else round(s, 6)}"
                f"{'' if gain is None else f' gain={gain:+.6f}'}{err}")

    def _io_reference(self, ctx: EvolveContext) -> Optional[Individual]:
        """The shortest program that scored -- usually the seed: the input/output format and
        helpers without a polished method to copy or fall back to."""
        best = None
        for i in ctx.store.of_kind(self.kind):
            s = score_of(i, self.score_key)
            if s is None or s <= 0:
                continue
            n = len(self._code(i))
            if best is None or n < best[0] or (n == best[0] and i.created_at < best[1].created_at):
                best = (n, i)
        return best[1] if best else None

    def _views(self, ctx: EvolveContext):
        live = self._live(ctx)
        ranked = sorted((e for e in live.values() if e["score"] is not None),
                        key=lambda e: -e["score"])
        recent = sorted(live.values(), key=lambda e: -e["created"])[:8]
        shown = {e["id"]: e for e in ranked[:self.catalog_size]}
        for e in recent:
            shown.setdefault(e["id"], e)
        catalog = "\n".join(
            f"- {e['id']}: score={e['score'] if e['score'] is None else round(e['score'], 6)} "
            f"chains={e['chains']} parents={e['parents'][:3]}"
            + (" [GLOBAL BEST]" if ranked and e["id"] == ranked[0]["id"] else "")
            for e in sorted(shown.values(), key=lambda e: -(e["score"] or -1e18)))
        attempts = [self._attempt_line(ctx, ind)
                    for ind in self._measured(ctx)[-3 * self.attempts_shown:]]
        codes, seen = [], set()
        for e in ranked:
            ind = ctx.store.get(e["id"])
            if ind is None or len(codes) >= self.code_shown:
                continue
            src = self._code(ind)
            if src in seen:
                continue
            seen.add(src)
            codes.append(f"### branch {ind.id} (score {score_of(ind, self.score_key)})\n```\n{src}\n```")
        tried = "\n".join(
            f"- {t['fate']}: best {t['best']} vs champion {t['planned_against']} -- {t['task']}"
            for t in self.tried[-self.tried_shown:]) or "(none yet)"
        return live, ranked, catalog, attempts, "\n\n".join(codes), tried

    # ---- planning ---------------------------------------------------------------------

    def _types(self) -> tuple:
        return (("integration", "exploration", "redesign") if self.use_redesign
                else ("integration", "exploration"))

    def _type_slots(self) -> Dict[str, str]:
        t = self._types()
        return {"type_names": ", ".join(t[:-1]) + " and " + t[-1],
                "type_enum": " | ".join(f'"{x}"' for x in t),
                "redesign_type": REDESIGN_TYPE if self.use_redesign else "",
                "redesign_rule": (f" Include at least {self.min_redesign} `redesign` "
                                  f"direction(s);\n  the other types are labels, not quotas."
                                  if self.use_redesign else
                                  " The types are labels, not quotas.")}

    async def _plan(self, ctx: EvolveContext) -> None:
        live, ranked, catalog, attempts, code, tried = self._views(ctx)
        frontier = ranked[0]["score"] if ranked else None

        async def one(p: int):
            view = attempts if len(attempts) <= self.attempts_shown else sorted(
                self.rng.sample(attempts, self.attempts_shown), key=attempts.index)
            reply = await self._llm(PLANNER.format(
                objective=ctx.objective, catalog=catalog, attempts="\n".join(view) or "(none)",
                code=code, tried=tried, num_directions=self.directions_per_planner,
                **self._type_slots()))
            out = _json(reply) or {}
            ds = [d for d in (out.get("directions") or []) if isinstance(d, dict)]
            return p, out, ds, bool(reply.strip())

        got = await asyncio.gather(*(one(p) for p in range(self.planners)))
        answered = sum(a for *_, a in got)
        res = [(p, out, ds) for p, out, ds, _ in got]
        n = len(self.interventions)
        pool: Dict[str, Dict[str, Any]] = {}
        diagnoses = []
        for p, out, ds in res:
            diagnoses.append(str(out.get("diagnosis") or "")[:600])
            for j, d in enumerate(ds):
                if d.get("target_branch_id") not in live:
                    continue                      # a target must be live
                if d.get("type") not in self._types():
                    d["type"] = "exploration"
                d["donor_branch_ids"] = [x for x in (d.get("donor_branch_ids") or [])
                                         if x in live][:2]
                pool[f"i{n}/planner_{p}/direction_{j:02d}"] = d
        kept = list(pool)
        if len(pool) > 1:
            listing = json.dumps({k: {x: v.get(x) for x in ("type", "target_branch_id",
                                                            "mechanism_hypothesis", "task",
                                                            "donor_branch_ids", "novel_axis")}
                                  for k, v in pool.items()}, indent=1)
            dd = _json(await self._llm(DEDUP.format(candidates=listing))) or {}
            k2 = [k for k in (dd.get("kept_direction_ids") or []) if k in pool]
            if k2:
                kept = k2
        rec = {"n": n, "at": self.attempts, "at_since": self.since, "frontier": frontier,
               "proposed": sum(len(ds) for _, _, ds in res), "valid": len(pool), "kept": len(kept),
               "planners_answered": answered,
               "diagnoses": diagnoses, "directions": [], "children": []}
        self.interventions.append(rec)
        for k in kept:
            d = pool[k]
            d["id"], d["intervention"], d["frontier"] = k, n, frontier
            self.pending.append(d)
            rec["directions"].append({"id": k, "type": d.get("type"),
                                      "target": d.get("target_branch_id"),
                                      "donors": d.get("donor_branch_ids"),
                                      "task": str(d.get("task"))[:300],
                                      "verbalized_probability": d.get("verbalized_probability")})
        logger.info(f"molt intervention {n}: {rec['proposed']} proposed, {rec['valid']} with live "
                    f"targets, {rec['kept']} kept "
                    f"({sum(1 for k in kept if pool[k].get('type') == 'redesign')} redesign)")
        if not kept:
            self.since = 0     # nothing to run: wait for another stall window

    def _direction_item(self, ctx: EvolveContext, d: Dict[str, Any]) -> Optional[Create]:
        tgt = ctx.store.get(d["target_branch_id"])
        if tgt is None:
            return None
        ts = score_of(tgt, self.score_key)
        if d.get("type") == "redesign":
            ref = self._io_reference(ctx) or tgt
            d["reference"] = ref.id
            lineage, shown = [tgt.id], [ref]
            roles = {ref.id: "I/O REFERENCE (format and helpers only)"}
        else:
            donors = [x for x in (ctx.store.get(i) for i in d.get("donor_branch_ids") or [])
                      if x is not None and x.id != tgt.id]
            shown = [tgt] + donors
            lineage = [x.id for x in shown]
            roles = {tgt.id: "TARGET (edit this one)", **{x.id: "DONOR" for x in donors}}
        evidence = []
        for a in d.get("evidence_attempt_ids") or []:
            ind = ctx.store.get(str(a))
            if ind is not None:
                evidence.append(self._attempt_line(ctx, ind, chars=400))
        fc = d.get("frontier_case") if isinstance(d.get("frontier_case"), dict) else {}
        prompt = {"type": d.get("type"), "task": d.get("task"),
                  "hypothesis": d.get("mechanism_hypothesis"),
                  "constraints": "; ".join(map(str, d.get("constraints") or [])) or "none",
                  "target": tgt.id, "target_score": ts, "roles": roles, "evidence": evidence,
                  "required_gain": fc.get("required_gain"), "why": fc.get("why_plausible"),
                  "falsifier": d.get("falsifier")}
        # parent_ids keep the lineage (a redesign descends from the program it replaces); the
        # prompt's parents are the programs actually shown
        return Create(kind=self.kind, parent_ids=lineage, k=1,
                      context=PromptContext(instruction=ctx.objective, parents=shown, history="",
                                            failures={}),
                      meta={"molt_intervention": d["intervention"], "molt_direction": d["id"],
                            "molt_type": d.get("type"), "molt_prompt": prompt})

    def _close_direction(self, ctx: EvolveContext, batch_id: str,
                         child: Optional[Individual], produced: bool = True) -> None:
        """`produced=False`: the model never wrote the program (a failed call), so the
        direction says nothing about its design."""
        d = self.open_dirs.pop(batch_id, None)
        if d is None:
            return
        s = score_of(child, self.score_key) if child is not None else None
        rec = self.interventions[d["intervention"]]
        entry: Dict[str, Any] = {"direction": d["id"], "type": d.get("type"),
                                 "child": child.id if child else None, "score": s}
        if not produced:
            entry["not_produced"] = True
        if d.get("type") == "redesign" and self.protect:
            if not produced:
                pass
            elif child is not None and s is not None:
                c = self._new_chain()
                self.chains[c].append(child.id)
                self.sprouts[c] = {"intervention": d["intervention"], "direction": d["id"],
                                   "task": d.get("task"), "old": d.get("frontier"),
                                   "hypothesis": d.get("mechanism_hypothesis"),
                                   "fate": "probation", "opened_at": self.attempts}
                entry["chain"] = c
                if not self.use_probation:          # ablation: kept at once, never judged
                    self.role[c] = ESTABLISHED
                    self.sprouts[c].update(fate="kept (no probation)", decided_at=self.attempts)
                echo = self._echo_of(ctx, s, d["target_branch_id"], d.get("reference"))
                if echo is not None:
                    self.sprouts[c]["echo"] = s
                    entry["echo"] = echo
                    self.failures[c][ECHO_NOTE.format(what=echo)] = 99.0
            else:
                self.tried.append({"task": str(d.get("task") or "")[:400], "best": None,
                                   "planned_against": d.get("frontier"),
                                   "fate": "failed to produce a program",
                                   "intervention": d["intervention"], "decided_at": self.attempts})
        elif child is not None and s is not None:
            # archive insertion: the child joins the active chains that hold its target
            placed = False
            for c, chain in enumerate(self.chains):
                if not self._retired(c) and d["target_branch_id"] in chain:
                    chain.append(child.id)
                    placed = True
            if not placed:
                active = self._active((BASE, ESTABLISHED, SPROUT))
                if active:
                    self.chains[active[0]].append(child.id)
        rec["children"].append(entry)
        if not self.open_dirs and not self.pending:
            self.since = 0     # the cohort has drained; the next stall window starts now

    # ---- the loop ---------------------------------------------------------------------

    async def ask(self, ctx: EvolveContext, n: int) -> List[Create]:
        self._sync_stall(ctx)
        self._review_sprouts(ctx)
        on_probation = len(self._active((SPROUT,)))
        in_probation = (on_probation >= self.max_sprouts if self.plan_during_probation
                        else on_probation > 0)
        if self.trigger == "stall":
            due = (self.since or 0) >= self.stall_evals
        else:
            last = (self.interventions[-1].get("at") if self.interventions
                    else self.period_first - self.period)
            due = (self.attempts or 0) - (last or 0) >= self.period
        if (not self._planning and not self.pending and not self.open_dirs and not in_probation
                and due):
            self._planning = True
            try:
                await self._plan(ctx)
            finally:
                self._planning = False
        items: List[Create] = []
        while self.pending and len(items) < n:
            d = self.pending.pop(0)
            it = self._direction_item(ctx, d)
            if it is not None:
                self.open_dirs[it.batch_id] = d
                items.append(it)
            else:
                self.interventions[d["intervention"]]["children"].append(
                    {"direction": d["id"], "type": d.get("type"), "child": None,
                     "score": None, "skipped": "target gone"})
        if len(items) < n:
            for it in await SimpleTES.ask(self, ctx, n - len(items)):
                c = (it.meta or {}).get("chain")
                if c in self.sprouts:
                    it.meta["molt_design"] = self._design_brief(ctx, c)
                items.append(it)
        return items

    def _design_brief(self, ctx: EvolveContext, c: int) -> Dict[str, Any]:
        sp = self.sprouts[c]
        recent = sorted((i for i in (ctx.store.get(x) for x in self.chains[c]) if i is not None),
                        key=lambda i: i.created_at)[-4:]
        if self.role.get(c) == SPROUT:
            status = (f"Status: on probation, turn {self._turns(c)} of {self.probation_turns}; it "
                      f"must reach within {self.probation_margin:.0%} of {sp.get('old')} to stay.")
        else:
            status = "Status: graduated -- an established design of this search."
        if sp.get("echo") is not None:
            status += (f" Its first program scored exactly {sp['echo']} -- the new design never "
                       f"ran and a fallback answered; make the design itself work.")
        return {"task": sp.get("task"), "hypothesis": sp.get("hypothesis"), "status": status,
                "recent": [self._attempt_line(ctx, i, chars=400) for i in recent]}

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement) -> None:
        self._sync_stall(ctx)
        self.attempts += 1
        self._note(ind)
        if m.batch_id in self.open_dirs:
            self._close_direction(ctx, m.batch_id, ind)
            return
        self._count_turn(m.batch_id)
        await super().on_measured(ctx, ind, m)
        self._forget_closed(m.batch_id)

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        self._sync_stall(ctx)
        produced = f.stage != "generate"
        if produced:                 # a program that crashed is an attempt; a silent model is not
            self.attempts += 1
            self._note(None)
        if f.batch_id in self.open_dirs:
            self._close_direction(ctx, f.batch_id, None, produced=produced)
            return
        if produced:
            self._count_turn(f.batch_id)
        await super().on_failed(ctx, f)
        self._forget_closed(f.batch_id)

    # ---- persistence ------------------------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        s = super().state_dict()
        s["molt"] = {"role": {str(k): v for k, v in self.role.items()},
                     "sprouts": {str(k): v for k, v in self.sprouts.items()},
                     "anchor": self.anchor, "since": self.since, "pending": self.pending,
                     "interventions": self.interventions, "tried": self.tried,
                     "turns": {str(k): v for k, v in self.turns.items()},
                     "attempts": self.attempts, "sheds": self.sheds}
        return s

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        super().load_state_dict(state)
        self.num_chains = len(self.chains)
        while len(self.failures) < self.num_chains:
            self.failures.append({})
        r = state.get("molt") or {}
        if r:
            self.role = {int(k): v for k, v in (r.get("role") or {}).items()}
            self.sprouts = {int(k): v for k, v in (r.get("sprouts") or {}).items()}
            self.anchor, self.since = r.get("anchor"), r.get("since")
            self.pending = list(r.get("pending") or [])
            self.interventions = list(r.get("interventions") or [])
            self.tried = list(r.get("tried") or [])
            self.attempts = r.get("attempts")
            self.sheds = list(r.get("sheds") or [])
            # checkpoints written before turns were counted: batches asked stood in for turns
            turns = r.get("turns")
            self.turns = ({int(k): v for k, v in turns.items()} if turns is not None
                          else {int(k): v for k, v in self.prompt_count.items()})
        for c in range(self.num_chains):
            self.role.setdefault(c, BASE)
        # directions in flight at the checkpoint never report back; their children, if any, are
        # in the store already
        self.open_dirs = {}
        self._turned = set()


class MoltVariator(UpstreamCompletionVariator):
    """SimpleTES's operator for ordinary chain steps; Molt's own prompt for a planned direction
    (DIRECTION) and a design brief on top of SimpleTES's prompt for a chain a redesign opened."""

    def build_block_prompt(self, ctx: EvolveContext, item: Create, path: str, eb) -> str:
        meta = item.meta or {}
        if "molt_prompt" in meta:
            return self._direction_prompt(ctx, item, path, eb, meta["molt_prompt"])
        base = super().build_block_prompt(ctx, item, path, eb)
        brief = meta.get("molt_design")
        if not brief:
            return base
        text = DESIGN_BRIEF.format(task=brief.get("task"), hypothesis=brief.get("hypothesis"),
                                   status=brief.get("status"),
                                   recent="\n".join(brief.get("recent") or []) or "(none yet)")
        head, sep, rest = base.partition("\n\nGeneration instruction")
        out = head + "\n\n" + text + sep + rest if sep else text + "\n" + base
        strategy = out.rfind("\n=== GENERATION STRATEGY ===")
        return (out[:strategy] + DESIGN_STRATEGY) if strategy >= 0 else out

    def _direction_prompt(self, ctx, item, path, eb, p) -> str:
        lang_name, tag = self._lang(path)
        blocks = []
        for ind in item.context.parents:
            src = ind.genome.files.get(path) if isinstance(ind.genome, CodeGenome) else None
            m = ind.metrics() or {}
            why = m.get("invalid_reason")
            blocks.append(f"--- [{p['roles'].get(ind.id, 'REFERENCE')}] branch {ind.id}, score "
                          f"{m.get(self.score_key)}{f' (rejected: {why})' if why else ''} ---\n"
                          f"```{tag}\n{src or ind.genome.render()}\n```\n")
        redesign = p.get("type") == "redesign"
        extra = ""
        if p.get("required_gain") is not None or p.get("why"):
            extra += (f"\nGain needed over the program it starts from to beat the current "
                      f"best: {p.get('required_gain')}. The planner's case: {p.get('why')}")
        if p.get("falsifier"):
            extra += f"\nWhat would falsify it: {p['falsifier']}"
        evidence = ""
        if p.get("evidence"):
            evidence = ("\n=== ATTEMPTS THE PLANNER CITES ===\n" + "\n".join(p["evidence"]) + "\n")
        assignment = (REDESIGN_ASSIGNMENT.format(target=p.get("target"),
                                                 target_score=p.get("target_score"))
                      if redesign else EDIT_ASSIGNMENT)
        return DIRECTION.format(
            objective=item.context.instruction or ctx.objective, type=p.get("type"),
            assignment=assignment, task=p.get("task"), hypothesis=p.get("hypothesis"),
            extra=extra, constraints=p.get("constraints"), programs="\n".join(blocks),
            evidence=evidence, start=eb.start_line, end=eb.end_line,
            block_word=f"{lang_name} code block" if lang_name else "code block", tag=tag,
            prefix=eb.prefix.rstrip("\n"), suffix=eb.suffix.rstrip("\n"),
            closing=REDESIGN_CLOSING if redesign else EDIT_CLOSING)


Molt.variator_cls = MoltVariator
