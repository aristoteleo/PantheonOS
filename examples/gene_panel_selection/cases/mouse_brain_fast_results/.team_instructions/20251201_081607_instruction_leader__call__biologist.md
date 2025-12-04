Project: Mouse brain receptor profiling panel

Workdir:
- project_workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir
- agent_workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/biologist

Context:
- Selection_expert has finalized a 500-gene receptor-centric panel with annotations and benchmarking.
- SpaPROS was excluded by design.

Your task:
- Review the following outputs from selection_expert and produce a concise biological interpretation focusing on:
  - How the final 500-gene panel enables identification of major brain cell classes (excitatory/inhibitory neurons, astrocytes, oligodendrocytes/OPCs, microglia, endothelial/pericytes/vascular, ependymal)
  - Coverage of key druggable receptor families in the CNS (GPCR subclasses including monoaminergic/neuropeptidergic, ligand/voltage-gated ion channels, RTKs, cytokine receptors, nuclear/steroid receptors, purinergic, cannabinoid, chemokine, TRP, etc.)
  - Notable genes and family representatives for region- or subtype-specific profiling (e.g., dopaminergic, serotonergic, cholinergic, adrenergic markers and receptors; glutamate/GABA receptor representation; microglia/immune signaling receptors; vascular receptors)
  - Any caveats for cross-region generalization and practical usage notes

Key inputs (read-only):
- Final panel gene list: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/final_receptor_profiling_panel_500.tsv
- Annotations table: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/final_panel_annotations.csv
- Category composition: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/final_panel_category_counts.csv
- Core subpanel (cell-typing markers): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/core_subpanel_celltyping.tsv
- Selection summary (methods/benchmarks overview): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/selection_summary.md

Deliverable:
- Write a markdown file named biological_interpretation.md in your agent_workdir summarizing the interpretation points above. Keep it clear and organized with short sections and bullets. Reference concrete gene examples across families and cell types using the annotation table. Save the file to:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/biologist/biological_interpretation.md

Notes:
- Do not modify selection decisions; provide interpretation only.
- Assume expert audience; be concise but informative.