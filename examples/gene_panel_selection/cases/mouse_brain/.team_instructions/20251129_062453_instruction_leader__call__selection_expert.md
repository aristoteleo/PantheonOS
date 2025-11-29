Project: Mouse brain receptor profiling panel selection
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert

Computational environment:
- Please refer to environment.md at the project root for OS/CPU/RAM and package versions. abc_atlas_access is installed and importable.

Goal overview:
- Build a 500-gene receptor-centric panel for mouse brain that both:
  1) Robustly identifies all major brain cell classes and finer cell types (excitatory vs inhibitory neurons, astrocytes, oligodendrocytes and OPCs, microglia, ependymal, vascular compartments including endothelial, pericytes, SMCs), across brain regions
  2) Profiles druggable receptor families broadly expressed/relevant in brain (GPCRs, ion channels, receptor tyrosine kinases, cytokine/interleukin receptors, neuropeptide receptors, neurotransmitter transporters/receptors, nuclear receptors, immune receptors), prioritizing brain-relevant, disease/druggable targets

Dataset:
- Use the Allen Brain Cell Atlas cache to explore the whole mouse brain dataset: WMB-10Xv3 (10x v3) and the taxonomy metadata WMB-taxonomy. Do not download full expression matrices; leverage metadata files and cache interfaces.
- Learn navigation from: https://alleninstitute.github.io/abc_atlas_access/notebooks/consensus_mouse_clustering_analysis_and_annotation.html
- Use cache utilities: abc_cache.list_directories(), abc_cache.list_expression_matrix_files("WMB-10Xv3"), abc_cache.list_metadata_files("WMB-taxonomy").

Step 1: Dataset inspection and smart subsampling
- Using only metadata (not full matrices), stratify and subsample the WMB-10Xv3 dataset to construct three balanced cell sets that together span all major brain regions (isocortex, hippocampus, thalamus, hypothalamus, midbrain, hindbrain, cerebellum, olfactory areas, striatum/pallidum) and major cell classes.
- Each derived dataset should contain < 50,000 cells and include cell-type labels and region annotations from WMB-taxonomy. Save as three scanpy AnnData files (.h5ad), with raw counts loaded for just the selected cells/genes. Ensure the files are small and manageable.
- Save these in your agent workdir and return their paths. Name them e.g., adata_wmb_subsample_set1.h5ad, set2, set3.

Constraints:
- Do not download the full matrices; avoid huge files. Use ABC cache functions to guide which files to access and pull only what is necessary to assemble the three <50k-cell AnnData objects with proper labels.
- If needed, perform gene subsetting to a manageable feature set (<= 3,000 genes) for each adata to speed up selection algorithms later while preserving receptor coverage.

Step 2: Panel selection planning
- Plan to run pre-established selection algorithms (HVG, SpaPROS, scGeneFit, DE, Random Forest) on the subsampled data. Because the primary goal is receptor profiling, your final panel should reserve a core of cell-typing markers (e.g., ~150-200 genes) while maximizing inclusion of receptor/druggable targets across families for the remaining slots.
- Integrate Allen annotations and external receptor target knowledge to prioritize target classes (e.g., GPCRs, ligand-gated ion channels, glutamate/GABA receptors, acetylcholine, dopamine, serotonin, histamine, adrenergic, neuropeptides, RTKs, cytokine receptors, chemokine receptors, Toll-like, complement, Fc receptors, adhesion GPCRs, nuclear receptors). Balance coverage across neuronal and non-neuronal compartments.

Step 3: Execute selection on the subsampled adatas
- For each of the three adata subsets (<50k cells each), run the pre-established selection algorithms you have for marker discovery and produce algorithm-specific panels and an integrated subpanel that optimizes cell-type separability given the size constraint and computational envelope. Respect the downsampling/gene subsetting rules. Benchmark separability (ARI/NMI/SI, UMAP) vs panel size.

Step 4: Complete to final 500-gene panel for receptor profiling
- Starting from the integrated marker subpanel, complete to 500 genes prioritizing druggable brain receptors and transporters, ensuring multiple families are represented. Use the Allen metadata for brain-region-specific relevance where useful. Provide annotations per gene: family, putative druggability, known CNS relevance.

Step 5: Benchmark and compare
- Compare the final 500-gene panel to panels from each pre-established algorithm; generate ARI vs panel size curves, intersections (UpSet plot), and quantitative UMAP comparisons. Save figures and tables in your agent workdir.

Deliverables to save in your agent workdir:
- The three subsampled AnnData files with labels (<50k cells each)
- Algorithm-specific panels, integrated subpanel, and the final 500-gene panel with annotations (CSV/TSV)
- Benchmark figures: ARI/NMI/SI vs size, UMAPs, UpSet plot, intersection tables
- A markdown summary of your pipeline, decisions, and results for the reporter

Please proceed autonomously within these constraints and report back the paths to the created adata files and the final panel CSV once ready.