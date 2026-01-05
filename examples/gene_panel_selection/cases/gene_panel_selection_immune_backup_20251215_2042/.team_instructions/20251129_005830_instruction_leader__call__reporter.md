Project: Human immune oncology gene profiling panel — Final report
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter

Goal: Generate a polished PDF report (report.pdf) summarizing the project.

Inputs (paths to include; you decide structure and content):
- Environment summary: environment.md
- Selection expert artifacts under selection_expert/results/ including:
  - dataset_summary.csv
  - paths.json
  - gene_panels/ (SpaPROS, scGeneFit, RandomForest, HVG, DE outputs)
  - candidate_subpanel_evidence.csv
  - candidate_subpanel.csv
  - figures/venn_top1500.png, ari_vs_panel_size.png
  - final_panel_1000.csv
  - final_panel_summary.md
  - final_panel_grouping.json
  - benchmark_metrics.csv, figures/benchmark_*.png
  - umap_similarity.csv, figures/umap_similarity.png
- Biologist interpretation: biologist/final_biology_interpretation.md

Requirements:
- Report must include: detailed description of the selection pipeline from selection_expert; all pre-established algorithm results; completion logic and reasoning for determining the optimal size for cell-type separability; ARI vs panel size curves; recap table like Gene | Methods where it appears | Biological relevance | Relevance score; Venn diagram of intersections of method outputs; Benchmarking with dataset splitting strategy, ARI/NMI/SI boxplots, UMAP comparisons, and UMAP similarity.
- Save the final PDF as report.pdf under the project root workdir.
- Keep intermediate build artifacts in your agent workdir.
