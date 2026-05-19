Project: Immune-oncology 1000-gene panel (human TME)

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Environment background (from environment.md):
- OS: Ubuntu 22.04.5; CPU: 96 vCPUs; RAM ~188 GiB; GPUs: 2x NVIDIA H100 (CUDA 12.8 via torch)
- Python env: scanpy 1.11.5, anndata 0.11.4, scikit-learn 1.7.2, scvi-tools 1.3.3, squidpy 1.6.5, spapros 0.1.5, scGeneFit installed, moscot 0.4.3, others commonly used in scverse
- Disk is relatively constrained (~51G free on /). Please keep intermediates compact and clean up large temporaries when possible.

Dataset
- Species: Human (HGNC symbols)
- Input AnnData to use for ALL subsequent analyses: the downsampled adata saved during your audit step. Use the path recorded in:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/downsampled_adata_path.txt
  (Ignore the original /home/erwinpi/data/adata.h5ad.)
- You have already produced: adata_downsampled.h5ad, adata_downsampled_preprocessed.h5ad, UMAPs and QC.

Biological context and final goal
Design a high-quality 1000-gene panel for profiling the human tumor microenvironment (TME), suitable for spatial (Vizgen-style) deployment and single-cell profiling, optimized for:
- Cell-type separability across all major immune lineages and stromal/epithelial compartments
- Deep immune profiling: T, NK, B, plasma; myeloid (macrophages, monocytes, DCs, neutrophils); regulatory (Tregs, MDSCs)
- Cancer biology: oncogenes, tumor suppressors, key pathway readouts (MAPK, PI3K, JAK-STAT, TGF-β, WNT), EMT, hypoxia, angiogenesis, proliferation, DNA damage/stress
- Cytokine/chemokine states: IL/TNF/IFN families, receptors/ligands, activation/cytotoxicity/inflammation, exhaustion markers (PDCD1, LAG3, HAVCR2, TIGIT, CTLA4, etc.)
- Malignant vs non-malignant distinction and intra-tumor heterogeneity (subclones, signaling states)
- Cell-state analysis: exhaustion, activation, proliferation, senescence, stress programs
- Spatial suitability: prioritize genes with robust, specific signal and avoid highly promiscuous or ultra-lowly expressed targets where possible

What to do (high-level plan)
1) Use ONLY the downsampled AnnData for all computations. Confirm it’s <50k cells. Reuse your preprocessed object if available.
2) Perform multiple complementary gene-selection strategies to build candidate sets, then integrate:
   - HVG across batches/studies (target ~300 genes; account for Study_name_cancer or similar batch key) to capture broad variability helpful for separability
   - Differential expression (DE):
     • Across major immune/stromal/malignant compartments and key immune subsets (T vs NK vs B vs Plasma; Mono/Macro vs DC vs Neutrophil; Treg vs other T)
     • Malignant vs non-malignant
     • Aim ~300 genes total from DE (balanced across contrasts; select top markers per group with specificity)
   - scGeneFit to identify discriminative markers for Leiden clusters and immune compartments (target ~250 genes)
   - SpaPROS to enrich pathway-aware genes with emphasis on cytokines/chemokines/exhaustion receptors-ligands and canonical signaling modules (target ~200 genes)
   - Random Forest feature importance for classification tasks:
     • Major immune classes and malignant vs non-malignant
     • Activation/exhaustion states within T/NK; myeloid polarization
     Target ~300 genes from the union of top-ranked features across tasks
3) Integrate and deduplicate all candidate genes into a unified list (~1600–1800 unique). Annotate each gene with:
   - Category: {Immune markers, Cytokines/Chemokines & receptors, Exhaustion/Activation/Cytotoxicity, Cell cycle/Proliferation, DNA damage/Stress, Hypoxia/Angiogenesis/EMT, Oncogenes/TSGs, Pathway readouts (MAPK/PI3K/JAK-STAT/TGF-β/WNT), Myeloid modules, B/Plasma modules, NK modules, Treg/T cell states, Malignancy markers, Housekeeping/Normalization (minimal)}
   - Subcategory (e.g., “T exhaustion”, “DC markers”, “MAPK readout”, “EMT”, etc.)
   - Evidence scores from each method (HVG rank, DE LFC/p-values, scGeneFit score, SpaPROS score, RF importance) and a combined priority score
   - Notes: receptor/ligand, membrane/secreted, known canonical marker, spatial suitability flag if highly specific and adequately expressed
4) Curate to final 1000 genes with explicit quotas to ensure coverage and interpretability while avoiding redundancy. Requirements:
   - Resolve all major immune types and regulatory populations (include canonical CDs/KLRs, immunoglobulin/plasma markers, DC subsets, neutrophil granule genes, monocyte vs macrophage markers, MDSC-associated)
   - Include an exhaustion/activation module (PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, TOX, PRDM1, BATF, etc.)
   - Include cytokines/chemokines (CCL/CCR, CXCL/CXCR, IL/ILR, IFN/TNF families) and cytotoxicity (GZMB/H/K, PRF1, NKG7)
   - Include oncogenes/TSGs and pathway readouts for MAPK, PI3K/AKT/mTOR, JAK-STAT, TGF-β, WNT, plus EMT, hypoxia (HIF1A targets), angiogenesis (VEGFA/VEGFRs), proliferation (MKI67, PCNA), DNA damage/stress (TP53 targets, GADD45, ATR/ATM axis)
   - Malignancy identification and heterogeneity: EPCAM/KRTs/CEACAMs vs immune/stromal, plus subclone/signaling markers as feasible
   - Maintain human HGNC symbols, remove duplicates, avoid low-utility highly ubiquitous genes unless needed for normalization
   - Favor genes with reasonable expression levels for spatial probe design
5) Produce outputs in your workdir:
   - Intermediate candidate lists per method (ranked TSV/CSV)
   - Integrated candidate list with annotations and scoring
   - Final curated 1000-gene panel (CSV/TSV) with columns: symbol, category, subcategory, brief function, receptor/ligand flag, membrane/secreted, evidence scores, combined score, notes
   - Diagnostic figures: UMAPs/heatmaps showing separability with panel vs all genes (e.g., classification accuracy using only panel), marker dotplots/heatmaps by cell type, pathway signature scores across compartments
   - A short README summarizing selection rationale and how to use the panel

Practical notes
- Keep memory/disk usage reasonable; delete temporary large matrices as needed. Reuse preprocessed AnnData.
- Use the study/batch covariates you already identified to avoid batch-driven feature selection.
- Ensure reproducibility: fix random seeds and save any trained models for RF-based selection.

Deliverables to produce for downstream agents
- Paths to: final panel file, integrated candidates file, key figures, and a brief summary markdown of methods and findings in your workdir.
