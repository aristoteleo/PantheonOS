# Gene Panel RL Evolution Example

This example demonstrates how to use **Pantheon Evolution** to optimize an RL-based gene panel selection algorithm for single-cell RNA sequencing data.

## Overview

Gene panel selection aims to identify a minimal set of genes that best captures cell type heterogeneity. This example uses reinforcement learning to learn which genes to include in the panel, optimizing for:
- Clustering quality (ARI, NMI, SI metrics)
- Panel size compliance (target ~500 genes)
- Training efficiency

Pantheon Evolution automatically improves the RL algorithm through:
- LLM-guided code mutations
- Multi-objective fitness evaluation
- MAP-Elites quality-diversity optimization

## Files

```
examples/evolution_gene_panel/
├── README.md              # This file
├── rl_gene_panel.py       # Initial RL implementation (evolution target)
├── evaluator.py           # Fitness evaluation function
├── run_evolution.py       # Main evolution script
├── evaluate_gene_panel.py # Final panel metrics
├── run_preprocessing.py   # Data preprocessing script
└── outputs/               # Preprocessed data directory (created by run_preprocessing.py)
    ├── adata_preprocessed.h5ad
    └── scores.pkl
```

## Quick Start

### 1. Prepare Data

First, run the preprocessing script to create the required data:

```bash
# Download the source data to ~/Downloads/ (see run_preprocessing.py for path)
# Then run preprocessing:
python run_preprocessing.py
```

This creates:
- `outputs/adata_preprocessed.h5ad` - Preprocessed AnnData object
- `outputs/scores.pkl` - Gene selection scores from multiple methods

### 2. Test the Evaluator

Verify the evaluator works correctly:

```bash
python evaluator.py
```

Expected output:
```
Gene Panel RL Evaluator Test
============================================================
Workspace: /path/to/evolution_gene_panel
Data dir: /path/to/evolution_gene_panel/outputs

Running evaluation (epochs=10)...
------------------------------------------------------------
  final_ari: 0.XXXX
  final_nmi: 0.XXXX
  final_si: X.XXXX
  size_score: X.XXXX
  training_speed: 0.XXXX
  ...
```

### 3. Run Evolution

```bash
# Quick test (5 iterations)
python run_evolution.py --iterations 5

# Full evolution with saved results
python run_evolution.py --iterations 50 --output results/

# Verbose mode
python run_evolution.py --iterations 20 --verbose --output results/
```

### 4. Use the CLI

You can also run evolution using the Pantheon CLI:

```bash
python -m pantheon.evolution \
    --initial rl_gene_panel.py \
    --evaluator evaluator.py \
    --objective "Optimize RL gene panel selection for ARI and training speed" \
    --iterations 50 \
    --output results/
```

## Evaluation Metrics

The evaluator measures six aspects of the RL implementation:

| Metric | Weight | Description |
|--------|--------|-------------|
| **Final ARI** | 35% | Adjusted Rand Index of best panel |
| **Final NMI** | 15% | Normalized Mutual Information |
| **Final SI** | 10% | Separation Index (inter/intra cluster distances) |
| **Size Score** | 15% | Panel size compliance (1.0 if ≤500, penalized above) |
| **Training Speed** | 15% | 1/(1 + training_time/60) |
| **Convergence Improvement** | 10% | Late rewards - early rewards |

## Evolution Targets

The evolution process targets these areas for improvement:

### 1. Reward Function (`reward_panel`) - Primary Target
**Current:** `reward = alpha * ari + (1 - alpha) * size_term`

Possible improvements:
- Multi-metric rewards (include NMI/SI)
- Progressive penalties based on training progress
- Non-linear ARI scaling
- Diversity bonuses for pathway coverage

### 2. Exploration Strategy (`explore`) - Secondary Target
**Current:** ε-greedy with Gaussian noise, top-K selection

Possible improvements:
- Boltzmann/temperature-based sampling
- UCB-style exploration bonuses
- Elite gene preservation strategies
- Adaptive noise schedules

### 3. Optimization (`optimize`) - Tertiary Target
**Current:** Single-step TD, fixed entropy coefficient

Possible improvements:
- GAE (Generalized Advantage Estimation)
- Entropy coefficient scheduling
- Advantage normalization
- PPO-style clipping

## Customization

### Modify the Objective

Edit the `objective` string in `run_evolution.py`:

```python
objective = """Focus on clustering quality:
- Maximize ARI above 0.8
- Secondary goal: reduce panel size below 400
- Maintain fast training (< 2 minutes)
"""
```

### Adjust Evaluation Weights

Modify `fitness_weights` in `evaluator.py`:

```python
fitness_weights = {
    "final_ari": 0.50,      # Increase ARI weight
    "final_nmi": 0.10,
    "final_si": 0.05,
    "size_score": 0.20,     # Increase size weight
    "training_speed": 0.10,
    "convergence_improvement": 0.05,
}
```

### Change Evolution Settings

Adjust parameters in `run_evolution.py`:

```python
config = EvolutionConfig(
    max_iterations=100,          # More iterations
    num_workers=2,               # Adjust for your hardware
    max_parallel_evaluations=1,  # Reduce if GPU limited
    evaluation_timeout=600,      # Longer timeout
)
```

## Programmatic Usage

Use the evolution module directly in Python:

```python
import asyncio
from pantheon.evolution import EvolutionTeam, EvolutionConfig
from pantheon.evolution.program import CodebaseSnapshot

async def main():
    config = EvolutionConfig(
        max_iterations=50,
        num_islands=2,
        evaluation_timeout=300,
    )

    initial_code = CodebaseSnapshot.from_single_file(
        "rl_gene_panel.py",
        open("rl_gene_panel.py").read()
    )

    team = EvolutionTeam(config=config)
    result = await team.evolve(
        initial_code=initial_code,
        evaluator_code=open("evaluator.py").read(),
        objective="Optimize for ARI while maintaining fast training",
    )

    print(f"Best score: {result.best_score}")
    with open("rl_gene_panel_optimized.py", "w") as f:
        f.write(result.best_code)

asyncio.run(main())
```

## Validating Results

After evolution, validate the best evolved algorithm with full training:

```python
from evaluator import evaluate_full

result = evaluate_full("results/", epochs=50)
print(f"Full evaluation ARI: {result['final_ari']:.4f}")
print(f"Panel size: {result['panel_size']}")
```

## Dependencies

- torch
- numpy
- scipy
- scanpy
- scikit-learn
- anndata
- pandas

## References

- Single-cell gene selection methods used for prior knowledge injection:
  - Highly Variable Genes (HVG)
  - Differential Expression
  - Random Forest feature importance
  - scGeneFit LP-based selection
