Store & Skills
==============

The Pantheon Store is a package registry for agent skills, team templates, and toolset
configurations. Install published packages from the store or load local skills defined in
your workspace.

Overview
--------

Skills are modular bundles that extend what agents can do — they can add domain-specific
knowledge, pre-built prompt templates, specialized tool configurations, or entire team
setups. The store makes it easy to share and reuse these bundles across projects and
installations.

Three sources of skills are available:

- **Pantheon Store** — published packages with versioning, ratings, and metadata.
- **Local skills** — files in ``.pantheon/skills/`` loaded automatically.
- **Factory templates** — the built-in templates shipped with Pantheon and updated via
  ``pantheon update-templates``.

Browsing the Store
------------------

Open the **Store** panel from the sidebar (the shopping bag icon).

- **Search** — type keywords in the search bar to filter packages by name or description.
- **Categories** — click a category tab (e.g., *Omics*, *Bioimaging*) to filter by domain.
- **Package details** — click a package card to open the detail view, which shows the
  author, version history, a description of what the package provides (skills, templates,
  toolsets), and the source repository URL.
- **Ratings** — community ratings are shown as a star score averaged over user installs.

Installing & Uninstalling Packages
-----------------------------------

**Install**

Click **Install** on a package card or in the detail view. Pantheon downloads the package
and writes its contents into the current ``.pantheon/`` workspace:

.. code-block:: text

   .pantheon/
   ├── skills/        ← skill YAML files from the package
   ├── teams/         ← team template files from the package
   └── toolsets/      ← toolset config files from the package

After install, click **Reload Settings** (or it reloads automatically) to make the new
skills available to agents in the current session.

**Uninstall**

Open the **Installed** tab in the Store panel. Click **Uninstall** next to a package.
Pantheon removes the files the package placed in ``.pantheon/`` and reloads settings.

.. note::

   Uninstalling a package only removes the files it originally installed. If you manually
   edited those files, your edits may have been overwritten on the last update. Back up
   custom changes before upgrading a package.

Local Skills
------------

Skills defined in ``.pantheon/skills/`` are automatically loaded at startup and after a
settings reload. No store interaction is required.

A local skill file is a YAML document that defines prompts, tool configurations, or
knowledge context available to agents. Example:

.. code-block:: yaml

   name: rna_seq_helper
   description: Provides RNA-seq analysis guidance and tool configurations
   system_prompt_extension: |
     You are an expert in bulk and single-cell RNA sequencing analysis.
     Preferred pipeline: Salmon → DESeq2 for bulk; Scanpy for single-cell.
   toolsets:
     - python_interpreter
     - r_interpreter

Place this file at ``.pantheon/skills/rna_seq_helper.yaml`` and reload settings to
activate it.

Skill Categories
----------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Category
     - Description
   * - **Omics**
     - RNA-seq, scRNA-seq, proteomics, multi-omics integration pipelines and analysis
       skills.
   * - **Live View**
     - Skills that configure and launch Live View viewers (IGV, Vitessce, Mol*, etc.)
       from agent responses.
   * - **Paper Writing**
     - Literature review, abstract drafting, citation management, and figure description
       skills.
   * - **Bioimaging**
     - Microscopy image analysis, segmentation, and visualization using Viv and related
       tools.
   * - **Structural Biology**
     - Protein structure prediction (AlphaFold, ESMFold), docking analysis, and 3D
       visualization via Mol*.
   * - **Rare Disease**
     - Clinical variant interpretation, HPO ontology integration, and gene-disease
       association skills.
   * - **Genomics**
     - Variant calling, genome assembly, annotation, and GWAS skills.
   * - **Coding Assistant**
     - Code review, test generation, documentation, and refactoring templates for
       multiple languages.
   * - **Data Science**
     - EDA templates, plotting, statistical testing, and machine learning pipeline skills.

CLI Equivalent
--------------

All store operations available in the UI can also be performed from the command line:

.. code-block:: bash

   # List available packages
   pantheon store list

   # Search for a package
   pantheon store search rna-seq

   # Install a package
   pantheon store install pantheon-omics-skills

   # Uninstall a package
   pantheon store remove pantheon-omics-skills

   # Show installed packages
   pantheon store installed

Factory Templates
-----------------

Factory templates are the built-in agent, team, and skill templates that ship with
Pantheon. They live inside the Pantheon package, not in your ``.pantheon/`` workspace.

Keep them up to date with:

.. code-block:: bash

   pantheon update-templates

This command downloads the latest versions of all built-in templates from the Pantheon
release channel. Run it after upgrading the ``pantheon-agents`` package to pick up new
or improved templates.

.. tip::

   If you have customized a built-in template by copying it into ``.pantheon/teams/``,
   your copy takes precedence over the factory default. Running ``update-templates`` does
   not overwrite files in your ``.pantheon/`` workspace.
