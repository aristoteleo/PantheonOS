Project: Immune oncology gene panel
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Phase 2 — Step 2A: Preprocess + HVG + DE

Inputs
- AnnData path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- label_key: cell_type

Tasks
1) Preprocess minimally if needed: normalize_total, log1p, highly_variable_genes; compute PCA/neighbors for QC plots.
2) HVG: compute HVG scores and export a ranked list with scores to hvg_scores.csv; plot mean-variance and save.
3) DE (one-vs-rest over cell_type): run rank_genes_groups with method=wilcoxon; export aggregated per-gene scores across groups (e.g., sum of scores/best score) to de_scores_wilcoxon.csv. Optionally also run logistic regression and export de_scores_logreg.csv if feasible.
4) Save intermediate AnnData (if created) in your workdir.
5) Create a short markdown summary with any QC figures.

Please execute only these and save outputs in your workdir. Use sparse-aware operations and avoid memory spikes.