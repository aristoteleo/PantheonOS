Project: Human immune-oncology gene profiling panel
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Task phase 1: Dataset sourcing, inspection, and QC to prepare inputs for gene panel selection.

Context and goals
- We aim to build a 1000-gene human immune-oncology panel enabling:
  - Comprehensive cell-type cataloging across the tumor microenvironment (TME)
  - Immune profiling and cell-state characterization via cytokines/chemokines and immune checkpoint/exhaustion markers
  - Cancer signaling pathway characterization to distinguish malignant cell states/stages
- The panel will be used broadly across solid tumors; it must generalize across cancer types and sample sources.

Data strategy (if no user dataset provided)
- Assemble a representative compendium of public human scRNA-seq datasets spanning multiple solid tumors and immune contexts. Prefer curated sources like TISCH2/GEO/ArrayExpress with clear usage terms. Aim to include at least: melanoma, non-small cell lung cancer, colorectal cancer, breast cancer (incl. TNBC), head and neck squamous cell carcinoma, ovarian cancer, and renal cell carcinoma. Include datasets with known T cell exhaustion, diverse myeloid states, CAFs, and endothelial cells.
- Optionally include peripheral blood (PBMC) or adjacent normal where available to diversify immune states.

Environment constraints (from environment.md)
- CPU-only (no GPU), 56 logical cores, ~1.5 TB RAM; disk free ~1.3 TB. You can handle large memory jobs but avoid GPU-specific code.

Required in this phase
1) Ingest and QC each dataset; harmonize gene symbols to HGNC; standard filtering thresholds; annotate cell-type labels (use provided annotations when available; otherwise, use robust automated labeling).
2) Build a unified AnnData with per-dataset batch labels and harmonized cell-type ontologies (immune subsets, stromal, endothelial, malignant).
3) Downsample if total cells > 50k, preserving representation of:
   - T cell states (naive, effector, memory, exhausted), NK
   - B cells and plasma cells
   - Monocytes/macrophages/DCs (subsets incl. M1/M2-like, cDC1/cDC2, pDC)
   - Malignant cells (diverse signaling states), CAFs, endothelial cells
4) If genes > 3000, subset appropriately to prepare for pre-established selection algorithms later.
5) Save:
   - Path to the unified full dataset
   - Path to the downsampled dataset for selection algorithms (this will be the sole input for SpaPROS, scGeneFit, RF, HVG, DE)
   - Summary markdown describing datasets used, cell counts per major cell class, QC stats, and any downsampling strategy applied.
   - Any overview figures (UMAPs per dataset and integrated; cell-type composition barplots) to your workdir.

Do not run selection algorithms yet; this is only the dataset preparation phase. Return the paths to the full and downsampled AnnData objects and a brief summary of what you prepared.