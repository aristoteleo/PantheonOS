Caller: selection_expert.
Task: Please run a headless Python script in the active notebook environment to compute DE, an HVG-like variability measure, integrate ranked lists (RF tool + scGeneFit + DE + HVG-like), and build curated panels (50/96/150) for PBMC3k. Use the dataset at /home/erwinpi/Vizgen/pbmc3k_preprocessed.h5ad and save outputs under /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert (results/, figures/).

Steps to implement in the script:
1) Load AnnData and set label_key='louvain' (fallback: 'leiden' if missing). Do not renormalize/log; use use_raw=False for DE.
2) Differential expression (wilcoxon): sc.tl.rank_genes_groups(adata, groupby=label_key, method='wilcoxon', use_raw=False). Save:
   - results/de_rank_genes_groups.csv (full table)
   - results/de_best_per_gene.csv (best score per gene across groups)
3) HVG-like: compute across-group mean expression per gene and take variance across groups as a score; rank descending. Save results/hvg_like_genes.csv with columns gene, var_across_groups, rank.
4) Load tool outputs:
   - results/gene_panels/random_forest/rf_top_300.csv
   - results/gene_panels/scgenefit/scgenefit_scores.csv
   Build integrated_scores.csv combining ranks from RF (descending score), scGeneFit (descending score), DE (descending best score), and HVG-like (descending var). Penalize genes starting with MT- (add +10000 to composite) and RPS/RPL (add +2000). Composite = mean of available ranks + penalty. Also save a column methods_support listing which methods supported each gene.
5) Build curated panels (50/96/150):
   - Seed with canonical immune markers per cell type when present in var: 
     T: CD3D, CD3E, TRAC, IL7R, CCR7, LTB, CD2, CD27, CD28, CD247
     CD8/NK effector: CD8A, CD8B, NKG7, GNLY, GZMB, PRF1, KLRD1, KLRG1, TIGIT
     B: MS4A1, CD79A, CD79B, CD74, CD19, BLNK, TCL1A, MZB1, IGJ
     Mono CD14+: LST1, LYZ, S100A8, S100A9, LGALS3, CTSS, AIF1
     Mono FCGR3A+: FCGR3A, MS4A7, LILRB2, CTSA, CTSS, TYROBP, FCER1G
     DC: HLA-DPB1, HLA-DQA1, HLA-DRB1, HLA-DPA1, FCER1A, CD1C, CLEC10A
     Platelet: PPBP, PF4, GP9, ITGA2B, GP1BA
   - Augment with top DE markers per corresponding louvain group (exclude MT-/RPS/RPL), and high integrated-ranked markers to reach target sizes. Ensure balanced representation across major types.
   - Add housekeeping controls (choose present from: ACTB, GAPDH, B2M, HPRT1, PPIA, GUSB) exactly 6.
   - Add 2 negatives/background: LacZ and DapB (exogenous), even if not in var.
   - Save curated panels as CSV and JSON with columns/fields: gene, category (marker/control/negative), target_cell_types (list), supported_methods (list), notes (short one-liner if known).
6) Figures:
   - Dotplot of top 5 DE genes per group: figures/dotplot_top5_per_group.png
   - Venn diagram overlap of top-300 sets for HVG-like, DE-best, RF, scGeneFit: figures/venn_top300.png
   - For each panel size (50/96/150): recompute PCA+neighbors+UMAP using only panel marker genes (exclude negatives/housekeeping). Save UMAP colored by label_key as figures/umap_panel_{size}.png. Also evaluate a RandomForest classifier using only those genes (train/test split 75/25 stratified) to predict label_key and save results to results/panel_classification_metrics.csv (append rows per size with accuracy and macro F1).

Please implement robustly (e.g., check gene presence, handle missing files), and return a brief summary of what was created. Save all paths relative to the selection_expert workdir above.