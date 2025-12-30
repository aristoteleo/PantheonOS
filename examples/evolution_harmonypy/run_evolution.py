#!/usr/bin/env python
"""
Run Pantheon Evolution on the Harmony Algorithm.

This script demonstrates how to use Pantheon Evolution to optimize
a data integration algorithm. The evolution process will:

1. Generate mutations of the harmony.py implementation
2. Evaluate each mutation on synthetic single-cell data
3. Select the best-performing variants
4. Iterate until convergence or max iterations

Usage:
    python run_evolution.py [--iterations N] [--output DIR]

Example:
    python run_evolution.py --iterations 50 --output results/
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load .env file from the example directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")


async def run_evolution(
    iterations: int = 50,
    output_dir: str = None,
    verbose: bool = False,
):
    """
    Run the evolution process.

    Args:
        iterations: Maximum number of evolution iterations
        output_dir: Directory to save results
        verbose: Enable verbose logging
    """
    from pantheon.evolution import EvolutionTeam, EvolutionConfig
    from pantheon.evolution.program import CodebaseSnapshot

    # Get paths
    example_dir = Path(__file__).parent
    harmony_path = example_dir / "harmony.py"
    evaluator_path = example_dir / "evaluator.py"

    # Load initial code and evaluator
    # Use CodebaseSnapshot with correct filename so evaluator can find it
    initial_code = CodebaseSnapshot.from_single_file("harmony.py", harmony_path.read_text())
    evaluator_code = evaluator_path.read_text()

    # Create configuration
    config = EvolutionConfig(
        max_iterations=iterations,
        num_islands=3,
        num_inspirations=2,
        num_top_programs=3,
        max_parallel_evaluations=2,
        evaluation_timeout=120,
        # Use system default model (configured via environment)
        log_level="DEBUG" if verbose else "INFO",
        log_iterations=True,
        checkpoint_interval=10,
        db_path=output_dir,
    )

    # Define optimization objective
    objective = """Optimize the Harmony algorithm implementation for:

1. **Integration Quality** (40% weight): Improve batch mixing while preserving biological structure.
   - The algorithm should effectively remove batch effects
   - Biological clusters should remain distinct after correction

2. **Performance** (20% weight): Reduce execution time.
   - Optimize hot loops and matrix operations
   - Consider vectorization opportunities
   - Avoid redundant computations

3. **Convergence** (10% weight): Improve convergence behavior.
   - Reduce number of iterations needed
   - Ensure stable convergence

4. **Biological Conservation** (30% weight): Preserve biological variance.
   - Don't over-correct and remove biological signal
   - Maintain cluster separation

Key areas to consider:
- The _update_R() method computes soft cluster assignments
- The _correct() method applies linear corrections
- The _compute_distances() method is called frequently
- Ridge regression in _correct() could be optimized
- The diversity penalty in _update_R() balances batch mixing

Constraints:
- Keep the public API (run_harmony function signature)
- Maintain numerical stability
- Don't remove essential functionality
"""

    print("=" * 60)
    print("Pantheon Evolution: Harmony Algorithm Optimization")
    print("=" * 60)
    print(f"\nInitial code: {harmony_path}")
    print(f"Evaluator: {evaluator_path}")
    print(f"Iterations: {iterations}")
    print(f"Output: {output_dir or 'None (results not saved)'}")
    print("\n" + "-" * 60)
    print("Starting evolution...\n")

    # Create and run evolution team
    team = EvolutionTeam(config=config)
    result = await team.evolve(
        initial_code=initial_code,
        evaluator_code=evaluator_code,
        objective=objective,
    )

    # Print results
    print("\n" + "=" * 60)
    print(result.get_summary())

    # Save results if output specified
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Save best code
        best_code_path = output_path / "harmony_optimized.py"
        best_code_path.write_text(result.best_code)
        print(f"\nBest code saved to: {best_code_path}")

        # Save report
        report_path = output_path / "evolution_report.json"
        result.save_report(str(report_path))
        print(f"Report saved to: {report_path}")

        # Save configuration
        config.to_yaml(str(output_path / "config.yaml"))

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Evolve the Harmony algorithm using Pantheon Evolution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test run
  python run_evolution.py --iterations 10

  # Full evolution with output
  python run_evolution.py --iterations 100 --output results/

  # Verbose mode
  python run_evolution.py --iterations 50 --verbose
        """,
    )

    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=50,
        help="Maximum number of evolution iterations (default: 50)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output directory for results",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    try:
        result = asyncio.run(run_evolution(
            iterations=args.iterations,
            output_dir=args.output,
            verbose=args.verbose,
        ))
        print(f"\nFinal best score: {result.best_score:.4f}")
    except KeyboardInterrupt:
        print("\nEvolution interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
