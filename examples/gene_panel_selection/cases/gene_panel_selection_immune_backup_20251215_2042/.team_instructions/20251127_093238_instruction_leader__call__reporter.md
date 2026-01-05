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
  - selection_expert/active_adata_path.txt
- Selection methods and aggregation
  - selection_expert/report_analysis_expert_selection_round1.md
  - methods folders: HVG/DE/SpaPROS/scGeneFit/RandomForest (include key intermediate tables if space permits)
  - overlap figures: selection_expert/overlap/upset_methods.png
  - aggregate/aggregate_ranking_scores.csv
  - draft panels and UMAP metrics
- Final curation outputs
  - selection_expert/curated/final_panel_1000.csv and final_panel_1000_grouped.tsv
  - coverage summary and curation notes (final_panel_coverage_summary.md, notes_curation.md)
  - figures: curated/figures/* (UMAPs using final panel, Venn three-way DE/scGeneFit/RF, UpSet)
  - method presence matrix: curated/tables/method_panels_presence.csv
- Biological interpretation
  - biologist/interpretation_final_panel.md
  - biologist/proposed_adjustments.csv (appendix)

Instructions
- Compose a structured, precise report with: Abstract, Introduction, Methods (environment, QC/downsampling, each selection method with short description and parameters, aggregation strategy, curation logic), Results (QC, per-method highlights, overlaps, UMAPs, RF metrics), Final 1000-gene panel (table excerpt and link to full CSV), Biological interpretation, Discussion/Limitations, and Conclusion.
- Include the requested recap table structure in the Results or Supplement, showing: Gene | Methods where it appears | Biological relevance (dataset context) | Relevance score. Generate it from curated final table columns and save as a figure/table embedded in the PDF; also export a CSV in reporter/recap_table.csv.
- Embed figures (UMAPs, Venn, UpSet, QC plots). Ensure high-resolution and consistent styling.
- Outputs:
  - PDF: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.pdf
  - Machine-readable summary JSON for key paths and metrics: reporter/summary.json
- If PDF generation fails due to LaTeX/pandoc, fallback to producing a well-formatted HTML and then convert to PDF via wkhtmltopdf or browser engine; if still failing, save the HTML and notify.
