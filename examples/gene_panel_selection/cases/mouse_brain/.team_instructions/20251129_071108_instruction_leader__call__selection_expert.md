Project: Mouse brain receptor profiling panel
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert

Task phase 1: Build datasets from Allen Brain Cell (ABC) Atlas
- Use abc_atlas_access to interact with the ABC cache for WMB-10Xv3 (whole mouse brain snRNA-seq) and WMB-taxonomy annotations.
- Review the official docs (links provided) to confirm the correct usage, but DO NOT download the full expression matrices. Use on-demand slicing to build subsets.
- Use metadata only to select cells representing all major brain regions and major cell classes, then fetch expression for those cells only.
- Construct three Scanpy AnnData files, each < 50,000 cells with proper cell-type labels and sufficient genes for downstream selection. Save all to your workdir with clear names and write a short dataset_summary.md.

Suggested region groupings (can adjust if needed for balance):
1) Telencephalon set: Isocortex + Hippocampal formation + Olfactory areas + Striatum + Pallidum
2) Diencephalon + Midbrain set: Thalamus + Hypothalamus + Midbrain
3) Hindbrain + Cerebellum set: Pons + Medulla + Cerebellum + (other hindbrain as available)

Cell-type coverage requirements:
- Include and label: excitatory neurons, inhibitory neurons, astrocytes, oligodendrocytes, OPCs, microglia, endothelial and pericytes (vascular), ependymal, and other stromal if available.
- Use taxonomy labels from WMB-taxonomy (e.g., class/subclass/cell_type/region fields as appropriate) so that AnnData.obs has meaningful labels.

Gene content per AnnData:
- Target ~3,000 genes per AnnData to stay within the 3k gene guideline. Select a union of HVGs (per subset) and a curated set of receptor genes so receptors are represented. If needed, prune non-receptor HVGs to respect the ~3k limit; always retain receptor genes.
- Do not materialize or download full matrices; use ABC cache’s matrix slicing utilities.

Outputs to produce in your workdir:
- Adata files: telencephalon.h5ad, diencephalon_midbrain.h5ad, hindbrain_cerebellum.h5ad
- A markdown summary with cell counts by class/region and number of genes per file.
- Log the exact cache selection strategy in a methods.md.

Computational environment context:
- See /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/environment.md for installed versions. abc_atlas_access is installed. No GPU. Plenty of RAM.

Task phase 2: Gene panel selection (after datasets are saved)
Goal: Build a 500-gene mouse brain receptor profiling panel with annotation and grouped categories.
High-level objectives:
- Primary purpose: receptor profiling across brain. Include as many relevant druggable receptors as possible while keeping a relatively small core set of marker genes for robust cell typing across the whole brain.
- The panel must enable identification of all major cell types (excitatory, inhibitory, astrocytes, oligodendrocytes, OPCs, microglia, endothelial/pericytes, ependymal, etc.).
- Receptor families of interest (non-exhaustive): GPCRs (including neurotransmitter receptors), ligand-gated ion channels (GABA-A, NMDA, AMPA, nicotinic, etc.), voltage-gated ion channels (Nav/Scn, Cav/Cacna, Hcn, Kcn families), receptor tyrosine kinases (Ntrk, Egfr, Pdgfr, Vegfr/Kdr, Fgfr, Eph), cytokine/chemokine receptors, purinergic (P2ry/P2rx, Adora), histamine, neuropeptide receptors (Sstr, Npy receptors, Crhr, Oxtr, Avpr), adhesion GPCRs, frizzled/wnt, notch, TGF-beta, nuclear hormone receptors (if informative), and key vascular receptors (Tie/Tek, Flt1, Kdr).
- Panel size: 500 genes total. Bias selection towards receptor coverage; restrict the marker core to the minimum needed for high cell-type separability.

Required selection/benchmarking scope:
- Perform dataset inspection/QC and, if needed, additional downsampling (<=50k cells) and gene subsetting (<=3k genes) per the workflow.
- Run pre-established methods (HVG, SpaPROS, scGeneFit, Differential Expression, Random Forest) on the downsampled adata(s). Use the downsampled versions for algorithmic selection while leveraging the full biological context for panel completion.
- Independently determine an optimal receptor-centric subpanel ensuring cell-type separability. Then complete to 500 by prioritizing druggable receptors across families.
- Benchmark and compare the final panel vs method-specific panels (ARI/NMI/SI, UMAP comparisons, ARI vs panel size curves, Upset plot). Use appropriate dataset splitting strategies.

Deliverables in your workdir:
- Final 500-gene panel CSV/TSV with annotations and category groups, and a recap table including “Methods where it appears”, “Biological relevance (context)”, and a relevance score.
- Figures: ARI vs panel size curves, ARI/NMI/SI boxplots, UMAPs, quantitative UMAP similarity, Upset plot of intersections.
- Markdown descriptions of your pipeline and logic, including completion rationale.

Please proceed autonomously. Save all outputs in your workdir and report back the paths of the three AnnData files once created; then continue through selection and benchmarking steps according to the workflow.