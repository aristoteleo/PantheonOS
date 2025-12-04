Project: Mouse brain receptor profiling panel

Workdir:
- project_workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir
- agent_workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/reporter

Task: Generate a professional PDF report (report.pdf) summarizing the full workflow, methods, results, benchmarking, and biological interpretation. Use the provided artifacts; do not recompute analyses.

Key inputs (paths):
- Environment/context: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/environment.md
- Dataset summary: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/dataset_summary.md
- Methods/selection pipeline summaries:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/methods.md
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/selection_summary.md
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/report_analysis_expert_mouse_brain_receptor_panel.md
- Final panel and annotations:
  - Final 500 genes: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/final_receptor_profiling_panel_500.tsv
  - Core subpanel: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/core_subpanel_celltyping.tsv
  - Annotations: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/final_panel_annotations.csv
  - Category/role counts: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/final_panel_category_counts.csv
  - Panel overlaps: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/panel_overlap_matrix.csv
- Algorithm baseline panels (existing):
  - HVG 1000: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/panel_hvg_1000.tsv
  - DE 1000: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/panel_de_1000.tsv
  - RF top 1000: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/random_forest/rf_top_1000.csv
  - scGeneFit scores: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/scgenefit/scgenefit_scores.csv
- Benchmarking outputs:
  - Metrics: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/benchmark_metrics.csv
  - Summary stats: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/benchmark_summary_stats.csv
  - UMAP similarity: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/umap_similarity.csv
- Figures (include all below):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/ari_vs_size_pretty.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/benchmark_ARI_boxplot_pretty.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/benchmark_NMI_boxplot_pretty.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/benchmark_SI_boxplot_pretty.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/umap_full_reference.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/umap_HVG_1000.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/umap_DE_1000.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/umap_RF_1000.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/umap_scGeneFit_1000.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/umap_Final_500.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/upset_like_top15_pretty.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/figures/final_panel_category_counts.png
- Biological interpretation:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/biologist/biological_interpretation.md

Notes:
- This report should include: selection pipeline, pre-established algorithm results (excluding SpaPROS), completion logic to 500 with receptor family quotas, ARI vs size curves, Upset-style intersections, benchmarking (split strategy, ARI/NMI/SI boxplots), UMAP comparisons and similarity, and a recap table as per selection_expert outputs. Please save the final PDF as /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/report.pdf