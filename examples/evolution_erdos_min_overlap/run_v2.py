#!/usr/bin/env python
"""Erdos minimum-overlap, run through the refactored loop with a choice of algorithm.

The same problem, evaluator and seed as `run_evolution.py`; what changes is that the search is a
plugged-in method rather than the one hard-coded into `EvolutionTeam`, so `--method map_elites`
and `--method simpletes` are the same run with a different algorithm and nothing else.

That comparison is the reason this file exists. Two algorithms on one problem, one evaluator, one
budget and one model is the only way to say anything about the algorithms rather than about the
harness they happen to be wired into.

    python run_v2.py --method map_elites --iterations 12 --model openai/gpt-5.6-luna
    python run_v2.py --method simpletes   --iterations 12 --model openai/gpt-5.6-luna
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))

OBJECTIVE = (HERE / "run_evolution.py").read_text().split('"""', 2)[1] if False else """
Improve `run_construction()` in sequence.py so that it returns a step-height vector h that
MINIMISES the Erdos minimum-overlap constant Psi = max_k (h * h)(k), subject to h being a valid
unit-mass profile: 1-D, length >= 8, every height in [0, 1], and sum(h) = K/2.

Fitness is combined_score = 1 - Psi, so higher is better.

How to improve: identify WHICH shift k currently attains the max overlap, and reshape h to push
that worst case down without letting another shift rise. The known-good shape is strongly
non-uniform and mirror-symmetric about the centre -- heights near 0 at the two ends, rising to a
plateau near the middle. Design a search or optimizer that drives Psi down while keeping h
feasible; always keep a valid h at least as good as the one you started from.

Write the core optimisation logic yourself. Do not hand the objective to a general-purpose solver
(scipy.optimize, cvxpy, OR-tools); numpy as a building block is fine, but the search must be your
own.
"""


def build_method(name: str, seed: int, judge=None, norm: str = "minmax"):
    from pantheon.evolution.methods import (
        AnnealedIdeaCode, IdeaCodeAlternating, MapElitesIslands, SimpleTES)

    if name == "annealed":
        # No `ideas_kept`: selection is a Boltzmann sample whose temperature anneals, so ideas are
        # deprioritised rather than cut. The seven schedule parameters are the library defaults on
        # purpose -- tuning them on the problem the method is then scored on would make the score
        # meaningless.
        return AnnealedIdeaCode(judge=judge, norm=norm, seed=seed)

    if name == "idea_code":
        # k_ideas=1: one proposal per request. At 3 the whole idea round was filled by a single
        # work item, so a run produced far more approaches than it could ever implement and each
        # got exactly one attempt -- proposals are not the scarce resource, implementations are.
        # A round is now 3 idea items + 2 kept ideas x 3 implementations = 9 items.
        return IdeaCodeAlternating(
            ideas_per_round=3, code_per_idea=3, ideas_kept=2,
            k_ideas=1, k_code=1, seed=seed,
        )

    if name == "map_elites":
        return MapElitesIslands(
            num_islands=2,
            feature_dimensions=["complexity", "diversity"],
            feature_bins=6,
            exploration_ratio=0.2,
            num_inspirations=2,
            migration_interval=8,
            function_weight=1.0,
            llm_weight=0.0,
            seed=seed,
        )
    if name == "simpletes":
        # k=2 keeps the per-item cost comparable to map_elites' single child, so the two arms are
        # matched on evaluations rather than on prompts
        return SimpleTES(
            num_chains=2,
            k_candidates=2,
            num_inspirations=2,
            selector="rpucg",
            seed=seed,
        )
    raise SystemExit(f"unknown method {name!r}")


async def main(a) -> None:
    from pantheon.evolution.core import Budget, CodeGenome
    from pantheon.evolution.core.loop import evolve
    from pantheon.evolution.variators import (
        AgentVariator, CodeEvaluator, CompletionVariator)

    # Resolved, not as given. The evaluator runs each program in a subprocess with its own working
    # directory, so a relative --output makes the workspace path unresolvable there and every
    # evaluation dies with FileNotFoundError -- including the seed's, which then silently zeroes
    # the whole run.
    out = Path(a.output or (HERE / f"results_v2_{a.method}")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    evaluator = CodeEvaluator(
        evaluator_code=(HERE / "evaluator.py").read_text(),
        timeout=a.eval_timeout,
        workspace_path=str(out / "_eval"),
    )
    judge = None
    if a.method == "annealed":
        from pantheon.evolution.variators import LearnedIdeaJudge

        judge = LearnedIdeaJudge(model=a.model, objective=OBJECTIVE,
                                 n_min=a.judge_n_min, prior_sigma=a.prior_sigma)
        # One run labels roughly ten ideas, which is not a training set. The file is what lets the
        # judge accumulate across runs; without it "learned" is a description of the code, not of
        # anything that happens.
        if a.judge_state:
            judge.load(a.judge_state)
    method = build_method(a.method, a.seed, judge=judge, norm=a.norm)

    # The operator belongs to the algorithm, so ask the method rather than deciding here:
    # SimpleTES is a chain policy AND a single completion that cannot run anything, and giving it
    # an agent that verifies its own edits first would score better while no longer being
    # SimpleTES. `--variator` overrides that on purpose, which is how one operator can be held
    # fixed to compare two search policies -- and it is a deliberate act, not the default.
    # Warm start hands each child its parent's best solution vector. It has been measured to cause
    # a sticky-champion collapse on this problem -- the best Erdos result on record (Psi 0.380909)
    # was produced with it OFF -- so it is a confound that has to be held fixed across arms, not
    # left to whatever each method's default happens to be.
    warm = None if a.no_warm_start else "warm_start.json"
    if a.variator == "completion":
        variator = CompletionVariator(model=a.model, target_file="sequence.py",
                                      timeout=a.mutation_timeout)
    elif a.variator == "agent":
        variator = AgentVariator(
            evaluator=evaluator, model=a.model, max_tool_calls=a.tool_budget,
            timeout=a.mutation_timeout, warm_start_file=warm,
            workspace_root=str(out / "_mut"), score_key="combined_score")
    else:
        variator = method.default_variator(
            evaluator=evaluator, model=a.model, timeout=a.mutation_timeout,
            max_tool_calls=a.tool_budget, warm_start_file=warm,
            workspace_root=str(out / "_mut"), target_file="sequence.py")

    # A two-population method routes by kind, so its operator is really two operators. Replacing
    # only the code half is what lets one search policy be compared against another with the
    # thing that writes the programs held fixed -- which is the only way a difference in score can
    # be attributed to the policy rather than to the coder.
    if a.code_variator and hasattr(variator, "code"):
        if a.code_variator == "agent":
            variator.code = AgentVariator(
                evaluator=evaluator, model=a.model, max_tool_calls=a.tool_budget,
                timeout=a.mutation_timeout, warm_start_file=warm,
                workspace_root=str(out / "_mut"), score_key="combined_score")
        else:
            variator.code = CompletionVariator(model=a.model, target_file="sequence.py",
                                               timeout=a.mutation_timeout)
    kind = type(variator).__name__
    if hasattr(variator, "code"):
        kind += f"({type(variator.code).__name__})"
    seed_genome = CodeGenome(files={
        "sequence.py": (HERE / "sequence.py").read_text(),
        # part of the genome so the evaluator sees it: the framework refreshes it with the best
        # step function each evaluation PRODUCED, so a child polishes its parent's solution
        # instead of re-deriving one
        "warm_start.json": "{}",
    })

    judge_label = (f"learned(n={len(judge.cal)},norm={a.norm})" if judge is not None
                   else (a.judge if method.name == "idea_code_alternating" else "none"))
    print("=" * 74)
    print(f"Erdos minimum-overlap | method={method.name} | variator={kind} | "
          f"judge={judge_label} | model={a.model}")
    print(f"budget={a.iterations} work items | concurrency={a.workers} | seed={a.seed}")
    print("=" * 74, flush=True)

    t0 = time.time()
    history = []

    fails: dict = {}

    def on_event(kind: str, data: dict) -> None:
        if kind == "failed":
            key = f'{data.get("stage")}: {data.get("reason")}'
            fails[key] = fails.get(key, 0) + 1
            print(f"  [fail] {key}", flush=True)
            return
        if kind != "measured":
            return
        s = data.get("metrics", {}).get("combined_score")
        if s is None:
            return
        history.append({"t": round(time.time() - t0, 1), "id": data["id"], "score": s})
        best = max(h["score"] for h in history)
        print(f"  [{len(history):>3}] {time.time()-t0:6.0f}s  score={s:.6f}  "
              f"best={best:.6f}  Psi={1 - best:.6f}", flush=True)

    evaluators = {"code": evaluator}
    if judge is not None:
        evaluators["idea"] = judge
    if method.name == "idea_code_alternating":
        from pantheon.evolution.variators import IdeaJudge, NullJudge

        # `--judge random|constant` ablates the LLM judge. It only orders ideas that have never
        # been implemented -- a measured score supersedes it the moment one exists -- so the
        # ablation asks a narrow question: is that first ordering worth a model call per idea?
        evaluators["idea"] = (
            IdeaJudge(model=a.model, objective=OBJECTIVE) if a.judge == "llm"
            else NullJudge(mode=a.judge, seed=a.seed))

    res = await evolve(
        method=method,
        variator=variator,
        evaluators=evaluators,
        seeds=[seed_genome],
        objective=OBJECTIVE,
        budget=Budget(max_items=a.iterations),
        concurrency=a.workers,
        on_event=on_event,
        checkpoint_path=str(out),
        checkpoint_every=2,
        resume=a.resume,
    )

    best = res.best
    best_score = best.metrics().get("combined_score", 0.0) if best else 0.0
    print("\n" + "=" * 74)
    print(f"method            {method.name}")
    print(f"work items        {res.items_run}   failures {res.failures}")
    for k, n in sorted(fails.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>3}  {k}")
    print(f"individuals       {len(res.store)}")
    print(f"best combined     {best_score:.6f}    -> Psi = {1 - best_score:.6f}")
    print(f"wall clock        {res.seconds:.0f}s")
    if hasattr(method, "coverage"):
        print(f"grid coverage     {method.coverage():.1%}")
    if hasattr(method, "prior_vs_realised"):
        from pantheon.evolution.core.method import Budget as _B, EvolveContext as _C

        rows = method.prior_vs_realised(_C(store=res.store, budget=_B()))
        print(f"\nideas             {len(rows)}   (what the judge predicted vs what was measured)")
        if rows and "predicted" in rows[0]:
            # The annealed method predicts a GAIN on the objective's scale, so the two columns are
            # directly subtractable -- which is the whole point of the redesign.
            for r in sorted(rows, key=lambda r: -(r["realised_best"] or -1))[:8]:
                print(f'  predicted {r["predicted"]:+.4f} -> measured {r["realised_first"]:+.4f} '
                      f'(best {r["realised_best"]:+.4f}, {r["implementations"]} impl)  '
                      f'{r["summary"][:52]}')
            err = [abs(r["predicted"] - r["realised_first"]) for r in rows
                   if r["realised_first"] is not None]
            if err:
                print(f'  mean |predicted - measured|  {sum(err)/len(err):.4f}   n={len(err)}')
        else:
            for r in sorted(rows, key=lambda r: -(r["realised"] or -1))[:8]:
                got = f'{r["realised"]:.4f}' if r["realised"] is not None else "never built"
                print(f'  prior {r["prior"]:.2f} -> {got:>11}  ({r["implementations"]} impl)  '
                      f'{r["text"][:60]}')
    if judge is not None:
        print(f"\njudge             {judge.report()}")
        if a.judge_state:
            judge.save(a.judge_state)
            print(f"                  saved {len(judge.cal)} observations -> {a.judge_state}")
    print("=" * 74)

    if best is not None and hasattr(best.genome, "files"):
        for path, content in best.genome.files.items():
            (out / f"best_{Path(path).name}").write_text(content)
    json.dump(
        {
            "method": method.name, "variator": kind, "judge": judge_label, "norm": a.norm,
            "model": a.model, "seed": a.seed,
            "items_run": res.items_run, "failures": res.failures,
            "individuals": len(res.store), "best_combined_score": best_score,
            "best_psi": 1 - best_score, "seconds": res.seconds,
            "history": history, "method_state_keys": sorted(res.method_state),
        },
        open(out / "summary.json", "w"), indent=1,
    )
    print(f"-> {out}/summary.json")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--method", default="map_elites",
                   choices=["map_elites", "simpletes", "idea_code", "annealed"])
    p.add_argument("--iterations", type=int, default=12, help="work items, i.e. LLM mutations")
    p.add_argument("--model", default="openai/gpt-5.6-luna")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--variator", default=None, choices=["agent", "completion"],
                   help="override the operator the method declares (for controlled comparisons)")
    p.add_argument("--tool-budget", type=int, default=14)
    p.add_argument("--eval-timeout", type=int, default=300)
    p.add_argument("--mutation-timeout", type=int, default=1800)
    p.add_argument("--output", default=None)
    p.add_argument("--judge", default="llm", choices=["llm", "random", "constant"],
                   help="idea-level evaluator; the nulls ablate it")
    p.add_argument("--code-variator", default=None, choices=["agent", "completion"],
                   help="replace only the code half of a two-population method, so two search "
                        "policies can be compared with the coder held fixed")
    p.add_argument("--no-warm-start", action="store_true",
                   help="do not seed each child with its parent's best solution vector; measured "
                        "to cause a sticky-champion collapse on plateau-prone problems")
    p.add_argument("--norm", default="minmax", choices=["minmax", "absolute"],
                   help="annealed: candidate normalisation before the softmax. minmax is the "
                        "published schedule and degenerates with few candidates; absolute uses "
                        "an observed score scale instead")
    p.add_argument("--judge-state", default=None,
                   help="annealed: JSON file the judge's training set is loaded from and saved "
                        "to, so calibration accumulates across runs instead of restarting at "
                        "~10 labelled ideas every time")
    p.add_argument("--judge-n-min", type=int, default=5,
                   help="annealed: observations required before the calibration stops being the "
                        "identity")
    p.add_argument("--prior-sigma", type=float, default=0.15,
                   help="annealed: uncertainty assigned to an unbuilt idea before any residuals "
                        "have been measured")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    # `find_dotenv` searches upward from the script, so running out of a git worktree finds the
    # repo's .env and stops -- never reaching ~/.pantheon/.env, where the OpenRouter key lives.
    # Load that explicitly, without overriding anything already in the environment.
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
