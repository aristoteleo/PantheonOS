Project: Immune-oncology 1000-gene panel for human TME profiling

Paths
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Selection expert workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Input anndata: /home/erwinpi/data/adata.h5ad

Environment
- OS: Ubuntu 22.04; Python 3.10 (conda env gps); GPUs: 2× H100; RAM ~188 GB.
- Key packages already installed: scanpy 1.11.5, anndata 0.11.4, scvi-tools 1.3.3 (GPU), scGeneFit, SpaPROS 0.1.5, squidpy 1.6.5, moscot 0.4.3, sklearn 1.7.2, statsmodels 0.14.5, igraph/leidenalg.
- See environment.md at repo root and logs under workdir/system_manager/ for details.

Dataset context
- Data source: bioRxiv 2024 preprint DOI 10.1101/2024.01.17.576110 (large pan-cancer TME compendium; ~356k cells, ~22.8k genes per initial summary present in your workdir).
- Biological goals:
  1) Resolve major immune types and key myeloid sublineages; include stromal (fibroblast, endothelial, epithelial) context for TME.
  2) Characterize cancer signaling and states: oncogenes, tumor suppressors, cell cycle, DNA damage, hypoxia, angiogenesis, EMT, proliferation.
  3) Profile cytokine/chemokine and checkpoint states (IL/IFN/TNF families; PDCD1/LAG3/HAVCR2/TIGIT, etc.); activation/cytotoxicity/inflammation.
  4) Distinguish malignant vs non-malignant; capture tumor heterogeneity and infer subclones; include pathway state readouts (MAPK, PI3K, JAK-STAT, TGF-β, WNT).
  5) Enable state analysis: exhaustion, activation, proliferation, senescence, stress programs.

Your tasks (execute sequentially and save artifacts in your workdir):

Step 1. Dataset understanding & QC (complete/extend)
- Load the adata; compute QC (mito/ribo), filter low-quality cells/genes, doublet detection if feasible at this scale; normalize/log1p; identify HVGs; PCA/Neighbors/UMAP; Leiden at multiple resolutions.
- Provide basic annotation of major compartments (T, NK, B, Plasma; Myeloid: Mono/Macro/DC/Neutro; plus Fibro/Endo/Epi) using canonical markers and available obs metadata; add malignant vs non-malignant labels (expression-based and/or inferCNV-like heuristic using reference normal immune/stromal bins).
- Score cell cycle; save annotated adata (backed .h5ad) and QC/UMAP figures. Reuse and extend existing notebooks/files already present in your workdir.

Step 2. Multi-strategy gene selection
- Methods to run: global HVGs and per-compartment HVGs; differential expression across major cell types and malignant vs non-malignant; state-focused DE (activation/cytotoxicity/exhaustion/proliferation); SpaPROS prioritization for spatial deployability; scGeneFit for cell-type/state separability; Random Forest (or gradient-boosted trees) feature importance for classifying major cell types.
- Aggregate per-gene scores across methods; maintain method provenance and scores. Target an overcomplete candidate set (~1500–2000 genes).

Step 3. Curation to final 1000-gene panel
- Curate to ensure coverage:
  • Identity markers for T/NK/B/Plasma and myeloid subtypes; include key stromal markers.
  • Cytokines/chemokines/checkpoints; cytotoxicity; activation/exhaustion; inflammation.
  • Oncogenes and tumor suppressors from curated lists; pathway readouts (MAPK, PI3K, JAK-STAT, TGF-β, WNT), plus EMT/hypoxia/angiogenesis/DNA damage/cell cycle/senescence/stress programs.
- Balance for spatial assays (e.g., Vizgen): remove excessive lowly expressed or highly homologous genes; avoid too many mitochondrial/ribosomal genes (keep minimal QC set); ensure gene names map to HGNC.
- Output: final 1000-gene CSV/TSV (columns: gene_symbol, category, subcategory, rationale, source_methods, aggregate_score) plus JSON metadata; include coverage barplots and UMAP overlays using only panel genes to demonstrate separability.

Deliverables & filenames (suggested)
- Step 1: annotated_adata.h5ad (and .h5ad.backed if large), figures/qc_*.png, figures/umap_*.png.
- Step 2: candidates_aggregated.tsv, per-method gene lists + scores under selection_methods/.
- Step 3: panel_final_1000.tsv, panel_final_1000.json, figures/panel_coverage.png, figures/umap_panel_only.png.

Please proceed autonomously, documenting decisions in markdown within your workdir, and update the project todolist checkboxes as steps complete.