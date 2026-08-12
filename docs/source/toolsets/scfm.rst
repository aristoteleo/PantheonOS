Single-Cell Foundation Models (SCFM)
=====================================

The ``SCFMToolSet`` provides agents with access to a curated collection of single-cell foundation models. Agents can list available models, select the right one for a task, run it on single-cell data, and interpret the results — all through natural-language interaction.

Overview
--------

Single-cell foundation models (SCFMs) are large pre-trained models for single-cell RNA-seq and related omics data. They perform tasks such as:

- **Cell type annotation** — predict cell types from expression profiles
- **Embedding generation** — low-dimensional representations for clustering or integration
- **Perturbation prediction** — predict transcriptional response to a drug or genetic perturbation
- **Gene regulatory inference** — infer regulatory networks from expression data
- **Batch correction / integration** — harmonize data across experiments

Quick Start
-----------

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets.scfm import SCFMToolSet

   scfm = SCFMToolSet(name="scfm")

   agent = Agent(
       name="sc_analyst",
       instructions="You are a single-cell analysis expert."
   )
   await agent.toolset(scfm)
   await agent.chat()

Tool Reference
--------------

``list_scfm_models``
~~~~~~~~~~~~~~~~~~~~

Returns the full catalog of available foundation models with name, version, task types, and data requirements.

.. code-block:: text

   Agent: [calls list_scfm_models]
   → scGPT          v0.2.1  cell annotation, embedding, perturbation
   → Geneformer     v2.0    cell annotation, embedding, dosage sensitivity
   → UCE            v1.0    embedding, cross-species
   → TOSICA         v1.0    cell type annotation
   → scFoundation   v1.0    embedding, cell annotation
   → ...

``select_scfm_model``
~~~~~~~~~~~~~~~~~~~~~

Choose a model for the current task. Sets the active model for subsequent ``run_scfm`` calls.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``model_name``
     - Name of the model (from ``list_scfm_models``)
   * - ``task``
     - Task type: ``"annotation"``, ``"embedding"``, ``"perturbation"``, ``"integration"``

``run_scfm``
~~~~~~~~~~~~

Execute the selected model on input data.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``input_path``
     - Path to the input AnnData (.h5ad) file
   * - ``output_path``
     - Where to write the result AnnData with added embeddings / annotations
   * - ``task``
     - Task override (optional — defaults to the task set in ``select_scfm_model``)
   * - ``kwargs``
     - Model-specific parameters (batch size, gene set, etc.)

``interpret_scfm_results``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Ask the model to interpret its own output: explain predicted cell types, highlight uncertain predictions, flag low-quality cells, or summarize the embedding structure.

Usage Examples
--------------

Cell type annotation workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Annotate the cell types in my PBMC dataset at data/pbmc.h5ad.
   Agent: [calls list_scfm_models → selects Geneformer for annotation]
          [calls run_scfm(input="data/pbmc.h5ad", output="data/pbmc_annotated.h5ad")]
   → Added column 'predicted_cell_type' to .obs
   → Confidence scores in .obs['scfm_confidence']

   User: Which cells have low confidence?
   Agent: [calls interpret_scfm_results]
   → 143 cells below 0.5 confidence — mostly at cluster boundaries.
      Recommend manual review of clusters 3 and 7.

Embedding for integration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Programmatic use
   await scfm.select_scfm_model(model_name="scGPT", task="embedding")
   result = await scfm.run_scfm(
       input_path="data/dataset_a.h5ad",
       output_path="data/dataset_a_embedded.h5ad",
   )
   # adata.obsm["X_scgpt"] now contains the embeddings

Requirements
------------

Model weights are downloaded automatically on first use to a local cache (typically ``~/.scfm_cache/``). Disk requirements vary by model (1–10 GB). GPU acceleration is supported when a CUDA-capable device is available; CPU inference is also supported but slower.

.. note::

   The SCFM catalog is extensible. Additional models can be registered via the skill system (see ``.pantheon/skills/omics/`` for examples of model registration prompts and configurations).

See Also
--------

- :doc:`/toolsets/python_interpreter` — Run custom single-cell Python workflows
- :doc:`/toolsets/notebook` — Interactive Jupyter notebook for exploratory analysis
- Omics skills (``.pantheon/skills/omics/``) — curated analysis workflows for scRNA-seq, spatial, and more
