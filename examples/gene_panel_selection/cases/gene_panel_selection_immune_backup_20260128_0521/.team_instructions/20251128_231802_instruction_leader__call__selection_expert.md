Project: Human immune oncology gene profiling panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Context and goals:
- Dataset (original): /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad (bioRxiv 2024.01.17.576110)
- Downsampled datasets (previously created):
  - full features: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/results/adata_downsampled_full.h5ad
  - 3k gene subset: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/results/adata_downsampled_3k.h5ad
- Use label key: cell_type
- Environment constraints: CPU-only (no GPU), abundant RAM; please optimize runtime accordingly.

Panel purpose:
- Build a 1000-gene human immune-oncology profiling panel capable of:
  - resolving all major immune and stromal cell types and malignant states in TME
  - profiling cytokine and checkpoint axes; determining activation/exhaustion/dysfunction
  - covering key cancer signaling pathways (JAK-STAT, IFN, NF-κB, PI3K/AKT/mTOR, MAPK, WNT, Notch, TGF-β, Hippo, apoptosis, DNA damage/repair, hypoxia, EMT, angiogenesis, antigen presentation, immune evasion)
  - enabling downstream benchmarking (ARI/NMI/SI; UMAP similarity) and interpretability with gene annotations and grouping into major categories

What’s already done in your workdir/results:
- SpaPROS scores and top_1500
- scGeneFit scores
- Random Forest rf_top_1500
- Dataset summary and downsampled adatas

Requested next steps (phase 1):
1) Run the remaining pre-established selection methods on the downsampled 3k dataset using cell_type labels:
   - HVG
   - Differential Expression (one-vs-rest per cell_type and/or pairwise strategy per your standard practice)
   Save ranked score tables and top_1500 lists under results/gene_panels/{hvg,de}/.

2) Aggregate all five methods (SpaPROS, scGeneFit, RandomForest, HVG, DE). Build a subpanel optimized for cell-type separability according to your standard algorithmic procedure. Do not add contextual/pathway genes yet; focus purely on separability.
   - Output: CSV with candidate subpanel genes including per-method evidence/scores and an overall rank.
   - Produce Venn diagram for intersections of top lists and ARI vs panel size curves for each method on held-out splits per your standard benchmarking.

3) Write a concise markdown summary of phase 1 (methods, parameters, key observations, and links to artifacts) in your agent workdir.

Notes:
- Use /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md for environment context.
- Keep all artifacts within your agent directory structure.
- We will perform contextual completion to 1000 genes, annotations, grouping, and full benchmarking in phase 2 after briefly reviewing the subpanel.
