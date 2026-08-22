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

``scfm_list_models``
~~~~~~~~~~~~~~~~~~~~

Returns the catalog of available foundation models. Optional filters:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``task``
     - Filter by task type: ``"embed"``, ``"annotate"``, ``"integrate"``, or ``"perturb"``
   * - ``skill_ready_only``
     - If ``True`` (default), only models with a complete adapter spec

Each entry includes name, version, skill-ready status, tasks, modalities, species,
gene-ID scheme, and hardware requirements.

.. code-block:: text

   Agent: [calls scfm_list_models(task="annotate")]
   → geneformer     v2.0    annotate, embed
   → scgpt          v0.2.1  annotate, embed, perturb
   → ...

``scfm_select_model``
~~~~~~~~~~~~~~~~~~~~~

Recommend a model for a given dataset and task. This does **not** store an active model
for later calls — pass the chosen ``model_name`` explicitly to ``scfm_run``.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``adata_path``
     - Path to the input AnnData (``.h5ad``) file (**required**)
   * - ``task``
     - Task type: ``"embed"``, ``"annotate"``, or ``"integrate"`` (**required**)
   * - ``prefer_zero_shot``
     - Prefer models that do not require fine-tuning (default ``True``)
   * - ``max_vram_gb``
     - Optional VRAM constraint

``scfm_run``
~~~~~~~~~~~~

Execute a foundation model task. ``task``, ``model_name``, and ``adata_path`` are all
required — there is no implicit "currently selected" model.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``task``
     - Task type: ``"embed"``, ``"annotate"``, or ``"integrate"``
   * - ``model_name``
     - Model to run (from ``scfm_list_models`` / ``scfm_select_model``)
   * - ``adata_path``
     - Path to the input AnnData (``.h5ad``) file
   * - ``output_path``
     - Where to write the result AnnData (default: overwrite ``adata_path``)
   * - ``batch_key``
     - Optional ``.obs`` column for batch information
   * - ``label_key``
     - Optional ``.obs`` column for cell-type labels (annotation)
   * - ``device``
     - ``"auto"`` (default), ``"cuda"``, or ``"cpu"``
   * - ``batch_size``
     - Optional inference batch size

``scfm_interpret_results``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Generate QA metrics and visualizations for model output. Both ``adata_path`` and
``task`` are required.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``adata_path``
     - Path to the ``.h5ad`` file that already contains model outputs
   * - ``task``
     - The task that was executed
   * - ``output_dir``
     - Directory for visualization files (default: same directory as ``adata_path``)
   * - ``generate_umap``
     - Whether to generate UMAP plots (default ``True``)
   * - ``color_by``
     - Optional list of ``.obs`` columns to color UMAP by

Related tools: ``scfm_describe_model(model_name)`` for a full spec, and
``scfm_profile_data(adata_path)`` / ``scfm_preprocess_validate`` for dataset checks
before a run.

Usage Examples
--------------

Cell type annotation workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Annotate the cell types in my PBMC dataset at data/pbmc.h5ad.
   Agent: [calls scfm_list_models(task="annotate")]
          [calls scfm_select_model(adata_path="data/pbmc.h5ad", task="annotate")]
          [calls scfm_run(task="annotate", model_name="geneformer",
                          adata_path="data/pbmc.h5ad",
                          output_path="data/pbmc_annotated.h5ad")]
   → Added predicted labels to .obs (key depends on the model)

   User: Which cells have low confidence?
   Agent: [calls scfm_interpret_results(adata_path="data/pbmc_annotated.h5ad",
                                       task="annotate")]
   → QA metrics and UMAP paths for the annotation result.

Embedding for integration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Programmatic use — every argument is explicit
   rec = scfm.scfm_select_model(adata_path="data/dataset_a.h5ad", task="embed")
   result = scfm.scfm_run(
       task="embed",
       model_name=rec["recommended"]["name"],
       adata_path="data/dataset_a.h5ad",
       output_path="data/dataset_a_embedded.h5ad",
   )
   # Embeddings land in adata.obsm under the model's output key
   # (see scfm_describe_model for the exact key)

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
