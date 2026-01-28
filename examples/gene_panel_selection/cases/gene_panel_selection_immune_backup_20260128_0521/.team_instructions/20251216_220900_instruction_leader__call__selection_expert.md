Project: Human immune oncology gene profiling panel
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Dataset:
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Organism: Human
- Source: bioRxiv Preprint (2024) — DOI: 10.1101/2024.01.17.576110

Computational environment: see environment.md at the project root for hardware/software details (large-memory, multi-core, 4x A100; scverse stack incl. scanpy; SpaPROS & scGeneFit available).

Goal:
- Design a 1000-gene immune oncology panel with annotations, with genes grouped in major categories suitable for a human tumor microenvironment.
- The panel must enable: (1) resolution of all major immune cell types (innate and adaptive) and stromal/tumor compartments; (2) profiling of cytokine/chemokine signaling and receptors; (3) assessment of T/NK exhaustion and activation states; (4) coverage of key cancer signaling pathways and oncogenes; (5) ability to distinguish cancer cell stages/states; (6) antigen presentation, costimulatory/co-inhibitory axes; (7) cell cycle/proliferation; (8) EMT/invasion/angiogenesis; (9) housekeeping/technical controls.

Workflow requests (follow your internal pipeline):
1) Perform dataset inspection, QC, and structure inspection; if >500k cells, downsample appropriately; if >30k genes, subset genes. Save and report any derived adata path(s). Consider batches if present.
2) Run your pre-established selection algorithms on the working dataset: HVG, SpaPROS, scGeneFit, Differential Expression, Random Forest. You independently choose parameters. Use only the downsampled dataset if downsampling is necessary.
3) Determine an optimal subpanel for cell-type separability based on your logic, then complete to a final 1000-gene panel integrating biological context and the goals above.
4) Provide clear group annotations for each gene (major categories) and maintain a mapping file.
5) Benchmark and compare the final panel to method-specific panels: include ARI vs panel size curves, Upset plot of intersections, clustering metrics (ARI/NMI/SI) with dataset splitting, and UMAP comparisons with quantitative similarity.
6) Save all outputs (figures, tables, markdown summaries) under your workdir and provide paths to key artifacts, including the final panel as CSV/TSV with annotations.

Please proceed end-to-end and document the process. If the initial dataset is too large, ensure resource-aware choices as per environment.md. Save the path of the final working dataset you used for algorithms.