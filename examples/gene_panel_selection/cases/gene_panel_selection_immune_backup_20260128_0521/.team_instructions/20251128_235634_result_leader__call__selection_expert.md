Plan and status for Phase 1 (completed now)

What I did in your agent workdir:
- Location: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

1) Remaining pre-established methods (HVG, DE) on downsampled 3k
- HVG
  - Scanpy highly_variable_genes on the 3k dataset (flavor=seurat_v3, n_top_genes=3000).
  - Saved:
    - results/gene_panels/hvg/hvg_scores.csv
    - results/gene_panels/hvg/hvg_top_1500.csv
- Differential Expression (one-vs-rest per cell_type)
  - Methods run: wilcoxon and t-test_overestim_var; ranked all genes per group.
  - Aggregated per gene across groups/methods using:
    rank_score = 0.5*rank(max score) + 0.4*rank(max logFC) + 0.1*rank(-log10(min adj p))
  - Saved:
    - results/gene_panels/de/de_longtable.csv
    - results/gene_panels/de/de_aggregated_scores.csv
    - results/gene_panels/de/de_top_1500.csv

2) Aggregate five methods and build separability-first subpanel
- Aggregation across SpaPROS, scGeneFit, RandomForest, HVG, DE:
  - Merged all per-gene scores, min-max normalized each method’s score, computed overall_score as the mean across available methods.
  - Saved evidence table: results/candidate_subpanel_evidence.csv
- Intersections figure:
  - Saved Venn/UpSet plot of top-1500 lists: results/figures/venn_top1500.png
- ARI vs panel size curves:
  - Implemented several CPU-optimized notebook pipelines; ultimately executed a quick pass (stratified ~6k subsample, sizes=[50,100,200,400,700,1000], PCA<=30, Leiden res=1.0).
  - Outputs produced:
    - results/ari_vs_panelsize.csv
    - results/figures/ari_vs_panel_size.png
- Best method/size by ARI (quick pass):
  - Best row: RandomForest, size=100, ARI ≈ 0.514
  - Derived and saved candidate subpanel (no contextual additions, separability only):
    - results/candidate_subpanel.csv
    - This is 100 genes ordered by the RandomForest method’s top list, with per-method evidence columns merged and a panel_rank column.

3) Concise Phase 1 summary written
- Report file (concise): report_analysis_expert_phase1.md
- Methods/parameters sheet: report_analysis_expert_phase1_methods_params.md

Key artifacts for Phase 1
- Dataset:
  - results/adata_downsampled_3k.h5ad
- HVG:
  - results/gene_panels/hvg/hvg_scores.csv
  - results/gene_panels/hvg/hvg_top_1500.csv
- DE:
  - results/gene_panels/de/de_longtable.csv
  - results/gene_panels/de/de_aggregated_scores.csv
  - results/gene_panels/de/de_top_1500.csv
- Aggregated evidence:
  - results/candidate_subpanel_evidence.csv
- Intersections figure:
  - results/figures/venn_top1500.png
- ARI vs size (quick pass):
  - results/ari_vs_panelsize.csv
  - results/figures/ari_vs_panel_size.png
- Candidate subpanel (separability-only, to be contextually expanded in Phase 2):
  - results/candidate_subpanel.csv

Notes on runtime and environment
- CPU-only; abundant RAM. I optimized ARI computations by:
  - Using a stratified subsample (~6k cells) to preserve class balance
  - Limiting sizes and PCA components (<=30) while keeping robust clustering (neighbors=15; Leiden res=1.0)
- This produced stable ARI curves quickly and allowed selection of the best initial subpanel for separability.
- The Venn plotting required a package; I coordinated installation and used venn with upsetplot fallback.

Next steps (Phase 2, as requested in your plan)
- Contextual completion up to 1000 genes:
  - Add pathway/cytokine/checkpoint/activation-exhaustion/apoptosis/DDR/hypoxia/EMT/angiogenesis/APC/immune evasion coverage.
  - Use browser-based evidence (GeneCards, GO, UniProt, literature) for curated additions with references.
- Annotations and grouping into major categories for interpretability.
- Full benchmarking on original full dataset using 5 non-redundant subsets (<50k cells each): ARI/NMI/SI boxplots and UMAP similarity, comparing:
  - All algorithmic 1000-gene panels vs the final curated 1000-gene panel vs full gene set.

Where to find things
- Notebooks containing HVG/DE and aggregation: notebooks/02_methods_hvg_de_and_subpanel.ipynb
- ARI computation notebooks (in addition to the executed quick pass via system manager):
  - notebooks/03_ari_curves_and_subpanel.ipynb
  - notebooks/04_quick_ari.ipynb

If you want me to re-run the ARI with:
- different panel sizes,
- a 3-fold CV protocol,
- or a larger stratified subsample (e.g., 10–20k) for more precision,
I can do so and update the candidate_subpanel accordingly.