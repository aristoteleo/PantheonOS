#!/usr/bin/env python
"""Run Pantheon Evolution on the circle-packing problem.

Pack N = 26 non-overlapping circles in the unit square [0,1]x[0,1] to maximize the
sum of their radii (the AlphaEvolve / OpenEvolve benchmark; record for N=26 ~ 2.635).

Usage:
    python run_evolution.py [--iterations N] [--output DIR] [--model M] [--workers W]

Example:
    python run_evolution.py --iterations 40 --workers 2 --output results/
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from the example directory.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


OBJECTIVE = """Pack N=26 non-overlapping circles inside the unit square [0,1]x[0,1] to MAXIMIZE the sum of their radii.

Rules (enforced by the evaluator — any violation scores 0):
- Every circle must stay fully inside the unit square.
- No two circles may overlap.
- run_packing() must return (centers, radii) with centers shape (26, 2) and radii shape (26,).

Your score (combined_score) IS the sum of radii itself — higher is better, no upper bound. The state of the art (AlphaEvolve) for N=26 is a sum of radii of about 2.635, so aim to reach and beat 2.635.

Improve construct_packing(): both the CENTER PLACEMENT and the RADIUS optimization matter. Ideas: move centers off the rigid rings, use a hexagonal-style or optimized layout, or run a numerical optimizer that jointly adjusts centers and radii while respecting the constraints. Always keep the packing valid."""


async def run_evolution(iterations=40, output_dir=None, model=None, workers=2,
                        verbose=False, resume=None, tool_budget=14, allow_solvers=False):
    from pantheon.evolution import EvolutionConfig, EvolutionTeam
    from pantheon.evolution.program import CodebaseSnapshot
    from pantheon.evolution.team import MUTATION_AGENT_SYSTEM_PROMPT

    # By default the mutation agent must write its OWN optimizer (AlphaEvolve-style algorithm
    # discovery — no off-the-shelf solvers). --allow-solvers lifts that so the agent may call
    # scipy.optimize / SLSQP / etc.; this measures the "library ceiling", not evolved-algorithm skill.
    solver_prompt = None
    if allow_solvers:
        _no_solver = ("- Write the CORE problem-solving logic YOURSELF. Do NOT call a general-purpose "
                      "solver or optimizer to do the work for you — e.g. scipy.optimize / minimize / "
                      "linprog, cvxpy, OR-tools, sklearn optimizers, or networkx graph algorithms that "
                      "solve the objective for you. Basic array math (numpy) is fine as a building block, "
                      "but the search / optimization / decision logic that drives the score must be your "
                      "own code and your own idea.")
        _allow = ("- You MAY use ANY library, including general-purpose optimizers (scipy.optimize / "
                  "minimize / SLSQP / trust-constr / linprog, cvxpy, OR-tools, etc.). Use whatever "
                  "reaches the best VALID score.")
        solver_prompt = MUTATION_AGENT_SYSTEM_PROMPT.replace(_no_solver, _allow)
        assert solver_prompt != MUTATION_AGENT_SYSTEM_PROMPT, "no-solver clause not found (prompt changed?)"

    example_dir = Path(__file__).parent
    initial_code = CodebaseSnapshot(files={
        "packing.py": (example_dir / "packing.py").read_text(),
        # Warm-start data file (part of the genome so the evaluator sees it). Seeds empty; the
        # framework refreshes it with the best PRODUCED layout after each evaluation, so later
        # generations load + polish it instead of re-deriving from scratch.
        "warm_start.json": "{}",
    })
    evaluator_code = (example_dir / "evaluator.py").read_text()

    config = EvolutionConfig(
        max_iterations=iterations,
        num_workers=workers,
        num_islands=2,
        num_inspirations=2,
        num_top_programs=3,
        max_parallel_evaluations=4,
        evaluation_timeout=60,          # circle packing eval is fast
        function_weight=1.0,
        llm_weight=0.0,
        fitness_absolute=True,          # fitness = raw combined_score (sum_radii/2.635), NOT min-max
                                        # normalized against the archive — keeps a real selection
                                        # gradient near the top and rewards beating 2.635 (>1.0)
        warm_start_file="warm_start.json",  # persist the produced layout so each generation warm-
                                        # starts (loads + polishes) instead of re-deriving from scratch
                                        # — lets breadth accumulate numerical refinement like depth
        single_agent_mutation=True,     # one full-capability agent that edits + submits
        mutation_timeout=max(600, 30 * tool_budget),  # scale wall-clock with the action budget
        max_tool_calls_per_mutation=tool_budget,  # HARD action budget (python/shell/run_evaluator/...
                                        # — NOT submit). Live countdown on each result; once spent,
                                        # every tool but submit() fails. submit() is always available,
                                        # so the agent finalizes its own best work, not cut off.
        early_stop_generations=max(50, iterations),
        checkpoint_interval=10,
        db_path=output_dir,
        log_level="DEBUG" if verbose else "INFO",
        log_iterations=True,
        mutation_system_prompt=solver_prompt,  # None -> default (no external solvers)
    )
    if model:
        config.mutator_model = model

    print("=" * 60)
    print("Pantheon Evolution: Circle Packing (N=26, unit square)")
    print("=" * 60)
    print(f"Iterations: {iterations} | workers: {workers} | model: {model or config.mutator_model}")
    print(f"Tool budget/mutation: {tool_budget} | mutation_timeout: {config.mutation_timeout}s")
    print(f"External solvers (scipy/etc): {'ALLOWED' if allow_solvers else 'forbidden (write your own)'}")
    print(f"Output: {output_dir or 'None (results not saved)'}")
    print("-" * 60)

    team = EvolutionTeam(config=config)
    result = await team.evolve(initial_code=initial_code, evaluator_code=evaluator_code,
                               objective=OBJECTIVE, resume_from=resume)

    print("\n" + "=" * 60)
    print(result.get_summary())
    if result.best_program:
        m = result.best_program.metrics
        print(f"Best sum_radii = {m.get('sum_radii', 0):.4f}  "
              f"(target 2.635, ratio {m.get('target_ratio', 0):.3f}, validity {m.get('validity', 0)})")

    if output_dir and result.best_program:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "packing_best.py").write_text(next(iter(result.best_program.snapshot.files.values())))
        result.save_report(str(out / "evolution_report.json"))
        config.to_yaml(str(out / "config.yaml"))
        print(f"\nBest packing saved to: {out / 'packing_best.py'}")

    return result


def main():
    ap = argparse.ArgumentParser(description="Evolve a circle packing with Pantheon Evolution")
    ap.add_argument("--iterations", "-n", type=int, default=40)
    ap.add_argument("--output", "-o", type=str, default=None)
    ap.add_argument("--model", "-m", type=str, default=None, help="mutator model (default: config 'normal')")
    ap.add_argument("--workers", "-w", type=int, default=2)
    ap.add_argument("--tool-budget", "-b", type=int, default=14,
                    help="action tool calls allowed per mutation (depth vs breadth knob)")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--resume", "-r", type=str, default=None)
    ap.add_argument("--allow-solvers", action="store_true",
                    help="let the agent use scipy.optimize / off-the-shelf solvers (default: forbidden)")
    a = ap.parse_args()
    try:
        result = asyncio.run(run_evolution(a.iterations, a.output, a.model, a.workers,
                                           a.verbose, a.resume, a.tool_budget, a.allow_solvers))
        print(f"\nFinal best score: {result.best_score:.4f}")
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
