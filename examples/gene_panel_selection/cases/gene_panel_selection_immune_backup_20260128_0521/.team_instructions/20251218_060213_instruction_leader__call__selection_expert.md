Project: Human immune-oncology gene panel selection
Steps 2–4: Selection algorithms, panel completion to 1000 genes, and benchmarking — finalize and fill any missing outputs

Inputs and context
- Working AnnData for algorithms: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_qc.h5ad
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Environment info: environment.md in project root
- Existing artifacts detected: gene_panels (rf/scgenefit/spapros), consensus_scores.csv, core_subpanel.csv, ari_vs_size.csv, final_panel_1000.csv, final_panel_recap.csv, and several figures. Please reuse existing results where possible and avoid recomputation unless necessary.

What to do (high-level)
2) Run/verify pre-established methods (HVG, SpaPROS, scGeneFit, DE, RF) on adata_qc.h5ad. If results already exist, just validate and register them. Ensure method outputs are saved in your workdir/gene_panels/<method>/ with top lists and full scores where applicable. Ensure method intersection inputs are available.
3) Using your internal logic, confirm the optimal subpanel for cell-type separability and complete to a final 1000-gene panel with required biological coverage for immune oncology use:
   - lineage markers across T/NK/B/plasma/myeloid/DC/endothelium/fibroblast/epithelial/cancer
   - immune checkpoints and exhaustion/activation markers
   - cytotoxicity genes, antigen presentation, cytokines/chemokines and receptors
   - pathway readouts (MAPK/PI3K/AKT/mTOR, JAK/STAT, WNT, TGF-β, NF-κB), oncogenes/TSGs, proliferation/cell cycle, EMT, hypoxia, stress/death, metabolism
   - annotate each gene into major categories; export final_panel_1000.csv and a recap table linking methods appearances and a relevance score
4) Benchmark and compare: produce
   - ARI vs panel size curves
   - dataset splitting strategy documentation and ARI/NMI/SI boxplots
   - UMAP comparisons and quantitative UMAP similarity
   - Upset plot of intersections across methods
   - recap table (gene | methods | biological relevance | relevance score)

Deliverables to ensure exist in your workdir
- gene_panels/<method>/ outputs for HVG, SpaPROS, scGeneFit, DE, RF
- ari_vs_size.csv and figure(s)
- upset plot image
- benchmarking figures: ARI/NMI/SI boxplots; UMAP comparisons; quantitative UMAP similarity
- final_panel_1000.csv (with categories) and final_panel_recap.csv
- selection_pipeline_summary.md that narrates your pipeline, chosen subpanel size logic, coverage rationale, and benchmarking summary

Proceed autonomously. Only add or recompute what is missing; otherwise reuse existing results.