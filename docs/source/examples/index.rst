Examples
========

Explore complete working examples in the ``examples/`` directory of the repository.

Available Examples
------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Example
     - Description
   * - ``evolution_harmonypy/``
     - Code evolution example: optimizing the Harmony batch correction algorithm
   * - ``evolution_agent/``
     - Agent-guided code evolution workflow
   * - ``fastq_processing/``
     - FASTQ file processing pipeline
   * - ``paper_reporter/``
     - Academic paper analysis and summarization
   * - ``paper_reporter_v2/``
     - Enhanced multi-agent paper analysis
   * - ``single_cell_spatial_analysis/``
     - Single-cell and spatial genomics analysis

Running Examples
----------------

Each example directory contains a README with specific instructions. General pattern:

.. code-block:: bash

   cd examples/<example_name>

   # Read the README
   cat README.md

   # Run the example
   python run.py  # or main.py, depending on the example

Evolution Example
-----------------

The ``evolution_harmonypy`` example demonstrates code evolution:

.. code-block:: bash

   cd examples/evolution_harmonypy

   # Test the evaluator
   python evaluator.py

   # Run evolution (quick test)
   python run_evolution.py --iterations 10

See :doc:`/advanced/evolution` for detailed documentation on the Evolution System.
