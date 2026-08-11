"""The LLM mutation operator, lifted out of `EvolutionTeam`.

A coding agent is given the parent's files in a workspace, a prompt built from whatever context
the method chose, and three tools: run the evaluator, look up an earlier program, submit. It edits
and commits; what it commits becomes the child.

**Per-call isolation is the point of this file.** In `EvolutionTeam` the same eight attributes --
the workspace path, the submitted result, the best-verified version, the evaluation counter --
live on the long-lived object and are shared by every worker, while each iteration begins by
`rmtree`-ing and rewriting that one shared workspace. With `num_workers > 1` two mutations edit
the same directory and write to the same submission slot, so one agent's files can be replaced
underneath it by another's parent. Here every call gets its own `_Session` and its own directory,
and nothing mutation-specific is stored on the variator.

Salvage is still load-bearing -- agents routinely edit the workspace and then stop without calling
submit, and throwing that work away wastes the whole iteration -- but it now insists on the same
thing `submit` does. Two fallbacks, in order: evaluate whatever is on disk, then commit the best
verified version seen during the mutation, and take neither unless it is FEASIBLE.

That last word was missing everywhere, and it was expensive. `success` from the evaluator means it
ran without crashing, which an invalid solution also manages -- it returns `success=True`,
`validity=0`, score 0. Three places here read `success` and got a broken program: the best-so-far
tracker, the on-disk salvage, and `submit`, which checked nothing at all and simply snapshotted the
directory. Measured across eight runs: 38 of 213 committed programs violated the constraints, and
more than half of those came from agents that HAD run the evaluator -- they verified one version,
edited again, and submitted the edit unchecked. Nothing was there to stop them.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pantheon.utils.log import logger

from ..core.genome import CodeGenome
from ..core.method import EvolveContext
from ..core.work import Create, Produced
from ..prompt_builder import MUTATION_SYSTEM_PROMPT_CODEBASE


MUTATION_AGENT_SYSTEM_PROMPT = """You are an expert algorithm designer, working in a real workspace as one step of an evolutionary search. Your job is to DISCOVER AND IMPLEMENT A BETTER ALGORITHM for the objective — not to hand the problem to an off-the-shelf solver.

Each assignment gives you a codebase in your working directory and an objective. You:
1. Read the current code and understand WHY it scores what it does — what is actually limiting it.
2. Invent a genuinely better approach and IMPLEMENT IT YOURSELF. Reason about the structure of the problem and try a different algorithmic idea, not just a small tweak.
3. VERIFY with the run_evaluator tool that your change actually improves the metrics; keep only what helps. Higher fitness is better. Experiment freely — edit files, run code, measure.
4. When your best verified version is on disk, call submit(summary=...) to COMMIT it — like a git commit. Nothing is recorded until you submit. The summary is 1-3 sentences: the algorithmic idea you used, whether it worked, and the measured metric change.

Rules:
- Write the CORE problem-solving logic YOURSELF. Do NOT call a general-purpose solver or optimizer to do the work for you — e.g. scipy.optimize / minimize / linprog, cvxpy, OR-tools, sklearn optimizers, or networkx graph algorithms that solve the objective for you. Basic array math (numpy) is fine as a building block, but the search / optimization / decision logic that drives the score must be your own code and your own idea.
- Validity is ENFORCED by the evaluator: an invalid result (constraint violations, wrong output shape, etc.) scores 0 no matter how good its raw objective looks. run_evaluator reports validity and, when invalid, a 'feedback' message explaining WHY — read it, fix the cause, and re-check. NEVER submit an invalid solution.
- Robustness first: ALWAYS leave on disk a valid solution at least as good as the one you started from. If an ambitious idea fails or throws, fall back to the best working version you have — never submit something worse or broken.
- Make concrete, correct edits. Call submit exactly once, at the end."""


def agent_kwargs(kw: Dict[str, Any]) -> Dict[str, Any]:
    """Every `AgentVariator` knob present in `kw`, taken from its signature.

    A method's `default_variator` used to name the knobs it forwarded, one by one. That is a list
    someone has to remember to extend, and twice nobody did: `max_tool_calls` went missing and one
    arm of a comparison ran on an unlimited action budget while the others ran on 14, and later
    `inner_fidelity` and `trace_path` were dropped the same way -- a cheap-measurement setting that
    silently never applied, and a trace file that was never written. Neither failure was visible in
    any number the run printed.

    Reading the signature means a new operator parameter reaches every method without anyone
    editing anything.
    """
    import inspect

    accepted = set(inspect.signature(AgentVariator.__init__).parameters) - {"self", "evaluator"}
    return {k: v for k, v in kw.items() if k in accepted and v is not None}


def extract_cost(response: Any) -> float:
    """Cost in USD for one agent run.

    It is carried on the last assistant message's `_metadata.current_cost`, not as an attribute of
    the response -- the response object has no cost field at all, so looking for one silently
    returns zero and every cost report reads $0.0000.
    """
    try:
        if response and response.details and response.details.messages:
            for msg in reversed(response.details.messages):
                if msg.get("role") == "assistant" and "_metadata" in msg:
                    return float(msg.get("_metadata", {}).get("current_cost", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


@dataclass
class _Session:
    """Everything one mutation needs to remember, for exactly as long as it runs."""

    workdir: Path
    parent_files: Dict[str, str]
    submitted: Optional[Dict[str, Any]] = None
    best: Optional[Dict[str, Any]] = None
    """The best VERIFIED-FEASIBLE version this session produced: the file snapshot from whichever
    `run_evaluator` call scored highest while satisfying the constraints. `None` means the agent
    never ran the evaluator, or never got a valid result out of it -- and either way there is
    nothing to fall back on."""
    evals: int = 0
    tool_calls: int = 0
    turns: int = 0
    rejects: int = 0
    cost: float = 0.0

    def current_files(self) -> Dict[str, str]:
        """Read back the stable file set. Scratch files the agent created are ignored, so a
        mutation cannot smuggle new modules into the genome by accident."""
        out = {}
        for path, original in self.parent_files.items():
            fp = self.workdir / path
            out[path] = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else original
        return out


class AgentVariator:
    """One full-capability coding agent per mutation, editing an isolated workspace."""

    def __init__(
        self,
        evaluator: Any,
        *,
        model: str = "high",
        system_prompt: Optional[str] = None,
        workspace_root: Optional[str] = None,
        max_evaluations: Optional[int] = None,
        max_tool_calls: Optional[int] = None,
        max_turns: Optional[int] = None,
        timeout: float = 1800,
        web_search: bool = False,
        warm_start_file: Optional[str] = None,
        score_key: str = "combined_score",
        valid_key: str = "validity",
        inner_fidelity: str = "full",
        max_submit_retries: int = 2,
        instruction_suffix: str = "",
        trace_path: Optional[str] = None,
    ):
        self.evaluator = evaluator
        self.model = model
        self.system_prompt = system_prompt or MUTATION_AGENT_SYSTEM_PROMPT
        self.workspace_root = workspace_root
        self.max_evaluations = max_evaluations
        self.max_tool_calls = max_tool_calls
        self.max_turns = max_turns
        self.timeout = timeout
        self.web_search = web_search
        self.warm_start_file = warm_start_file
        self.score_key = score_key
        self.valid_key = valid_key
        self.inner_fidelity = inner_fidelity
        """Fidelity for the agent's own `run_evaluator` calls, as distinct from the one that
        decides what is recorded.

        A cheap reading is worth having when the authoritative one is slow: AHC039 scores 150 cases
        three times over, 124s, and an agent calling it four times spends eight minutes per
        mutation on measurement alone. A 30-case reading answers "did that help?" in ten seconds
        and lands within 0.4% of the full score. `submit` and the salvage path always measure at
        full fidelity, so a cheap number can never become the recorded one.
        """
        """The metric that says a solution satisfies the problem's constraints.

        Kept separate from `score_key` because the two answer different questions and the
        evaluator reports a violated constraint as `success=True, validity=0, score=0`. Reading
        that zero as a score -- rather than as "there is no score" -- is a single confusion that
        had crept into four separate places: selection, the judge's training set, the history
        shown to the agent, and this operator's own commit path.
        """
        self.max_submit_retries = max_submit_retries
        self.instruction_suffix = instruction_suffix
        self.trace_path = trace_path
        """Where to append a record of every tool call and what it did to the workspace.

        Off by default -- it costs a hash of the workspace per call. Turned on to answer a
        question the stored genomes could not: 6 of 35 mutations on a 924-line C++ file committed
        something that would not compile (a header block gone, control characters at the top,
        `#include <set> d_se ||`), and the genome shows the wreckage without showing which call
        produced it.
        """
        """Text appended to whatever instruction the method produced.

        A harness-level knob, so a prompt can be varied without editing an algorithm -- which is
        the only way to attribute a change to the prompt rather than to the search.
        """

    async def _evaluate(self, files, fidelity: str):
        """Measure, asking for a fidelity where the evaluator understands them."""
        try:
            return await self.evaluator.evaluate_files(files, fidelity=fidelity)
        except TypeError:
            # an evaluator from before fidelities existed
            return await self.evaluator.evaluate_files(files)

    # ---- feasibility ------------------------------------------------------

    def _feasible(self, res: Dict[str, Any]) -> Tuple[bool, str]:
        """Did the evaluator return a solution that actually satisfies the constraints?

        Returns `(ok, why_not)`. `success` alone is not enough -- it only says the evaluator ran.
        """
        if not res.get("success"):
            return False, str(res.get("error") or "the evaluator could not run it")[:300]
        v = (res.get("metrics") or {}).get(self.valid_key)
        if v is not None and v <= 0:
            art = res.get("artifacts") or {}
            why = art.get("feedback") or art.get("error") or art.get("reason")
            return False, str(why or f"{self.valid_key}=0, constraints violated")[:300]
        return True, ""

    def _remember_best(self, sess: _Session, files: Dict[str, str],
                       res: Dict[str, Any]) -> None:
        """Keep this version if it is feasible and the best so far."""
        ok, _ = self._feasible(res)
        if not ok:
            return
        score = float((res.get("metrics") or {}).get(self.score_key, 0.0) or 0.0)
        if sess.best is None or score > sess.best["score"]:
            sess.best = {"files": dict(files), "score": score,
                         "summary": f"(auto-committed best of {sess.evals} verified attempts; "
                                    f"{self.score_key} {score:.4f})"}

    # ---- prompt ----------------------------------------------------------

    def build_prompt(self, ctx: EvolveContext, item: Create) -> str:
        c = item.context
        parts = [c.instruction or ctx.objective or "Improve the program."]
        if self.instruction_suffix:
            # Appended to the method's instruction rather than folded into the system prompt,
            # because the two are not interchangeable: the system prompt already forbids invalid
            # submissions and three arms that all read it produced infeasible programs at 5.6%,
            # 12% and 35%. Whatever this buys, it buys by sitting next to the task.
            parts.append(self.instruction_suffix)
        parent = c.parents[0] if c.parents else None
        if parent is not None:
            m = {k: v for k, v in parent.metrics().items() if k != "fitness_weights"}
            parts.append(
                f"\n## Current program (id {parent.id}, generation {parent.generation})\n"
                f"metrics: {json.dumps(m, default=str)[:600]}\n"
                "The files are already in your working directory. Read them there."
            )
        if c.history:
            parts.append(f"\n## What has been tried\n{c.history}")
        if c.inspirations:
            lines = []
            for i in c.inspirations[:4]:
                s = i.metrics().get(self.score_key)
                summary = i.meta.get("summary", "")
                lines.append(f"- #{i.order} {i.id}: {self.score_key}="
                             f"{s if s is not None else '?'} {summary}".rstrip())
            parts.append(
                "\n## Other programs in the population\n" + "\n".join(lines) +
                "\n\nUse `inspect_program(ref)` with a #N or id to read one in full."
            )
        if c.failures:
            worst = sorted(c.failures.items(), key=lambda kv: -kv[1])[:5]
            parts.append("\n## Recurring failures\n" +
                         "\n".join(f"- {k} (x{int(v)})" for k, v in worst))
        parts.append(
            "\n## How to work\n"
            "Edit the files in your working directory. Call `run_evaluator()` to measure what you "
            "have; it returns the metrics and, when the result is invalid, why. When you are "
            "satisfied call `submit(summary)` exactly once with a 1-3 sentence description of what "
            "you changed and what it measured. Submit a small verified gain rather than an "
            "unverified large one."
        )
        return "\n".join(parts)

    # ---- the agent -------------------------------------------------------

    async def _build_agent(self, ctx: EvolveContext, sess: _Session):
        """A fresh agent per mutation.

        Rebuilding costs a little; sharing one agent means sharing the tool closures, and those
        closures are exactly what has to be per-mutation. Correctness under concurrency is worth
        more than the setup time.
        """
        from pantheon.agent import Agent
        from pantheon.internal.compression.plugin import CompressionPlugin
        from pantheon.team.pantheon import PantheonTeam
        from pantheon.toolsets.file import FileManagerToolSet
        from pantheon.toolsets.python import PythonInterpreterToolSet
        from pantheon.toolsets.shell import ShellToolSet

        wt = sess.workdir

        async def submit(summary: str) -> str:
            """Commit your best version together with a short summary — like a git commit. Reads
            the current files in your working directory as the result and CHECKS them: an invalid
            solution is rejected and you get to fix it. Call this when you are done. The summary
            is 1-3 sentences: what you changed, whether it worked, and the measured metric
            change."""
            files = sess.current_files()
            res = await self._evaluate(files, "full")
            ok, why = self._feasible(res)
            if ok:
                self._remember_best(sess, files, res)
                sess.submitted = {"files": files, "summary": (summary or "").strip()}
                return "Submitted. Your result and summary are recorded."

            # Measured: 34 of 204 deliberate submissions were infeasible, and more than half of
            # those came from agents that HAD run the evaluator -- they checked one version, edited
            # again, and submitted the edit unchecked. Nothing stopped them, because submit()
            # simply snapshotted the directory. Checking here closes the loop the agent already
            # has, which is why it costs a rejection rather than a rule.
            sess.rejects += 1
            if sess.rejects < self.max_submit_retries:
                return (f"⛔ NOT submitted — the current files are not a valid solution: {why}\n"
                        f"Fix the cause and call submit() again "
                        f"({self.max_submit_retries - sess.rejects} attempt(s) left). "
                        f"submit() costs no action budget.")
            if sess.best is not None:
                sess.submitted = {"files": sess.best["files"], "summary": sess.best["summary"]}
                return ("⛔ Still invalid. Your best VERIFIED version was submitted instead of the "
                        "current files.")
            # Nothing valid was ever produced. Record it anyway -- the method's own feasibility
            # rule ignores it, and a visible failed attempt is better than a silent gap.
            sess.submitted = {"files": files,
                              "summary": f"(invalid after {sess.rejects} attempts: {why})"}
            return "⛔ Still invalid, and nothing valid was ever verified. Recorded as a failure."

        async def run_evaluator() -> dict:
            """Run the objective's evaluator on the CURRENT code in your working directory and
            return its metrics. Higher is better. If the solution is INVALID it scores 0 and the
            'feedback' field explains why — fix that before submitting. Always verify your edits
            helped before you submit."""
            budget = self.max_evaluations
            if budget is not None and sess.evals >= budget:
                return {"success": False, "metrics": {}, "error":
                        f"Evaluation budget exhausted ({sess.evals}/{budget} used). No more "
                        "run_evaluator calls this mutation — call submit() with your best version NOW."}
            sess.evals += 1
            files = sess.current_files()
            res = await self._evaluate(files, self.inner_fidelity)
            self._remember_best(sess, files, res)
            out = {"success": res.get("success"), "metrics": res.get("metrics"),
                   "error": res.get("error")}
            feedback = {k: v for k, v in (res.get("artifacts") or {}).items()
                        if k not in ("llm_feedback", "issues", "suggestions") and v}
            if feedback:
                out["feedback"] = feedback
            if budget is not None:
                out["evaluations_left"] = budget - sess.evals
            return out

        async def inspect_program(ref: str) -> dict:
            """Look up the FULL record of an earlier program in this run's archive by its
            reference (the #N number shown in the history, or an id). The history only shows a
            one-line summary; this returns the complete summary, code and metrics, so you can
            learn from what actually worked. Read-only."""
            ind = _resolve(ctx, ref)
            if ind is None:
                return {"found": False,
                        "error": f"No program matches '{ref}'. Use the #N number or id."}
            m = {k: v for k, v in ind.metrics().items() if k != "fitness_weights"}
            files = getattr(ind.genome, "files", None)
            return {"found": True, "id": ind.id, "order": ind.order,
                    "generation": ind.generation, "metrics": m,
                    "summary": ind.meta.get("summary", "(no summary)"),
                    "files": dict(files) if files else {"content": ind.genome.render()}}

        def think(thought: str) -> str:
            """Think out loud before acting. Returns the thought unchanged."""
            return thought

        tools = [think, run_evaluator, submit, inspect_program]
        if self.web_search:
            tools.append(_web_search)

        agent = Agent(name="code-evolver", instructions=self.system_prompt,
                      model=self.model, tools=tools, use_memory=True)
        await agent.toolset(FileManagerToolSet(f"evo-fm-{id(sess)}", str(wt)))
        await agent.toolset(PythonInterpreterToolSet(name=f"evo-py-{id(sess)}", workdir=str(wt)))
        await agent.toolset(ShellToolSet(f"evo-sh-{id(sess)}", workdir=str(wt)))
        self._attach_budget(agent, sess)
        if self.trace_path:
            self._attach_trace(agent, sess)
        return PantheonTeam(agents=[agent], plugins=[CompressionPlugin(
            {"enable": True, "threshold": 0.8, "preserve_recent_messages": 5})])

    def _attach_trace(self, agent, sess: _Session) -> None:
        """Record each tool call together with the state of the workspace afterwards.

        The file digest is the point. A tool that reports success while leaving a broken file
        behind is invisible in its own return value, so the trace carries what the workspace
        actually looks like after every call and the first bad line is attributable to one call.
        """
        import hashlib

        def snapshot():
            out = {}
            for path in sorted(sess.parent_files):
                fp = sess.workdir / path
                if not fp.exists():
                    out[path] = {"missing": True}
                    continue
                b = fp.read_bytes()
                head = b[:60].decode("utf-8", "replace")
                out[path] = {"sha": hashlib.sha256(b).hexdigest()[:10], "bytes": len(b),
                             "lines": b.count(b"\n") + 1, "head": head}
            return out

        def write(rec):
            try:
                with open(self.trace_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
            except OSError:
                pass

        async def pre(func_name, params):
            write({"sess": id(sess), "phase": "pre", "tool": func_name.split("__")[-1],
                   "args": {k: (v[:600] if isinstance(v, str) else v)
                            for k, v in (params or {}).items()},
                   "files": snapshot(), "at": time.time()})
            return None

        async def post(func_name, params, result):
            write({"sess": id(sess), "phase": "post", "tool": func_name.split("__")[-1],
                   "result": (result if isinstance(result, (dict, int, float, bool))
                              else str(result)[:600]),
                   "files": snapshot(), "at": time.time()})
            return None

        agent._pre_tool_hooks.append(pre)
        agent._post_tool_hooks.append(post)

    def _attach_budget(self, agent, sess: _Session) -> None:
        """Charge every tool call except submit against a quota, show the countdown on each
        result, and once spent make further calls fail while leaving submit open. An in-band
        signal the agent has to react to beats a turn limit it cannot see."""
        def is_submit(name: str) -> bool:
            return name.split("__")[-1] == "submit"

        async def pre(func_name, params):
            budget = self.max_tool_calls
            if not budget or is_submit(func_name):
                return None
            if sess.tool_calls >= budget:
                return (f"⛔ Action budget exhausted: {sess.tool_calls}/{budget} tool calls used, "
                        f"so '{func_name.split('__')[-1]}' was NOT run. Your only remaining action "
                        "is submit() — call submit(summary=...) NOW with the best VALID version "
                        "already on disk.")
            sess.tool_calls += 1
            return None

        async def post(func_name, params, result):
            budget = self.max_tool_calls
            if not budget or is_submit(func_name):
                return None
            left = budget - sess.tool_calls
            warn = ("  ⚠ almost out — make sure a VALID improvement is on disk, then submit()."
                    if left <= max(2, budget // 4) else "")
            note = (f"\n\n[action budget: {left}/{budget} tool calls left before only submit() "
                    f"works.{warn}]")
            if isinstance(result, str):
                return result + note
            if isinstance(result, dict):
                out = dict(result)
                out["actions_left"] = left
                return out
            return None

        agent._pre_tool_hooks.append(pre)
        agent._post_tool_hooks.append(post)

        async def winddown(history, _ctx):
            budget = self.max_turns
            if not budget or self.max_tool_calls:
                return []
            sess.turns += 1
            left = budget - sess.turns
            if left <= 0:
                return [{"role": "user", "content":
                         "⏳ FINAL turn — do not explore further. Call submit() with the best VALID "
                         "version you have RIGHT NOW."}]
            if left <= 3:
                return [{"role": "user", "content":
                         f"⏳ Only {left} turn(s) left. Stop exploring — make sure a VALID "
                         "improvement is on disk and call submit() soon."}]
            return []

        agent._ephemeral_hooks.append(winddown)

    # ---- the Variator protocol ------------------------------------------

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        """Run `item.k` independent mutations from the same prompt.

        One agent editing one workspace produces one child, so a method asking for k candidates --
        best-of-K, as SimpleTES does -- needs k separate sessions. They share only the prompt:
        separate workspaces, separate agents, separate submission slots. Run concurrently because
        they cannot interfere, which is only true because the isolation above is real.
        """
        parent = item.context.parents[0] if item.context.parents else (
            ctx.store.get(item.parent_ids[0]) if item.parent_ids else None)
        if parent is None or not isinstance(parent.genome, CodeGenome):
            logger.warning("AgentVariator needs a CodeGenome parent; got %r",
                           type(getattr(parent, "genome", None)))
            return []
        prompt = self.build_prompt(ctx, item)
        results = await asyncio.gather(
            *(self._one(ctx, item, parent, prompt, c) for c in range(max(1, item.k))),
            return_exceptions=True,
        )
        out: List[Produced] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"[{item.id}] candidate raised: {type(r).__name__}: {r}")
            elif r is not None:
                out.append(r)
        return out

    async def _one(self, ctx: EvolveContext, item: Create, parent, prompt: str,
                   candidate: int) -> Optional[Produced]:
        from pantheon.internal.memory import Memory

        root = self.workspace_root or tempfile.mkdtemp(prefix="evo_mut_")
        workdir = Path(root) / f"wt_{item.id}_{candidate}"
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)
        for path, content in parent.genome.files.items():
            fp = workdir / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")

        sess = _Session(workdir=workdir, parent_files=dict(parent.genome.files))
        team = await self._build_agent(ctx, sess)

        # max_turns counts HISTORY MESSAGES, not tool rounds (each round adds ~2), so the hard
        # ceiling must sit above what the action budget itself consumes or it bounds nothing
        if self.max_tool_calls:
            hard_turns = 2 * self.max_tool_calls + 12
        elif self.max_turns:
            hard_turns = self.max_turns + 2
        else:
            hard_turns = float("inf")

        t0 = time.time()
        err = None
        try:
            resp = await asyncio.wait_for(
                team.run(prompt, memory=Memory(name=f"evo-mut-{item.id}-{candidate}"),
                         max_turns=hard_turns),
                timeout=self.timeout)
            sess.cost = extract_cost(resp)
        except asyncio.TimeoutError:
            err = "mutation_timeout"
        except Exception as e:  # noqa: BLE001
            err = f"mutation_failed: {str(e)[:120]}"
        if err:
            logger.warning(f"[{item.id}#{candidate}] {err}")

        await self._salvage(sess, err)
        if not sess.submitted:
            logger.warning(f"[{item.id}#{candidate}] no child produced ({err or 'no_submit'})")
            return None

        files = dict(sess.submitted["files"])
        if self.warm_start_file:
            state = getattr(self.evaluator, "last_state", None)
            if state is not None:
                try:
                    files[self.warm_start_file] = json.dumps(state)
                except Exception:
                    pass
        return Produced(
            genome=CodeGenome(files=files),
            item_id=item.id,
            batch_id=item.batch_id,
            parent_ids=list(item.parent_ids),
            anchor_id=item.anchor_id,
            # `tool_calls` and `rejects` are recorded because the interesting question about a
            # mutation is usually whether it ran out of room. Infeasible submissions tracked the
            # evaluator count almost perfectly across three arms -- 1.11 evals/program went with
            # 18.9% infeasible, 2.25 with 6.3% -- and without the budget actually consumed there
            # is no way to tell a cap that bound from an agent that stopped early.
            meta={"summary": sess.submitted["summary"], "cost": sess.cost,
                  "mutation_seconds": time.time() - t0, "evals": sess.evals,
                  "tool_calls": sess.tool_calls, "budget": self.max_tool_calls,
                  "rejects": sess.rejects, "candidate": candidate},
        )

    async def _salvage(self, sess: _Session, err: Optional[str]) -> None:
        """Recover work the agent did but never committed. Two fallbacks, in order.

        Agents routinely explore, edit the files, and then stop without a final
        run_evaluator/submit. Discarding that is throwing away the whole iteration's spend.
        """
        if not sess.submitted and sess.best is None:
            try:
                final = sess.current_files()
                if final != sess.parent_files:
                    res = await self._evaluate(final, "full")
                    # `success` means the evaluator did not crash, which an invalid solution also
                    # manages: it comes back success=True, validity=0, score 0. Salvaging on that
                    # committed abandoned half-edits as children -- 44% of the programs this path
                    # produced were infeasible, against 17% for deliberate submissions.
                    self._remember_best(sess, final, res)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"final on-disk salvage failed: {e}")
        if not sess.submitted and sess.best:
            sess.submitted = {"files": sess.best["files"], "summary": sess.best["summary"]}
            logger.info(f"auto-committed best of {sess.evals} verified attempts "
                        f"({err or 'no submit'})")


def _resolve(ctx: EvolveContext, ref: str):
    """A program reference: '#N' / 'N' order number, an id, or an id prefix."""
    ref = str(ref).strip().lstrip("#")
    ind = ctx.store.get(ref)
    if ind is not None:
        return ind
    if ref.isdigit():
        order = int(ref)
        for i in ctx.store:
            if i.order == order:
                return i
    for i in ctx.store:
        if i.id.startswith(ref):
            return i
    return None


def _web_search(query: str, max_results: int = 6) -> str:
    """Search the web (DuckDuckGo) to research the domain. Returns titles, snippets and URLs."""
    try:
        from ddgs import DDGS
        rs = list(DDGS().text(query, max_results=max_results))
        return "\n".join(
            f"- {r.get('title', '')}: {(r.get('body') or '')[:220]} ({r.get('href', '')})"
            for r in rs) or "(no results)"
    except Exception as e:  # noqa: BLE001
        return f"web_search error: {type(e).__name__}: {e}"
