Project: Mouse brain receptor profiling panel

Workdir:
- project_workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir
- agent_workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert

Computational environment context:
- CPU-only machine, 56 cores, 1.5 TiB RAM; no GPU
- Please reuse existing results and avoid recomputation whenever possible
- IMPORTANT: Do NOT run SpaPROS in this project

Dataset to use (reuse existing prepared data):
- primary adata for algorithmic selection/benchmarking: combined_downsampled_50k.h5ad
  path: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/combined_downsampled_50k.h5ad
- additional subsets already available if needed for context:
  - combined_union_downsampled_50k_hvg3k.h5ad
  - telencephalon.h5ad; diencephalon_midbrain.h5ad; hindbrain_cerebellum.h5ad
- dataset summary exists: dataset_summary.md

Existing pre-established algorithm outputs to reuse (do not recompute):
- HVG top list (1000): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/panel_hvg_1000.tsv
- Differential Expression panel (1000): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/panel_de_1000.tsv
- Random Forest top 1000: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/random_forest/rf_top_1000.csv
- scGeneFit scores: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain_fast_results/workdir/selection_expert/gene_panels/scgenefit/scgenefit_scores.csv
- SpaPROS: do not run (ignore)

Goal and constraints for panel selection:
- Build a 500-gene brain receptor profiling panel with annotations and grouping into major categories
- Primary purpose: enable profiling of druggable receptor targets in the mouse brain while retaining a relatively small but sufficient core set of marker genes for accurate cell-type mapping across the brain (excitatory/inhibitory neurons, astrocytes, oligodendrocytes/OPCs, microglia, endothelial/pericytes and other vascular cells, ependymal)
- Include broad coverage of receptor families relevant to CNS pharmacology, e.g., GPCRs (monoaminergic, neuropeptidergic, chemokine, purinergic), ligand/voltage-gated ion channels (glutamate, GABA, glycine, nAChR, 5-HT3, P2X, TRP), receptor tyrosine kinases (ErbB, Eph, Ntrk), cytokine/JAK-STAT, TGF-β, Wnt/Frizzled, Notch, nuclear receptors, steroid receptors, adrenergic/cholinergic/dopaminergic/serotonergic/histaminergic receptors, cannabinoid receptors, orphan GPCRs with brain relevance
- Keep core cell-typing markers lean (e.g., ~150–200 genes) and allocate the remainder to receptor coverage
- Exclude SpaPROS entirely; rely on existing HVG, DE, RF, scGeneFit outputs and your standard integration logic to derive a core separability subpanel

Tasks to perform now:
1) Integrate the existing algorithm outputs (HVG, DE, RF, scGeneFit) to produce an optimal core subpanel maximizing cell-type separability on the provided downsampled dataset. Reuse the combined_downsampled_50k.h5ad. Save intermediate artifacts and figures in agent_workdir.
2) Complete to a final 500-gene receptor-centric panel by prioritizing druggable receptors and CNS-relevant receptor families. Use the full ABC context only for biological enrichment if needed, but do not recompute base selections. Provide category annotations per gene (e.g., cell-type marker, GPCR subtype, ion channel subtype, RTK, cytokine receptor, nuclear receptor, etc.).
3) Benchmark and compare: include the final panel and the baseline algorithm panels (HVG, DE, RF, scGeneFit-derived) with ARI/NMI/SI, ARI-vs-panel-size curves, UMAPs, and an Upset plot of intersections. Use appropriate train/test splits and quantitative UMAP similarity as in your standard pipeline. Save all figures/tables under selection_expert/figures and panel files under selection_expert/gene_panels.
4) Generate concise markdown summaries of methods, selection rationale, completion logic to 500, and a recap table (Gene | Methods where it appears | Biological relevance | Relevance score). Save as selection_expert/selection_summary.md.

Please proceed autonomously, saving all outputs into your agent workdir. Avoid recomputing any already-existing results; only compute what is missing for integration, completion, annotations, and benchmarking. Exclude SpaPROS completely.