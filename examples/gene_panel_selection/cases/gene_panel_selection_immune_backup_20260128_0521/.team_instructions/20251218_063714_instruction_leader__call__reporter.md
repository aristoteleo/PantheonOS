Project: Human immune-oncology gene panel selection (1000 genes)
Workdir (project root): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter

Goal: Create a professional PDF report (report.pdf) summarizing the full workflow, results, and interpretations for this study.

Input material paths (produced by other agents)
- Environment: environment.md
- System notes: notes_*.md; todolist.md
- Selection expert outputs (all under selection_expert/):
  - inspection_qc_summary.md
  - selection_pipeline_summary.md (if missing, summarize from available artifacts)
  - report_analysis_expert_selection_and_benchmark.md (if missing, compile from other files)
  - QC figures: selection_expert/figures/qc_*.png and selection_expert/figs/violin_qc_violin.png, selection_expert/figs/scatter_qc_scatter.png
  - Algorithm outputs: selection_expert/gene_panels/** (HVG, DE, RF, scGeneFit, SpaPROS)
  - ARI vs size: selection_expert/ari_vs_size.csv and figs/ari_vs_size_methods.png
  - Benchmarking: benchmark_ARI.csv, benchmark_NMI.csv, benchmark_SI.csv; figs/benchmark_*_boxplot.png
  - UMAPs and similarity: figs/umap_*_1000.png; umap_similarity_metrics.csv; figs/umap_knn_jaccard.png; figs/umap_procrustes_disparity.png
  - UpSet: figs/upset_panels_1000.png
  - Final panel: final_panel_1000.csv; final_panel_recap.csv; final_panel_category_counts.csv
- Biologist outputs: biologist/biological_interpretation.md

Instructions
- Organize the report with: intro/context; dataset and environment; inspection & QC; selection methods and subpanel discovery; panel completion logic; final panel overview with category counts; benchmarking; biological interpretation highlights; conclusions.
- Include key figures (QC, ARI vs size, benchmarking boxplots, UMAP comparisons, UpSet), and a small recap table (gene | methods where it appears | biological relevance | relevance score) by sampling from final_panel_recap.csv.
- Save report as: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.pdf

Proceed autonomously and keep the narrative concise but complete. Do not recompute analyses; only compile and format.