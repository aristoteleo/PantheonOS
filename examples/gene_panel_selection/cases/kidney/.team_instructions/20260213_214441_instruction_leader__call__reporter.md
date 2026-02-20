Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/reporter

Goal: Generate a final PDF report (report.pdf) summarizing the kidney cancer panel selection, benchmarking, and biological interpretation.

Inputs (paths only; please organize content professionally):
- Selection expert artifacts (pipeline, panels, figures):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/report_analysis_expert_kidney.md
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/final_panel_1000.txt
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/final_panel_1000_annotations.csv
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/final_panel_recap_table.csv
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/ari_vs_size_pub.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/metrics_box_pub.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/upset_panels.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_procrustes_bars.png
  - UMAP comparisons:
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_Full_panel.png
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_Final_panel.png
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_HVG_panel.png
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_DE_panel.png
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_RF_panel.png
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_SpaPROS_panel.png
    * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert/results/figs/umap_scGeneFit_panel.png
- Biologist interpretation:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/biologist/biologist_summary.md

Requirements:
- Include a section describing the selection pipeline, including pre-established method panels and completion logic for N=1000 and cell-type separability considerations.
- Include ARI vs panel size curve, ARI/NMI/SI boxplots, UMAP comparisons and similarity bar plot, and UpSet intersections figure.
- Include a recap table (Gene | Methods where it appears | Biological relevance | Relevance score). Use the provided CSV paths to populate.
- Save the final report to: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/report.pdf