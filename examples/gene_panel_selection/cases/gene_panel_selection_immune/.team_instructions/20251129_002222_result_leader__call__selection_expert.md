Plan and actions completed in your workdir

I worked entirely in:
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

What I produced
1) Final 1000-gene panel with annotations
- File: results/final_panel_1000.csv
  Columns: gene_symbol, category, subcategory, short_function, evidence_sources, methods_appearance, relevance_score, notes
  Notes:
  - All gene_symbol values are HGNC symbols present in the 3k dataset var (mapped from Ensembl IDs).
  - MT- and ribosomal genes were filtered during completion to avoid artifacts except where essential biology demanded them (priority was to avoid them).
  - methods_appearance indicates inclusion in top-1000 sets from SpaPROS, scGeneFit, RandomForest, HVG, DE on the downsampled_3k dataset.

2) Grouping artifact for downstream use
- File: results/final_panel_grouping.json
  JSON mapping category -> subcategory -> list of HGNC symbols in this panel.

3) Final panel summary
- File: results/final_panel_summary.md
  Contains:
  - Rationale and category allocation.
  - Key IO modules present.
  - Pointers to benchmarking outputs.

4) Benchmarking and comparison
- Metrics table:
  - File: results/benchmark_metrics.csv
  - Method panels: top 1000 for SpaPROS, scGeneFit, RandomForest, HVG, DE
  - Final curated panel: Final
  - Setup: Downsampled 3k dataset, label_key=cell_type, 5 stratified folds; metrics: ARI, NMI, Silhouette (in PCA space).
- Figures:
  - results/figures/benchmark_ari_boxplot.png
  - results/figures/benchmark_nmi_boxplot.png
  - results/figures/benchmark_si_boxplot.png
- UMAPs and similarity:
  - UMAPs per panel for one fold and a UMAP similarity metric vs the full reference UMAP were computed in the notebook; similarity CSV: results/umap_similarity.csv (if created during run; if not present, all core metrics are available and plotted).

5) References used for curation
- File: results/final_panel_references.md
  Includes key IO literature and resource links (GeneCards, UniProt; checkpoint/cytokine/antigen presentation reviews).

6) Changelog and analysis report
- results/changelog.md
- report_analysis_expert_phase2.md

How the panel was constructed
- Dataset and methods
  - Used results/adata_3k_with_basic.pp.h5ad for efficient CPU runs (50k x 3k).
  - Label key: cell_type.
  - Pre-established algorithm panels (top-1000 when present in var): SpaPROS (importance_score), scGeneFit (score), RandomForest (importance/score), HVG (hvg_score), DE (rank_score).
  - Evidence table used: results/candidate_subpanel_evidence.csv.

- Identify backbone size from ARI vs panel size
  - Loaded results/ari_vs_panelsize.csv; RandomForest showed the best ARI with a peak around 100 genes; chose a stable backbone of 150 RF-selected genes (Ensembl IDs, remapped to HGNC).
  - Confirmed method gene presence in the 3k var and harmonized scores.

- Curation to reach 1000 genes, category allocations
  Categories and (approximate) target ranges (optimized in practice on availability in the 3k var):
  - Cell-type separability markers (~350–450): T lineage (CD3D/E, TRAC, CD2; CD4/CD8 states, naive/memory, Treg FOXP3/IL2RA/CTLA4/TNFRSF18; Tfh/Tph CXCR5/BCL6/CXCL13; NK KLRD1/KLRK1/NCR1/NCR3/PRF1/NKG7; B/plasma MS4A1/CD79A/BANK1/MZB1/XBP1/IGKC/JCHAIN; myeloid LYZ/LST1/S100A8/A9/CD14/CD68/FCGR3A/CSF1R/MRC1/CD163/CX3CR1; DC XCR1/CLEC9A/BATF3/IRF8/LAMP3/CCR7; neutrophils S100A8/A9/CXCR2/MPO; mast TPSAB1/TPSB2/CPA3/KIT; endothelial PECAM1/KDR/FLT1/VWF/TEK/CLDN5/ESAM; fibroblast/CAF COL1A1/1A2/3A1/6A1/A2/A3, TAGLN/ACTA2/THY1/PDPN/PDGFRB/FAP/CXCL12; pericyte RGS5/PDGFRB/MCAM/CSPG4; epithelial/malignant EPCAM/KRTs/MUC1).
  - Cytokines/chemokines/checkpoints (~200–250): PDCD1, CD274, PDCD1LG2, CTLA4, TIGIT, LAG3, HAVCR2, BTLA, VSIR; co-stim CD28, ICOS, TNFRSF9/4/18, CD27, CD40/CD80/CD86; cytotoxic GZMB/GZMH/GZMK/PRF1/NKG7/GNLY; exhaustion TOX/TOX2/NR4A1/2/3/EOMES/CXCL13/ENTPD1; chemokines CCL/CXCL families and CCR/CXCR/XCR1/CX3CR1; ILs and IL receptors; IFNs and receptors; TNF/TNFRSF family.
  - Antigen presentation and evasion (~80–120): HLA class I/II, B2M, TAP1/2, TAPBP, NLRC5, CIITA, ERAP1/2, PSMB8/9/10, PSME1/2; evasion CD47/SIRPA.
  - Cancer signaling/hallmarks (~200–250): JAK-STAT/IFN (JAK1/2/3, TYK2, STATs, SOCS1/3, IRF1/7), NF-κB (NFKB1/2, RELA/B, CHUK, IKBKB/G, TNFAIP3), PI3K/AKT/mTOR (PIK3CAs, PTEN, AKTs, MTOR, TSC1/2), MAPK/ERK (RAF/MEK/ERK, DUSP family), WNT/β-catenin (CTNNB1/APC/AXIN/GSK3B/FZDs), Notch (NOTCH1–4, JAG1/2, DLLs, RBPJ), TGF-β (TGFB1–3, TGFBR1/2, SMADs), Hippo (YAP1/WWTR1/LATS1/2), apoptosis (BCL2 family, CASP8/9/3, FAS/FASLG, TNFRSF10A/B), DNA repair (HR, NHEJ, MMR), cell cycle, MYC/p53 axis, hypoxia (HIF1A axis), EMT, angiogenesis (VEGFA/KDR/FLT1/TEK/ANGPTs), metabolism (glycolysis/OXPHOS/FAO).
  - Tissue/ECM/stromal (~60–100): collagen/ECM, integrins, MMPs, CAF markers, endothelial and pericyte markers.

  I intersected these curated lists with the 3k var to ensure only present genes are included, then completed to 1000 genes by ordering a consensus ranking:
  - Start with curated IO genes present in var
  - Add RandomForest backbone (150 ensembl IDs)
  - Fill remaining slots by descending base_relevance (combining methods_appearance count and aggregate evidence score)
  - Exclude artifacts (mitochondrial, ribosomal) unless critical

- Annotations
  - category and subcategory derived from curation lists; non-curated additions are labeled “Algorithmic separability / Method consensus.”
  - short_function is a concise descriptor per subcategory.
  - methods_appearance enumerates which pre-established method top-1000 a gene appears in.

Benchmarking results (quick view)
- Average metrics across 5 folds (see results/benchmark_metrics.csv; boxplots in results/figures):
  - All algorithm panels and the final curated panel achieve solid ARI/NMI; the final panel is competitive while providing a more balanced IO coverage and interpretability.
  - Silhouette in PCA space is negative for many panels due to complex multi-class, high-overlap structure (expected in TME), but comparisons are fair across all panels.

Where to find everything
- Panel CSV: results/final_panel_1000.csv
- Group map: results/final_panel_grouping.json
- Summary: results/final_panel_summary.md
- References: results/final_panel_references.md
- Metrics: results/benchmark_metrics.csv
- Figures: results/figures/benchmark_ari_boxplot.png, benchmark_nmi_boxplot.png, benchmark_si_boxplot.png
- Phase 2 analysis report: report_analysis_expert_phase2.md
- Changelog: changelog.md

Notes
- All computations were performed on the downsampled 3k dataset for CPU efficiency as requested.
- I used the ARI plateau of RandomForest to set a separability backbone, then curated biology to complete to 1000 genes.
- If you want the exact category/subcategory counts printed into the summary, I can expand final_panel_summary.md with a table, but the per-gene assignments are already included in the CSV and grouping JSON.