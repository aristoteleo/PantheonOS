Project: Human immune oncology gene profiling panel (1000 genes)

Paths:
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Dataset for algorithms: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsample_cells_3kHVG.h5ad
- Full-genes downsample: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsample_cells_fullgenes.h5ad

Selection scope & goals:
- Panel size: 1000 genes total
- Objectives:
  1) Enable robust cell-type separability across all 31 annotated types with margin for unannotated subtypes.
  2) Immune profiling and cell-state characterization: exhaustion, activation, cytotoxicity, proliferation, interferon response, antigen presentation, cytokine/chemokine axes, costimulation/co-inhibition.
  3) Cancer signaling and oncogenic programs: RTK/RAS/MAPK, PI3K/AKT/mTOR, JAK/STAT, NFkB, WNT/β-catenin, TGF-β/SMAD, Notch, Hippo, Hedgehog; DNA damage/repair; cell cycle; EMT/hypoxia; metabolism; adhesion/migration.
  4) Tumor states and staging via canonical oncogenes/tumor suppressors (EGFR, ERBB2/3, KRAS/NRAS/HRAS, BRAF, MET, FGFRs, ALK/ROS1/RET/NTRK, MYC, CCND1, MDM2, PTEN, RB1, CDKN2A, APC, CTNNB1, SMAD4, VHL, PBRM1, etc.).

Tasks (Step 2–4 core):
- Run pre-established methods HVG, SpaPROS, scGeneFit, Differential Expression, and Random Forest using the designated dataset(s) to derive an optimal subpanel that maximizes cell-type separability and stability across resampling.
- Then complete to a final 1000-gene panel by integrating the biological context above, ensuring coverage of cytokines/chemokines, checkpoints, receptors/ligands, antigen presentation, pathway nodes, TFs, and oncogenes/tumor suppressors.
- Produce benchmark comparisons among method panels and the final panel (ARI/NMI/SI, ARI vs panel size, UMAP comparisons, Upset intersections).
- Provide annotations and groupings for each gene into major categories (e.g., Immune markers, Cytokines/Chemokines, Checkpoints/Co-stim, Antigen presentation, Signaling pathways [subcategories], Cell cycle/DNA repair, EMT/Hypoxia/Stress, Metabolism, Adhesion/Migration, Oncogenes/TSGs, Housekeeping/controls).

Deliverables in your workdir:
- A subpanel derived from algorithms (not final), the final 1000-gene panel (CSV/TSV), annotation table, category assignments.
- Figures: ARI vs panel size, UMAPs, boxplots, Upset plot, recap table CSV.
- A concise methods/summary markdown describing pipeline, completion logic, and rationale for optimal subpanel size.

Please proceed through these steps. Save all outputs in your workdir as you go. We will call the biologist to interpret interim and final results after you finish your selection and benchmarking.