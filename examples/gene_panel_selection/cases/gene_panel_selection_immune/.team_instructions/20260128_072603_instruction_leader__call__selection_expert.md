Project: Human immune oncology gene profiling panel (scRNA-seq)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Dataset
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Source: bioRxiv Preprint (2024) — DOI: 10.1101/2024.01.17.576110

Environment context
- See environment.md in project root for hardware/software details.
- Use downsampling if >500k cells and gene subsetting if >30k genes to respect resource constraints. If you downsample, save the downsampled AnnData path and treat it as the sole input for pre-established selection algorithms. Retain the full dataset for context searches.

Goal and panel requirements
- Target panel size: 1000 genes
- Purpose: comprehensive immune-oncology profiling panel for human tumor microenvironment.
- The panel must enable: 1) cell type cataloging across TME (immune, stromal, endothelial, malignant); 2) immune profiling including resolution of major and fine-grained immune cell types; 3) cell-state characterization via cytokine and cancer signaling pathways; 4) T cell exhaustion and activation states; 5) differentiation of cancer cell stages via oncogenes/signaling modules.
- Organize final panel into major categories with annotation for each gene (functional category and brief rationale). Example categories (adjust/extend as needed):
  - Lineage/cell-identity markers (pan-leukocyte, myeloid, lymphoid, stromal, endothelial, epithelial)
  - Fine-grained immune markers (T cell subsets incl. CD4, CD8, Treg, Th1/Th2/Th17, Tfh; NK subsets; B/Plasma; myeloid: monocytes, macrophage M1/M2, DC subsets, neutrophils, mast)
  - Antigen presentation and checkpoint molecules (MHC I/II, HLA, CD274/PD-L1, PDCD1/PD-1, CTLA4, LAG3, HAVCR2/TIM3, TIGIT, SIGLEC family, CD80/CD86, PVR/NECTIN, etc.)
  - Cytokines, chemokines, and receptors (IL, IFN, TNF families; CCL/CXCL; receptors ILRs, IFNRs, TNFRSFs, CCR/CXCR)
  - Signaling/cancer pathways (MAPK/ERK, PI3K/AKT/mTOR, WNT, TGF-β, JAK/STAT, NF-κB, Hippo, Notch, Hedgehog, DNA damage/repair, cell cycle)
  - Oncogenes, tumor suppressors, EMT/metastasis markers, stemness
  - Proliferation and cell cycle genes
  - Metabolism and stress response (glycolysis/OXPHOS, hypoxia, autophagy, ER stress)
  - Spatial/TME interaction modules (adhesion, ECM, angiogenesis, CAF markers)
- Ensure compatibility with human scRNA-seq naming (HGNC symbols).

What to do now (Phase 1)
1) Inspect the dataset (basic QC, structure inspection, cell/gene counts, annotations available). If too large, perform downsampling (>500k cells) and gene subsetting (>30k genes). Save any derived dataset paths in your workdir and report them in a summary markdown.
2) Plan selection runs. Unless the user requested a single method, plan to run CelltypistGPS, HVG, SpaPROS, scGeneFit, Differential Expression, and Random Forest. You will later benchmark and integrate these results to select an optimal subpanel for cell-type separability, then complete to 1000 genes with biological context for the specified immune-oncology goals.
3) Write a short Phase 1 summary including: dataset overview, any downsampling/subsetting decisions, and a high-level plan of algorithms to run and computational considerations (reference environment.md). Save this as selection_expert/phase1_summary.md.

Please proceed autonomously within these instructions and save all outputs in your workdir.