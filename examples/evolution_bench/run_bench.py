#!/usr/bin/env python
"""Run any method against any task in `tasks/`.

`run_v2.py` next door is the Erdos runner with the problem baked into it -- the seed filename, the
objective, the metric. Adding a second problem by copying it would give two files that drift, so
this one takes the problem as data:

    tasks/<name>/task.json      optional: which file is evolved (default solution.py) and env
    tasks/<name>/evaluator.py   evaluate(workspace_path) -> {combined_score, validity, ...}
    tasks/<name>/objective.md   what the agent is told

    python run_bench.py --task hadamard29 --method annealed --iterations 40
    python run_bench.py --task sums_diffs --method agent_map_elites --iterations 120

The point of the first runs is not a score. It is whether the score is still climbing at the end of
the budget: Erdos put every arm within 0.0005 of every other across twenty runs, and circle packing
reached the published record on its second iteration. A benchmark that saturates cannot separate
two search policies, and finding that out costs one long run rather than six.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE.parent.parent))
TASKS = HERE / "tasks"


def build_method(name: str, seed: int, judge=None, norm: str = "minmax", sched=None,
                 low_fidelity: bool = False):
    from pantheon.evolution.methods import (
        AnnealedIdeaCode, HypothesisBandit, IdeaCodeAlternating, AgentMapElites,
        LabNotebook, SimpleTES)

    if name in ("hypothesis_bandit", "pantheon_evo"):   # old token accepted
        # low_fidelity follows the task: staged promotion only where a cheap fidelity exists.
        return HypothesisBandit(seed=seed, low_fidelity=low_fidelity)

    if name == "annealed":
        # `sched` carries only the knobs the caller actually set, so the method's own defaults
        # stay authoritative for everything else. The ablations pin one at a time: `gamma=0`
        # freezes the action mix, `t0=t1` holds the selection temperature, `beta0=0` removes the
        # exploration bonus.
        return AnnealedIdeaCode(judge=judge, norm=norm, seed=seed, **(sched or {}))
    if name == "idea_code":
        return IdeaCodeAlternating(ideas_per_round=3, code_per_idea=3, ideas_kept=2,
                                   k_ideas=1, k_code=1, seed=seed)
    if name in ("agent_map_elites", "niche_menu", "map_elites"):   # old tokens accepted
        return AgentMapElites(num_islands=2, feature_dimensions=["complexity", "diversity"],
                                feature_bins=6, exploration_ratio=0.2, num_inspirations=2,
                                migration_interval=8, function_weight=1.0, llm_weight=0.0,
                                seed=seed)
    if name == "simpletes":
        return SimpleTES(num_chains=2, k_candidates=2, num_inspirations=2,
                         selector="rpucg", seed=seed)
    if name == "lab_notebook":
        # Inner steps use SimpleTES's k=2 / 2-inspiration settings above, so the two arms differ
        # only in the outer loop: K short trajectories per cycle instead of two long chains.
        return LabNotebook(ideas_per_cycle=4, steps_per_trajectory=2, k_candidates=2,
                           num_inspirations=2, seed=seed)
    raise SystemExit(f"unknown method {name!r}")


async def main(a) -> None:
    from pantheon.evolution.core import Budget, CodeGenome
    from pantheon.evolution.core.loop import evolve
    from pantheon.evolution.variators import (
        AgentVariator, CodeEvaluator, CompletionVariator)

    task_dir = TASKS / a.task
    if not task_dir.is_dir():
        raise SystemExit(f"no task {a.task!r} in {TASKS} "
                         f"(have: {', '.join(sorted(p.name for p in TASKS.iterdir()))})")
    # A task says which file it evolves. Hardcoding `solution.py` was fine while every task was
    # Python; AHC039 evolves C++, and a runner that assumes the extension silently seeds the run
    # with an empty genome.
    cfg = {}
    if (task_dir / "task.json").exists():
        cfg = json.loads((task_dir / "task.json").read_text())
    evolve_file = cfg.get("evolve", "solution.py")
    for k, v in (cfg.get("env") or {}).items():
        os.environ.setdefault(k, str(v))
    # An evaluator is exec()d, not imported, so it cannot locate its own directory from __file__.
    # Anything it needs from beside itself -- cached inputs, a helper script, a data file -- has to
    # be told to it.
    os.environ["AHC_TASK_DIR"] = str(task_dir)
    os.environ["TASK_DIR"] = str(task_dir)

    objective = (task_dir / "objective.md").read_text()
    seed_src = (task_dir / evolve_file).read_text()
    seed_sha = hashlib.sha256(seed_src.encode()).hexdigest()[:12]

    out = Path(a.output or (HERE / "results" / f"{a.task}_{a.method}_s{a.seed}")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if a.eval_server:
        # every measurement of the wave on ONE dedicated machine; the server also serializes,
        # so the local lock stays off (see eval_server.py for what this buys and what it not)
        from remote_eval import RemoteEvalAdapter

        evaluator = RemoteEvalAdapter(a.eval_server, a.task)
    else:
        evaluator = CodeEvaluator(evaluator_code=(task_dir / "evaluator.py").read_text(),
                                  timeout=a.eval_timeout or cfg.get("eval_timeout", 400),
                                  workspace_path=str(out / "_eval"),
                                  serialize=bool(cfg.get("serialize_evals")))

    judge = None
    if a.method == "annealed":
        from pantheon.evolution.variators import LearnedIdeaJudge, NulledJudge

        # `--judge random|constant` ablates the model's opinion while keeping the judge's whole
        # apparatus -- base resolution, calibration, metric names -- so the arms differ in
        # information content and nothing else.
        if a.judge == "llm":
            judge = LearnedIdeaJudge(model=a.model, objective=objective, n_min=a.judge_n_min)
        else:
            judge = NulledJudge(mode=a.judge, seed=a.seed, n_min=a.judge_n_min)
        if a.judge_state:
            judge.load(a.judge_state)
    sched = {k: getattr(a, k) for k in ("t0", "t1", "beta0", "gamma")
             if getattr(a, k) is not None}
    method = build_method(a.method, a.seed, judge=judge, norm=a.norm, sched=sched,
                          low_fidelity=(a.inner_fidelity or cfg.get("inner_fidelity")) == "low")

    variator = method.default_variator(
        evaluator=evaluator, model=a.model, timeout=a.mutation_timeout,
        reasoning_max_tokens=a.reasoning_max_tokens,
        max_output_tokens=a.max_output_tokens,
        max_tool_calls=a.tool_budget, max_evaluations=a.max_inner_evals,
        workspace_root=str(out / "_mut"),
        target_file=evolve_file,
        inner_fidelity=a.inner_fidelity or cfg.get("inner_fidelity", "full"),
        trace_path=(str(out / "trace.jsonl") if a.trace else None))
    if a.code_variator and hasattr(variator, "code"):
        variator.code = (
            AgentVariator(evaluator=evaluator, model=a.model, max_tool_calls=a.tool_budget,
                          max_evaluations=a.max_inner_evals,
                          timeout=a.mutation_timeout, workspace_root=str(out / "_mut"),
                          score_key="combined_score",
                          inner_fidelity=a.inner_fidelity
                          or cfg.get("inner_fidelity", "full"))
            if a.code_variator == "agent"
            else CompletionVariator(model=a.model, target_file=evolve_file,
                                    timeout=a.mutation_timeout))

    _op = getattr(variator, "code", variator)
    operator = {"class": type(_op).__name__,
                "inner_fidelity": getattr(_op, "inner_fidelity", None),
                "max_tool_calls": getattr(_op, "max_tool_calls", None),
                "max_evaluations": getattr(_op, "max_evaluations", None),
                "max_submit_retries": getattr(_op, "max_submit_retries", None)}

    print("=" * 78)
    print(f"{a.task} | method={method.name} | "
          f"variator={type(variator).__name__}({type(_op).__name__}) | model={a.model}")
    print(f"budget={a.iterations} items | workers={a.workers} | seed={a.seed} | "
          f"evolving {evolve_file} | seed_sha={seed_sha}")
    print(f"operator {operator}")
    print("=" * 78, flush=True)

    t0 = time.time()
    history: list = []
    fails: dict = {}

    def on_event(kind: str, data: dict) -> None:
        if kind == "failed":
            k = f'{data.get("stage")}: {data.get("reason")}'
            fails[k] = fails.get(k, 0) + 1
            return
        if kind != "measured":
            return
        s = data.get("metrics", {}).get("combined_score")
        if s is None:
            return
        # fidelity is load-bearing, not decoration: a method's cheap screens fire this event
        # too, and a lucky 30-case read outscores the true 150-case measurement often enough
        # that a history without it reports screens as records (wave6 did, for two arms).
        history.append({"t": round(prev_seconds + time.time() - t0, 1),
                        "n": len(history) + 1, "score": s,
                        "id": data.get("id"), "fidelity": data.get("fidelity", "full"),
                        "valid": data.get("metrics", {}).get("validity")})
        if len(history) % 5 == 0:
            from pantheon.evolution.variators.usage import (eval_snapshot as _pe,
                                                            snapshot as _pl, timeline as _pt)
            try:
                json.dump({"partial": True, "t0_epoch": t0, "history": history,
                           "llm_usage": _pl(), "eval_usage": _pe(),
                           "usage_timeline": _pt(), "drift": drift},
                          open(out / "partial_summary.json", "w"))
            except Exception:  # noqa: BLE001
                pass
        best = max(h["score"] for h in history)
        print(f"  [{len(history):>3}] {time.time()-t0:6.0f}s  score={s:.6f}  best={best:.6f}",
              flush=True)

    # Only the problem's evaluator: a method that owns an idea judge registers it itself through
    # `default_evaluators()`.
    # Wall-clock tasks: the very first evaluation on a fresh container lands on a machine
    # still settling, and wave5 showed later reads on the same container running 100+ points
    # higher. One discarded warm-up read moves every arm's recorded seed onto a warm machine.
    prev_hist, prev_seconds, prev_drift = [], 0.0, []
    if a.resume:
        # A continued run must CONTINUE its budget and its curves: reload the previous
        # session's ledgers and history, so ceilings are cumulative and the budget axes
        # keep their positions across sessions.
        prev = None
        for name in ("summary.json", "partial_summary.json"):
            fp = out / name
            if fp.exists():
                prev = json.loads(fp.read_text())
                break
        if prev:
            from pantheon.evolution.variators.usage import restore as _restore
            _restore(prev.get("llm_usage"), prev.get("eval_usage"),
                     prev.get("usage_timeline"))
            prev_hist = list(prev.get("history") or [])
            prev_drift = list(prev.get("drift") or [])
            prev_seconds = float(prev.get("seconds") or
                                 (prev_hist[-1]["t"] if prev_hist else 0.0))
            print(f"resume: restored {len(prev_hist)} history rows, "
                  f"llm={prev.get('llm_usage')}", flush=True)

    warmup_score = None
    if cfg.get("warmup_eval"):
        w = await evaluator.evaluate_files({evolve_file: seed_src}, "full",
                                           book_as="harness")
        warmup_score = w.get("metrics", {}).get("combined_score")
        print(f"warm-up eval (discarded): {warmup_score}", flush=True)

    # Drift probes: re-measure the SEED every 20 minutes, off to the side. Not shown to the
    # method, not part of the search -- a ruler for how much this container's readings move,
    # so per-arm error bars come from data instead of assumption.
    drift: list = []

    async def _drift_probe():
        while True:
            await asyncio.sleep(1200)
            try:
                o = await evaluator.evaluate_files({evolve_file: seed_src}, "full",
                                                   book_as="harness")
                drift.append({"t": round(time.time() - t0, 1),
                              "score": o.get("metrics", {}).get("combined_score")})
            except Exception:  # noqa: BLE001
                pass

    probe = asyncio.create_task(_drift_probe()) if cfg.get("drift_probe") else None

    from pantheon.evolution.variators.usage import eval_snapshot as _ev, snapshot as _llm

    def _over_budget() -> bool:
        lu = _llm()
        if a.max_llm_calls and lu["calls"] >= a.max_llm_calls:
            return True
        if a.max_llm_tokens and (lu["prompt_tokens"] + lu["completion_tokens"]
                                 ) >= a.max_llm_tokens:
            return True
        ev = _ev()
        if a.max_eval_calls and (ev["calls"] - ev["calls_harness"]) >= a.max_eval_calls:
            return True
        return False

    budget = Budget(max_items=a.iterations,
                    stop_when=(_over_budget if (a.max_llm_calls or a.max_eval_calls
                                                or a.max_llm_tokens) else None))
    history[:0] = prev_hist
    drift[:0] = prev_drift
    res = await evolve(method=method, variator=variator,
                       evaluators={"code": evaluator},
                       seeds=[CodeGenome(files={evolve_file: seed_src})],
                       objective=objective, budget=budget,
                       concurrency=a.workers, on_event=on_event,
                       checkpoint_path=str(out), checkpoint_every=2, resume=a.resume)
    if probe is not None:
        probe.cancel()

    best = res.best
    # Summary scores come from the STORE, not the event log. Three lessons paid for in wave5:
    # the seed's measurement fires no "measured" event, so history[0] is the FIRST CHILD and
    # reading it as the seed mislabels every gain; `metrics()` returns an individual's LATEST
    # measurement, so on a wall-clock task a lucky-early individual can report an unlucky-late
    # score; and the dedup store books re-measurements of an UNCHANGED genome (agents
    # resubmitting identical code) onto one individual, whose peaks are then re-reads of the
    # same program, not improvements.
    def _valid_full(v):
        return [m.metrics["combined_score"] for m in v.measurements
                if m.ok and m.fidelity == "full"
                and (m.metrics.get("validity", 1) or 0) > 0
                and m.metrics.get("combined_score") is not None]

    seed_ind = next((v for v in res.store if not v.parent_ids), None)
    seed_ms = _valid_full(seed_ind) if seed_ind else []
    seed_score = seed_ms[0] if seed_ms else 0.0
    # FIRST read per individual, never the max over its re-measurements. Re-reads of one
    # unchanged program differ by ~12 points of wall-clock noise here, so max-over-k-reads is
    # biased upward by about the size of the between-method differences this bench exists to
    # measure -- and the arms that re-read most (an agent resubmitting identical code, a seed
    # re-measured 12 times) would win on luck. `rebuild_from_stores.py` already used first-read;
    # this makes the live summary agree with it.
    per_ind = {v.id: _valid_full(v) for v in res.store}
    best_score = max((ms[0] for ms in per_ind.values() if ms), default=0.0)
    best_child_score = max((ms[0] for i, ms in per_ind.items()
                            if ms and seed_ind is not None and i != seed_ind.id),
                           default=None)
    print("\n" + "=" * 78)
    print(f"best {best_score:.6f}   items {res.items_run}   failures {res.failures}   "
          f"{res.seconds:.0f}s")
    for k, n in sorted(fails.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>3}  {k}")
    print("=" * 78)

    if best is not None and hasattr(best.genome, "files"):
        for path, content in best.genome.files.items():
            (out / f"best_{Path(path).name}").write_text(content)
    if judge is not None and a.judge_state:
        # Loading without saving would make `--judge-state` a read-only flag and the cross-run
        # chain silently train nothing.
        judge.save(a.judge_state)
        print(f"judge state ({len(judge.cal)} pairs) -> {a.judge_state}")
    # The RESOLVED search configuration, not the flags: an arm whose knob silently failed to
    # reach the method would otherwise present itself as the ablation it is not.
    search = ({"judge": f"{a.judge}(n_min={a.judge_n_min})", "norm": method.norm,
               "t0": method.t0, "t1": method.t1, "beta0": method.beta0, "gamma": method.gamma}
              if a.method == "annealed" else {})
    from pantheon.evolution.variators.usage import (eval_snapshot, snapshot as llm_usage,
                                                     timeline)
    json.dump({"task": a.task, "evolve": evolve_file, "method": method.name, "model": a.model, "seed": a.seed,
               "operator": operator, "search": search, "seed_sha": seed_sha,
               "items_run": res.items_run, "failures": res.failures,
               "best_combined_score": best_score, "seed_combined_score": seed_score,
               "best_child_combined_score": best_child_score,
               "seed_remeasures": max(0, len(seed_ms) - 1),
               "warmup_score": warmup_score, "drift": drift, "t0_epoch": t0,
               "eval_server": a.eval_server or None,
               "eval_server_boots": getattr(evaluator, "boot_ids", None),
               "seconds": prev_seconds + res.seconds, "llm_usage": llm_usage(),
               "eval_usage": eval_snapshot(), "usage_timeline": timeline(),
               "history": history},
              open(out / "summary.json", "w"), indent=1)
    print(f"-> {out}/summary.json")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True)
    p.add_argument("--method", default="agent_map_elites",
                   choices=["agent_map_elites", "map_elites", "simpletes", "idea_code", "annealed",
                            "pantheon_evo", "hypothesis_bandit", "lab_notebook"])
    p.add_argument("--iterations", type=int, default=40)
    p.add_argument("--model", default="openai/gpt-5.6-luna")
    p.add_argument("--eval-server", default=os.environ.get("EVAL_SERVER_URL", ""),
                   help="URL of the shared evaluation server; empty = evaluate locally")
    p.add_argument("--max-llm-calls", type=int, default=None,
                   help="stop issuing items once the run's LLM-call ledger reaches this; the "
                        "spend-parity budget for cross-method comparison")
    p.add_argument("--max-output-tokens", type=int, default=None,
                   help="per-call output ceiling for the completion operators. Upstream "
                        "SimpleTES uses 32768, which fits gpt-oss; deepseek-v4-flash needs "
                        "~26k of THINKING on erdos before it writes anything, so 32768 leaves "
                        "no room for the program and it returns none at all. Raising the "
                        "ceiling preserves the model's behaviour where capping its reasoning "
                        "would not.")
    p.add_argument("--reasoning-max-tokens", type=int, default=None,
                   help="cap a reasoning model's thinking so it leaves room to answer; "
                        "deepseek-v4-flash spends all 32768 output tokens thinking on erdos "
                        "and returns no program at all without it")
    p.add_argument("--max-llm-tokens", type=int, default=None,
                   help="stop once prompt+completion tokens reach this; the third parity axis")
    p.add_argument("--max-eval-calls", type=int, default=None,
                   help="companion ceiling on evaluator calls (warm-up/drift probes excluded)")
    p.add_argument("--max-inner-evals", type=int, default=None,
                   help="cap on an agent mutation's own run_evaluator calls; None = unlimited. "
                        "Set it (uniformly) when comparing methods, or the agent operators get "
                        "an unbounded hidden evaluation budget the completion operators lack.")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--tool-budget", type=int, default=28)
    p.add_argument("--code-variator", default=None, choices=["agent", "completion"])
    p.add_argument("--eval-timeout", type=int, default=None,
                   help="per-evaluation cap; defaults to the task's own setting")
    p.add_argument("--mutation-timeout", type=int, default=1800)
    p.add_argument("--trace", action="store_true",
                   help="record every tool call and the workspace digest after it, to "
                        "trace/jsonl. Answers which call broke a file, which the stored genome "
                        "cannot")
    p.add_argument("--inner-fidelity", default=None,
                   help="fidelity for the agent's own run_evaluator calls, when the task offers "
                        "more than one. What gets RECORDED is always measured at full fidelity")
    p.add_argument("--norm", default="minmax", choices=["minmax", "absolute"])
    p.add_argument("--judge", default="llm", choices=["llm", "random", "constant"],
                   help="ablate the annealed method's judge: the model's opinion is replaced by "
                        "noise while the base/calibration/metric apparatus stays")
    p.add_argument("--judge-state", default=None)
    p.add_argument("--judge-n-min", type=int, default=5)
    # Schedule ablations. Unset means the method's own default; the record in summary.json is the
    # resolved value either way.
    p.add_argument("--t0", type=float, default=None)
    p.add_argument("--t1", type=float, default=None)
    p.add_argument("--beta0", type=float, default=None)
    p.add_argument("--gamma", type=float, default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        try:
            from dotenv import load_dotenv
            load_dotenv(Path.home() / ".pantheon" / ".env", override=False)
        except ImportError:
            pass
    if args.model.startswith(("openai/", "openrouter/")) and os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        os.environ["OPENAI_API_BASE"] = "https://openrouter.ai/api/v1"
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("no API key: set OPENROUTER_API_KEY or OPENAI_API_KEY")
    asyncio.run(main(args))
