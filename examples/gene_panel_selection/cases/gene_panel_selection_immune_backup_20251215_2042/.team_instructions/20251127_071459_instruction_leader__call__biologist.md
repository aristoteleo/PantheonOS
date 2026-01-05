Project: Immune-oncology 1000-gene panel (Human TME)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist

Inputs to review (produced by selection_expert):
- Downsampled active adata: selection_expert/adata_downsampled_50k_3kHVG.h5ad
- Selection method outputs and aggregate ranking under selection_expert/methods and selection_expert/aggregate
- Final curated panel and coverage summary:
  - selection_expert/curated/final_panel_1000.csv
  - selection_expert/curated/final_panel_coverage_summary.md
  - selection_expert/curated/figures (UMAPs, Venn/UpSet, confusion matrices)
  - selection_expert/curated/notes_curation.md

Task:
Provide a biological interpretation focusing on:
1) How well the 1000-gene panel captures the human TME landscape and each required capability:
   - Immune lineage resolution (T/NK/B/plasma; macrophages/monocytes/DCs/neutrophils; Tregs/MDSC)
   - Cytokine/chemokine states; exhaustion and activation axes
   - Malignant vs non-malignant discrimination; signatures for cancer pathways (RTK/MAPK/PI3K/JAK-STAT/TGF-β/WNT) and stress programs (cell cycle, DNA damage, hypoxia/angiogenesis/EMT)
2) Identify any residual gaps or risks (e.g., lowly expressed cytokines, cross-family homology causing probe ambiguity, lineage ambiguity) and propose concrete adjustments (≤20 genes) if absolutely necessary.
3) Provide concise annotations for 20 sentinel genes across categories, explaining why they are critical in this dataset context (cite general literature knowledge as needed).

Deliverables:
- Write your interpretation and recommendations to: biologist/interpretation_final_panel.md
- If proposing any adjustments, produce a small CSV with two columns [gene_symbol, action] at biologist/proposed_adjustments.csv
- Keep the writing precise and suitable for inclusion in a report.
