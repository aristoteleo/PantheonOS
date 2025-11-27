Project: PBMC3k gene panel selection

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/reporter

Goal: Generate a PDF report (report.pdf in the project workdir) summarizing the entire workflow, key results, the final 500-gene panel, methods, evaluation metrics, and biological interpretation.

Please compile the following sources from sub-agents, but feel free to organize content as you see fit:
- Selection expert outputs (directory): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert
  - Key files: final_panel_500.csv, final_panel_500.txt, consensus_all_methods.csv, ranking_*.csv, gene_panels/*, evaluation_metrics.json, rf_cv_metrics.json, umap_panel_leiden.png, umap_full_louvain.png, confusion_matrix_panel_rf.png, coverage_barplot.png, overlap_heatmap_methods.png, README.md, report_analysis_expert_PBMC3k.md, pbmc3k_panel_selection.ipynb, pbmc3k_step1_qc.ipynb
- Biologist outputs (directory): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/biologist
  - Key files: report_biologist_PBMC3k_gene_panel.md, biological_interpretation.md, report_browser_use_pbmc_marker_background.md, references_1.bib

Important notes:
- Do not require user input. Include a clear statement that the input AnnData was already HVG-limited (~1838 genes), which constrained the gene universe.
- Include the final panel table (top of appendix) and link/append the TXT gene list.
- Include figures (UMAPs, confusion matrix, coverage barplot, overlap heatmap) and the core evaluation metrics.
- Export the final PDF as: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/report.pdf