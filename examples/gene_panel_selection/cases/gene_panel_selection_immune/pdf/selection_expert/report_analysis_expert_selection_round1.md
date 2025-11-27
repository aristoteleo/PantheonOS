Selection Round 1: Immune-oncology 1000-gene panel (Human TME)

Summary
- Active dataset: selection_expert/adata_downsampled_50k_3kHVG.h5ad (50,000 cells × 3,000 genes). Labels available: cell_type, Cell_type_broad (Malignant/Other), custom Malignant_vs_Other (added), Immune_broad (derived from cell_type).
- Methods executed and saved with parameters, ranked lists and figures:
  1) HVG stability across subsamples (Seurat v3 flavor; reps=5, frac=0.8, n_top=1500) → methods/HVG/
  2) Differential Expression: Immune_broad and Malignant_vs_Other with Wilcoxon; effect-size and pct filters → methods/DE/
  3) SpaPROS: label=Immune_broad, n_hvg=3000, num_markers=400 → methods/SpaPROS/
  4) scGeneFit: label=Cell_type_broad, method=centers, max_constraints=800 → methods/scGeneFit/
  5) Random Forest feature importance: multiclass cell_type (top1000) and binary Malignant_vs_Other (top600) → methods/RandomForest/
- Overlap visualization: overlap/upset_methods.png
- Aggregate ranking combining method presence, normalized ranks and scores: aggregate/aggregate_ranking_scores.csv
- Draft panels: draft_panels/panel_agg_top_{500,800,1000,1200}.csv; UMAP evaluations in draft_panels/umap/

Notes per method
1) HVG stability
- Approach: subsample 80% cells×5, compute HVGs (Seurat v3). Score = inclusion frequency − mean_rank/1500. Outputs: hvg_stability_ranked.csv, with HGNC symbols; frequency histogram. 
- Use: prioritize robustly variable genes across TME samples; good backbone for panel.

2) Differential expression
- rank_genes_groups (Wilcoxon) on Immune_broad and Malignant_vs_Other. Filters: log2FC>0.5, padj<0.05; additional pct criteria for Immune_broad.
- Aggregated per gene by within-group rank; produced DE_Immune_broad_ranked.csv and DE_Malignant_vs_Other_ranked.csv (+ _with_symbol).
- Use: lineage and state markers, and tumor vs other discriminants.

3) SpaPROS
- label=Immune_broad; selected 400 spatially informative markers. Outputs: spapros_scores.csv and spapros_top_400.csv.
- Use: niche-aware TME segmentation.

4) scGeneFit
- label=Cell_type_broad; centers constraints; max_constraints=800 per instruction. Outputs: scgenefit_scores.csv with ranked genes.
- Use: separability among predefined classes.

5) Random Forest
- Multiclass cell_type (top1000) and binary Malignant_vs_Other (top600) with feature importances saved.
- Use: non-linear discriminative features capturing combinatorial patterns.

Aggregation and panel drafts
- Combined methods into aggregate ranking using: aggregate_score = 2×(#methods present) + mean normalized rank + mean normalized score. Saved as aggregate/aggregate_ranking_scores.csv.
- Enforced inclusion of immune-oncology must-have markers when present in the dataset: lineage (CD3D/E, TRAC, FOXP3, NKG7, MS4A1, SDC1/PRDM1/IRF4, FCGR3A, LST1, ITGAM, ITGAX, CLEC10A, CCR7, S100A8/A9), checkpoints (PDCD1, CTLA4, LAG3, HAVCR2, TIGIT), cytotoxic/activation (GZMB, PRF1, IFNG, CD69, HLA-DRA/DRB1), inflammatory (SPP1, IL1B, TNF), cell-cycle/stress/hypoxia/EMT, and oncogenic pathways (EGFR, KRAS, BRAF, PIK3CA, PTEN, RB1, MYC, MAPK/PI3K/JAK-STAT/TGFb/WNT proxies).
- Draft panels saved at sizes 500/800/1000/1200 (HGNC+ENSG columns). UMAPs and silhouette metrics saved in draft_panels/umap/.

Curation-ready observations (gaps to fill later)
- Cytokines/chemokines: ensure broad coverage for CXCL/CCL and receptors (CCR/CXCR). Some may be lowly expressed in scRNA-seq; consider substitutes with better capture for Vizgen.
- HLA class I/II diversity: verify representation beyond HLA-DRA/DRB1 (e.g., HLA-A/B/C, HLA-DPA1/DPB1), balanced with panel space.
- Exhaustion/activation: PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, TOX, BATF, TNFRSF9 (4-1BB), ICOS inclusion checks.
- Fibroblast/EMT/angiogenesis: COL1A1/A2, PDGFRA, PDGFRB, ACTA2; ANGPT2, KDR, VWF covered; consider PECAM1, EMCN.
- Myeloid: S100A8/A9, LST1, FCGR3A present; consider CCR2, CX3CR1, CSF1R, MRC1, MARCO, ITGAX, CLEC10A; inflammasome (NLRP3), antigen presentation (CD74).
- Tumor pathways: include canonical drivers when expressed in the cohort (EGFR, KRAS, BRAF, PIK3CA, PTEN, RB1, MYC, TP53 targets CDKN1A/MDM2), and stress response (HSPA1A/B, HSPH1, ATF4, DDIT3).
- Cell cycle/DNA repair: MKI67, TOP2A, PCNA, MCMs present; consider RAD51, BRCA1/2 proxies (if expressed), CHEK1/2.

Files and figures
- Methods: selection_expert/methods/{HVG,DE,SpaPROS,scGeneFit,RandomForest}
- Overlap: selection_expert/overlap/upset_methods.png
- Aggregate ranking: selection_expert/aggregate/aggregate_ranking_scores.csv
- Draft panels: selection_expert/draft_panels/panel_agg_top_{500,800,1000,1200}.csv
- UMAPs & metrics: selection_expert/draft_panels/umap/

Next steps
- Biological curation: run knowledge-base checks (GeneCards/UniProt/GO) on top 1500 aggregated genes; score biological relevance for Human TME; finalize curated 1000-gene panel.
- Compare UMAP separability across draft sizes; adjust weights and forced inclusions to cover missing pathways.
- Add UpSet subgroup analysis to quantify convergence for lineage-defining genes and checkpoints.
