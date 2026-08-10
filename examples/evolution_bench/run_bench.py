#!/usr/bin/env python
"""Run any method against any task in `tasks/`.

`run_v2.py` next door is the Erdos runner with the problem baked into it -- the seed filename, the
objective, the metric. Adding a second problem by copying it would give two files that drift, so
this one takes the problem as data:

    tasks/<name>/solution.py    the program that gets evolved; exposes run_code()
    tasks/<name>/evaluator.py   evaluate(workspace_path) -> {combined_score, validity, ...}
    tasks/<name>/objective.md   what the agent is told

    python run_bench.py --task hadamard29 --method annealed --iterations 40
    python run_bench.py --task sums_diffs --method map_elites --iterations 120

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


def build_method(name: str, seed: int, judge=None, norm: str = "minmax"):
    from pantheon.evolution.methods import (
        AnnealedIdeaCode, IdeaCodeAlternating, MapElitesIslands, SimpleTES)

    if name == "annealed":
        return AnnealedIdeaCode(judge=judge, norm=norm, seed=seed)
    if name == "idea_code":
        return IdeaCodeAlternating(ideas_per_round=3, code_per_idea=3, ideas_kept=2,
                                   k_ideas=1, k_code=1, seed=seed)
    if name == "map_elites":
        return MapElitesIslands(num_islands=2, feature_dimensions=["complexity", "diversity"],
                                feature_bins=6, exploration_ratio=0.2, num_inspirations=2,
                                migration_interval=8, function_weight=1.0, llm_weight=0.0,
                                seed=seed)
    if name == "simpletes":
        return SimpleTES(num_chains=2, k_candidates=2, num_inspirations=2,
                         selector="rpucg", seed=seed)
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
    objective = (task_dir / "objective.md").read_text()
    seed_src = (task_dir / "solution.py").read_text()
    seed_sha = hashlib.sha256(seed_src.encode()).hexdigest()[:12]

    out = Path(a.output or (HERE / "results" / f"{a.task}_{a.method}_s{a.seed}")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    evaluator = CodeEvaluator(evaluator_code=(task_dir / "evaluator.py").read_text(),
                              timeout=a.eval_timeout, workspace_path=str(out / "_eval"))

    judge = None
    if a.method == "annealed":
        from pantheon.evolution.variators import LearnedIdeaJudge

        judge = LearnedIdeaJudge(model=a.model, objective=objective, n_min=a.judge_n_min)
        if a.judge_state:
            judge.load(a.judge_state)
    method = build_method(a.method, a.seed, judge=judge, norm=a.norm)

    variator = method.default_variator(
        evaluator=evaluator, model=a.model, timeout=a.mutation_timeout,
        max_tool_calls=a.tool_budget, workspace_root=str(out / "_mut"),
        target_file="solution.py")
    if a.code_variator and hasattr(variator, "code"):
        variator.code = (
            AgentVariator(evaluator=evaluator, model=a.model, max_tool_calls=a.tool_budget,
                          timeout=a.mutation_timeout, workspace_root=str(out / "_mut"),
                          score_key="combined_score")
            if a.code_variator == "agent"
            else CompletionVariator(model=a.model, target_file="solution.py",
                                    timeout=a.mutation_timeout))

    _op = getattr(variator, "code", variator)
    operator = {"class": type(_op).__name__,
                "max_tool_calls": getattr(_op, "max_tool_calls", None),
                "max_evaluations": getattr(_op, "max_evaluations", None),
                "max_submit_retries": getattr(_op, "max_submit_retries", None)}

    print("=" * 78)
    print(f"{a.task} | method={method.name} | "
          f"variator={type(variator).__name__}({type(_op).__name__}) | model={a.model}")
    print(f"budget={a.iterations} items | workers={a.workers} | seed={a.seed} | "
          f"seed_sha={seed_sha}")
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
        history.append({"t": round(time.time() - t0, 1), "n": len(history) + 1,
                        "score": s, "valid": data.get("metrics", {}).get("validity")})
        best = max(h["score"] for h in history)
        print(f"  [{len(history):>3}] {time.time()-t0:6.0f}s  score={s:.6f}  best={best:.6f}",
              flush=True)

    res = await evolve(method=method, variator=variator,
                       evaluators={"code": evaluator, **({"idea": judge} if judge else {})},
                       seeds=[CodeGenome(files={"solution.py": seed_src})],
                       objective=objective, budget=Budget(max_items=a.iterations),
                       concurrency=a.workers, on_event=on_event,
                       checkpoint_path=str(out), checkpoint_every=2, resume=a.resume)

    best = res.best
    best_score = best.metrics().get("combined_score", 0.0) if best else 0.0
    seed_score = history[0]["score"] if history else 0.0
    print("\n" + "=" * 78)
    print(f"best {best_score:.6f}   items {res.items_run}   failures {res.failures}   "
          f"{res.seconds:.0f}s")
    for k, n in sorted(fails.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>3}  {k}")
    print("=" * 78)

    if best is not None and hasattr(best.genome, "files"):
        for path, content in best.genome.files.items():
            (out / f"best_{Path(path).name}").write_text(content)
    json.dump({"task": a.task, "method": method.name, "model": a.model, "seed": a.seed,
               "operator": operator, "seed_sha": seed_sha,
               "items_run": res.items_run, "failures": res.failures,
               "best_combined_score": best_score, "seed_combined_score": seed_score,
               "seconds": res.seconds, "history": history},
              open(out / "summary.json", "w"), indent=1)
    print(f"-> {out}/summary.json")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True)
    p.add_argument("--method", default="map_elites",
                   choices=["map_elites", "simpletes", "idea_code", "annealed"])
    p.add_argument("--iterations", type=int, default=40)
    p.add_argument("--model", default="openai/gpt-5.6-luna")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--tool-budget", type=int, default=28)
    p.add_argument("--code-variator", default=None, choices=["agent", "completion"])
    p.add_argument("--eval-timeout", type=int, default=400)
    p.add_argument("--mutation-timeout", type=int, default=1800)
    p.add_argument("--norm", default="minmax", choices=["minmax", "absolute"])
    p.add_argument("--judge-state", default=None)
    p.add_argument("--judge-n-min", type=int, default=5)
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
