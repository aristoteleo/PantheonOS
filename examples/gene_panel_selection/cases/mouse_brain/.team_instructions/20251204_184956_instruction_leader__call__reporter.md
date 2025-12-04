Please generate a final PDF report for the Mouse brain receptor profiling panel project.

Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/reporter

Inputs to include (paths relative to project workdir):
- selection_expert/dataset_summary.md
- selection_expert/methods.md
- selection_expert/report_analysis_expert_mouse_brain.md
- selection_expert/results_log.md
- selection_expert/ari_curves_all_methods.csv
- selection_expert/figures/*.png
- selection_expert/gene_panels/final_receptor_centric_500.tsv
- selection_expert/gene_panels/final500_recap_table.csv
- selection_expert/gene_panels/final500_annotations.csv
- selection_expert/gene_panels/final500_grouped_by_category.md
- selection_expert/panel_*.tsv
- selection_expert/results/curves/*.csv
- selection_expert/benchmark_metrics_*.csv
- selection_expert/biologist/biologist_notes.md

High-level requirements for the report
- Describe the selection pipeline executed by selection_expert
- Summarize the pre-established algorithm outputs and intersections (with Upset plot)
- Explain completion logic to 500 genes and receptor-family prioritization
- Present benchmarking (ARI/NMI/SI, UMAPs, ARI vs panel size curves)
- Provide recap table structure and final panel annotations with category counts

Please save the final PDF as report.pdf in the project workdir. You may structure the content as you see fit based on the provided materials.