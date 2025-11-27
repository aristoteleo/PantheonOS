Random Forest feature importance
- Multiclass: label_key = cell_type, n_top=1000
- Binary: label_key = Malignant_vs_Other, n_top=600
- return_scores: true

Outputs in gene_panels/random_forest/
- rf_top_1000.csv
- rf_top_600.csv