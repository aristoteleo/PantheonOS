Project: PBMC3k gene panel selection

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/biologist

Inputs to interpret:
- Final panel file: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert/final_panel_500.csv
- Supporting outputs (figures, rankings, evaluation metrics) are in /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert

Task:
- Provide a concise biological interpretation of the curated 500-gene panel for human PBMCs.
- Map key genes in the panel to major PBMC compartments and functional programs (CD4/CD8 T, NK, B, CD14+ mono, FCGR3A+ mono, dendritic/AP, megakaryocyte, interferon response, naive/memory states), citing hallmark markers present in the panel.
- Comment on balance (coverage) across compartments and any notable absences or limitations due to the HVG-limited input.
- Save your interpretive summary to a text/markdown file (e.g., biological_interpretation.md) in your sub-agent workdir and optionally reference relevant literature without deep citation requirements.
