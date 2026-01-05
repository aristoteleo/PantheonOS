Project: Human immune oncology gene profiling panel (1000 genes)

Paths:
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Dataset (h5ad): /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- environment.md: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md

Computational environment context:
- Large-memory, multi-GPU server; 1.5 TiB RAM; 4x A100 40GB; CUDA 12.8 available via PyTorch; JAX CPU-only
- Recommendation: operate on full 356k cells if feasible; maintain sparse/backed operations; subset genes for algorithms; downsample only if runtime requires it

Biological context & goals:
- Human tumor microenvironment scRNA-seq dataset (bioRxiv 2024.01.17.576110). We need a 1000-gene panel to:
  1) Resolve all major immune cell types and fine subtypes (T cell subsets incl. naive/effector/memory/Treg; NK subsets; B and plasma cells; myeloid subsets incl. cDC1/cDC2/pDC/monocytes/macrophages/MDSCs; mast cells; granulocytes if present; stromal (CAF), endothelial, pericytes; and malignant epithelial compartments).
  2) Characterize cell states via cytokine/chemokine axes and exhaustion/activation markers; include checkpoint genes (PDCD1, CTLA4, LAG3, HAVCR2, TIGIT), co-stimulatory (CD28, ICOS, TNFRSF family), cytotoxic effectors (GZMB, PRF1), transcriptional regulators (TOX, TCF7, TBX21, EOMES), proliferation (MKI67), apoptosis, stress, interferon responses.
  3) Profile key cancer signaling pathways and oncogenic states: RTK–RAS–MAPK; PI3K–AKT–mTOR; JAK–STAT; NF-kB; WNT/beta-catenin; TGF-beta/SMAD; Notch; Hippo/YAP-TAZ; Hedgehog; DNA damage/repair (TP53 pathway, HR, NHEJ, MMR); cell cycle; EMT, hypoxia, metabolism (glycolysis, OXPHOS, fatty acid metabolism); antigen presentation (MHC-I/II); HLA diversity; antigen processing; proteasome; IFN signaling; chemokine receptors/ligands; adhesion and migration (integrins, selectins, ICAMs, VCAMs, CXCRs).
  4) Enable assessment of tumor cell states (stemness, proliferation, stress, EMT) and distinguish cancer cell stages via oncogenes/tumor suppressors (EGFR, ERBB2/3, KRAS/NRAS/HRAS, BRAF, MET, FGFRs, ALK, ROS1, RET, NTRK, MYC, CCND1, MDM2, PTEN, RB1, CDKN2A, APC, CTNNB1, SMAD4, VHL, PBRM1).

Tasks (Step 1: dataset understanding and QC):
- Inspect the dataset structure, labels/metadata, and perform QC.
- If dataset > 50k cells, you may downsample strategically for algorithm speed, but environment allows full dataset; your call. If downsampling, save the new adata path and use it as the only input for pre-established selection algorithms.
- If >3000 genes, perform gene subsetting for algorithmic steps; ensure this is saved/documented.
- Save a dataset summary and decisions in your workdir, including any derived adata paths.

Please proceed with this Step 1 and report outputs in your workdir. Do not run selection methods yet; we will trigger them after reviewing your inspection summary.