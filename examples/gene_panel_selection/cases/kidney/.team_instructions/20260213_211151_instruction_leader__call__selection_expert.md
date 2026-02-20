Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/selection_expert

Context and existing artifacts:
- There is a prior kidney backup with completed Step 1 (dataset QC/inspection) and precomputed method outputs and a finalized 1000-gene panel at: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/do_not_touch_kidney_backup/workdir/selection_expert/results
  Key files: adata_kidney_combined_pp.h5ad; gene_panels/* for DE/HVG/RF/SpaPROS/scGeneFit; final_panel_1000.txt and annotations; benchmarking CSVs and figures.
- Inputs for this case:
  * Seed subpanel (should match core_subpanel_path.txt): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/best_G_star.txt (note: reported missing in current tree; use backup core_subpanel_path as seed if this path is unavailable)
  * Pre-established gene scores: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/scores_kidney_filtered.csv (if missing, infer from existing method outputs in backup results)
  * Train dataset (full): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/do_not_touch_kidney_backup/preliminary_results/adata_kidney.h5ad
  * Benchmark test datasets (batches): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/kidney_batches

Goal:
Design a human kidney cancer gene profiling panel of size within 500-1000 genes (choose 1000 if justified by separability curves) to:
- Catalog all cell types in kidney tumors and adjacent renal tissue,
- Resolve immune/stromal/vascular compartments,
- Differentiate malignant renal epithelial cells from normal compartments,
- Capture hypoxia/angiogenesis, metabolic reprogramming, proliferation, cytokine/interferon signaling,
- Distinguish immune activation vs exhaustion,
- Cover oncogenes and signaling molecules relevant to kidney cancer progression states.

Instructions:
1) Step 1 (Understanding/QC): Reuse existing processed adata and summaries if available from the backup; if needed, re-inspect the provided train adata. Only downsample if >500k cells; subset genes if >30k. Save any new summary into your workdir.
2) Pre-established methods: Plan to run CelltypistGPS, HVG, SpaPROS, scGeneFit, DE, Random Forest. If equivalent outputs already exist in the backup, reuse them. Otherwise compute using the combined preprocessed adata. Use the downsampled/combined dataset for algorithmic steps as per your standard.
3) Seed integration and panel building: Start from the seed subpanel (best_G_star.txt; if the provided path is missing, take the path stored in the backup core_subpanel_path.txt). Complete to target size using your biological consensus fill, leveraging the provided scores file if available; otherwise derive scores/consensus from method outputs. Ensure genes are human symbols.
4) Annotation and categorization: Provide annotations for each gene and categorize them into major biological categories aligned with the project purpose (e.g., renal epithelial markers, malignant-state markers, hypoxia/angiogenesis, metabolism, proliferation/cell-cycle, cytokine/interferon and receptors, immune activation/exhaustion, stromal/ECM, vascular/endothelium/pericytes, myeloid/lymphoid markers, antigen presentation, NK cytotoxicity, TCR/BCR signaling, checkpoint molecules, EMT/epithelial programs, stress responses, DDR/apoptosis/autophagy/ferroptosis).
5) Benchmarking: Benchmark the final panel against each pre-established method panel using the provided kidney_batches as test sets. Produce ARI/NMI/SI boxplots, ARI vs panel size curves, UMAP comparisons, quantitative UMAP similarity, and an UpSet plot of intersections between panels. Follow your standard evaluation.
6) Outputs: Save to your workdir/results:
   - final_panel_[N].txt and final_panel_[N]_annotations.(csv|tsv)
   - recap table with methods overlap and biological relevance columns
   - all figures (ARI vs size, ARI/NMI/SI boxplots, UMAP comparisons, Upset, UMAP similarity bars)
   - dataset summary and any notes. Also include paths_summary.* capturing any reused backup paths.
7) Document the selection pipeline you followed and the completion logic for choosing final N within 500-1000 based on separability curves and TME coverage.

Environment constraints: System has ample CPU/GPU and full scverse stack per environment.md. Use GPU if it speeds RF/NN components.

Proceed autonomously and reuse already available artifacts to avoid recomputation. Save everything under the specified workdir for this case.