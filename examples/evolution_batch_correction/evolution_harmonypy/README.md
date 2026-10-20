# Harmony Algorithm Evolution Example

This example demonstrates how to use **Pantheon Evolution** to optimize the [Harmony algorithm](https://github.com/slowkow/harmonypy) for single-cell data integration.

## Overview

Harmony is an algorithm for integrating multiple high-dimensional datasets, commonly used in single-cell RNA sequencing analysis to remove batch effects while preserving biological structure.

This example shows how Pantheon Evolution can automatically improve the algorithm through:
- LLM-guided code mutations
- Multi-objective fitness evaluation
- MAP-Elites quality-diversity optimization

## Files

```
examples/evolution_harmonypy/
├── README.md           # This file
├── harmony.py          # Initial Harmony implementation (evolution target)
├── evaluator.py        # Fitness evaluation function
└── run_evolution.py    # Main evolution script
```

## Quick Start

### 1. Test the Initial Implementation

First, verify the initial implementation and evaluator work:

```bash
# Test the evaluator
python evaluator.py
```

Expected output:
```
Evaluation Results:
----------------------------------------
  combined_score: 0.5XXX
  mixing_score: 0.6XXX
  bio_conservation_score: 0.5XXX
  speed_score: 0.4XXX
  convergence_score: 0.6XXX
  execution_time: 1.XXX
  iterations: 10
```

### 2. Run Evolution

```bash
# Quick test (10 iterations)
python run_evolution.py --iterations 10

# Full evolution with saved results
python run_evolution.py --iterations 100 --output results/

# Verbose mode
python run_evolution.py --iterations 50 --verbose --output results/
```

### 3. Use the CLI

You can also run evolution using the Pantheon CLI:

```bash
python -m pantheon.evolution \
    --initial harmony.py \
    --evaluator evaluator.py \
    --objective "Optimize Harmony for speed and integration quality" \
    --iterations 100 \
    --output results/
```

## Evaluation Metrics

The evaluator measures four aspects of the Harmony implementation:

| Metric | Weight | Description |
|--------|--------|-------------|
| **Batch Mixing** | 40% | How well different batches are mixed after correction |
| **Bio Conservation** | 30% | How well biological structure is preserved |
| **Speed** | 20% | Execution time (faster = better) |
| **Convergence** | 10% | Quality and speed of convergence |

### Combined Score

```
combined_score = 0.4 * mixing + 0.3 * bio + 0.2 * speed + 0.1 * convergence
```

## Optimization Targets

The evolution process targets these areas for improvement:

1. **Matrix Operations**: Vectorization of distance computations
2. **Clustering Step**: Efficient centroid updates
3. **Correction Step**: Optimized ridge regression
4. **Convergence**: Better stopping criteria

## Example Results

After 100 iterations, typical improvements:

| Metric | Initial | Optimized | Improvement |
|--------|---------|-----------|-------------|
| Combined Score | 0.52 | 0.68 | +31% |
| Mixing Score | 0.61 | 0.75 | +23% |
| Speed Score | 0.42 | 0.58 | +38% |

## Customization

### Modify the Objective

Edit the `objective` string in `run_evolution.py` to focus on specific aspects:

```python
objective = """Focus on speed optimization:
- Reduce execution time by 50%
- Maintain integration quality above 0.6
- Use vectorized numpy operations
"""
```

### Adjust Evaluation Weights

Modify the weights in `evaluator.py`:

```python
combined_score = (
    0.5 * mixing_score +    # Increase focus on mixing
    0.2 * bio_score +       # Reduce bio weight
    0.2 * speed_score +
    0.1 * conv_score
)
```

### Change Test Data

Adjust the synthetic data parameters:

```python
X, batch_labels, true_labels = generate_test_data(
    n_cells=5000,           # More cells
    n_features=100,         # More features
    n_batches=5,            # More batches
    batch_effect_strength=3.0,  # Stronger effects
)
```

## Programmatic Usage

Use the evolution module directly in Python:

```python
import asyncio
from pantheon.evolution import EvolutionTeam, EvolutionConfig

async def main():
    config = EvolutionConfig(
        max_iterations=100,
        num_islands=3,
    )

    team = EvolutionTeam(config=config)
    result = await team.evolve(
        initial_code=open("harmony.py").read(),
        evaluator_code=open("evaluator.py").read(),
        objective="Optimize for speed while maintaining quality",
    )

    print(f"Best score: {result.best_score}")
    print(f"Improved code:\n{result.best_code}")

asyncio.run(main())
```

## Using with Agent ToolSet

Integrate evolution into an Agent workflow:

```python
from pantheon.agent import Agent
from pantheon.toolsets import EvolutionToolSet

agent = Agent(
    name="code-optimizer",
    instructions="You optimize algorithms using evolutionary methods.",
)
agent.toolset(EvolutionToolSet("evolve"))

response = await agent.run("""
Optimize this Harmony implementation for better batch mixing.
Use 50 iterations and save results to ./optimized/
""")
```

## References

- [Harmony Paper](https://www.nature.com/articles/s41592-019-0619-0): Korsunsky et al., "Fast, sensitive and accurate integration of single-cell data with Harmony", Nature Methods, 2019.
- [harmonypy](https://github.com/slowkow/harmonypy): Python implementation by Kamil Slowikowski
- [Pantheon Evolution](../../../apps/agent/pantheon/evolution/): Evolutionary code optimization framework
