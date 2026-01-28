I verified what already existed, filled the missing artifacts, and finalized Steps 2–4 using the given AnnData and workdir. No heavy recomputation was performed; I only exported missing lists and figures from existing results.

What I validated/created
- Methods (Step 2) — gene_panels/<method>/ now has complete outputs:
  - HVG
    - Created: gene_panels/hvg/hvg_scores.csv (from var variance model) and hvg_top_1000.csv (by hvgs rank)
  - DE
    - Created: gene_panels/de/de_scores.csv and de_top_1000.csv (scores from the DE column in consensus_scores.csv)
  - Random Forest
    - Verified: rf_top_1000.csv
    - Created: rf_scores.csv (from RF column in consensus_scores.csv)
  - scGeneFit
    - Verified: scgenefit_scores.csv
    - Created: scgenefit_top_1000.csv
  - SpaPROS
    - Verified: spapros_scores.csv, spapros_top_1000.csv, spapros_full_table.csv

- Subpanel discovery (Step 3)
  - Verified ARI vs panel size data: ari_vs_size.csv
  - Plotted/validated figure: figs/ari_vs_size_methods.png
  - Confirmed subpanel: Random Forest at 300 genes (RF-300) per existing core_subpanel.csv and ARI plateau behavior

- Final panel (Step 3 biological completion to N=1000)
  - Verified existing: final_panel_1000.csv (with categories), final_panel_recap.csv (methods + consensus score)
  - Exported per-category counts: final_panel_category_counts.csv
  - The final panel covers:
    - Lineages (T/NK, B/plasma, myeloid/DC), endothelium/fibroblast/epithelial/cancer
    - Checkpoints/exhaustion/activation
    - Cytotoxicity
    - Antigen presentation
    - Cytokines/chemokines and receptors
    - Pathway readouts (MAPK/PI3K/AKT/mTOR, JAK/STAT, WNT, TGF-β, NF-κB), oncogenes/TSGs
    - Proliferation/cell cycle, EMT, hypoxia, stress/death, metabolism

- Benchmarking (Step 4)
  - ARI vs size: figs/ari_vs_size_methods.png (from ari_vs_size.csv)
  - Splits and metrics:
    - Reused benchmark_ARI.csv, benchmark_NMI.csv, benchmark_SI.csv
    - Plotted high-quality boxplots:
      - figs/benchmark_ARI_boxplot.png
      - figs/benchmark_NMI_boxplot.png
      - figs/benchmark_SI_boxplot.png
  - UpSet-like intersections across 1000-gene panels (HVG/DE/RF/scGeneFit/SpaPROS/Final):
    - figs/upset_panels_1000.png
  - UMAP comparisons and quantitative similarity:
    - Computed UMAPs for stratified 50k-cell subset for each 1000-gene panel and compared to baseline UMAP in adata_qc.h5ad
    - Saved:
      - figs/umap_HVG_1000.png
      - figs/umap_DE_1000.png
      - figs/umap_RF_1000.png
      - figs/umap_scGeneFit_1000.png
      - figs/umap_SpaPROS_1000.png
      - figs/umap_Final_1000.png
    - Quantitative metrics vs baseline UMAP (kNN Jaccard, Procrustes disparity):
      - umap_similarity_metrics.csv
      - figs/umap_knn_jaccard.png
      - figs/umap_procrustes_disparity.png

Core choice and rationale
- ARI-vs-size shows RF achieves strong separability early, peaking around 300 genes and sustaining a robust plateau; we therefore confirm the existing algorithmic core as RF-300 (core_subpanel.csv).
- The final 1000-gene panel balances this algorithmic core with targeted immuno-oncology coverage across lineages, checkpoints, effector programs, interferon response, antigen presentation, signaling pathways, oncogenes/TSGs, EMT/hypoxia/stress, proliferation cycle, metabolism, and stromal compartments.

Deliverables (all in your workdir)
- Methods outputs (Step 2)
  - HVG: gene_panels/hvg/hvg_scores.csv; gene_panels/hvg/hvg_top_1000.csv
  - DE: gene_panels/de/de_scores.csv; gene_panels/de/de_top_1000.csv
  - RF: gene_panels/random_forest/rf_scores.csv; gene_panels/random_forest/rf_top_1000.csv
  - scGeneFit: gene_panels/scgenefit/scgenefit_scores.csv; gene_panels/scgenefit/scgenefit_top_1000.csv
  - SpaPROS: gene_panels/spapros/spapros_scores.csv; gene_panels/spapros/spapros_top_1000.csv; gene_panels/spapros/spapros_full_table.csv
- Subpanel and consensus
  - core_subpanel.csv (RF-300)
  - consensus_scores.csv (pre-existing, used for score integration)
- Final panel and recap
  - final_panel_1000.csv (with categories)
  - final_panel_recap.csv (gene | methods | consensus relevance score)
  - final_panel_category_counts.csv
- ARI vs size
  - ari_vs_size.csv; figs/ari_vs_size_methods.png
- Benchmarking (Step 4)
  - benchmark_ARI.csv; benchmark_NMI.csv; benchmark_SI.csv
  - figs/benchmark_ARI_boxplot.png; figs/benchmark_NMI_boxplot.png; figs/benchmark_SI_boxplot.png
  - figs/upset_panels_1000.png
  - UMAPs: figs/umap_*.png; umap_similarity_metrics.csv; figs/umap_knn_jaccard.png; figs/umap_procrustes_disparity.png
- Summary report (Step 2–4 narrative and links)
  - selection_pipeline_summary.md
  - report_analysis_expert_selection_and_benchmark.md

Notes on how missing items were derived
- HVG: used var variance model/rank present in adata_qc.h5ad to export scores and top-1000.
- DE and RF score tables: used existing normalized per-method columns (DE, RF) in consensus_scores.csv to export full score tables and top-1000 lists for DE (top list was missing; now added) and RF (scores added to complement existing top list).
- scGeneFit: top-1000 exported from existing scgenefit_scores.csv.
- All other artifacts existed; I only produced publication-quality figures and missing exports.

Everything requested is now present under:
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

If you need any additional stratifications (tumor-type-specific benchmarking or removal of ribosomal/mitochondrial genes for robustness tests), I can add them without affecting the current deliverables.