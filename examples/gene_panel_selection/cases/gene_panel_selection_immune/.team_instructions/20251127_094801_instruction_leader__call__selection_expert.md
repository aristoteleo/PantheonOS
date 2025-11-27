Project: Immune-oncology 1000-gene panel (Human TME)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Active dataset input: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad

Task: Apply biologist’s proposed adjustments to the final 1000-gene panel and regenerate artifacts.

Inputs
- Current curated panel: selection_expert/curated/final_panel_1000.csv
- Biologist adjustments CSV: biologist/proposed_adjustments.csv (two columns: gene_symbol, action; actions include Add or Drop)

Requirements
1) Read proposed adjustments, perform additions/drops to yield a panel of exactly 1000 genes. If a requested add is already present, skip; if a requested drop is absent, skip. For net size mismatch, adjust by removing the lowest aggregate_score genes within the same category of any additions to keep category balance.
2) Version outputs as v2 and update the default pointer:
   - Write selection_expert/curated/final_panel_1000_v2.csv
   - Overwrite selection_expert/curated/final_panel_1000.csv with v2 to make it the active final panel
   - Update grouped TSV, presence table, coverage summary, and notes (append a section noting applied adjustments)
3) Recompute method presence matrix for the final v2 panel and ensure it is non-empty/accurate.
4) Recompute and save UMAPs and RF confusion using only the v2 panel, to:
   - selection_expert/curated/figures/umap_finalpanel_*.png
   - selection_expert/curated/figures/confusion_*_rf_finalpanel.png
5) Update curation report: selection_expert/report_analysis_expert_curation_final.md with a brief changelog for v2.

Deliverables
- Updated curated tables and figures per above
- Short note: selection_expert/curated/notes_curation.md (append “v2 adjustments applied” with list of genes added/dropped)

