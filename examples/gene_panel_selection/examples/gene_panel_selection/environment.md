Task: execute_quick_ari (selection_expert)

- Executed Python script to compute ARI vs panel size and produce candidate_subpanel.
- Key outputs: results/ari_vs_panelsize.csv, results/figures/ari_vs_panel_size.png, results/candidate_subpanel.csv
- Best ARI: RandomForest at size=100 (ARI≈0.514)
- Environment: existing conda env with scanpy, pandas, numpy, seaborn, matplotlib, scikit-learn available; CPU-only run.
- Notes: Several non-fatal warnings (sparse zero-centering; future leiden backend change).