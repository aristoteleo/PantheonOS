Generate a final PDF report for the Human immune oncology 1000-gene profiling panel.

Workdir
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter

Inputs (paths only; please organize content professionally)
- Environment: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/environment.md
- Selection expert outputs dir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
  - Dataset QC: dataset_QC_overview.md; figures/qc_histograms.png; author_cell_type_update_top30_counts_improved.png; UMAP figures
  - Method outputs: methods/* top lists; tables/consensus_scores.csv; figures/upset_methods_top500.png
  - Recommended subpanel: tables/recommended_subpanel_500.csv
  - Final panel deliverables: tables/final_panel_1000_annotated.csv/.xlsx/.txt; tables/final_panel_README.md; figures/final_panel_category_counts.png; figures/upset_with_final_panel.png
  - Benchmarking: figures/ari_vs_size_all_methods.png; tables/ari_vs_size_all_methods.csv; per-method curves; benchmark_*.png; tables/benchmark_metrics_splits.csv; tables/umap_similarity_metrics.csv; UMAP comparisons
  - Analysis summary: methods_overview.md; report_analysis_expert_finalize_and_benchmark.md; benchmark_summary.md
- Biologist interpretation: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist/biologist_interpretation.md

Expectations
- Include a detailed description of the selection pipeline executed by the selection_expert, all pre-established algorithm results, and the completion logic to reach 1000 genes.
- Include figures: ARI vs panel size curves, UpSet plots, UMAP comparisons, category counts figure, and benchmarking boxplots.
- Include a recap table in the PDF: Gene | Methods where it appears | Biological relevance (context) | Relevance score. Use the available tables to build it.

Output
- Save the final PDF report as: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.pdf
- Also save a short plain-text README listing the included sections and figures: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter/report_README.txt