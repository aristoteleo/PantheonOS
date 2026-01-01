Evolution System
================

Automatic code and agent improvement through LLM-guided mutations and evaluation.

.. contents:: On this page
   :local:
   :depth: 2

Overview
--------

Pantheon's Evolution System automatically optimizes code through iterative LLM-guided mutations and evaluations.

It uses evolutionary algorithms with:

- **LLM-guided mutations**: Intelligent code/prompt modifications
- **MAP-Elites**: Quality-diversity optimization preserving diverse solutions
- **Multi-island evolution**: Parallel search across different solution spaces
- **Hybrid evaluation**: Combine function-based metrics with LLM feedback

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                    Evolution Pipeline                       │
   │                                                             │
   │  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
   │  │ Initial  │───▶│  Mutate  │───▶│ Evaluate │              │
   │  │  Code    │    │  (LLM)   │    │ (Hybrid) │              │
   │  └──────────┘    └──────────┘    └────┬─────┘              │
   │                                       │                     │
   │  ┌──────────┐    ┌──────────┐    ┌────▼─────┐              │
   │  │ Improved │◀───│  Select  │◀───│ Archive  │              │
   │  │  Code    │    │  (Elite) │    │(MAP-Elites)│             │
   │  └──────────┘    └──────────┘    └──────────┘              │
   └─────────────────────────────────────────────────────────────┘

Code Evolution
--------------

The core feature for optimizing algorithm implementations.

Basic Usage
^^^^^^^^^^^

.. code-block:: python

   import asyncio
   from pantheon.evolution import EvolutionTeam, EvolutionConfig

   async def main():
       config = EvolutionConfig(
           max_iterations=100,
           num_islands=3,
       )

       team = EvolutionTeam(config=config)
       result = await team.evolve(
           initial_code=open("algorithm.py").read(),
           evaluator_code=open("evaluator.py").read(),
           objective="Optimize for speed while maintaining accuracy",
       )

       print(f"Best score: {result.best_score}")
       print(f"Improved code:\n{result.best_code}")

   asyncio.run(main())

Multi-file Codebase
^^^^^^^^^^^^^^^^^^^

For projects with multiple files:

.. code-block:: python

   from pantheon.evolution import EvolutionTeam, EvolutionConfig
   from pantheon.evolution.program import CodebaseSnapshot

   # Create snapshot from multiple files
   codebase = CodebaseSnapshot({
       "main.py": open("src/main.py").read(),
       "utils.py": open("src/utils.py").read(),
       "config.py": open("src/config.py").read(),
   })

   result = await team.evolve(
       initial_code=codebase,
       evaluator_code=evaluator_code,
       objective="Optimize the data processing pipeline",
   )

CLI Usage
^^^^^^^^^

Run evolution from the command line:

.. code-block:: bash

   # Basic usage
   python -m pantheon.evolution run \
       --initial algorithm.py \
       --evaluator evaluator.py \
       --objective "Optimize for speed" \
       --iterations 100

   # With output directory
   python -m pantheon.evolution run \
       --initial algorithm.py \
       --evaluator evaluator.py \
       --objective "Improve accuracy" \
       --iterations 50 \
       --output results/

   # Generate visualization
   python -m pantheon.evolution visualize results/

Writing an Evaluator
^^^^^^^^^^^^^^^^^^^^

The evaluator measures how well the code performs. It must define an ``evaluate(workspace_path)`` function:

.. code-block:: python

   # evaluator.py
   import time
   from pathlib import Path

   def evaluate(workspace_path: str) -> dict:
       """
       Evaluate the code in the workspace.

       Args:
           workspace_path: Directory containing the evolved code

       Returns:
           Dictionary with 'combined_score' (0-1) and optional metrics
       """
       workspace = Path(workspace_path)

       # Load the evolved module
       import importlib.util
       spec = importlib.util.spec_from_file_location(
           "module", workspace / "algorithm.py"
       )
       module = importlib.util.module_from_spec(spec)
       spec.loader.exec_module(module)

       # Run tests and measure performance
       try:
           start = time.time()
           result = module.run(test_data)
           elapsed = time.time() - start

           # Compute metrics
           accuracy = compute_accuracy(result, expected)
           speed_score = 1.0 / (1 + elapsed)

           return {
               "combined_score": 0.7 * accuracy + 0.3 * speed_score,
               "accuracy": accuracy,
               "speed_score": speed_score,
               "execution_time": elapsed,
           }
       except Exception as e:
           return {"combined_score": 0.0, "error": str(e)}

Configuration
^^^^^^^^^^^^^

``EvolutionConfig`` provides fine-grained control:

.. code-block:: python

   from pantheon.evolution import EvolutionConfig

   config = EvolutionConfig(
       # Evolution parameters
       max_iterations=100,          # Maximum iterations
       early_stop_generations=20,   # Stop if no improvement

       # Multi-island settings
       num_islands=3,               # Parallel populations
       migration_interval=20,       # Migration frequency
       migration_rate=0.1,          # Fraction to migrate

       # Evaluation
       evaluation_timeout=120,      # Seconds per evaluation
       max_parallel_evaluations=4,  # Parallel evaluations
       function_weight=0.7,         # Weight for evaluator score
       llm_weight=0.3,              # Weight for LLM feedback

       # Mutation
       temperature=0.7,             # LLM creativity (0.1-0.9)
       diff_based_evolution=True,   # Use diffs vs full rewrites

       # Persistence
       db_path="./evolution_db",    # Save progress
       checkpoint_interval=10,      # Checkpoint frequency
   )

**Preset Configurations:**

.. code-block:: python

   from pantheon.evolution import (
       get_fast_config,      # 20 iterations, 1 island (quick test)
       get_balanced_config,  # 100 iterations, 3 islands (default)
       get_thorough_config,  # 500 iterations, 5 islands (deep search)
   )

   config = get_fast_config()

Example: Harmony Algorithm
--------------------------

A complete example optimizing the Harmony batch correction algorithm.

Directory Structure
^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   examples/evolution_harmonypy/
   ├── harmony.py          # Initial implementation (evolution target)
   ├── evaluator.py        # Fitness evaluation function
   ├── run_evolution.py    # Main evolution script
   └── README.md           # Detailed instructions

Running the Example
^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   cd examples/evolution_harmonypy

   # Test the evaluator first
   python evaluator.py

   # Run evolution (quick test)
   python run_evolution.py --iterations 10

   # Full evolution with saved results
   python run_evolution.py --iterations 100 --output results/

Evaluation Metrics
^^^^^^^^^^^^^^^^^^

The Harmony evaluator measures four aspects:

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Metric
     - Weight
     - Description
   * - Batch Mixing
     - 40%
     - How well different batches are mixed after correction
   * - Bio Conservation
     - 30%
     - How well biological structure is preserved
   * - Speed
     - 20%
     - Execution time (faster = better)
   * - Convergence
     - 10%
     - Quality and speed of convergence

Expected Results
^^^^^^^^^^^^^^^^

After 100 iterations, typical improvements:

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Metric
     - Initial
     - Optimized
     - Improvement
   * - Combined Score
     - 0.52
     - 0.68
     - +31%
   * - Mixing Score
     - 0.61
     - 0.75
     - +23%
   * - Speed Score
     - 0.42
     - 0.58
     - +38%

Programmatic Usage
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import asyncio
   from pathlib import Path
   from pantheon.evolution import EvolutionTeam, EvolutionConfig
   from pantheon.evolution.program import CodebaseSnapshot

   async def main():
       example_dir = Path("examples/evolution_harmonypy")

       config = EvolutionConfig(
           max_iterations=100,
           num_islands=3,
           num_inspirations=2,
           evaluation_timeout=120,
       )

       # Define optimization objective
       objective = """Optimize the Harmony algorithm for:
       1. Integration Quality (40%): Improve batch mixing
       2. Performance (20%): Reduce execution time
       3. Convergence (10%): Faster, stable convergence
       4. Bio Conservation (30%): Preserve biological variance
       """

       team = EvolutionTeam(config=config)
       result = await team.evolve(
           initial_code=CodebaseSnapshot.from_single_file(
               "harmony.py",
               (example_dir / "harmony.py").read_text()
           ),
           evaluator_code=(example_dir / "evaluator.py").read_text(),
           objective=objective,
       )

       # Save optimized code
       Path("harmony_optimized.py").write_text(result.best_code)
       print(result.get_summary())

   asyncio.run(main())

EvolutionToolSet
----------------

Integrate evolution into agent workflows:

.. code-block:: python

   from pantheon import Agent
   from pantheon.toolsets import EvolutionToolSet

   agent = Agent(
       name="code-optimizer",
       instructions="You optimize algorithms using evolutionary methods.",
   )
   agent.toolset(EvolutionToolSet("evolve"))

   response = await agent.run("""
   Optimize this sorting algorithm for better performance.
   Use 50 iterations and save results to ./optimized/
   """)

Best Practices
--------------

1. **Start with a working baseline**: Ensure your initial code runs correctly
2. **Write comprehensive evaluators**: Cover all important metrics
3. **Use appropriate timeouts**: Allow enough time for complex evaluations
4. **Save checkpoints**: Use ``db_path`` for long evolution runs
5. **Validate results**: Test evolved code on held-out test cases
6. **Monitor diversity**: Use multiple islands for better exploration
7. **Iterate on objectives**: Refine your objective description for better results
