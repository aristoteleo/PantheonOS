Project: Immune-oncology 1000-gene panel (Human TME)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Environment summary: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager/environment_summary.txt

Active dataset input (ONLY INPUT):
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad

Context and objective:
- Produce the final curated 1000-gene immune-oncology panel suitable for spatial deployment (Vizgen-style), with full per-gene annotations and grouping, leveraging the outputs you already generated (HVG stability, DE, SpaPROS, scGeneFit, Random Forest, aggregate ranking, draft panels, UMAP metrics).

Curation requirements:
1) Start from your aggregate ranking and draft panels; consider the top ~1500 genes as the candidate pool.
2) Compute a biological relevance score per gene for human TME (0–5), using:
   - Expression specificity/robustness in the dataset (pct expressing, mean, Gini)
   - Method consensus (number of methods and average rank/score)
   - Known biology: lineage markers, cytokines/chemokines/receptors, checkpoints, signaling/pathway membership, tumor suppressor/oncogene, cell-cycle/DDR/stress/hypoxia/angiogenesis/EMT
   - Prefer genes with reliable spatial detectability (robust expression and sequence uniqueness; deprioritize very lowly expressed cytokines if necessary)
3) Balance the panel composition roughly as follows (flexible ±10–15%):
   - Immune lineage & state markers: ~400
   - Cytokines/chemokines/receptors & checkpoint axes: ~200
   - Cancer pathways (MAPK/PI3K/JAK-STAT/TGF-β/WNT/Notch/RTK) including oncogenes/tumor suppressors: ~200
   - Cell cycle, DNA damage/repair, stress, senescence: ~120
   - Hypoxia/angiogenesis/EMT/ECM/fibroblast/vasculature: ~80
4) Ensure coverage for major immune cell types and regulatory/myeloid subsets: T (CD3D/E, TRAC), CD4/CD8, Treg (FOXP3, IL2RA), Tfh (BCL6/ICOS), Th1/Th2/Th17 (TBX21/GATA3/RORC), NK (NKG7, KLRD1), B (MS4A1, CD79A/B), plasma (SDC1/PRDM1/IRF4), DC (ITGAX/CLEC9A/LAMP3), monocytes/macrophages (LST1/FCGR3A/ITGAM/CSF1R/MRC1/MARCO), neutrophils (S100A8/A9/FCGR3B), MDSC markers as feasible.
5) Ensure checkpoint/exhaustion/activation markers: PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, TOX, BATF, CXCL13; activation CD69, CD40LG, HLA-DRA/DRB1; cytotoxic GZMB/PRF1/IFNG; inflammation SPP1, IL1B, TNF; add TNFRSF9, ICOS, and relevant TNFRSF/TNFSF members.
6) Malignant vs non-malignant and subclones: include EPCAM, KRTs, MUC1, PTPRC exclusion markers; key RTKs (EGFR, ERBB2/3), RAS/RAF/MEK axis (KRAS, BRAF, MAPK1/3), PI3K/PTEN/AKT/MTOR, JAK/STAT, TGF-β (TGFB1, TGFBR1/2, SMADs), WNT (ligands, FZD, CTNNB1), cell state markers (MKI67, TOP2A, PCNA, MCMs, E2Fs), TP53 pathway (TP53, CDKN1A, MDM2), DDR (ATM/ATR, CHEK1/2, BRCA1/2 proxies), stress (HSPA1A/B, HSPH1, ATF4, DDIT3), hypoxia/angiogenesis (HIF1A, VEGFA, KDR, FLT1, ANGPT2, VWF), EMT (VIM, FN1, SNAI1/2, TWIST1/2), fibroblast/ECM (COL1A1/A2, COL3A1, COL4A1, DCN, PDGFRA/B), vasculature (PECAM1, EMCN, KDR, VWF).
7) HLA presentation breadth: HLA-A/B/C and class II HLA-DPA1/DPB1/DR/DP where appropriate.
8) Produce the final curated 1000-gene panel and comprehensive annotations.

Outputs to produce in your workdir:
A) curated/final_panel_1000.csv with columns at minimum:
   - gene_symbol, ensembl_id (if available), category_major, category_minor, methods_present (semicolon-separated), aggregate_rank, aggregate_score, bio_relevance_score_0to5, rationale_short, spatial_suitability (Yes/No), key_metadata (e.g., pct_expr, mean_expr)
B) curated/final_panel_1000_grouped.tsv grouped by major categories for easy viewing
C) curated/final_panel_coverage_summary.md: bullet coverage stats by category and lineage/pathway
D) Figures:
   - figures/venn_methods.png: at least one Venn diagram comparing intersections between three representative methods (e.g., DE, scGeneFit, RF)
   - figures/upset_methods.png: existing UpSet for all 5 methods; if already created, link or copy to curated/figures
   - figures/umap_finalpanel_[Immune_broad|cell_type|Malignant_vs_Other].png: UMAPs using only the 1000-gene panel
   - figures/confusion_[cell_type|Malignant_vs_Other]_rf_finalpanel.png: quick RF performance using only final panel
E) tables/method_panels_presence.csv: binary presence of each final gene across methods
F) notes_curation.md: describe the curation logic, trade-offs, and any manual inclusions/exclusions to satisfy IO coverage and Vizgen suitability.

Please also update your selection round report with the curated panel paths, and provide a one-paragraph executive summary for downstream biologist and reporter.
