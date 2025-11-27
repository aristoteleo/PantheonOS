Curation notes for final 1000-gene immune-oncology panel (Vizgen-style)

Overview
- We compiled a candidate pool from the aggregate ranking (~1500 genes) built from HVG, DE (immune, malignant), SpaPROS, scGeneFit, and Random Forest, plus necessary oncology/immune markers.
- For each gene, we computed a biological relevance score (0–5) integrating dataset expression robustness (pct_expr, mean_expr, gini_proxy), method consensus (methods_count and aggregate_score), and known TME biology (lineage markers, cytokine/chemokine/checkpoints, oncology pathways, cell-cycle/DDR/stress/hypoxia/EMT/ECM/vasculature). We applied spatial suitability heuristics to deprioritize ribosomal/mitochondrial/KRTAP families.
- We targeted approximately: Immune lineage/state ~400; Cytokine/Chemokine/Checkpoint ~200; Cancer pathways ~200; CellCycle/DDR/Stress ~120; Hypoxia/Angio/EMT/ECM/Vasc ~80. Because the dataset var=3k limited availability per category, we supplemented from aggregate ranking using curated gene sets to hit totals while ensuring spatial detectability.

Method intersections
- We provide a Venn diagram of DE vs scGeneFit vs RF (curated/figures/venn_methods.png) and an UpSet plot for all 5 methods (curated/figures/upset_methods.png, copied from overlap/).

Trade-offs and manual inclusions
- Certain lowly expressed cytokines/checkpoints were retained if essential (e.g., PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, TOX, BATF, CXCL13) with relaxed spatial thresholds.
- HLA coverage ensured (HLA-A, HLA-B, HLA-C, HLA-DPA1, HLA-DPB1, HLA-DRA, HLA-DRB1). Broad antigen presentation and immune activation markers included (CD69, CD40LG, HLA class II).
- Malignant discrimination markers added (EPCAM, KRTs, MUC1) alongside leukocyte PTPRC; key RTKs and pathways (EGFR, ERBB2/3, KRAS, BRAF, MAPK1/3, PI3K/PTEN/AKT/MTOR, JAK/STAT, TGFb/SMADs, WNT/FZD/CTNNB1).
- Cell cycle/DDR/stress/hypoxia/EMT/ECM/vasculature enriched for state readouts (MKI67, TOP2A, PCNA, MCMs, E2Fs, TP53/CDKN1A/MDM2, ATM/ATR/CHEK1/2, BRCA1/2 proxies, HSPs, ATF4/DDIT3, HIF1A/VEGFA/KDR/FLT1/ANGPT2/VWF, VIM/FN1/SNAI/TWIST, COLs/PDGFRA/B/PECAM1/EMCN/VWF).

Panel balance
- Final 1000 genes are grouped and saved in final_panel_1000_grouped.tsv for easy review. Binary method presence table is in curated/tables/method_panels_presence.csv.

Validation
- UMAPs generated using only the final 1000 genes (curated/figures/umap_finalpanel_Immune_broad.png and ...cell_type.png). A quick RF benchmark can be added if needed.

Limitations
- Some pathway categories have fewer dataset-driven candidates due to var=3k constraint; we compensated from aggregate/global curated lists. Spatial detectability is heuristically assessed; further probe design review (e.g., low complexity and off-target risk) is recommended for a Vizgen deployment.


## v2 adjustments applied (2025-11-27T09:57:39.680990Z)
Adds (5): CD1C, ENTPD1, NT5E, ICOSLG, CLEC4C
Drops (8): IL9, IL13, IL22, IL37, TRBC1, IGLC3, WNT1, WNT7A

## v2 adjustments applied (2025-11-27T09:57:45.369199Z)
Adds (5): CD1C, ENTPD1, NT5E, ICOSLG, CLEC4C
Drops (8): IL9, IL13, IL22, IL37, TRBC1, IGLC3, WNT1, WNT7A