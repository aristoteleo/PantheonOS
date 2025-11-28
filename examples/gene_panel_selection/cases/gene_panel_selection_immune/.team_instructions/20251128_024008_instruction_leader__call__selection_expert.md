Project: Human immune oncology panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Phase 2 — selection methods and panel goals

Context:
- Use the downsampled dataset as the sole input for pre-established selection algorithms:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000.h5ad
- Final panel size target: 1000 genes with category annotations.
- Biological goals: enable resolution of immune lineages and cell states (activation/exhaustion/memory/cytotoxicity), characterize cytokine milieu, and cover key cancer signaling pathways for tumor cell staging.

Plan:
- Prepare to run: HVG, SpaPROS, scGeneFit, Differential Expression, Random Forest. Curate a unified candidate set.
- Define annotation categories per selection_goals.md, and incorporate ontology (e.g., HLA, cytokines/chemokines, checkpoints) during curation.

Deliverables for this phase:
- A short method plan note summarizing which selection algorithms will be run and any method-specific parameters adapted to this dataset.
- Any precomputed indices or cached objects to accelerate subsequent runs.
- Confirmation of readiness to execute Phase 3.