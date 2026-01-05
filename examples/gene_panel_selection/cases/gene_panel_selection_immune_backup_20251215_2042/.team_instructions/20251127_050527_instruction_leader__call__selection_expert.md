Project: Immune-oncology 1000-gene panel (Human TME)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Environment summary: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager/environment_summary.txt

Dataset
- Initial adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Source: bioRxiv 2024 preprint DOI: 10.1101/2024.01.17.576110

High-level tasks for this phase (Understanding/QC/Downsampling):
1) Load the dataset and perform QC to understand cells and genes. Identify if it exceeds thresholds (>50k cells and/or >3k genes). If so, downsample to <=50k cells and subset genes to <=3k. If multiple tissues or tumors are present, stratified downsampling is preferred to preserve composition.
2) Save standard QC plots (n_genes, n_counts, mito%, basic UMAP) to your workdir. Document key observations in a markdown note.
3) IMPORTANT: If downsampled, save the new adata to disk and return the path. From now on, this downsampled adata becomes THE ONLY input for the rest of the study. Record it clearly in a file named active_adata_path.txt in your workdir.

Biological context and goals:
- Build a 1000-gene panel that:
  - Resolves major immune lineages: T, NK, B, plasma; myeloid (macrophages, monocytes, DCs, neutrophils); regulators (Tregs, MDSCs)
  - Captures cancer signaling: oncogenes, tumor suppressors, cell cycle, DNA damage/stress, hypoxia, angiogenesis, EMT, proliferation
  - Profiles cytokines/chemokines and immune states (exhaustion, activation, cytotoxicity, inflammation)
  - Distinguishes malignant vs non-malignant; identify subclones; cover MAPK, PI3K, JAK-STAT, TGF-β, WNT
  - Enables cell-state analysis: exhaustion, activation, proliferation, senescence, stress
- Deployment target: spatial transcriptomics (Vizgen-style), so prioritize genes with robust expression and specificity.

Deliverables for this step:
- QC summary note (markdown) and figures
- Downsampled adata path (if applied) and active_adata_path.txt
- Brief note whether malignant annotations are present and any existing metadata that will be useful for panel selection
