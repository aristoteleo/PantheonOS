Interpret the final immune-oncology 1000-gene panel biologically (do not change the panel), highlighting coverage of immune lineages, states, cytokine/chemokine programs, antigen presentation, and tumor-intrinsic signaling. Provide a concise narrative suitable for a report.

Workdir
- Project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist

Inputs
- Final annotated panel CSV: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/tables/final_panel_1000_annotated.csv
- Final panel README: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/tables/final_panel_README.md
- Figures to reference: final_panel_category_counts.png; upset_with_final_panel.png; ari_vs_size_all_methods.png; UMAP comparisons.

Deliverable
- Write biologist_interpretation.md in your agent workdir. Emphasize: ability to resolve T/NK subsets and exhaustion continuum; myeloid diversity (mono→mac→DC); B/plasma maturation; stromal/tumor segmentation; cytokine/chemokine axes and L–R coverage; antigen presentation breadth; and representation of major cancer signaling pathways. Note any expected limitations (e.g., very fine DC subsets, rare ILCs).