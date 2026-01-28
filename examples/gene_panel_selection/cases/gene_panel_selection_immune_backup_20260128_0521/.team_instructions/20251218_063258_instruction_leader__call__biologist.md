Project: Human immune-oncology gene panel selection (1000 genes)
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir

Task: Review the final 1000-gene panel and benchmarking summaries produced by selection_expert (in selection_expert/), and write a concise biological interpretation targeting an end user who will profile TME composition and states in tumors.

Focus points (do not alter selection):
- Explain how the panel resolves T/NK/B/plasma/myeloid/DC/stromal/epithelial-cancer compartments and key sub-states (e.g., naïve/CM/EM/exhausted cytotoxic T, Tfh; mono vs TAM vs DC subsets; CAF and endothelial activation; epithelial EMT states).
- Explain how checkpoint, cytokine/chemokine, antigen presentation, interferon/hypoxia/EMT/cell cycle/metabolism modules can be used to infer cell states and pathway activity.
- Suggest 6–10 practical composite scores (exhaustion, cytotoxicity, Tfh/GC, MHC-I/II-AP, IFN-I/II, hypoxia, EMT, proliferation) using genes present in the panel.

Deliverable: Save markdown to biologist/biological_interpretation.md and keep it concise (~1–2 pages). Include references to figures where helpful (e.g., UMAP comparisons, benchmarking boxplots, UpSet intersections) but do not reproduce them. Use the existing final_panel_1000.csv categories as anchors for the narrative.