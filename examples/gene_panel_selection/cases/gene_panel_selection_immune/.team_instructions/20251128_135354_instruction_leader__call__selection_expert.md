Phase 3a — Run remaining selection methods (resume) and produce ranked lists

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Prepared AnnData: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000_prepared.h5ad
label_key: leiden

Note: SpaPROS outputs already exist under results_phase3/gene_panels/spapros. Do not recompute SpaPROS.

Tasks in this call:
1) HVG ranked list
   - Use existing HVG metadata (cache_phase2/hvg_table.csv) and export a ranked list of genes by highly_variable_rank. Save CSV plus top lists for sizes 100, 200, 400, 600, 800, 1000 under results_phase3/gene_panels/hvg/.
2) scGeneFit
   - Run scGeneFit with label_key=leiden on the prepared data; export full scores/ranks and top lists for sizes 100..1000 under results_phase3/gene_panels/scgenefit/.
3) Differential Expression (DE)
   - Use existing DE cache if present (cache_phase2/de_leiden_all.csv). From per-cluster DE rankings (wilcoxon), assemble a consensus ranked list optimized for global separability (e.g., take top-N per cluster proportional to cluster size, aggregate by best rank/score). Export full consensus CSV and top lists for sizes 100..1000 under results_phase3/gene_panels/de/.

Return the key output paths created in this step.