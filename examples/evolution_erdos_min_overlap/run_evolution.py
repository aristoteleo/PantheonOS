#!/usr/bin/env python
"""Run Pantheon Evolution on the Erdős Minimum Overlap Problem.

Construct a step function h:[0,2]->[0,1] with unit mass minimizing the worst translated
overlap Psi(h) = max_k integral h(x)(1-h(x+k)) dx. This is the AlphaEvolve / SimpleTES /
CORAL benchmark; the scorer is DeepMind's ground-truth compute_upper_bound. Records
(lower better): Haugland 0.380927, AlphaEvolve 0.380924, SimpleTES 0.380868.

Usage:
    python run_evolution.py [--iterations N] [--tool-budget B] [--allow-solvers] [--model M]
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


OBJECTIVE = """Construct a step function h on [0,2] with values in [0,1] and unit mass to MINIMIZE the worst-case translated overlap (the Erdős Minimum Overlap Problem).

h is represented by K equal-width steps (an array of K heights). The score is:
  Psi(h) = max over all integer shifts k of  sum_i h_i * (1 - h_{i+k})  * (2/K)
which discretizes  max_k integral h(x)(1 - h(x+k)) dx. LOWER Psi is better.

Rules (enforced by the evaluator — any violation scores 0):
- Every step height must be in [0, 1].
- Unit mass: sum(h) must equal K/2 (i.e. integral h = 1). Renormalize after every edit.
- run_construction() must return the 1-D array of K step heights (K >= 8; K=95 is a good default).

Your score is combined_score = 1 - Psi (higher is better, so maximizing it minimizes Psi). The uniform h=1/2 scores Psi=0.5 (combined_score 0.5). The records to BEAT are Psi <= 0.380924 (AlphaEvolve) and Psi <= 0.380868 (SimpleTES) — i.e. combined_score >= 0.619132.

How to improve: identify WHICH shift k currently attains the max overlap, and reshape h to push that worst case down without letting another shift rise. The known-good shape is strongly non-uniform and mirror-symmetric about the center (heights near 0 at the two ends, rising to a plateau near the middle). Design a search/optimizer that drives Psi down while keeping h feasible; always keep a valid h at least as good as the one you started from."""


async def run_evolution(iterations=30, output_dir=None, model=None, workers=1,
                        verbose=False, resume=None, tool_budget=14, allow_solvers=False,
                        warm_start=True):
    from pantheon.evolution import EvolutionConfig, EvolutionTeam
    from pantheon.evolution.program import CodebaseSnapshot
    from pantheon.evolution.team import MUTATION_AGENT_SYSTEM_PROMPT

    # Default: the mutation agent must write its OWN optimizer (AlphaEvolve-style algorithm
    # discovery — no off-the-shelf solvers). --allow-solvers lifts that (scipy.optimize / SLSQP /
    # linprog / cvxpy ...); that measures the "library ceiling", not evolved-algorithm skill.
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
        "sequence.py": (example_dir / "sequence.py").read_text(),
        # Warm-start data file (part of the genome so the evaluator sees it). Seeds empty; the
        # framework refreshes it with the best PRODUCED step function after each evaluation.
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
        evaluation_timeout=60,
        function_weight=1.0,
        llm_weight=0.0,
        fitness_absolute=True,          # fitness = raw combined_score (1 - Psi), NOT archive-normalized
        # carry the best step function across generations. Disable (--no-warm-start) to force the
        # genome CODE to construct the sequence each time — closes the loophole where the agent finds
        # a solution with scipy in its scratchpad and smuggles it out via the warm_start.json data file.
        warm_start_file=("warm_start.json" if warm_start else None),
        single_agent_mutation=True,
        mutation_timeout=max(600, 30 * tool_budget),
        max_tool_calls_per_mutation=tool_budget,
        early_stop_generations=max(50, iterations),
        checkpoint_interval=10,
        db_path=output_dir,
        log_level="DEBUG" if verbose else "INFO",
        log_iterations=True,
        mutation_system_prompt=solver_prompt,  # None -> default (no external solvers)
    )
    if model:
        config.mutator_model = model

    print("=" * 64)
    print("Pantheon Evolution: Erdős Minimum Overlap (minimize Psi, record ~0.380868)")
    print("=" * 64)
    print(f"Iterations: {iterations} | workers: {workers} | model: {model or config.mutator_model}")
    print(f"Tool budget/mutation: {tool_budget} | mutation_timeout: {config.mutation_timeout}s")
    print(f"External solvers (scipy/etc): {'ALLOWED' if allow_solvers else 'forbidden (write your own)'}")
    print(f"Warm start: {'on' if warm_start else 'OFF (code must construct each time)'}")
    print(f"Output: {output_dir or 'None (results not saved)'}")
    print("-" * 64)

    team = EvolutionTeam(config=config)
    result = await team.evolve(initial_code=initial_code, evaluator_code=evaluator_code,
                               objective=OBJECTIVE, resume_from=resume)

    print("\n" + "=" * 64)
    print(result.get_summary())
    if result.best_program:
        m = result.best_program.metrics
        psi = m.get("overlap", 1.0)
        print(f"Best Psi (overlap) = {psi:.6f}  (records: AlphaEvolve 0.380924, SimpleTES 0.380868; "
              f"lower is better)  K={m.get('K')}  validity={m.get('validity')}")

    if output_dir and result.best_program:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "sequence_best.py").write_text(result.best_program.snapshot.files["sequence.py"])
        result.save_report(str(out / "evolution_report.json"))
        config.to_yaml(str(out / "config.yaml"))
        print(f"\nBest construction saved to: {out / 'sequence_best.py'}")

    return result


def main():
    ap = argparse.ArgumentParser(description="Evolve an Erdős min-overlap step function")
    ap.add_argument("--iterations", "-n", type=int, default=30)
    ap.add_argument("--output", "-o", type=str, default=None)
    ap.add_argument("--model", "-m", type=str, default=None)
    ap.add_argument("--workers", "-w", type=int, default=1)
    ap.add_argument("--tool-budget", "-b", type=int, default=14,
                    help="action tool calls allowed per mutation (depth vs breadth knob)")
    ap.add_argument("--allow-solvers", action="store_true",
                    help="let the agent use scipy.optimize / off-the-shelf solvers (default: forbidden)")
    ap.add_argument("--no-warm-start", action="store_true",
                    help="disable warm-start (force the genome code to construct the sequence each time)")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--resume", "-r", type=str, default=None)
    a = ap.parse_args()
    try:
        result = asyncio.run(run_evolution(a.iterations, a.output, a.model, a.workers,
                                           a.verbose, a.resume, a.tool_budget, a.allow_solvers,
                                           not a.no_warm_start))
        m = result.best_program.metrics if result.best_program else {}
        print(f"\nFinal best Psi: {m.get('overlap', float('nan')):.6f}")
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
