Project: Human immune oncology gene profiling panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter

Task: Generate a comprehensive PDF report (report.pdf) summarizing the pipeline and results. Include:
- A detailed description from selection_expert’s pipeline and the methods used (Phase 1–4), including dataset understanding, per-method scoring, optimal subpanel selection, consensus, and curated completion to 1000 genes.
- All pre-established algorithm outputs and comparisons, completion logic, and panel size reasoning.
- Figures and files to include (pull from paths; do not re-plot):
  - environment.md (summary snippet)
  - selection_expert/phase1_summary.md
  - selection_expert/hvg_mean_variance.png
  - selection_expert/pca_explained_variance_ratio_subset.png
  - selection_expert/rank_genes_groups_cell_type_phase2_deg_top20.png
  - selection_expert/phase2/ari_vs_size_curves.png
  - selection_expert/phase2/method_panels_upset_top15.png
  - selection_expert/Benchmarking_Pantheon_Vizgen_Cancer.pdf (extract key plots)
  - selection_expert/UMAP_Jaccard_Pantheon_Vizgen_Cancer.pdf
  - selection_expert/final_panel_category_counts.png
  - selection_expert/recap_table.tsv (render as table)
  - selection_expert/final_panel_1000.tsv (render head and schema; link full table as appendix)
- Benchmarking tables: selection_expert/benchmark_ARI.csv, benchmark_NMI.csv, benchmark_SI.csv, benchmark_genes_used.csv.
- Biological interpretation from biologist: biologist/biological_interpretation.md

Output
- Save the PDF as: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.pdf
- Save any intermediate markdown or LaTeX you create in your workdir. Keep paths relative where possible. Do not hardcode content; assemble from the provided files and summaries.