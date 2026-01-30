Project: Immune oncology gene panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Phase 2 wrap-up and Phase 3–4 execution

Context
- You already generated multiple per-method outputs (HVG, DE, Random Forest, scGeneFit, SpaPROS) and benchmarking CSVs/PDFs are present. Please finalize the optimal subpanel selection and then complete the immune-oncology panel to 1000 genes with annotations and categories.

Tasks
A) Phase 2 wrap-up
- Confirm the optimal subpanel for cell-type separability based on ARI vs size analysis across methods. Export:
  - subpanel_optimal_genes.txt and subpanel_optimal_summary.md
  - ARI vs size curves per method (PNG/PDF)
  - Upset plot of method panel intersections (for 1000-size panels)

B) Phase 3 — Consensus + biological completion to 1000 genes
- Using the consensus of pre-established methods, complete to a final panel of exactly 1000 genes tailored to the immune-oncology goals described earlier (lineage and fine-grained immune resolution; antigen presentation and checkpoints; cytokines/chemokines and receptors; signaling/cancer pathways; oncogenes/tumor suppressors/EMT; proliferation; metabolism/stress; spatial/TME interactions).
- Provide category assignments and brief rationale for each gene. Deliverables:
  - final_panel_1000.tsv (columns: Gene, Category, Subcategory, Rationale, Methods_supporting, Consensus_score)
  - final_panel_1000_genes.txt (one gene per line)
  - category_counts.json and a barplot figure summarizing category composition
  - recap_table.tsv capturing the example schema (Gene | Methods where it appears | Biological relevance | Relevance score)

C) Phase 4 — Benchmarking and comparison
- Benchmark the final 1000-gene panel against the method-specific 1000-gene panels you computed (HVG, DE, SpaPROS, RF) and any reference panel present (e.g., Vizgen815_815 in your benchmarking files). Include:
  - dataset splitting strategy documentation
  - ARI/NMI/SI boxplots (PNG/PDF)
  - UMAP comparisons and quantitative UMAP similarity
  - benchmarking CSVs (ARI/NMI/SI, genes used)
- Write brief summaries:
  - phase3_completion_summary.md
  - phase4_benchmarking_summary.md

General notes
- Keep everything saved under your workdir. Use existing computations where possible to avoid recomputation. Use HGNC symbols.
- If any earlier file already exists (e.g., benchmark_*.csv), reuse and complement rather than overwrite.
- If you require any additional lightweight web lookups to support rationale text, do so and cite compactly.

Please proceed autonomously within these instructions and notify on completion.