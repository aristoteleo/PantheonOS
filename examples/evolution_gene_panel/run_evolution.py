#!/usr/bin/env python
"""
Run Pantheon Evolution on the RL Gene Panel Selection Algorithm.

This script evolves the RL-based gene panel selection algorithm to optimize:
1. Final panel quality (ARI, NMI, SI clustering metrics)
2. Training efficiency (faster convergence)
3. Panel size compliance (target 500 genes, max 1000)

Usage:
    python run_evolution.py [--iterations N] [--output DIR]

Example:
    python run_evolution.py --iterations 50 --output results/
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load .env file from the example directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Set GENE_PANEL_DATA_DIR so evaluator can find data when running in temp workspace
_example_dir = Path(__file__).parent.resolve()
os.environ.setdefault("GENE_PANEL_DATA_DIR", str(_example_dir / "data"))


async def run_evolution(
    iterations: int = 50,
    output_dir: str = None,
    verbose: bool = False,
    resume: str = None,
):
    """
    Run the evolution process.

    Args:
        iterations: Maximum number of evolution iterations
        output_dir: Directory to save results
        verbose: Enable verbose logging
        resume: Path to resume evolution from
    """
    from pantheon.evolution import EvolutionTeam, EvolutionConfig
    from pantheon.evolution.program import CodebaseSnapshot

    example_dir = Path(__file__).parent
    rl_panel_path = example_dir / "rl_gene_panel.py"
    evaluator_path = example_dir / "evaluator.py"

    initial_code = CodebaseSnapshot.from_single_file("rl_gene_panel.py", rl_panel_path.read_text())
    evaluator_code = evaluator_path.read_text()

    config_path = Path(output_dir) / "config.yaml" if output_dir else None
    if config_path and config_path.exists():
        config = EvolutionConfig.from_yaml(str(config_path))
        config.max_iterations = iterations
        config.log_level = "DEBUG" if verbose else "INFO"
        print(f"Loaded config from: {config_path}")
    else:
        config = EvolutionConfig(
            max_iterations=iterations,
            num_workers=1,              # Fewer workers (heavy GPU training)
            num_islands=2,              # Simpler population structure
            num_inspirations=2,
            num_top_programs=3,
            max_parallel_evaluations=2, # Limited by GPU memory
            evaluation_timeout=600,     # 10 minutes per evaluation
            analyzer_timeout=180,
            feature_dimensions=["final_ari", "training_speed", "size_score"],
            early_stop_generations=30,
            function_weight=1.0,        # Function-only evaluation
            llm_weight=0.0,
            log_level="DEBUG" if verbose else "INFO",
            log_iterations=True,
            checkpoint_interval=5,
            db_path=output_dir,
        )

    objective = """Optimize the RL-based gene panel selection algorithm for:

1. **Final Panel Quality** (60% weight): Maximize ARI, NMI, SI clustering metrics.
   - ARI (Adjusted Rand Index) measures clustering agreement with ground truth
   - NMI (Normalized Mutual Information) provides additional clustering quality signal
   - SI (Separation Index) measures inter-cluster vs intra-cluster distances

2. **Training Efficiency** (25% weight): Faster convergence to good solutions.
   - Reduce number of epochs needed to find good panels
   - Improve early convergence behavior
   - Better reward signal leads to faster learning

3. **Panel Size Compliance** (15% weight): Target 500 genes, maximum 1000.
   - Panels at or below 500 genes receive full size score
   - Penalty increases linearly from 500 to 1000 genes
   - Panels above 1000 genes receive zero size score

Evolution Targets (priority order):
1. **Reward function (reward_panel)** - Primary target
   - Current: `reward = alpha * ari + (1 - alpha) * size_term`
   - Possible improvements: multi-metric rewards (include NMI/SI), progressive penalties,
     non-linear ARI scaling, diversity bonuses for pathway coverage

2. **Exploration strategy (SmartCurationTrainer.explore)** - Secondary target
   - Current: epsilon-greedy with Gaussian noise, top-K selection
   - Possible improvements: Boltzmann/temperature-based sampling, UCB-style exploration,
     elite gene preservation, adaptive noise schedules

3. **Optimization (SmartCurationTrainer.optimize)** - Tertiary target
   - Current: single-step TD, fixed entropy coefficient
   - Possible improvements: GAE (Generalized Advantage Estimation), entropy scheduling,
     advantage normalization, PPO-style clipping

Constraints:
- **Keep public API unchanged**: `train_gene_panel_selector` function signature must remain stable
- **Maintain numerical stability**: Clamp probabilities, handle edge cases properly
- **Don't remove essential functionality**: Keep core RL components working
- **Keep imports stable**: Don't add new external dependencies
"""

    print("=" * 60)
    print("Pantheon Evolution: RL Gene Panel Selection")
    print("=" * 60)
    print(f"\nInitial code: {rl_panel_path}")
    print(f"Evaluator: {evaluator_path}")
    print(f"Iterations: {iterations}")
    print(f"Output: {output_dir or 'None (results not saved)'}")
    if resume:
        print(f"Resume from: {resume}")
    print("\n" + "-" * 60)
    print("Starting evolution...\n")

    team = EvolutionTeam(config=config)
    result = await team.evolve(
        initial_code=initial_code,
        evaluator_code=evaluator_code,
        objective=objective,
        resume_from=resume,
    )

    print("\n" + "=" * 60)
    print(result.get_summary())

    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        best_code_path = output_path / "rl_gene_panel_optimized.py"
        best_code_path.write_text(result.best_code)
        print(f"\nBest code saved to: {best_code_path}")

        report_path = output_path / "evolution_report.json"
        result.save_report(str(report_path))
        print(f"Report saved to: {report_path}")

        config.to_yaml(str(output_path / "config.yaml"))

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Evolve the RL gene panel selection algorithm using Pantheon Evolution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test run
  python run_evolution.py --iterations 5

  # Full evolution with output
  python run_evolution.py --iterations 50 --output results/

  # Verbose mode
  python run_evolution.py --iterations 20 --verbose

  # Resume from checkpoint
  python run_evolution.py --iterations 50 --output results/ --resume results/
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
    parser.add_argument(
        "--resume", "-r",
        type=str,
        default=None,
        help="Resume evolution from checkpoint directory",
    )

    args = parser.parse_args()

    try:
        result = asyncio.run(run_evolution(
            iterations=args.iterations,
            output_dir=args.output,
            verbose=args.verbose,
            resume=args.resume,
        ))
        print(f"\nFinal best score: {result.best_score:.4f}")
    except KeyboardInterrupt:
        print("\nEvolution interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
