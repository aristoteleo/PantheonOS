Project: Human immune oncology gene profiling panel

Goal: Build a 1000-gene panel (with annotations and grouped into major categories) that can:
- Catalogue all major cell types in the tumor microenvironment (immune + stromal + malignant)
- Resolve immune cell subtypes and key states (activation/exhaustion/cytotoxicity/regulatory)
- Profile cytokine/chemokine networks and receptors
- Characterize cancer signaling pathways and oncogene programs
- Distinguish malignant cell stages/states and proliferation
- Enable robust clustering/ARI across methods; include benchmarking

Dataset
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Source: bioRxiv Preprint (2024) DOI: 10.1101/2024.01.17.576110

Computational environment
- See environment.md at project root for constraints (CPU-only; ample RAM and disk)
- Workdir (project): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Instructions
1) Inspect the dataset, perform QC and structure inspection. If >50k cells, downsample appropriately; if >3000 genes, subset genes for algorithm runs. Save any downsampled/filtered adata paths for subsequent steps.
2) Plan to run the pre-established selection algorithms (HVG, SpaPROS, scGeneFit, Differential Expression, Random Forest) on the downsampled input only. Determine a subpanel that optimizes cell-type separability.
3) Complete to a 1000-gene final panel by integrating immune-oncology biological context: immune celltype markers, cytokines/chemokines and their receptors, exhaustion/activation/cytotoxicity/regulatory markers, antigen presentation, interferon response, hypoxia, EMT, angiogenesis, cell cycle/proliferation, DNA damage/repair, apoptosis, and key oncogenic pathways (RAS/MAPK, PI3K/AKT/mTOR, WNT/beta-catenin, TGF-β, Notch, JAK/STAT, NF-κB, Hippo/YAP, MYC, p53, RB, RTKs, integrins). Ensure gene annotations and grouping tags are provided.
4) Benchmark and compare the final panel to method-specific panels. Include ARI vs panel size curves, UMAP comparisons, Venn intersections, and quantitative metrics (ARI/NMI/SI) as per your standard pipeline. Save figures/tables in your workdir.
5) Save: panel CSV/TSV with columns [gene, category, subcategory, notes], panels from each method, and a README of your pipeline & decisions. Provide a summary markdown for the reporter.

Proceed end-to-end; conserve compute where possible (CPU-only).