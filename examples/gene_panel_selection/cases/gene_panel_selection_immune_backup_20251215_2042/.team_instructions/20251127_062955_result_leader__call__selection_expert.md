Plan and progress update

I have started and completed the first multi-method selection round on the active dataset you provided, following the requested workflows. Everything is done strictly in the workdir:
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

What I ran and saved (paths):

1) HVG stability across subsamples
- Notebook: 02_selection_round1.ipynb
- Method folder: methods/HVG
- Outputs:
  - hvg_stability_ranked.csv
  - hvg_stability_ranked_with_symbol.csv
  - hvg_freq_hist.png
  - params.json
  - Note: methods/HVG/README.md
- Approach: 5 subsamples at 80% cells; Seurat v3 HVGs n_top=1500; ranking by inclusion frequency and rank-based score.

2) Differential expression (Wilcoxon)
- Method folder: methods/DE
- Labels:
  - Immune_broad (derived from cell_type; T, B, Plasma, NK, DC, Monocyte/Mac, Fibroblast, Endothelial, etc.)
  - Malignant_vs_Other (binary)
- Outputs:
  - DE_Immune_broad_ranked.csv (+ _with_symbol)
  - DE_Malignant_vs_Other_ranked.csv (+ _with_symbol)
  - Note: methods/DE/README.md
- Filters: log2FC>0.5, padj<0.05; for Immune_broad also pct_nz_group>0.2 and pct_nz_reference<0.2. Aggregate ranking by mean within-group rank and scores.

3) SpaPROS (spatially informative)
- Method folder: methods/SpaPROS
- Command ran via tool; label_key=Immune_broad; num_markers=400; n_hvg=3000; return_scores=true
- Outputs (under methods/SpaPROS/gene_panels/spapros/):
  - spapros_scores.csv (scored ranking)
  - spapros_top_400.csv
  - spapros_full_table.csv
  - Note: methods/SpaPROS/README.md

4) scGeneFit (separability optimization)
- Method folder: methods/scGeneFit
- Command ran via tool; label_key=Cell_type_broad; method=centers; max_constraints=800; n_top_genes requested=800; return_scores=true
- Output (under methods/scGeneFit/gene_panels/scgenefit/):
  - scgenefit_scores.csv
  - Note: methods/scGeneFit/README.md

5) Random Forest feature importance
- Method folder: methods/RandomForest
- Commands ran via tool:
  - Multiclass cell_type n_top=1000
  - Binary Malignant_vs_Other n_top=600
- Outputs (under methods/RandomForest/gene_panels/random_forest/):
  - rf_top_1000.csv
  - rf_top_600.csv
  - Note: methods/RandomForest/README.md

Aggregation, overlaps, draft panel builds, and UMAP evaluation

- Overlap visualization:
  - overlap/upset_methods.png (UpSet plot for method-set intersections)

- Aggregate ranking:
  - aggregate/aggregate_ranking_scores.csv
  - Aggregate score combines:
    • presence across methods (weighted)
    • normalized rank (per method)
    • normalized score (per method)
  - Scoring: aggregate_score = 2*(number of methods gene appears in) + mean_normrank + mean_normscore

- Draft panel CSVs:
  - draft_panels/panel_agg_top_500.csv
  - draft_panels/panel_agg_top_800.csv
  - draft_panels/panel_agg_top_1000.csv
  - draft_panels/panel_agg_top_1200.csv
  - Enforced inclusion of required IO markers when present in the dataset (CD3D/E, TRAC, FOXP3, NKG7, MS4A1, SDC1/PRDM1/IRF4, FCGR3A, LST1, ITGAM, ITGAX, CLEC10A, CCR7, S100A8/A9; checkpoints PDCD1, CTLA4, LAG3, HAVCR2, TIGIT; cytotoxic GZMB/PRF1/IFNG; activation CD69, HLA-DRA/DRB1; inflammation SPP1, IL1B, TNF; proliferation/cell cycle MKI67/TOP2A/PCNA/MCMs/E2F1; TP53/HIF1A/VEGFA/KDR/ANGPT2/VWF/EPCAM/KRTs/VIM/FN1/TWIST1/SNAI1/2; EGFR/KRAS/BRAF/PIK3CA/PTEN/RB1/MYC; STAT1/STAT3/TGFB1/SMAD3/WNT5A).
  - All genes saved as ENSG with an added symbol column.

- UMAP separability and basic metrics:
  - draft_panels/umap/umap_[Immune_broad|cell_type|Malignant_vs_Other]_panel{size}.png
  - draft_panels/umap/panel_silhouette_metrics.csv
  - Per panel size, recomputed PCA/neighbors/UMAP and saved plots. Computed silhouette on PCA as a lightweight separability metric for Immune_broad and Malignant_vs_Other.

Short curation-ready note: observed gaps to fill for final 1000-gene IO panel
- Cytokines/chemokines and receptors: include broader CXCL/CCL repertoire and corresponding CCR/CXCR genes; for Vizgen deployment, favor those with robust TME expression.
- HLA diversity: add HLA-A/B/C and HLA-DPA1/DPB1 if coverage is sparse.
- Additional immune checkpoints/activation: TOX, BATF, TNFRSF9 (4-1BB), ICOS; costimulatory/coinhibitory TNF/TNFRSF members.
- Myeloid repertoire depth: CCR2, CX3CR1, CSF1R, MRC1, MARCO, NLRP3, CD74; DC subsets (CLEC9A, LAMP3) as relevant.
- Fibroblast/vasculature/EMT: COL1A1/A2, PDGFRA/B, ACTA2, PECAM1, EMCN; confirm ANGPT2/KDR/VWF presence.
- Tumor/stress/DNA damage: HSPA1A/B, HSPH1, ATF4, DDIT3; TP53 axis (CDKN1A, MDM2), DDR (RAD51, BRCA1/2 proxies if expressed), CHEK1/2.
- Pathway breadth: proxy coverage for MAPK/PI3K/JAK-STAT/TGF-β/WNT beyond the listed exemplars.

Central report for this phase
- I compiled a short report with all paths, notes, and next steps:
  - report_analysis_expert_selection_round1.md
- This includes a summary, method parameters, outputs, overlap figure, aggregate scores path, draft panel paths, and curation gaps.

Next steps I propose
- Biological curation round: query top ~1500 aggregated genes against GeneCards/UniProt/GO to assign biological relevance scores for Human TME; use that to adjust the aggregate into a curated 1000-gene panel balancing detection, specificity, and pathway coverage.
- Compare UMAP separability across 500/800/1000/1200 drafts and tweak weights/forced inclusions accordingly.
- Generate a recap table and a full UpSet-specific intersection breakdown for lineage markers and checkpoint genes.

If you want, I can proceed now with:
- The biological context lookup and scoring for the top ~1500 aggregate genes.
- Producing the recap table (gene | methods where it appears | IO relevance | relevance score) and a Venn/UpSet focusing on HVG/DE/SpaPROS/scGeneFit/RF intersections.
- Delivering a refined curated 1000-gene panel candidate.