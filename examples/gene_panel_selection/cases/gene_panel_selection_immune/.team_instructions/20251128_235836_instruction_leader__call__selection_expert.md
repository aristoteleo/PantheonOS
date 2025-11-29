Project: Human immune oncology gene profiling panel (complete to 1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Context available in your results/ dir:
- Downsampled datasets:
  - full features: results/adata_downsampled_full.h5ad
  - 3k gene subset: results/adata_downsampled_3k.h5ad
  - basic preprocessed 3k: results/adata_3k_with_basic.pp.h5ad
- Label key: cell_type
- Pre-established methods completed: SpaPROS, scGeneFit, RandomForest, HVG, DE
- Aggregated evidence: results/candidate_subpanel_evidence.csv
- Quick ARI curves: results/ari_vs_panelsize.csv; figure in results/figures/ari_vs_panel_size.png
- Intersections: results/figures/venn_top1500.png

Environment constraints:
- CPU-only, abundant RAM (see environment.md). Optimize for CPU.

Phase 2 — Complete to final 1000-gene immune-oncology panel with annotation and grouping
Goal: Curate a 1000-gene panel that:
- maintains strong cell-type separability across the TME
- profiles immune checkpoints and cytokine/chemokine axes, enabling activation vs exhaustion/dysfunction assessment
- covers key cancer signaling and hallmarks
- is organized into major categories and subcategories with clear annotations for interpretability

High-level guidance for composition (you may optimize counts empirically):
- Cell-type separability markers (immune, stromal, malignant states): ~350–450 genes
  - Cover: T cells (CD4, CD8, Treg, Tfh, naive/memory/activated/exhausted), NK, B/plasma (incl. isotypes/proxies), myeloid (mono/DC subsets, macrophage M1/M2, neutrophils), mast, endothelial, fibroblast/CAF subtypes (myCAF, iCAF, apCAF), pericytes, epithelial/malignant lineage and states.
- Cytokines, chemokines, receptors, immune checkpoints: ~200–250 genes
  - Include: IFNs and receptors, IL/TNF families, chemokines (CCL/CXCL) and receptors (CCR/CXCR), checkpoints/co-receptors (PDCD1/PD-1, CD274/PD-L1, CTLA4, TIGIT, LAG3, HAVCR2/TIM-3, BTLA, VISTA, SIGLECs, KLRs), costimulators (CD28, ICOS, 4-1BB/TNFRSF9, OX40/TNFRSF4, GITR/TNFRSF18), cytotoxicity (GZMK/GZMB/PRF1/NKG7) and exhaustion modules (TOX, NR4A, EOMES, PDCD1, LAG3, HAVCR2, TIGIT).
- Antigen processing/presentation and immune evasion: ~80–120 genes
  - HLA class I/II, B2M, TAP1/2, NLRC5, CIITA, ERAP1/2, proteasome immunosubunits (PSMB8/9/10), CD47/SIRPA axis, HLA loss proxies.
- Cancer signaling pathways and hallmarks: ~200–250 genes
  - JAK-STAT, IFN signaling; NF-κB; PI3K/AKT/mTOR; MAPK/ERK; WNT/β-catenin; Notch; TGF-β; Hippo (YAP/TAZ); apoptosis (intrinsic/extrinsic) and autophagy; DNA damage/repair (HR, NHEJ, MMR); cell cycle; MYC; p53; hypoxia (HIF1A axis); EMT; angiogenesis; metabolic modules (glycolysis, OXPHOS, fatty acid metabolism) as needed for state scoring.
- Tissue/ECM/stromal context: ~60–100 genes
  - Collagens, integrins, MMPs, CAF markers (ACTA2, TAGLN, COL1A1/1A2/3A1, FAP, PDPN, PDGFRB), endothelial markers (KDR/FLT1/TEK/PECAM1/VCAM1/SELE), pericyte markers (RGS5/PDGFRB/MCAM), adhesion and ECM regulators.

Requirements for deliverables:
1) Final panel CSV (exactly 1000 rows) at results/final_panel_1000.csv with columns:
   - gene_symbol (HGNC), category, subcategory/pathway, short_function, evidence_sources (semicolon-separated links/IDs), methods_appearance (which pre-established methods selected it), relevance_score (0–1), notes
   Ensure all genes are present in the dataset’s var and deduplicated; avoid MT- and ribosomal unless justified.

2) A readable markdown summary: results/final_panel_summary.md
   - Brief rationale of allocation across categories; counts per category and per subcategory
   - Key genes for exhaustion/activation, antigen presentation, and major signaling axes
   - Pointers to evidence tables and figures

3) Benchmarking and comparison:
   - Build 1000-gene panels from each method (top 1000 from SpaPROS, scGeneFit, RandomForest, HVG, DE) and compare to the final panel.
   - Evaluation on downsampled_3k dataset using stratified splits (CPU-friendly): report ARI, NMI, silhouette index boxplots; and UMAPs per panel with quantitative UMAP similarity versus full 3k.
   - Save metrics table: results/benchmark_metrics.csv and figures under results/figures/ (boxplots, UMAP grids, UMAP similarity barplot).

4) Gene group artifacts to aid downstream use:
   - results/final_panel_grouping.json: mapping from category->subcategory->list of genes
   - results/final_panel_references.bib (if you compile bib entries) or results/final_panel_references.md containing key literature/web refs used for contextual curation.

General notes:
- Keep all computations within your agent workdir. You may use the full dataset text/meta for context but run selection/benchmarking on downsampled_3k to keep CPU time reasonable.
- Use the prior candidate_subpanel as the separability backbone; complete contextually to 1000 according to the guidance, refining as needed based on ARI/NMI.
- Ensure consistent gene symbol casing and remove duplicates.
- Produce a brief changelog in your workdir when done.
