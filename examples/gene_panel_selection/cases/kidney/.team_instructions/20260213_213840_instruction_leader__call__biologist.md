Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/biologist

Task: Provide a concise biological interpretation of the final kidney cancer panel and its category coverage, focusing on how it enables:
- Cell type resolution across tumor and adjacent renal tissue,
- Immune profiling (activation vs exhaustion; cytokine/interferon states),
- Distinguishing malignant epithelial vs normal compartments,
- Pathway/state profiling (hypoxia/angiogenesis; metabolic reprogramming; proliferation; EMT; DDR/apoptosis/autophagy/ferroptosis).

Inputs to consult in selection_expert workdir:
- results/final_panel_1000_annotations.csv
- results/final_panel_recap_table.csv
- results/figs/* for performance context.

Output:
- Write biologist_summary.md in your workdir summarizing:
  - Key marker genes per compartment (renal epithelial subtypes, tumor markers, endothelium/pericytes, fibroblasts, myeloid, T/NK, B/plasma),
  - How checkpoints/cytokines allow activation/exhaustion inference,
  - How oncogenic/hypoxia/metabolic/proliferation gene sets support malignant state stratification,
  - Any obvious gaps or suggested minor additions (not to modify the final panel, just interpret and note).
Keep it concise (<=2 pages).