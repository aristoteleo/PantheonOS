Proceed with pre-established gene selection algorithms for the human immune oncology panel.

Workdir
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Dataset
- adata_path for algorithms: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Label key for separability: author_cell_type_update (34 classes)

Environment context
- High RAM/CPU; GPUs available but numba CUDA not configured. Prefer CPU neighbors (pynndescent). If runtime limits occur, generate a stratified downsample (100–150k cells), save it in your workdir, and update QC report.

Goal of selection
- Derive a robust subpanel that maximizes cell-type separability across the TME, to be later completed to a 1000-gene immune-oncology panel.
- Run methods: HVG, SpaPROS, scGeneFit, Differential Expression (per class vs rest), Random Forest (multiclass importance).

Deliverables
- Save each method’s ranked gene list and figures into method-specific subfolders under your workdir.
- Produce an upset plot of method intersections.
- Provide an interim markdown summary with recommended separability subpanel and rationale, deferring final completion to immune-oncology categories later.
- File names: methods_overview.md; place figures under figures/ and tables/ as appropriate.