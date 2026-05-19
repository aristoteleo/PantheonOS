Project: Immune-oncology 1000-gene panel (human TME)

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Inputs
- adata_path (raw): /home/erwinpi/data/adata.h5ad
- Dataset: bioRxiv preprint 2024 — DOI: 10.1101/2024.01.17.576110
- Environment summary file (read-only): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md

Biological context & goal
Design a high-quality 1000-gene panel for human tumor microenvironment profiling suitable for spatial deployment (Vizgen-style). The panel must:
1) Resolve major immune cell types and regulatory subsets (T/NK/B/plasma; macrophage/monocyte/DC/neutrophil; Tregs, MDSCs)
2) Capture cancer signaling and hallmarks: oncogenes, TSGs, cell cycle, DNA damage, hypoxia/angiogenesis/EMT, proliferation
3) Profile cytokines/chemokines and immune states: IL/TNF/IFN families; exhaustion (PDCD1, LAG3, HAVCR2, TIGIT, etc.); activation/cytotoxic/inflammation
4) Distinguish malignant vs non-malignant; enable tumor subclone and pathway states (MAPK, PI3K, JAK-STAT, TGF-β, WNT)
5) Enable analysis of exhaustion/activation/proliferation/senescence/stress programs

Step 1 — Understanding/QC/Downsampling
Please start by understanding the dataset and performing QC/EDA. If the dataset has >50k cells, downsample to <50k cells while preserving diversity across clusters/tissues; save the downsampled AnnData and report its new path. Generate standard QC plots (UMAPs by clusters/annotations, metrics). Provide initial annotations: major immune lineages, epithelial/tumor vs stroma, and a first-pass malignant vs non-malignant call if feasible (e.g., CNV-based/marker-based heuristic). Save all outputs in your workdir.

Notes
- Use the environment summary for guidance on installed packages and hardware. Keep intermediates compact due to limited free disk on /.
- After downsampling, treat the downsampled adata as the only input for all subsequent steps.
