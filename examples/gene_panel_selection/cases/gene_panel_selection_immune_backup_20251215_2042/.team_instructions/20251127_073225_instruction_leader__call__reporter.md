Project: Immune-oncology 1000-gene panel (Human TME)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter

Inputs to include and cite in a publication-grade PDF report (generate report.pdf in the project workdir, not your subdir):
- Environment and dataset
  - environment.md at project root
  - Dataset source: bioRxiv 2024. DOI: 10.1101/2024.01.17.576110
  - Active dataset: selection_expert/adata_downsampled_50k_3kHVG.h5ad
- QC + downsampling
  - selection_expert/report_analysis_expert_qc_downsample.md
  - selection_expert/qc_figures/*.png
  - active_adata_path.txt
- Selection methods and aggregation
  - selection_expert/report_analysis_expert_selection_round1.md
  - methods folders: HVG/DE/SpaPROS/scGeneFit/RandomForest (include key intermediate tables if space permits)
  - overlap figures: selection_expert/overlap/upset_methods.png
  - aggregate/aggregate_ranking_scores.csv
  - draft panels and UMAP metrics
- Final curation outputs
  - selection_expert/curated/final_panel_1000.csv and grouped.tsv
  - coverage summary and curation notes
  - figures: curated/figures/* (UMAPs using final panel, Venn three-way DE/scGeneFit/RF, UpSet)
  - method presence matrix: curated/tables/method_panels_presence.csv
- Biological interpretation
  - biologist/interpretation_final_panel.md
  - biologist/proposed_adjustments.csv (include as optional appendix)

Instructions
- Compose a structured, precise report with: Abstract, Introduction, Methods (environment, QC/downsampling, each selection method with short description and parameters, aggregation strategy, curation logic), Results (QC, per-method highlights, overlaps, UMAPs, RF metrics), Final 1000-gene panel (table excerpt and link to full CSV), Biological interpretation, Discussion/Limitations, and Conclusion.
- Include the requested recap table structure in the Results or Supplement, showing: Gene | Methods where it appears | Biological relevance (dataset context) | Relevance score. It should be generated from curated final table columns.
- Embed figures (UMAPs, Venn, UpSet, QC plots). Ensure high-resolution and consistent styling.
- Output: report.pdf saved at /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.pdf
- Also output a machine-readable summary JSON for keys paths and metrics at reporter/summary.json
