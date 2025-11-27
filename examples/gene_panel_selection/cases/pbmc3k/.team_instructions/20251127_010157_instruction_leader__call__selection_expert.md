Project: PBMC3k gene panel selection

Paths:
- Input AnnData (may already be preprocessed): /home/erwinpi/Vizgen/pbmc3k_preprocessed.h5ad
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir
- Your sub-workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert
- Environment details: see /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/environment.md

Context and goals:
- Biological context: Human PBMC 3k dataset. Aim to distinguish major immune cell populations (CD4 T, CD8 T, NK, B cells, monocyte subsets CD14+/FCGR3A+, dendritic cells; platelets if present). We also want to optionally capture activation/cell-cycle variability without over-representing mitochondrial/ribosomal genes.
- Goal: Perform comprehensive gene panel selection using multiple complementary approaches, then curate final practical panels. Target panel sizes: 50, 96, and 150 genes. Include a small set of housekeeping controls and 1-2 negative/background probes.

Tasks:
1) Understanding and dataset prep
- Load the provided AnnData, summarize n_cells, n_genes, sparsity, key obs columns (clusters/labels if any), and basic QC.
- If n_cells > 50,000, downsample to < 50,000 cells and save the downsampled AnnData path. IMPORTANT: If downsampled, save and REPLY with the new AnnData path to be used for all subsequent work. If not downsampled, confirm we continue with the original.

2) Run multiple gene selection approaches and produce intermediate outputs/figures:
- HVG
- SpaPros
- scGeneFit
- Differential expression markers per cluster/cell type (choose clustering method if none present)
- Random Forest (or similar) feature importance for cell type prediction

3) Integration and curation:
- Combine ranked lists from all methods; penalize MT- and RP- genes; require reasonable expression fraction; prioritize robust markers for target cell types.
- Seed with canonical immune markers (e.g., CD3D/E, CCR7, IL7R, LST1/LYZ, MS4A1, NKG7, GNLY, FCGR3A, S100A8/A9 if present), validating with the data.
- Add controls: 4–6 housekeeping genes and 1–2 negatives.
- Produce curated panels at sizes 50, 96, 150.

4) Deliverables:
- Save: per-method ranked lists (CSV), integrated ranking (CSV), and final curated panels (CSV/JSON) with annotations (gene, category: marker/control/negative; target cell types; source methods supporting).
- Save figures: UMAPs, dotplots/heatmaps showing marker specificity; metrics of classification accuracy using only panel genes.
- Update the project workdir todolist.md, checking off completed items and noting final AnnData path used.

Please proceed autonomously and reply with: dataset summary, any downsampling actions and final AnnData path, the locations of your saved results (CSVs/PNGs/JSON), and any key observations that will guide biologist interpretation.