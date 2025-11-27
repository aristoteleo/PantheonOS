# Final curation and delivery: Immune-oncology 1000-gene spatial panel (Human TME)

Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Active dataset: selection_expert/adata_downsampled_50k_3kHVG.h5ad (50k cells, 3k genes, human TME)

Summary
- Objective: deliver a curated 1000-gene panel suitable for Vizgen-style spatial profiling of the tumor microenvironment (TME). The panel must cover immune lineages/states, cytokines/chemokines/checkpoints, cancer pathway nodes, cell cycle/DDR/stress, and hypoxia/angiogenesis/EMT/ECM/vasculature, with per-gene annotations and method presence.
- Inputs leveraged: 5 selection methods (HVG stability, DE immune, DE malignant vs other, SpaPROS, scGeneFit, Random Forest), aggregate ranking, draft panels and UMAPs. Dataset expression metrics (pct_expr, mean_counts, standardized variance as gini_proxy) were used to compute a per-gene biological relevance score (0–5).
- Outputs delivered (curated/): final_panel_1000.csv; final_panel_1000_grouped.tsv; final_panel_coverage_summary.md; figures/venn_methods.png; figures/upset_methods.png; figures/umap_finalpanel_Immune_broad.png and ...cell_type.png; tables/method_panels_presence.csv; notes_curation.md.

Methods and scoring
1) Candidate pool: aggregate top ~1500 genes from combined method evidence.
2) Expression robustness: per-gene pct_expr = 100 - dropout; mean_expr = mean_counts; gini_proxy = vst standardized variance. Scaled and combined to an expr_score (0–3).
3) Method consensus: methods_count (0–5+) and aggregate_score scaled to consensus_score.
4) Biology priors: curated sets defined for each category (immune lineages, cytokines/chemokines/receptors/checkpoints; RTK/MAPK/PI3K/JAK-STAT/TGFβ/WNT/Notch and tumor suppressor/oncogene nodes; cell-cycle/DDR/stress; hypoxia/angiogenesis/EMT/ECM/vasculature). Boosts added for HLA and core checkpoint/exhaustion/CTL markers.
5) Spatial suitability heuristics: blacklist RPL/RPS/MT-/KRTAP/LOC; category-aware detectability thresholds (slightly relaxed for cytokines/checkpoints and mandated markers like PDCD1, CTLA4, etc.).

Composition balancing
- Target composition: Immune lineage/state ~400; Cytokines/Chemokines/Checkpoints ~200; Cancer pathways ~200; CellCycle/DDR/Stress ~120; Hypoxia/Angio/EMT/ECM/Vasc ~80.
- Because var=3k limited in-category availability, we augmented from the aggregate/global curated sets. Final list is assembled to 1000 genes and grouped by categories for review.

Coverage highlights (examples)
- Immune T cells: CD3D/E/G, TRAC, CD4, CD8A/B; Treg (FOXP3, IL2RA, IKZF2), Tfh (BCL6, ICOS), Th1/Th2/Th17 (TBX21, GATA3, RORC), activation (CD69, CD40LG), antigen presentation HLA class I (HLA-A/B/C) and class II (HLA-DRA/DRB1/DPA1/DPB1).
- B lineage: MS4A1, CD79A/B; plasma/plasmablasts SDC1, PRDM1, IRF4, XBP1, MZB1.
- NK/cytotoxic: NKG7, KLRD1, PRF1, GZMB/GZMK, IFNG.
- Myeloid: ITGAX/CLEC9A/LAMP3 DCs; monocyte/macrophage LST1/FCGR3A/ITGAM/CSF1R/MRC1/MARCO; neutrophil/MDSC S100A8/A9/FCGR3B.
- Checkpoint/exhaustion: PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, TOX, BATF; chemokines/chemokine receptors (CXCL9/10/11/13, CCL2/5/19/21; CCR/ CXCR families); ligands/receptors (CD274/PDCD1LG2; TNFRSF/TNFSF including TNFRSF9, CD27/CD40).
- Malignant/epithelial and exclusion: EPCAM, KRT8/18/19, MUC1; PTPRC.
- Cancer pathways: RTKs (EGFR, ERBB2/3, FGFRs, KDR/FLT1), RAS/RAF/MEK/ERK (KRAS, BRAF, MAPK1/3), PI3K/PTEN/AKT/MTOR axis, JAK/STAT, TGFβ/SMADs, WNT (ligands, FZD, CTNNB1), Notch.
- Cell state/DDR/stress: MKI67, TOP2A, PCNA, MCMs, E2Fs; TP53/CDKN1A/MDM2; ATM/ATR/CHEK1/2; BRCA1/2 proxies; HSP family, HSPH1, ATF4, DDIT3.
- Hypoxia/angiogenesis/EMT/ECM/vasculature: HIF1A, VEGFA, KDR, FLT1, ANGPT2, VWF; EMT markers VIM, FN1, SNAI1/2, TWIST1/2; ECM/fibroblast COL1A1/A2/3A1/4A1, DCN, PDGFRA/B; vasculature PECAM1, EMCN, VWF.

Figures
- Method intersections: curated/figures/venn_methods.png (DE vs scGeneFit vs RF) and curated/figures/upset_methods.png (all methods).
- UMAP using final panel: curated/figures/umap_finalpanel_Immune_broad.png and ...cell_type.png.

Tables
- curated/final_panel_1000.csv: gene_symbol, ensembl_id, category_major/minor, methods_present, aggregate_rank, aggregate_score, bio_relevance_score_0to5, rationale_short, spatial_suitability, key metadata (pct_expr, mean_expr, gini_proxy).
- curated/final_panel_1000_grouped.tsv: grouped by category for human reading.
- curated/tables/method_panels_presence.csv: binary presence across methods for final genes.

Path references
- Final CSV: selection_expert/curated/final_panel_1000.csv
- Grouped TSV: selection_expert/curated/final_panel_1000_grouped.tsv
- Coverage summary: selection_expert/curated/final_panel_coverage_summary.md
- Figures: selection_expert/curated/figures/
- Method presence: selection_expert/curated/tables/method_panels_presence.csv
- Notes: selection_expert/curated/notes_curation.md



### v2 changelog (2025-11-27T09:57:45.369420Z)
- Applied biologist-proposed gene additions/drops with category-balanced resolution using aggregate_score to trim/fill.
- Adds (5), Drops (8). Panel remains at 1000 genes.
- Recomputed method presence matrix and regenerated UMAPs and Random Forest confusion matrices using only v2 panel.
