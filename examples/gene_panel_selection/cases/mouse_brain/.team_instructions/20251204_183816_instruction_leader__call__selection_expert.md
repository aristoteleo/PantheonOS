Please add detailed receptor-family annotations and grouped categories for the final 500-gene panel.

Workdirs:
- Project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert

Inputs:
- gene_panels/final_receptor_centric_500.tsv (current final panel)
- gene_panels/final500_recap_table.csv (methods presence, is_receptor flag, relevance_score)

Tasks:
- Create an annotation table mapping each gene to major categories suitable for a brain receptor profiling panel. Suggested categories (adjust as needed):
  - GPCRs (subtags where possible: neurotransmitter GPCRs, adhesion GPCRs, chemokine receptors, purinergic P2Y, others)
  - Ligand-gated ion channels (GABA-A, nicotinic, 5-HT3, P2X, glutamate ionotropic: AMPA/Kainate/NMDA)
  - Voltage-gated ion channels (NaV/Scn, CaV/Cacna – alpha/beta/gamma/delta subunits, HCN, K+ channels KCNx, KCNA/B/C/D/E/F/K etc.)
  - Receptor tyrosine kinases and related (EGFR/ERBB, FGFR, PDGFR, VEGFR, EPHA/EPHB, NTRK)
  - Cytokine/interleukin receptors and TNF receptor superfamily
  - Other receptor families: TGF-beta/Activin (TGFBR/ACVR), Notch, Frizzled (FZD), Nuclear hormone receptors (NRx), Purinergic P2X (if not above), etc.
  - Vascular/endothelial receptors (TEK/Tie2, KDR/FLT1, PDGFRB, etc.)
  - Non-receptor core markers for cell typing
- Ensure the is_receptor flag is correct (e.g., ERBB family should be receptors). Fix any mislabels.
- Output files:
  - gene_panels/final500_annotations.csv with columns: gene, category, subcategory (if applicable), is_receptor (Y/N), brief_function (one-liner), known_brain_relevance (short note), methods_presence, relevance_score
  - gene_panels/final500_grouped_by_category.md summarizing counts per category/subcategory and listing genes

Please save outputs in your workdir and update results_log.md with a brief note.