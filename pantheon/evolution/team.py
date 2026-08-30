"""
Evolution team for coordinating multi-agent code evolution.

EvolutionTeam orchestrates:
- Mutator agent for generating code changes
- Evaluator for scoring programs
- Optional critic agent for analysis
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pantheon.utils.log import logger

from .config import EvolutionConfig
from .database import EvolutionDatabase
from .evaluator import EvaluationResult, HybridEvaluator
from .program import CodebaseSnapshot, Program
from .prompt_builder import (
    EvolutionPromptBuilder,
    MUTATION_SYSTEM_PROMPT_CODEBASE,
    MUTATION_SYSTEM_PROMPT_SIMPLE,
    SUMMARIZER_SYSTEM_PROMPT,
)
from .result import EvolutionResult, IterationResult
from .utils.diff import parse_diff, apply_diff


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


def think(thought: str) -> str:
    """
    Use this tool to think through problems step by step.

    Args:
        thought: Your reasoning, analysis, or intermediate thoughts

    Returns:
        Acknowledgment that thinking was recorded
    """
    return "Thought recorded. Continue your analysis."


def format_metrics_for_log(metrics: Dict[str, Any], max_metrics: int = 3) -> str:
    """
    Format raw metrics for logging display.

    Args:
        metrics: Dict of metric name -> value
        max_metrics: Maximum number of metrics to show

    Returns:
        Formatted string like "fidelity=0.644 coverage=0.95"
    """
    if not metrics:
        return "no_metrics"

    # Get fitness_weights to know which metrics matter
    fitness_weights = metrics.get("fitness_weights", {})

    # Prioritize metrics that have fitness weights
    priority_metrics = []
    other_metrics = []

    for key, value in metrics.items():
        if key in ("fitness_weights", "error", "llm_feedback"):
            continue
        if not isinstance(value, (int, float)):
            continue
        if key in fitness_weights:
            priority_metrics.append((key, value))
        else:
            other_metrics.append((key, value))

    # Combine and limit
    all_metrics = priority_metrics + other_metrics
    selected = all_metrics[:max_metrics]

    if not selected:
        return "no_metrics"

    return " ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in selected)


def get_primary_metric(metrics: Dict[str, Any]) -> Tuple[str, float]:
    """
    Get the primary metric (first in fitness_weights) and its value.

    Returns:
        Tuple of (metric_name, value)
    """
    if not metrics:
        return ("score", 0.0)

    fitness_weights = metrics.get("fitness_weights", {})
    if fitness_weights:
        # Return first weighted metric
        for key in fitness_weights:
            if key in metrics and isinstance(metrics[key], (int, float)):
                return (key, float(metrics[key]))

    # Fallback to fitness_score if present
    if "fitness_score" in metrics:
        return ("fitness_score", float(metrics["fitness_score"]))

    return ("score", 0.0)


def compute_deltas(
    parent: Program,
    child: Program,
    feature_dimensions: List[str],
    metric_ranges: Dict[str, Tuple[float, float]] = None,
    function_weight: float = 1.0,
    llm_weight: float = 0.0,
) -> tuple:
    """
    Compute fitness and metrics deltas between parent and child.

    Args:
        parent: Parent program
        child: Child program
        feature_dimensions: Feature dimensions for fitness calculation
        metric_ranges: Optional dict of metric name -> (min, max) for normalization
        function_weight: Weight for function_score (default 1.0)
        llm_weight: Weight for llm_score (default 0.0)

    Returns:
        Tuple of (fitness_delta, metrics_delta)
        - fitness_delta: Change in fitness score
        - metrics_delta: Dict of per-metric changes (missing metrics treated as 0)
    """
    # fitness_delta uses the new fitness formula
    fitness_delta = (
        child.fitness_score(feature_dimensions, metric_ranges, function_weight, llm_weight)
        - parent.fitness_score(feature_dimensions, metric_ranges, function_weight, llm_weight)
    )

    # metrics_delta records per-metric changes (only numeric values)
    all_keys = set(parent.metrics.keys()) | set(child.metrics.keys())
    metrics_delta = {}
    for key in all_keys:
        child_val = child.metrics.get(key, 0.0)
        parent_val = parent.metrics.get(key, 0.0)
        # Only compute delta for numeric values
        if isinstance(child_val, (int, float)) and isinstance(parent_val, (int, float)):
            metrics_delta[key] = child_val - parent_val

    return fitness_delta, metrics_delta


def extract_cost_from_response(response) -> float:
    """
    Extract LLM cost from an agent response.

    Args:
        response: AgentResponse from agent.run()

    Returns:
        Cost in USD, or 0.0 if not available
    """
    try:
        if response and response.details and response.details.messages:
            # Find the last assistant message which contains cost info
            for msg in reversed(response.details.messages):
                if msg.get("role") == "assistant" and "_metadata" in msg:
                    return msg.get("_metadata", {}).get("current_cost", 0.0)
    except Exception:
        pass
    return 0.0


class EvolutionTeam:
    """
    Evolution team coordinating mutation, evaluation, and selection.

    Implements the core evolution loop:
    1. Sample parent and inspirations from database
    2. Build mutation prompt
    3. Generate mutation via LLM
    4. Apply diff to create child program
    5. Evaluate child
    6. Add to database (MAP-Elites decides if kept)
    7. Repeat
    """

    def __init__(
        self,
        mutator: Optional[Any] = None,  # Agent
        evaluator: Optional[Union[HybridEvaluator, Any]] = None,  # HybridEvaluator or Agent
        analyzer: Optional[Any] = None,  # Agent for code analysis
        critic: Optional[Any] = None,  # Agent
        database: Optional[EvolutionDatabase] = None,
        config: Optional[EvolutionConfig] = None,
    ):
        """
        Initialize evolution team.

        Args:
            mutator: Agent for generating mutations (created if None)
            evaluator: HybridEvaluator or Agent for evaluation
            analyzer: Agent for code analysis (created if None when use_analyzer=True)
            critic: Optional critic agent for failure analysis
            database: Program database (created if None)
            config: Evolution configuration (created if None)
        """
        self.config = config or EvolutionConfig()

        # Configure log level from config
        if self.config.log_level:
            from pantheon.utils.log import set_level
            set_level(self.config.log_level)

        self.database = database or EvolutionDatabase(config=self.config)
        self.prompt_builder = EvolutionPromptBuilder(
            max_code_length=self.config.max_code_length,
            max_top_programs=self.config.num_top_programs,
            max_inspirations=self.config.num_inspirations,
        )

        # Agents (lazy-initialized)
        self._mutator = mutator
        self._evaluator = evaluator
        self._analyzer = analyzer
        self._critic = critic
        self._python_toolset = None  # Python interpreter for analyzer (lazy-initialized)
        # Single-agent mutation: one full-capability coding agent (one-agent PantheonTeam)
        # that edits a workspace and commits via submit(). Built once, reused; run per
        # iteration with a fresh Memory (stateless across iterations).
        self._mut_team = None
        self._mut_agent = None
        self._mut_workdir = None
        self._mut_eval_count = 0      # run_evaluator calls in the current mutation
        self._mut_best = None         # best VERIFIED version seen this mutation (auto-commit fallback)
        self._mut_turn_count = 0      # LLM turns taken this mutation (for the graceful wind-down)
        self._mut_tool_calls_used = 0 # ACTION tool calls used this mutation (max_tool_calls_per_mutation)
        self._mut_parent_files: Dict[str, str] = {}
        self._mut_submitted = None
        self._summarizer = None

        # State
        self.objective: str = ""
        self.evaluator_code: str = ""
        self._initialized = False

    async def _ensure_mutator(self):
        """Ensure mutator agent is initialized."""
        if self._mutator is None:
            try:
                from pantheon.agent import Agent
                # Use simplified prompt when analyzer is enabled
                system_prompt = (
                    MUTATION_SYSTEM_PROMPT_SIMPLE
                    if self.config.use_analyzer
                    else MUTATION_SYSTEM_PROMPT_CODEBASE
                )
                self._mutator = Agent(
                    name="code-mutator",
                    instructions=system_prompt,
                    model=self.config.mutator_model,
                    use_memory=False,  # Prevent context accumulation across iterations
                )
            except ImportError:
                raise RuntimeError("Pantheon Agent not available for mutation")
        return self._mutator

    async def _ensure_mutation_agent(self):
        """One full-capability coding agent (a one-agent PantheonTeam) that edits a workspace
        and commits via submit(). Replaces the analyzer+mutator(+summarizer) pipeline. Built
        once and reused; run per iteration with a fresh Memory so context does not accumulate."""
        if self._mut_team is not None:
            return self._mut_team

        from pantheon.agent import Agent
        from pantheon.internal.compression.plugin import CompressionPlugin
        from pantheon.team.pantheon import PantheonTeam
        from pantheon.apps.builtin.file import FileManagerToolSet
        from pantheon.apps.builtin.python import PythonInterpreterToolSet
        from pantheon.apps.builtin.shell import ShellToolSet

        base = self.config.workspace_path or tempfile.mkdtemp(prefix="evo_mut_")
        wt = Path(base) / "_mutation_wt"
        wt.mkdir(parents=True, exist_ok=True)
        self._mut_workdir = wt

        def _current_files() -> Dict[str, str]:
            # Read back the STABLE (parent) file set; scratch files the agent created are ignored.
            files = {}
            for path, original in self._mut_parent_files.items():
                fp = wt / path
                files[path] = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else original
            return files

        async def submit(summary: str) -> str:
            """Commit your best version together with a short summary — like a git commit.
            Reads the current files in your working directory as the result and records them
            plus your summary into the program database. Call this exactly ONCE, when you are
            done. The summary is 1-3 sentences: what you changed, whether it worked, and the
            measured metric change."""
            self._mut_submitted = {"files": _current_files(), "summary": (summary or "").strip()}
            return "Submitted. Your result and summary are recorded."

        async def run_evaluator() -> dict:
            """Run the objective's evaluator on the CURRENT code in your working directory and
            return its metrics. Higher fitness is better. If the solution is INVALID it scores 0
            and the 'feedback' field explains WHY (e.g. which constraint is violated) — fix that
            before submitting. Always verify your edits helped before you submit."""
            budget = self.config.max_evaluations_per_mutation
            if budget is not None and self._mut_eval_count >= budget:
                return {"success": False, "metrics": {}, "error":
                        f"Evaluation budget exhausted ({self._mut_eval_count}/{budget} used). No more "
                        "run_evaluator calls this mutation — call submit() with your best version NOW."}
            self._mut_eval_count += 1
            evaluator = await self._ensure_evaluator()
            files = _current_files()
            res = await evaluator.evaluate(
                Program(id="_probe", snapshot=CodebaseSnapshot(files=files), generation=0))
            # remember the best VERIFIED version so it can be auto-committed if the agent never submits
            score = float((res.metrics or {}).get("combined_score", 0.0) or 0.0)
            if res.success and (self._mut_best is None or score > self._mut_best["score"]):
                self._mut_best = {"files": files, "score": score,
                                  "summary": f"(auto-committed best of {self._mut_eval_count} verified "
                                             f"attempts; combined_score {score:.4f})"}
            out = {"success": res.success, "metrics": res.metrics, "error": res.error}
            feedback = {k: v for k, v in (res.artifacts or {}).items()
                        if k not in ("llm_feedback", "issues", "suggestions") and v}
            if feedback:
                out["feedback"] = feedback
            if budget is not None:
                out["evaluations_left"] = budget - self._mut_eval_count
            return out

        def _find_program(ref: str):
            """Resolve a program reference (#order, order, or id / id-prefix) to a Program."""
            ref = str(ref).strip().lstrip("#")
            progs = self.database.programs
            if ref in progs:                       # exact id
                return progs[ref]
            if ref.isdigit():                      # order number (as shown in the history as #N)
                order = int(ref)
                for p in progs.values():
                    if p.order == order:
                        return p
            for pid, p in progs.items():           # id prefix
                if pid.startswith(ref):
                    return p
            return None

        async def inspect_program(ref: str) -> dict:
            """Look up the FULL record of an earlier program in this run's archive by its
            reference (the #N number shown in the evolution-history / population sections, or a
            program id). Use this to study a promising or high-scoring earlier attempt in detail —
            the compact history only shows a one-line summary; this returns the COMPLETE mutation
            summary, the full code, and the measured metrics, so you can learn from what actually
            worked and adapt it. This is read-only reference material; it does not change your work."""
            p = _find_program(ref)
            if p is None:
                return {"found": False, "error": f"No program matches '{ref}'. Use the #N number "
                        "or id from the history/population sections."}
            m = p.metrics or {}
            return {
                "found": True, "id": p.id, "order": p.order, "generation": p.generation,
                "combined_score": m.get("combined_score"), "sum_radii": m.get("sum_radii"),
                "metrics": {k: v for k, v in m.items() if k != "fitness_weights"},
                "summary": p.mutation_summary or "(no summary)",
                "files": dict(p.snapshot.files),
            }

        agent_tools = [think, run_evaluator, submit, inspect_program]
        if self.config.mutation_web_search:
            def web_search(query: str, max_results: int = 6) -> str:
                """Search the web (DuckDuckGo) to research the domain — e.g. marker genes, pathways,
                cell-type biology. Returns result titles, snippets and URLs."""
                try:
                    from ddgs import DDGS
                    rs = list(DDGS().text(query, max_results=max_results))
                    return "\n".join(
                        f"- {r.get('title', '')}: {(r.get('body') or '')[:220]} ({r.get('href', '')})"
                        for r in rs) or "(no results)"
                except Exception as e:  # noqa: BLE001
                    return f"web_search error: {type(e).__name__}: {e}"
            agent_tools.append(web_search)

        agent = Agent(name="code-evolver",
                      instructions=self.config.mutation_system_prompt or MUTATION_AGENT_SYSTEM_PROMPT,
                      model=self.config.mutator_model, tools=agent_tools,
                      use_memory=True)
        await agent.toolset(FileManagerToolSet("evo-fm", str(wt)))
        await agent.toolset(PythonInterpreterToolSet(name="evo-py", workdir=str(wt)))
        await agent.toolset(ShellToolSet("evo-sh", workdir=str(wt)))

        # Action budget: charge every tool call EXCEPT submit against a per-mutation quota, surface a
        # live countdown on each result, and once spent make further tool calls FAIL (submit stays
        # open). This replaces the turn-based wind-down with an in-band signal the agent must react to
        # and always leaves it able to finalize its own best work via submit().
        def _is_submit(func_name: str) -> bool:
            return func_name.split("__")[-1] == "submit"

        async def _budget_pre_hook(func_name, params):
            budget = self.config.max_tool_calls_per_mutation
            if not budget or _is_submit(func_name):
                return None
            if self._mut_tool_calls_used >= budget:
                logger.info(f"[action-budget] BLOCKED {func_name} — "
                            f"{self._mut_tool_calls_used}/{budget} used, forcing submit")
                return (f"⛔ Action budget exhausted: {self._mut_tool_calls_used}/{budget} tool calls "
                        f"used, so '{func_name.split('__')[-1]}' was NOT run. Your only remaining "
                        "action is submit() — call submit(summary=...) NOW with the best VALID version "
                        "already on disk. Do not attempt other tools; they will keep failing.")
            self._mut_tool_calls_used += 1
            return None

        async def _budget_post_hook(func_name, params, result):
            budget = self.config.max_tool_calls_per_mutation
            if not budget or _is_submit(func_name):
                return None
            left = budget - self._mut_tool_calls_used
            warn = ("  ⚠ almost out — make sure a VALID improvement is on disk, then submit()."
                    if left <= max(2, budget // 4) else "")
            note = f"\n\n[action budget: {left}/{budget} tool calls left before only submit() works.{warn}]"
            if isinstance(result, str):
                return result + note
            if isinstance(result, dict):
                result = dict(result)
                result["actions_left"] = left
                return result
            return None

        agent._pre_tool_hooks.append(_budget_pre_hook)
        agent._post_tool_hooks.append(_budget_post_hook)

        # Graceful wind-down (legacy, turn-based): only used when the action budget is NOT set.
        # Warns the agent as it nears its turn budget so it can finalize on its own terms. Fires each
        # turn (ephemeral, not persisted). The hard max_turns + auto-commit-best are the safety net.
        async def _winddown_hook(history, ctx):
            budget = self.config.max_mutation_turns
            if not budget or self.config.max_tool_calls_per_mutation:
                return []
            self._mut_turn_count += 1
            left = budget - self._mut_turn_count
            if left <= 3:
                logger.info(f"[wind-down] turn {self._mut_turn_count}/{budget} — {left} left, "
                            "nudging agent to submit")
            if left <= 0:
                return [{"role": "user", "content":
                         "⏳ FINAL turn — do not explore further. Call submit() with the best VALID "
                         "version you have RIGHT NOW (or fix it minimally and submit)."}]
            if left <= 3:
                return [{"role": "user", "content":
                         f"⏳ Only {left} turn(s) left in this mutation. Stop exploring — make sure a "
                         "VALID improvement is on disk and call submit() soon. A small verified gain "
                         "submitted now beats being cut off with nothing."}]
            return []
        agent._ephemeral_hooks.append(_winddown_hook)

        self._mut_agent = agent
        self._mut_team = PantheonTeam(agents=[agent], plugins=[CompressionPlugin(
            {"enable": True, "threshold": 0.8, "preserve_recent_messages": 5})])
        return self._mut_team

    def _build_single_agent_prompt(self, parent: Program, iteration: int,
                                    inspirations: Optional[List[Program]] = None) -> str:
        wd = str(self._mut_workdir)
        parts = [f"Iteration {iteration + 1}. Objective:", self.objective, "",
                 f"Your working directory is: {wd}",
                 "The files to improve are there. IMPORTANT: your python interpreter's current "
                 f"directory may NOT be this one, so read/write files with ABSOLUTE paths under "
                 f"{wd} (e.g. open('{wd}/panel.txt', 'w')). The evaluator reads the files from "
                 f"{wd}, so edits written elsewhere will NOT be scored."]
        if self.config.warm_start_file:
            parts += [f"WARM START: '{self.config.warm_start_file}' in your working directory holds the "
                      "best solution PRODUCED by the parent (as data, e.g. the best coordinates). The "
                      "framework manages this file automatically — do NOT edit it by hand. Your code "
                      "SHOULD load it if present and refine from it (a short polish of an already-good "
                      "solution beats re-deriving one from scratch), falling back to a full search when "
                      "it is empty/absent. After each evaluation the framework refreshes it with the "
                      "new best, so improvements accumulate across generations."]
        history = self.prompt_builder.build_evolution_history_section(
            sibling_summaries=self.database.get_sibling_summaries(parent.id),
            ancestor_summaries=self.database.get_ancestor_summaries(parent.id),
            parent_order=parent.order or 0,
            max_siblings=self.config.evolution_history_max_siblings,
            max_ancestors=self.config.evolution_history_max_ancestors,
            max_chars=self.config.evolution_history_max_chars,
        )
        if history:
            parts += ["", "What earlier attempts in this lineage tried "
                      "(learn from these, then improve or do something different):", history,
                      "The summaries above are one-liners. To see the FULL summary + complete code "
                      "of any node, call inspect_program(#N) with its #number."]
        # Lateral inspiration: concise pointers to strong programs from OTHER lineages (diverse
        # MAP-Elites elites). The agent pulls their full code on demand via inspect_program.
        insp_lines = []
        for ins in (inspirations or []):
            if ins.id == parent.id:
                continue
            cs = (ins.metrics or {}).get("combined_score")
            score = f"{cs:.3f}" if isinstance(cs, (int, float)) else "?"
            head = (ins.mutation_summary or "").splitlines()[0][:70] if ins.mutation_summary else "(no summary)"
            insp_lines.append(f"- #{ins.order} (score {score}): \"{head}\"")
        if insp_lines:
            parts += ["", "Other strong approaches elsewhere in the population (DIFFERENT lineages — "
                      "consider borrowing their ideas). Call inspect_program(#N) to read one's full "
                      "code/summary:", *insp_lines]
        artifacts = parent.artifacts or {}
        feedback = artifacts.get("llm_feedback") or artifacts.get("evaluation_error")
        if feedback:
            parts += ["", "Notes from the last evaluation:", str(feedback)[:1000]]
        parts += ["", "Edit the files, verify with run_evaluator, then call submit(summary=...) "
                  "to commit your best version. Nothing is recorded until you submit."]
        eval_budget = self.config.max_evaluations_per_mutation
        if eval_budget is not None:
            parts += [f"EVALUATION BUDGET: you may call run_evaluator at most {eval_budget} time(s) this "
                      "mutation — spend them wisely (make a substantial, considered change before each "
                      "check) and submit your best."]
        tool_budget = self.config.max_tool_calls_per_mutation
        if tool_budget is not None:
            parts += [f"ACTION BUDGET: you have {tool_budget} tool calls for this mutation (every "
                      "python / shell / run_evaluator / web_search / file call counts — submit() does "
                      "NOT). Each tool result shows how many you have left. When the budget hits 0, "
                      "every tool EXCEPT submit() will FAIL, so plan to have a VALID improvement on "
                      "disk and call submit() before then. submit() is always available — a small "
                      "verified gain submitted in time beats spending the whole budget and being "
                      "forced to submit whatever happens to be on disk."]
        elif self.config.max_mutation_turns is not None:
            turn_budget = self.config.max_mutation_turns
            parts += [f"TURN BUDGET: you have about {turn_budget} action-turns for this mutation. You "
                      "will be WARNED a few turns before the end — when warned, stop exploring, make sure "
                      "a VALID improvement is on disk, and submit(). Plan to leave the panel/code in a "
                      "submittable state well before then; a small verified gain beats being cut off."]
        return "\n".join(parts)

    async def _run_iteration_single_agent(self, iteration: int, max_iterations: int = 0,
                                          worker_id: Optional[int] = None) -> IterationResult:
        """One evolution iteration using a single full-capability coding agent that edits a
        workspace and commits via submit() (analyzer + mutator + summarizer collapsed into one)."""
        from pantheon.internal.memory import Memory

        iter_start = time.time()
        log_prefix = f"[Worker {worker_id}]" if worker_id is not None else f"[{iteration + 1}/{max_iterations}]"
        logger.info(f"{log_prefix} Starting iteration (single-agent)...")

        parent, inspirations = await self.database.sample_async(
            num_inspirations=self.config.num_inspirations)
        parent_score = parent.fitness_score(
            self.config.feature_dimensions, self.database.metric_ranges,
            self.config.function_weight, self.config.llm_weight)

        team = await self._ensure_mutation_agent()
        self._mut_parent_files = dict(parent.snapshot.files)
        self._mut_submitted = None
        self._mut_eval_count = 0      # reset the per-mutation eval budget + best-seen + turn counter
        self._mut_best = None
        self._mut_turn_count = 0
        self._mut_tool_calls_used = 0
        parent.snapshot.to_workspace(str(self._mut_workdir))  # fresh workspace = parent code

        prompt = self._build_single_agent_prompt(parent, iteration, inspirations)
        memory = Memory(name=f"evo-mut-{worker_id}-{iteration}")  # fresh -> stateless per iteration

        mutation_start = time.time()
        err = None
        iteration_cost = 0.0
        try:
            # Hard ceiling on Agent.run's max_turns — which counts HISTORY MESSAGES, not tool
            # rounds (each tool round adds ~2: an assistant tool_call + its tool result). This is
            # only a last-resort net; the action budget is meant to be the real limiter, so it must
            # sit ABOVE what the budget itself consumes (~2 messages per allowed tool call) plus a
            # few post-exhaustion "you must submit now" rounds. Otherwise the tail bounds nothing.
            if self.config.max_tool_calls_per_mutation:
                hard_turns = 2 * self.config.max_tool_calls_per_mutation + 12
            elif self.config.max_mutation_turns:
                hard_turns = self.config.max_mutation_turns + 2
            else:
                hard_turns = float("inf")
            resp = await asyncio.wait_for(
                team.run(prompt, memory=memory, max_turns=hard_turns),
                timeout=self.config.mutation_timeout)
            iteration_cost = extract_cost_from_response(resp)
        except asyncio.TimeoutError:
            logger.warning(f"{log_prefix} Mutation agent timeout")
            err = "mutation_timeout"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{log_prefix} Mutation agent failed: {e}")
            err = f"mutation_failed: {str(e)[:120]}"
        mutation_time = time.time() - mutation_start

        # Salvage step 1: the agent ended without submitting AND never verified anything, but it may
        # still have EDITED the workspace (a common failure mode: it explores in python, tweaks the
        # files, then stops without a final run_evaluator/submit). Do one last evaluation of whatever
        # is on disk so that work isn't discarded — if it scores, it becomes the best-seen version.
        if not self._mut_submitted and self._mut_best is None:
            try:
                final_files = {}
                for path, original in self._mut_parent_files.items():
                    fp = self._mut_workdir / path
                    final_files[path] = (fp.read_text(encoding="utf-8", errors="replace")
                                         if fp.exists() else original)
                if final_files != self._mut_parent_files:  # only if the agent actually changed something
                    evaluator = await self._ensure_evaluator()
                    res = await evaluator.evaluate(Program(
                        id="_final", snapshot=CodebaseSnapshot(files=final_files), generation=0))
                    if res.success:
                        score = float((res.metrics or {}).get("combined_score", 0.0) or 0.0)
                        self._mut_best = {"files": final_files, "score": score,
                                          "summary": f"(auto-evaluated final on-disk edit the agent "
                                                     f"left unverified; combined_score {score:.4f})"}
                        logger.info(f"{log_prefix} Final on-disk edit evaluated (combined_score "
                                    f"{score:.4f}) — salvaging the agent's unsubmitted work")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{log_prefix} Final on-disk salvage eval failed: {e}")

        # Salvage step 2: if the agent never called submit (timed out, errored, hit the eval budget, or
        # just forgot), commit the BEST verified version it reached so exploration is not thrown away.
        if not self._mut_submitted and self._mut_best:
            self._mut_submitted = {"files": self._mut_best["files"], "summary": self._mut_best["summary"]}
            logger.info(f"{log_prefix} Auto-committed best of {self._mut_eval_count} verified attempts "
                        f"(agent did not submit: {err or 'no submit'})")

        if not self._mut_submitted:
            logger.warning(f"{log_prefix} No child this iteration ({err or 'no_submit'})")
            return IterationResult(
                iteration=iteration, parent_id=parent.id, child_id=parent.id,
                parent_score=parent_score, child_score=parent_score, improvement=0, accepted=False,
                mutation_time=mutation_time, evaluation_time=0,
                total_time=time.time() - iter_start, error=err or "no_submit", llm_cost=iteration_cost)

        child_snapshot = CodebaseSnapshot(files=self._mut_submitted["files"])
        summary = self._mut_submitted["summary"]
        diff_from_parent = child_snapshot.diff_from(parent.snapshot)
        logger.info(f"{log_prefix} Mutation: {mutation_time:.1f}s (${iteration_cost:.4f}) — submitted")

        child = Program(
            id=str(uuid.uuid4())[:8], snapshot=child_snapshot, diff_from_parent=diff_from_parent,
            parent_id=parent.id, generation=parent.generation + 1, mutation_summary=summary)

        eval_start = time.time()
        evaluator = await self._ensure_evaluator()
        eval_result = await evaluator.evaluate(child)
        eval_time = time.time() - eval_start
        child.metrics = eval_result.metrics
        child.artifacts = eval_result.artifacts
        child.llm_feedback = eval_result.llm_feedback
        # Warm-start persistence: if the evaluator returned the produced solution, store it as the
        # child's warm_start.json (a data file in the genome) so the NEXT generation loads and
        # polishes it instead of re-deriving from scratch. Done AFTER diff_from_parent is computed
        # (line above) so the large solution blob never shows up in the code diff. It is the
        # evaluator-produced, verified solution — not something the agent claimed.
        if eval_result.state is not None and self.config.warm_start_file:
            try:
                child.snapshot.files[self.config.warm_start_file] = json.dumps(eval_result.state)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{log_prefix} Could not persist warm-start state: {e}")
        if child.metrics.get("fitness_weights"):
            self.database._update_metric_ranges(child.metrics)

        child_score = child.fitness_score(
            self.config.feature_dimensions, self.database.metric_ranges,
            self.config.function_weight, self.config.llm_weight)
        improvement = child_score - parent_score
        child.fitness_delta, child.metrics_delta = compute_deltas(
            parent, child, self.config.feature_dimensions, self.database.metric_ranges,
            self.config.function_weight, self.config.llm_weight)

        accepted = await self.database.add_async(child)
        total_time = time.time() - iter_start
        result_str = "✓ Improved" if improvement > 0 else "✗ No improvement"
        logger.info(f"{log_prefix} Result: {result_str} ({improvement:+.4f}) "
                    f"{'(accepted)' if accepted else '(rejected)'} [{total_time:.1f}s]")

        return IterationResult(
            iteration=iteration, parent_id=parent.id, child_id=child.id,
            parent_score=parent_score, child_score=child_score, improvement=improvement,
            accepted=accepted, mutation_time=mutation_time, evaluation_time=eval_time,
            total_time=total_time, llm_cost=iteration_cost)

    def _sandbox_provider_env(self) -> Dict[str, str]:
        """LLM provider keys (+ base-url overrides) to forward into the sandbox — only those
        present in the host env. OPENAI_API_BASE lets us route an OpenAI-compatible provider at
        OpenRouter (e.g. Anthropic models via ``openai/anthropic/claude-*``, which sidesteps the
        image's native-Anthropic adapter mis-routing OpenRouter ids to the website)."""
        keys = ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_BASE_URL",
                "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY",
                "DEEPSEEK_API_KEY")
        return {k: os.environ[k] for k in keys if os.environ.get(k)}

    def _build_sandbox_objective(self, parent: Program, iteration: int) -> str:
        parts = [self.objective]
        history = self.prompt_builder.build_evolution_history_section(
            sibling_summaries=self.database.get_sibling_summaries(parent.id),
            ancestor_summaries=self.database.get_ancestor_summaries(parent.id),
            parent_order=parent.order or 0,
            max_siblings=self.config.evolution_history_max_siblings,
            max_ancestors=self.config.evolution_history_max_ancestors,
            max_chars=self.config.evolution_history_max_chars,
        )
        if history:
            parts += ["", "What earlier attempts in this lineage tried "
                      "(learn from these, then improve or do something different):", history]
        return "\n".join(parts)

    def _build_sandbox_inspirations(self, inspirations: List[Program]) -> List[dict]:
        """Turn sampled MAP-Elites elites (other niches) into reference payloads for the sandbox."""
        payload = []
        for ins in inspirations:
            if not ins.snapshot.files:
                continue
            score = ins.fitness_score(
                self.config.feature_dimensions, self.database.metric_ranges,
                self.config.function_weight, self.config.llm_weight)
            payload.append({
                "files": dict(ins.snapshot.files),
                "score": round(score, 4),
                "summary": ins.mutation_summary or "",
            })
        return payload

    async def _run_iteration_sandbox(self, iteration: int, max_iterations: int = 0,
                                     worker_id: Optional[int] = None) -> IterationResult:
        """One iteration where the mutation (agent + tools + eval) runs in an ISOLATED Modal
        sandbox — no host filesystem access. The sandbox worker evaluates the child too, so we
        never run evolved code on the host; its metrics feed QD directly."""
        from pantheon.evolution.sandbox import run_mutation_in_sandbox

        iter_start = time.time()
        log_prefix = f"[Worker {worker_id}]" if worker_id is not None else f"[{iteration + 1}/{max_iterations}]"
        logger.info(f"{log_prefix} Starting iteration (sandbox)...")

        parent, inspirations = await self.database.sample_async(
            num_inspirations=self.config.num_inspirations)
        parent_score = parent.fitness_score(
            self.config.feature_dimensions, self.database.metric_ranges,
            self.config.function_weight, self.config.llm_weight)
        insp_payload = (self._build_sandbox_inspirations(inspirations)
                        if self.config.sandbox_inspirations else None)

        mut_start = time.time()
        try:
            result = await run_mutation_in_sandbox(
                dict(parent.snapshot.files), self.evaluator_code,
                self._build_sandbox_objective(parent, iteration),
                self.config.mutation_system_prompt or MUTATION_AGENT_SYSTEM_PROMPT,
                model=self.config.mutator_model, provider_env=self._sandbox_provider_env(),
                inspirations=insp_payload,
                image_ref=self.config.sandbox_image, timeout=self.config.mutation_timeout,
                tags={"evo_iter": str(iteration),
                      "worker": str(worker_id if worker_id is not None else 0)})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{log_prefix} Sandbox mutation failed: {e}")
            return IterationResult(
                iteration=iteration, parent_id=parent.id, child_id=parent.id,
                parent_score=parent_score, child_score=parent_score, improvement=0, accepted=False,
                total_time=time.time() - iter_start, error=f"sandbox_failed: {str(e)[:120]}")
        mutation_time = time.time() - mut_start

        if not result.get("ok") or not result.get("submitted"):
            reason = result.get("error") or ("no_submit" if result.get("ok") else "sandbox_error")
            logger.warning(f"{log_prefix} No child from sandbox ({reason})")
            return IterationResult(
                iteration=iteration, parent_id=parent.id, child_id=parent.id,
                parent_score=parent_score, child_score=parent_score, improvement=0, accepted=False,
                mutation_time=mutation_time, total_time=time.time() - iter_start, error=reason)

        child_snapshot = CodebaseSnapshot(files=result["child_files"])
        diff_from_parent = child_snapshot.diff_from(parent.snapshot)
        child = Program(id=str(uuid.uuid4())[:8], snapshot=child_snapshot,
                        diff_from_parent=diff_from_parent, parent_id=parent.id,
                        generation=parent.generation + 1, mutation_summary=result.get("summary", ""))
        # metrics come from the sandbox worker's eval — evolved code never runs on the host
        child.metrics = result.get("metrics", {}) or {}
        if child.metrics.get("fitness_weights"):
            self.database._update_metric_ranges(child.metrics)

        child_score = child.fitness_score(
            self.config.feature_dimensions, self.database.metric_ranges,
            self.config.function_weight, self.config.llm_weight)
        improvement = child_score - parent_score
        child.fitness_delta, child.metrics_delta = compute_deltas(
            parent, child, self.config.feature_dimensions, self.database.metric_ranges,
            self.config.function_weight, self.config.llm_weight)
        accepted = await self.database.add_async(child)
        total_time = time.time() - iter_start
        result_str = "✓ Improved" if improvement > 0 else "✗ No improvement"
        logger.info(f"{log_prefix} Result: {result_str} ({improvement:+.4f}) "
                    f"{'(accepted)' if accepted else '(rejected)'} [{total_time:.1f}s] "
                    f"sandbox={result.get('sandbox')}")
        return IterationResult(
            iteration=iteration, parent_id=parent.id, child_id=child.id,
            parent_score=parent_score, child_score=child_score, improvement=improvement,
            accepted=accepted, mutation_time=mutation_time, total_time=total_time)

    async def _create_analyzer(self, generation: int):
        """
        Create analyzer agent with generation-appropriate system prompt.

        The analyzer's optimization direction (exploration vs exploitation) is
        probabilistically determined based on generation. Early generations favor
        algorithm-level exploration; later generations favor implementation-level
        exploitation.

        If user provided a custom analyzer at init time, returns that instead
        (with direction="custom" and probability=0.0).

        Optionally includes Python interpreter capability when config.analyzer_use_python=True.

        Args:
            generation: Current program generation for adaptive prompt selection

        Returns:
            Tuple of (analyzer_agent, direction, exploration_probability)
        """
        # If user provided custom analyzer, use it without adaptive prompts
        if self._analyzer is not None:
            return self._analyzer, "custom", 0.0

        from pantheon.agent import Agent

        # Get adaptive system prompt based on generation (with Python section if enabled)
        system_prompt, direction, exploration_prob = self.prompt_builder.get_analyzer_system_prompt(
            generation=generation,
            initial_prob=self.config.analyzer_exploration_initial,
            final_prob=self.config.analyzer_exploration_final,
            decay_generations=self.config.analyzer_exploration_decay_generations,
            use_python=self.config.analyzer_use_python,
        )

        analyzer = Agent(
            name="code-analyzer",
            instructions=system_prompt,
            model=self.config.analyzer_model,
            tools=[think],
            use_memory=False,  # Prevent context accumulation across iterations
        )

        # Add Python interpreter toolset if enabled
        if self.config.analyzer_use_python:
            if self._python_toolset is None:
                from pantheon.apps.builtin.python import PythonInterpreterToolSet

                workdir = self.config.analyzer_python_workdir or self.config.workspace_path
                self._python_toolset = PythonInterpreterToolSet(
                    name="analyzer-python",
                    workdir=workdir,
                )
            await analyzer.toolset(self._python_toolset)

        return analyzer, direction, exploration_prob

    async def _cleanup_python_interpreters(self):
        """
        Clean up Python interpreters to prevent process accumulation.

        Each analyzer run creates a new interpreter (due to unique client_id).
        This method cleans up all interpreters to prevent LokyProcess accumulation.
        """
        if self._python_toolset is None:
            return

        try:
            # Get list of all interpreters
            result = await self._python_toolset.list_interpreters()
            interpreters = result.get("interpreters", [])

            # Delete each interpreter
            for interp in interpreters:
                try:
                    await self._python_toolset.delete_interpreter(interp["id"])
                except Exception as e:
                    logger.debug(f"Failed to delete interpreter {interp['id']}: {e}")

            # Clear the client_id mapping
            self._python_toolset.clientid_to_interpreterid.clear()

            logger.debug(f"Cleaned up {len(interpreters)} Python interpreters")
        except Exception as e:
            logger.warning(f"Failed to cleanup Python interpreters: {e}")

    def _create_summarizer(self):
        """
        Create summarizer agent for extracting exploration directions.

        The summarizer is a lightweight agent that extracts structured
        direction information from analyzer output.

        Returns:
            Summarizer agent
        """
        from pantheon.agent import Agent

        return Agent(
            name="direction-summarizer",
            instructions=SUMMARIZER_SYSTEM_PROMPT,
            model="low",  # Use low-cost model for summarization
            use_memory=False,
        )

    async def _extract_direction(
        self,
        analysis_text: str,
        diff_text: str = "",
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Extract exploration direction from analyzer output and diff using summarizer.

        The summarizer analyzes both the proposed changes (analysis) and the actual
        code changes (diff) to determine what was actually implemented.

        Args:
            analysis_text: The analyzer's output text with proposed changes
            diff_text: The actual code diff that was applied
            timeout: Timeout for summarizer call

        Returns:
            Dict with keys: direction, category, is_algorithmic, match_confidence
            Returns default values if extraction fails
        """
        default_result = {
            "direction": "No clear direction proposed",
            "category": "other",
            "is_algorithmic": False,
            "match_confidence": "low",
        }

        if not analysis_text or len(analysis_text.strip()) < 20:
            return default_result

        try:
            summarizer = self._create_summarizer()

            # Build prompt with both analysis and diff
            prompt_parts = ["## ANALYSIS (proposed changes):", analysis_text]

            if diff_text and diff_text.strip():
                # Truncate diff if too long
                max_diff_len = 3000
                if len(diff_text) > max_diff_len:
                    diff_text = diff_text[:max_diff_len] + "\n... (truncated)"
                prompt_parts.append("\n## DIFF (actual code changes):")
                prompt_parts.append(diff_text)
            else:
                prompt_parts.append("\n## DIFF (actual code changes):")
                prompt_parts.append("(No code changes were made)")

            prompt_parts.append("\n## Task:")
            prompt_parts.append("Identify which proposed direction was actually implemented in the diff.")

            prompt = "\n".join(prompt_parts)

            response = await asyncio.wait_for(
                summarizer.run(prompt, update_memory=False),
                timeout=timeout
            )

            # Parse JSON from response
            content = response.content.strip()
            # Handle potential markdown code blocks
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])  # Remove first and last lines

            result = json.loads(content)

            # Validate required fields
            if "direction" not in result:
                result["direction"] = default_result["direction"]
            if "category" not in result:
                result["category"] = default_result["category"]
            if "is_algorithmic" not in result:
                result["is_algorithmic"] = default_result["is_algorithmic"]
            if "match_confidence" not in result:
                result["match_confidence"] = default_result["match_confidence"]

            return result

        except asyncio.TimeoutError:
            logger.debug("Summarizer timeout, using default direction")
            return default_result
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse summarizer JSON: {e}")
            return default_result
        except Exception as e:
            logger.debug(f"Direction extraction failed: {e}")
            return default_result

    def _classify_result(
        self,
        score_delta: float,
        has_error: bool = False,
    ) -> str:
        """
        Classify the result of an exploration attempt.

        Args:
            score_delta: Score change (child - parent)
            has_error: Whether evaluation had an error

        Returns:
            One of: "success", "marginal", "neutral", "failure", "error"
        """
        if has_error:
            return "error"

        # Thresholds for classification
        if score_delta > 0.01:  # > +1%
            return "success"
        elif score_delta > 0:  # 0 to +1%
            return "marginal"
        elif score_delta > -0.01:  # -1% to 0
            return "neutral"
        else:  # < -1%
            return "failure"

    async def _ensure_evaluator(self):
        """Ensure evaluator is initialized."""
        if self._evaluator is None:
            self._evaluator = HybridEvaluator(
                evaluator_code=self.evaluator_code,
                function_weight=self.config.function_weight,
                llm_weight=self.config.llm_weight,
                max_parallel=self.config.max_parallel_evaluations,
                timeout=self.config.evaluation_timeout,
                workspace_base=self.config.workspace_path,
                feedback_max_lines_per_file=self.config.feedback_max_lines_per_file,
            )
        return self._evaluator

    async def evolve(
        self,
        initial_code: Union[str, CodebaseSnapshot],
        evaluator_code: str,
        objective: str,
        max_iterations: Optional[int] = None,
        initial_path: Optional[str] = None,
        resume_from: Optional[str] = None,
        progress_callback: Optional[callable] = None,
        **kwargs,
    ) -> EvolutionResult:
        """
        Run the evolution loop.

        Args:
            initial_code: Initial code string or CodebaseSnapshot
            evaluator_code: Python code defining evaluate(workspace_path) function
            objective: Natural language optimization objective
            max_iterations: Override config max_iterations
            initial_path: Path for loading initial codebase (if initial_code is path)
            resume_from: Path to resume from (directory with evolution_state.json)
            progress_callback: Optional callback(iteration: int, best_score: float) for progress updates
            **kwargs: Additional arguments

        Returns:
            EvolutionResult with best program and history
        """
        max_iterations = max_iterations or self.config.max_iterations
        self.objective = objective
        self.evaluator_code = evaluator_code
        self.progress_callback = progress_callback  # Store callback for use in checkpoints

        # Initialize result
        result = EvolutionResult(
            config_used=self.config.to_dict(),
        )

        # Check if resuming from checkpoint
        start_iteration = 0
        best_score = 0
        generations_without_improvement = 0

        if resume_from:
            resume_path = Path(resume_from)
            state_file = resume_path / "evolution_state.json"

            if state_file.exists():
                logger.info(f"Resuming evolution from {resume_from}")

                # Load database
                self.database = EvolutionDatabase.load(str(resume_path))

                # Load evolution state
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)

                start_iteration = state.get("current_iteration", 0) + 1
                best_score = state.get("best_score", 0)
                result.score_history = state.get("score_history", [])
                result.best_score_history = state.get("best_score_history", [])
                generations_without_improvement = state.get("generations_without_improvement", 0)

                # Restore objective and evaluator if not provided
                if not objective:
                    self.objective = state.get("objective", "")
                if not evaluator_code:
                    self.evaluator_code = state.get("evaluator_code", "")

                logger.info(f"Resumed from iteration {start_iteration}, best_fitness_score={best_score:.4f} (normalized)")
                logger.info(f"Database has {len(self.database.programs)} programs")

                # Find initial_program (order=0) and best_program from database
                initial_program = None
                best_program = None
                best_program_id = self.database.best_program_id

                for prog in self.database.programs.values():
                    if prog.order == 0:
                        initial_program = prog
                    if prog.id == best_program_id:
                        best_program = prog

                if initial_program is None:
                    # Fallback: use first program added
                    initial_program = min(self.database.programs.values(), key=lambda p: p.order)
                if best_program is None:
                    # Fallback: find program with highest fitness
                    best_program = max(
                        self.database.programs.values(),
                        key=lambda p: p.fitness_score(
                            self.config.feature_dimensions,
                            self.database.metric_ranges,
                            self.config.function_weight,
                            self.config.llm_weight,
                        )
                    )
            else:
                logger.warning(f"No evolution_state.json found in {resume_from}, starting fresh")
                resume_from = None

        if not resume_from:
            # Create initial snapshot
            if isinstance(initial_code, CodebaseSnapshot):
                initial_snapshot = initial_code
            elif initial_path:
                initial_snapshot = CodebaseSnapshot.from_directory(initial_path)
            else:
                # Single file code
                initial_snapshot = CodebaseSnapshot.from_single_file("main.py", initial_code)

            # Create and evaluate initial program
            initial_program = Program(
                id=str(uuid.uuid4())[:8],
                snapshot=initial_snapshot,
                generation=0,
            )

            logger.info(f"Starting evolution with {initial_program.file_count()} files, "
                       f"{initial_program.total_lines()} lines")

            # Evaluate initial program
            evaluator = await self._ensure_evaluator()
            eval_result = await evaluator.evaluate(initial_program)

            initial_program.metrics = eval_result.metrics
            initial_program.artifacts = eval_result.artifacts
            initial_program.llm_feedback = eval_result.llm_feedback

            # Keep fitness_weights for dynamic function_score calculation
            fitness_weights = initial_program.metrics.get("fitness_weights")
            if fitness_weights:
                # Update metric_ranges (for normalization in fitness_score)
                self.database._update_metric_ranges(initial_program.metrics)

            # Auto-detect feature_dimensions from evaluator metrics if not configured
            if not self.config.feature_dimensions and fitness_weights:
                metric_keys = [
                    k for k in fitness_weights.keys()
                    if k in initial_program.metrics and isinstance(initial_program.metrics[k], (int, float))
                ]
                if len(metric_keys) >= 2:
                    self.config.feature_dimensions = metric_keys[:2]
                    logger.info(f"Auto-detected feature dimensions from evaluator: {self.config.feature_dimensions}")
                else:
                    # Fallback to code-based features
                    self.config.feature_dimensions = ["complexity", "diversity"]
                    logger.info(f"Using default feature dimensions: {self.config.feature_dimensions}")
            elif not self.config.feature_dimensions:
                self.config.feature_dimensions = ["complexity", "diversity"]

            self.database.add(initial_program)

            initial_score = initial_program.fitness_score(
                self.config.feature_dimensions,
                self.database.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )
            result.score_history.append(initial_score)
            result.best_score_history.append(initial_score)
            best_score = initial_score
            best_program = initial_program  # Track best program for consistent fitness comparison

            # Log raw metrics for initial program
            initial_metrics_str = format_metrics_for_log(initial_program.metrics)
            logger.info(f"Initial program: {initial_metrics_str}")

        # Evolution loop - use workers if num_workers > 1
        if self.config.num_workers > 1:
            # Parallel worker-based evolution
            logger.info(f"Starting parallel evolution with {self.config.num_workers} workers")

            # Shared atomic counter for iteration numbers
            iteration_lock = asyncio.Lock()
            iteration_state = {"next": start_iteration}

            async def get_next_iteration():
                """Atomically get and increment iteration counter."""
                async with iteration_lock:
                    val = iteration_state["next"]
                    iteration_state["next"] += 1
                    return val

            result_queue = asyncio.Queue()

            # Start workers
            workers = [
                asyncio.create_task(
                    self._worker(i, get_next_iteration, max_iterations, result_queue)
                )
                for i in range(self.config.num_workers)
            ]

            # Collect results as they come in
            completed_iterations = 0
            target_iterations = max_iterations - start_iteration

            while completed_iterations < target_iterations:
                try:
                    iter_result = await asyncio.wait_for(result_queue.get(), timeout=300)
                    result.iteration_results.append(iter_result)
                    completed_iterations += 1

                    # Track scores
                    result.score_history.append(iter_result.child_score)

                    is_new_best = False
                    # Recompute best_program's fitness using current metric_ranges
                    # This ensures consistent comparison as ranges expand during evolution
                    current_best_score = best_program.fitness_score(
                        self.config.feature_dimensions,
                        self.database.metric_ranges,
                        self.config.function_weight,
                        self.config.llm_weight,
                    )
                    if iter_result.child_score > current_best_score:
                        best_score = iter_result.child_score
                        best_program = self.database.programs[iter_result.child_id]
                        generations_without_improvement = 0
                        is_new_best = True
                    else:
                        generations_without_improvement += 1

                    result.best_score_history.append(best_score)

                    # Log every iteration with clear progress
                    progress_pct = completed_iterations / target_iterations * 100
                    status = "★ NEW BEST" if is_new_best else ("✓ accepted" if iter_result.accepted else "✗ rejected")
                    # Get raw metrics for logging
                    child_program = self.database.programs.get(iter_result.child_id)
                    child_metrics_str = format_metrics_for_log(child_program.metrics) if child_program else "?"
                    best_metrics_str = format_metrics_for_log(best_program.metrics)
                    logger.info(
                        f"[{completed_iterations}/{target_iterations}] ({progress_pct:.0f}%) "
                        f"iter={iter_result.iteration} child=[{child_metrics_str}] "
                        f"best=[{best_metrics_str}] {status}"
                    )

                    # Periodic summary (every 10 iterations)
                    if completed_iterations % 10 == 0:
                        stats = self.database.get_statistics()
                        initial_metrics_str = format_metrics_for_log(initial_program.metrics)
                        logger.info(
                            f"=== Summary: {completed_iterations}/{target_iterations} complete, "
                            f"initial=[{initial_metrics_str}], best=[{best_metrics_str}], "
                            f"programs={stats['total_programs']} ==="
                        )

                    # Trigger progress callback on every iteration (independent of checkpoint)
                    if self.progress_callback:
                        self.progress_callback(
                            start_iteration + completed_iterations,
                            best_score
                        )

                    # Periodic checkpoint
                    if self.config.db_path and completed_iterations % self.config.checkpoint_interval == 0:
                        self._save_checkpoint(
                            self.config.db_path,
                            start_iteration + completed_iterations,
                            best_score,
                            result.score_history,
                            result.best_score_history,
                            generations_without_improvement,
                        )

                    # Early stopping check
                    if generations_without_improvement >= self.config.early_stop_generations:
                        logger.info(
                            f"Early stopping: no improvement for "
                            f"{generations_without_improvement} iterations"
                        )
                        break

                except asyncio.TimeoutError:
                    logger.warning("Waiting for worker results...")
                    continue

            # Cancel remaining workers
            for worker in workers:
                worker.cancel()

            # Wait for workers to finish
            await asyncio.gather(*workers, return_exceptions=True)

        else:
            # Sequential evolution (original behavior)
            for iteration in range(start_iteration, max_iterations):
                try:
                    if self.config.sandbox_mutation:
                        iter_result = await self._run_iteration_sandbox(iteration, max_iterations)
                    elif self.config.single_agent_mutation:
                        iter_result = await self._run_iteration_single_agent(iteration, max_iterations)
                    else:
                        iter_result = await self._run_iteration(iteration, max_iterations)
                    result.iteration_results.append(iter_result)

                    # Track scores
                    result.score_history.append(iter_result.child_score)

                    # Recompute best_program's fitness using current metric_ranges
                    # This ensures consistent comparison as ranges expand during evolution
                    current_best_score = best_program.fitness_score(
                        self.config.feature_dimensions,
                        self.database.metric_ranges,
                        self.config.function_weight,
                        self.config.llm_weight,
                    )
                    if iter_result.child_score > current_best_score:
                        best_score = iter_result.child_score
                        best_program = self.database.programs[iter_result.child_id]
                        generations_without_improvement = 0
                        best_metrics_str = format_metrics_for_log(best_program.metrics)
                        logger.info(
                            f"  ★ New best: [{best_metrics_str}]"
                        )
                    else:
                        generations_without_improvement += 1

                    result.best_score_history.append(best_score)

                    # Periodic logging
                    if self.config.log_iterations and iteration % 10 == 0 and iteration > 0:
                        stats = self.database.get_statistics()
                        initial_metrics_str = format_metrics_for_log(initial_program.metrics)
                        best_metrics_str = format_metrics_for_log(best_program.metrics)
                        logger.info(
                            f"--- Progress: {iteration}/{max_iterations}, initial=[{initial_metrics_str}], "
                            f"best=[{best_metrics_str}], programs={stats['total_programs']} ---"
                        )

                    # Trigger progress callback on every iteration (independent of checkpoint)
                    if self.progress_callback:
                        self.progress_callback(iteration, best_score)

                    # Periodic migration
                    if iteration > 0 and iteration % self.config.migration_interval == 0:
                        self.database.migrate()

                    # Periodic checkpoint
                    if self.config.db_path and iteration % self.config.checkpoint_interval == 0:
                        self._save_checkpoint(
                            self.config.db_path,
                            iteration,
                            best_score,
                            result.score_history,
                            result.best_score_history,
                            generations_without_improvement,
                        )

                    # Early stopping
                    if generations_without_improvement >= self.config.early_stop_generations:
                        logger.info(
                            f"Early stopping: no improvement for "
                            f"{generations_without_improvement} generations"
                        )
                        break

                except Exception as e:
                    logger.error(f"Iteration {iteration} failed: {e}")
                    result.errors.append(f"Iteration {iteration}: {e}")
                    result.iteration_results.append(
                        IterationResult(
                            iteration=iteration,
                            parent_id="",
                            child_id="",
                            parent_score=0,
                            child_score=0,
                            improvement=0,
                            accepted=False,
                            error=str(e),
                        )
                    )
                    # Notify progress callback about the failed iteration
                    if self.progress_callback:
                        self.progress_callback(iteration, best_score, error=str(e))

        # Finalize result
        result.total_iterations = len(result.iteration_results)
        result.best_program = self.database.get_best_program()
        result.best_score = best_score
        result.database = self.database
        result.finalize()

        # Save final checkpoint
        if self.config.db_path:
            # Compute final iteration number
            final_iteration = start_iteration + len(result.iteration_results) - 1
            
            # Trigger final progress callback
            if self.progress_callback:
                self.progress_callback(final_iteration, best_score)
            
            self._save_checkpoint(
                self.config.db_path,
                final_iteration,
                best_score,
                result.score_history,
                result.best_score_history,
                generations_without_improvement,
            )

        logger.info(result.get_summary())

        return result

    def _save_checkpoint(
        self,
        path: str,
        iteration: int,
        best_score: float,
        score_history: List[float],
        best_score_history: List[float],
        generations_without_improvement: int,
    ) -> None:
        """Save evolution checkpoint including state for resume."""
        # Save database
        self.database.save(path)

        # Save evolution state
        state = {
            "current_iteration": iteration,
            "best_score": best_score,
            "score_history": score_history,
            "best_score_history": best_score_history,
            "generations_without_improvement": generations_without_improvement,
            "objective": self.objective,
            "evaluator_code": self.evaluator_code,
            "max_iterations": self.config.max_iterations,
            "num_islands": self.config.num_islands,
            "mutator_model": self.config.mutator_model,
            "created_at": time.time(),  # For session restoration
        }

        state_path = Path(path) / "evolution_state.json"
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        logger.info(f"Checkpoint saved: iteration {iteration}, {len(self.database.programs)} programs")

    async def _run_iteration(
        self,
        iteration: int,
        max_iterations: int = 0,
        worker_id: Optional[int] = None,
    ) -> IterationResult:
        """
        Run a single evolution iteration.

        Args:
            iteration: Current iteration number
            max_iterations: Total iterations for logging progress
            worker_id: Worker ID for parallel execution (None for sequential)

        Returns:
            IterationResult with details
        """
        iter_start = time.time()
        log_prefix = f"[Worker {worker_id}]" if worker_id is not None else f"[{iteration + 1}/{max_iterations}]"
        logger.info(f"{log_prefix} Starting iteration...")

        # Sample parent and inspirations (thread-safe)
        parent, inspirations = await self.database.sample_async(
            num_inspirations=self.config.num_inspirations,
        )

        parent_score = parent.fitness_score(
            self.config.feature_dimensions,
            self.database.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )

        # Get top programs for reference (thread-safe)
        async with self.database._lock:
            top_programs = self.database.get_top_programs(
                n=self.config.num_top_programs,
            )

        # Apply probability filtering for context sections
        use_top_programs = random.random() < self.config.top_programs_probability
        use_inspirations = random.random() < self.config.inspirations_probability

        effective_top_programs = top_programs if use_top_programs else None
        effective_inspirations = inspirations if use_inspirations else None

        # Build prompt (with or without analyzer)
        analysis_text = ""  # Store analyzer output for program record
        analysis_prompt = ""  # Store analyzer prompt for program record
        analyzer_direction = ""  # Track exploration vs exploitation direction
        extracted_direction = None  # Direction info from summarizer
        iteration_cost = 0.0  # Track LLM cost for this iteration
        if self.config.use_analyzer:
            # === Analyzer Phase (full context) ===
            analysis_start = time.time()
            try:
                # Create analyzer with generation-adaptive prompt
                analyzer, analyzer_direction, exploration_prob = await self._create_analyzer(
                    generation=parent.generation
                )

                # Build evolution history from sibling and ancestor summaries
                sibling_summaries = self.database.get_sibling_summaries(parent.id)
                ancestor_summaries = self.database.get_ancestor_summaries(parent.id)
                evolution_history_text = self.prompt_builder.build_evolution_history_section(
                    sibling_summaries=sibling_summaries,
                    ancestor_summaries=ancestor_summaries,
                    parent_order=parent.order or 0,
                    max_siblings=self.config.evolution_history_max_siblings,
                    max_ancestors=self.config.evolution_history_max_ancestors,
                    max_chars=self.config.evolution_history_max_chars,
                )

                analysis_prompt = self.prompt_builder.build_analysis_prompt(
                    parent=parent,
                    objective=self.objective,
                    top_programs=effective_top_programs,
                    inspirations=effective_inspirations,
                    artifacts=parent.artifacts,
                    iteration=iteration,
                    exploration_history=evolution_history_text,  # Always include history
                    metric_ranges=self.database.metric_ranges,
                    feature_dimensions=self.config.feature_dimensions,
                    function_weight=self.config.function_weight,
                    llm_weight=self.config.llm_weight,
                )
                # analysis_prompt is already stored above for program record
                analysis_response = await asyncio.wait_for(
                    analyzer.run(analysis_prompt, update_memory=False),
                    timeout=self.config.analyzer_timeout
                )
                analysis_text = analysis_response.content
                iteration_cost += extract_cost_from_response(analysis_response)
                analysis_time = time.time() - analysis_start
                logger.info(
                    f"{log_prefix} Analysis ({analyzer_direction}, p={exploration_prob:.2f}): "
                    f"{analysis_time:.1f}s (${iteration_cost:.4f})"
                )
                # Cleanup Python interpreters to prevent process accumulation
                await self._cleanup_python_interpreters()
                # Note: Direction extraction moved to after mutation to include diff
            except asyncio.TimeoutError:
                analysis_time = time.time() - analysis_start
                logger.warning(f"{log_prefix} Analyzer timeout after {analysis_time:.1f}s, skipping iteration")
                await self._cleanup_python_interpreters()
                return IterationResult(
                    iteration=iteration,
                    parent_id=parent.id,
                    child_id=parent.id,
                    parent_score=parent_score,
                    child_score=parent_score,
                    improvement=0,
                    accepted=False,
                    mutation_time=0,
                    evaluation_time=0,
                    total_time=time.time() - iter_start,
                    error="analyzer_timeout",
                )
            except Exception as e:
                logger.warning(f"{log_prefix} Analyzer failed: {e}, skipping iteration")
                await self._cleanup_python_interpreters()
                return IterationResult(
                    iteration=iteration,
                    parent_id=parent.id,
                    child_id=parent.id,
                    parent_score=parent_score,
                    child_score=parent_score,
                    improvement=0,
                    accepted=False,
                    mutation_time=0,
                    evaluation_time=0,
                    total_time=time.time() - iter_start,
                    error=f"analyzer_failed: {e}",
                )

            # === Mutator Phase (code + instructions only) ===
            prompt = self.prompt_builder.build_simple_mutation_prompt(
                parent=parent,
                analysis=analysis_text,
            )
        else:
            # Original behavior: mutator gets full context
            prompt = self.prompt_builder.build_mutation_prompt(
                parent=parent,
                objective=self.objective,
                top_programs=effective_top_programs,
                inspirations=effective_inspirations,
                artifacts=parent.artifacts,
                iteration=iteration,
                metric_ranges=self.database.metric_ranges,
                feature_dimensions=self.config.feature_dimensions,
                function_weight=self.config.function_weight,
                llm_weight=self.config.llm_weight,
            )

        # Generate mutation with timeout
        mutation_start = time.time()
        mutator = await self._ensure_mutator()

        try:
            response = await asyncio.wait_for(
                mutator.run(prompt, update_memory=False),
                timeout=self.config.mutation_timeout
            )
            mutation_cost = extract_cost_from_response(response)
            iteration_cost += mutation_cost
            mutation_time = time.time() - mutation_start
            logger.info(f"{log_prefix} Mutation: {mutation_time:.1f}s (${mutation_cost:.4f})")
        except asyncio.TimeoutError:
            mutation_time = time.time() - mutation_start
            logger.warning(f"{log_prefix} Mutation timeout after {mutation_time:.1f}s")
            return IterationResult(
                iteration=iteration,
                parent_id=parent.id,
                child_id=parent.id,
                parent_score=parent_score,
                child_score=parent_score,
                improvement=0,
                accepted=False,
                mutation_time=mutation_time,
                evaluation_time=0,
                total_time=time.time() - iter_start,
                error="mutation_timeout",
                llm_cost=iteration_cost,
            )

        # Apply mutation
        mutation_applied = False  # Track if code actually changed
        try:
            child_snapshot = self._apply_mutation(parent.snapshot, response.content)
            default_file = next(iter(parent.snapshot.files.keys()), "main.py")
            changes = parse_diff(response.content, default_file)
            logger.info(f"{log_prefix} Applied {len(changes)} change(s)")
        except Exception as e:
            logger.warning(f"{log_prefix} Failed to apply mutation: {e}")
            child_snapshot = parent.snapshot

        # Check if code actually changed
        diff_from_parent = child_snapshot.diff_from(parent.snapshot)
        if diff_from_parent and diff_from_parent.strip():
            mutation_applied = True
        else:
            logger.warning(f"{log_prefix} Mutation produced no code changes (SEARCH blocks may not have matched)")

        # Create child program
        child = Program(
            id=str(uuid.uuid4())[:8],
            snapshot=child_snapshot,
            diff_from_parent=diff_from_parent,
            parent_id=parent.id,
            generation=parent.generation + 1,
            mutator_prompt_used=prompt if self.config.save_prompts else "",
            analysis_prompt_used=analysis_prompt if self.config.save_prompts else "",
            analysis_used=analysis_text if self.config.save_prompts else "",
        )

        # Evaluate child
        eval_start = time.time()
        evaluator = await self._ensure_evaluator()
        eval_result = await evaluator.evaluate(child)
        eval_time = time.time() - eval_start

        child.metrics = eval_result.metrics
        child.artifacts = eval_result.artifacts
        child.llm_feedback = eval_result.llm_feedback

        # Extract fitness_weights from evaluator (keep it for dynamic function_score calculation)
        fitness_weights = child.metrics.get("fitness_weights")
        if fitness_weights:
            # Update metric_ranges first (for normalization)
            self.database._update_metric_ranges(child.metrics)

        child_score = child.fitness_score(
            self.config.feature_dimensions,
            self.database.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )
        improvement = child_score - parent_score

        # Log evaluation results
        logger.info(f"{log_prefix} Evaluation: {eval_time:.1f}s, score: {child_score:.4f}")

        # Log key metrics if available
        metric_parts = []
        for key in ['mixing_score', 'speed_score', 'function_score']:
            if key in child.metrics:
                metric_parts.append(f"{key.replace('_score', '')}={child.metrics[key]:.3f}")
        if metric_parts:
            logger.info(f"{log_prefix} Metrics: {', '.join(metric_parts)}")

        # Extract direction using summarizer (always, not just exploration mode)
        if analysis_text and mutation_applied:
            extracted_direction = await self._extract_direction(
                analysis_text,
                diff_text=diff_from_parent or "",
                timeout=self.config.summarizer_timeout,
            )
            # Store mutation summary in child program
            if extracted_direction.get("direction") not in ("No clear direction proposed", "No implementation found"):
                child.mutation_summary = extracted_direction.get("direction", "")
                child.mutation_category = extracted_direction.get("category", "other")
                child.is_algorithmic = extracted_direction.get("is_algorithmic", True)

            logger.debug(
                f"{log_prefix} Direction extracted: '{extracted_direction.get('direction', 'N/A')[:50]}...' "
                f"(confidence: {extracted_direction.get('match_confidence', 'N/A')})"
            )

        # Compute fitness and metrics deltas
        child.fitness_delta, child.metrics_delta = compute_deltas(
            parent,
            child,
            self.config.feature_dimensions,
            self.database.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )

        # Add to database (thread-safe)
        accepted = await self.database.add_async(child)

        total_time = time.time() - iter_start

        # Log result
        result_str = "✓ Improved" if improvement > 0 else "✗ No improvement"
        accepted_str = "(accepted)" if accepted else "(rejected)"
        cost_str = f"${iteration_cost:.4f}" if iteration_cost > 0 else ""
        logger.info(f"{log_prefix} Result: {result_str} ({improvement:+.4f}) {accepted_str} [{total_time:.1f}s] {cost_str}")

        return IterationResult(
            iteration=iteration,
            parent_id=parent.id,
            child_id=child.id,
            parent_score=parent_score,
            child_score=child_score,
            improvement=improvement,
            accepted=accepted,
            mutation_time=mutation_time,
            evaluation_time=eval_time,
            total_time=total_time,
            llm_cost=iteration_cost,
        )

    async def _worker(
        self,
        worker_id: int,
        get_next_iteration,
        max_iterations: int,
        result_queue: asyncio.Queue,
    ) -> None:
        """
        Single worker that runs evolution iterations independently.

        Args:
            worker_id: Identifier for this worker
            get_next_iteration: Async function to get next iteration number
            max_iterations: Stop when counter reaches this value
            result_queue: Queue to put iteration results
        """
        while True:
            # Get next iteration number atomically
            iteration = await get_next_iteration()

            if iteration >= max_iterations:
                break

            logger.info(f"[Worker {worker_id}] Starting iteration {iteration + 1}/{max_iterations}")

            try:
                if self.config.sandbox_mutation:
                    iter_result = await self._run_iteration_sandbox(iteration, max_iterations, worker_id=worker_id)
                elif self.config.single_agent_mutation:
                    iter_result = await self._run_iteration_single_agent(iteration, max_iterations, worker_id=worker_id)
                else:
                    iter_result = await self._run_iteration(iteration, max_iterations, worker_id=worker_id)
                await result_queue.put(iter_result)
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Iteration {iteration} failed: {e}")
                await result_queue.put(IterationResult(
                    iteration=iteration,
                    parent_id="",
                    child_id="",
                    parent_score=0,
                    child_score=0,
                    improvement=0,
                    accepted=False,
                    error=str(e),
                ))

    def _apply_mutation(
        self,
        parent_snapshot: CodebaseSnapshot,
        mutation_response: str,
    ) -> CodebaseSnapshot:
        """
        Apply LLM mutation response to parent snapshot.

        Args:
            parent_snapshot: Parent codebase snapshot
            mutation_response: LLM response with SEARCH/REPLACE blocks

        Returns:
            New CodebaseSnapshot with mutations applied
        """
        # Parse changes from response
        default_file = next(iter(parent_snapshot.files.keys()), "main.py")
        changes = parse_diff(mutation_response, default_file)

        if not changes:
            logger.warning("No valid changes parsed from mutation response")
            return parent_snapshot

        # Apply changes
        new_files = apply_diff(parent_snapshot.files, changes)

        return CodebaseSnapshot(
            files=new_files,
            base_path=parent_snapshot.base_path,
        )


async def evolve(
    initial_code: Union[str, CodebaseSnapshot],
    evaluator_code: str,
    objective: str,
    config: Optional[EvolutionConfig] = None,
    **kwargs,
) -> EvolutionResult:
    """
    Convenience function to run evolution.

    Args:
        initial_code: Initial code or CodebaseSnapshot
        evaluator_code: Evaluation function code
        objective: Optimization objective
        config: Evolution configuration
        **kwargs: Additional arguments

    Returns:
        EvolutionResult
    """
    team = EvolutionTeam(config=config)
    return await team.evolve(
        initial_code=initial_code,
        evaluator_code=evaluator_code,
        objective=objective,
        **kwargs,
    )
