Project: Immune-oncology 1000-gene panel (human TME)
Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Active AnnData (to use for all steps):
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_qc.h5ad

Task (Steps 2–4 planning and execution): Gene panel selection toward a curated 1000-gene immune-oncology panel for human TME profiling.

High-level instructions (you decide software/steps):
1) Run multiple complementary selection strategies on the active AnnData:
   - HVG (~2,000 genes) for robustness to technical noise
   - Differential expression across annotated clusters/lineages to capture discriminative markers, including regulatory populations (Tregs, MDSCs) and myeloid granularity (macrophage/monocyte/DC/neutrophil)
   - scGeneFit to optimize cell-type separability
   - SpaPROS to account for dropout and stability
   - Random Forest feature importance for supervised separability of major classes (cell types and malignant vs. non-malignant). Optionally include state labels (activation/exhaustion/proliferation) if feasible from markers.

2) Construct a scored union of candidate genes. Integrate curated knowledge to ensure coverage of:
   - Major immune lineages and key subtypes
   - Cytokines, chemokines and their receptors (IL/TNF/IFN/CXCL/CCL families, CCR/CXCR, etc.)
   - Immune checkpoints and exhaustion/activation/cytotoxicity markers (e.g., PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, TOX, CXCL13, PRF1, GZMB, IFNG)
   - Oncogenes and tumor suppressors (e.g., TP53, KRAS, BRAF, EGFR, PTEN, MYC, RB1)
   - Pathway nodes: MAPK, PI3K/AKT/mTOR, JAK-STAT, TGF-β, WNT/β-catenin, NF-κB, Hippo, Notch, Hedgehog
   - Cell-cycle, DNA damage, stress, hypoxia, angiogenesis, EMT and proliferation markers
   - Housekeeping controls and spike-ins where relevant

3) Curation and constraints for final panel (~1000 genes):
   - Enforce per-category quotas to balance interpretability and coverage (you choose exact splits; aim ~40–50% immune/cytokine/checkpoints, ~25–30% cancer/pathway, ~15–20% cell states, ~5–10% controls)
   - Reduce redundancy (paralogs/highly correlated); prefer broadly expressed yet specific markers for spatial probe design
   - Favor genes with consistent detection across batches; penalize extremely low-detection cytokines unless critical
   - Include sentinel genes for malignant vs. non-malignant discrimination and subclone detection

Deliverables and artifacts
- Save method-specific rankings and selected sets (csv/tsv), and a merged scored table with categories and final selection flag.
- Save plots: overlap UpSet, score distributions, per-category coverage, UMAP overlays for representative markers.
- Save the final curated gene list as:
  - final_panel_1000.tsv (with gene symbol and category annotations)
  - final_panel_1000.json (machine-readable; include annotations and scores)
- Provide a narrative markdown: selection_summary.md describing methods, thresholds, and rationale.

Keep memory/disk in mind and log key decisions. When complete, let me know paths to main outputs.