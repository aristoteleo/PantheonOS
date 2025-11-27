Differential expression markers
- rank_genes_groups Wilcoxon
- Groups: Immune_broad; Binary: Malignant_vs_Other
- Filters: log2FC>0.5, padj<0.05; for Immune_broad also pct_nz_group>0.2 and pct_nz_reference<0.2
- Aggregation: rank within group by score; global aggregate by mean rank and mean score

Outputs:
- DE_Immune_broad_ranked.csv (+ _with_symbol)
- DE_Malignant_vs_Other_ranked.csv (+ _with_symbol)