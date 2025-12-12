Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/reporter

Task: Update the report to replace the current UpSet-like intersections figure with the new true UpSet figure generated from the existing 500-gene panels and the Final500 panel.

Context:
- The current report includes a figure named upset_like_overlaps_hires.(png|pdf) which is not a standard UpSet plot.
- The selection_expert has produced a proper UpSet figure and intersection tables for the six sets: HVG500, DE500, RF500, scGeneFit500, SpaPROS500, and Final500.

Inputs to include/reference:
- New figure(s):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/figures/upset_500_true_hires.png
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/figures/upset_500_true_hires.pdf
- Tables:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/results/intersections_upset_500.csv
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/results/jaccard_pairs_500.csv
- Optional notes (for concise wording):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/results/upset_500_notes.md

Requirements:
- In the Results section where the UpSet-like figure currently appears (Section 2.2), replace it with the new true UpSet plot.
- Adjust the caption at a high level to indicate it is a true UpSet plot showing intersections among HVG500, DE500, RF500, scGeneFit500, SpaPROS500, and Final500, with set-size bars and intersection-size bars.
- Optionally add a brief, one-sentence guidance on how to read the plot; detailed interpretation is not needed.
- Keep all other report content unchanged.
- Rebuild the PDF and overwrite report.pdf in the project root and in your workdir.

Outputs:
- Updated PDF: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/report.pdf
- Copy under reporter/: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/reporter/report.pdf
- Log any changes in reporter/CHANGELOG.md