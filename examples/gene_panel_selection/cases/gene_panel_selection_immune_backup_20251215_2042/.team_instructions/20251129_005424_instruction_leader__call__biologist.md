Project: Human immune oncology gene profiling panel
Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist

Task: Interpret the final 1000-gene panel and benchmarking outputs biologically. Produce a concise markdown note linking key genes to TME interpretation and how an end user would use this panel.

Inputs to consult in selection_expert/results:
- final_panel_1000.csv (gene -> category/subcategory/function)
- final_panel_summary.md
- benchmark_metrics.csv and figures/benchmark_*.png
- umap_similarity.csv and figures/umap_similarity.png
- figures/venn_top1500.png and ari_vs_panel_size.png

Guidance:
- Focus on interpretability: how the panel resolves immune cell identities and states (activation/exhaustion), antigen presentation/immune evasion, and major cancer pathways (with examples).
- Provide practical pointers: example gene sets to score (e.g., exhaustion, cytotoxicity, IFN response, EMT, CAF programs), and how to read the benchmarking results when applying the panel.
- Keep to a succinct 1–2 pages in markdown. Save under your agent workdir as final_biology_interpretation.md.
