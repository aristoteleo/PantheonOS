Project: Immune oncology gene panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Phase 2 — Execute per-method scoring and optimal sub-panel discovery

Inputs
- AnnData path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Label key for supervised methods: cell_type
- Environment: see environment.md

Tasks
1) Preprocess as needed (normalize_total, log1p; compute PCA/neighbors as required) without altering the biological labels.
2) Run per-method gene scoring:
   - HVG (Scanpy)
   - Differential Expression (one-vs-rest across cell_type; at least wilcoxon; optionally logreg)
   - Random Forest feature importance
   - scGeneFit (pairwise/pairwise_centers; max_constraints ≤ 1000)
   - SpaPROS (n_hvg ≤ 3000)
   - CellTypistGPS (train annotator on cell_type; export gene weights)
   Save the gene score tables (CSV/TSV) per method in your workdir and record any key plots.
3) For each method independently, construct sub-panels at sizes {100, 200, …, 1000}; for each size, compute neighbors+Leiden on the panel genes, annotate via CellTypist voting (using your trained model), compute ARI vs true cell_type, and produce ARI vs size curves.
4) Select the best method+size combination as the optimal sub-panel for cell-type separability (this subpanel is not the final 1000-gene panel yet). Save the selected sub-panel as a text/CSV file and a markdown summary with key findings and plots. Document any computational tradeoffs or downsampling you may need.

Outputs
- Preprocessing summary and any intermediate AnnData path(s)
- Score CSVs for each method and plots
- ARI vs size curves per method
- Selected optimal sub-panel (genes and size)
- A markdown summary: selection_expert/phase2_selection_summary.md