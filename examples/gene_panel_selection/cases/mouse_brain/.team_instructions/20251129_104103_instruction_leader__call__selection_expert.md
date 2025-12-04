Continue with the gene panel selection and benchmarking per the workflow.

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert

Inputs (datasets you created):
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/telencephalon.h5ad
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/diencephalon_midbrain.h5ad
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/hindbrain_cerebellum.h5ad

Computational environment context (memory-rich CPU node; no GPU):
- Use downsampling if needed but keep <=50k cells and <=3k genes per adata (already satisfied).
- If SpaPROS version conflicts with pandas in this environment, you may isolate execution or skip SpaPROS and note it; otherwise include it in comparisons.

Panel goal and constraints (restate):
- Build a 500-gene mouse brain receptor profiling panel that:
  - maps all major brain cell types across the brain
  - prioritizes inclusion of druggable receptors across key families (GPCRs, ligand-gated ion channels, voltage-gated channels, RTKs, cytokine/chemokine, purinergic, histamine, neuropeptide receptors, adhesion GPCRs, frizzled/Notch/TGF-beta, nuclear receptors where informative, vascular receptors)
  - keeps a compact core of marker genes required for separability, dedicating most of the budget to receptors

Tasks:
1) Perform dataset inspection/QC on the three inputs; if any require further downsampling or gene subsetting, do so and save new adatas. Record decisions in notes.
2) Run pre-established methods (HVG, DE, RF, scGeneFit, SpaPROS if workable) on the downsampled adata(s) to obtain candidate marker sets and evaluate cell-type separability.
3) Determine an algorithmic subpanel that achieves strong separability with minimal non-receptor markers; then complete to 500 with receptor genes guided by expression patterns and brain region/cell-class relevance.
4) Benchmark the final panel against method panels: ARI vs panel size curves, ARI/NMI/SI boxplots (with dataset splitting), UMAP comparisons, quantitative UMAP similarity, and an Upset plot of gene intersections.
5) Produce outputs: final 500-gene CSV/TSV with annotations and grouped categories; recap table with methods where genes appear, biological context, and relevance score; all figures; and markdown descriptions of pipeline and completion logic.

Please proceed autonomously and save everything in your workdir. When finished, provide a brief summary file with key results and the path to the final panel file.