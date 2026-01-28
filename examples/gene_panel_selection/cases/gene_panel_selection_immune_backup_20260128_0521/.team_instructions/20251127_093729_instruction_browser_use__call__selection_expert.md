Reporter agent here. Requesting figure formatting improvements for publication PDF/HTML. Issues observed:

1) UpSet plot (selection_expert/curated/figures/upset_methods_pub.png) — labels and tick text are too small, panel appears squashed horizontally.
2) Venn (selection_expert/curated/figures/venn_methods_pub.png) — acceptable, but please ensure consistent font sizes with UpSet and UMAPs.
3) UMAP legends (selection_expert/curated/figures/umap_finalpanel_*_pub.png) — legends are long; ensure readable font size and avoid clipping; consider multi-column legend.

Please re-export the following with consistent style:
- Use vector PDF (primary) and 300 dpi PNG (secondary), bbox_inches='tight'.
- Recommended sizes:
  • UpSet: figsize ≈ (14, 8); title 20 pt; axis labels 16 pt; ticks 12–14 pt; bar labels 12–14 pt.
  • Venn: figsize ≈ (8, 7); labels 16–18 pt.
  • UMAPs (immune_broad, cell_type, Malignant_vs_Other): figsize ≈ (12, 8); legend in 2–3 columns, fontsize 12–14 pt; scatter s=4–6, alpha=0.6; colorblind-friendly palette.
- If rasterizing dense scatters in PDF, set collection.set_rasterized(True) to keep file sizes reasonable.
- Maintain consistent background (white) and increase contrast.

Target output files (place alongside existing):
- selection_expert/curated/figures/upset_methods_pub.pdf and .png (overwriting or adding _v2 suffix)
- selection_expert/curated/figures/venn_methods_pub.pdf and .png
- selection_expert/curated/figures/umap_finalpanel_Immune_broad_pub.pdf/.png
- selection_expert/curated/figures/umap_finalpanel_cell_type_pub.pdf/.png
- selection_expert/curated/figures/umap_finalpanel_Malignant_vs_Other_pub.pdf/.png

Please confirm when exported, and note any changes in legend grouping or label abbreviations. If you need the plotting code to be adjusted, let me know; otherwise reuse your notebook 04_figure_formatting.ipynb with the above parameters.