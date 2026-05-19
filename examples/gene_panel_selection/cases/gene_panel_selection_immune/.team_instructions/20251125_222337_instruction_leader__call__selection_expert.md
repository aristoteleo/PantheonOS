Project: 1000-gene immune-oncology panel for human TME
Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Primary input (IMPORTANT): use ONLY the downsampled adata from previous step
- Path file: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/downsampled_adata_path.txt

Environment context
- See environment summary at /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md

Task: Run gene panel selection algorithms and curate a 1000-gene panel

High-level objectives
- Build a panel enabling:
  1) Resolution of major immune populations and key regulatory subsets (T, NK, B, Plasma, Mono/Macro, DCs, Neutrophils, Tregs, MDSCs)
  2) Cancer signaling coverage: oncogenes, tumor suppressors, MAPK/PI3K/JAK-STAT/TGF-β/WNT, EMT, hypoxia, angiogenesis, DNA damage, cell cycle
  3) Cytokine/chemokine states with receptors and exhaustion markers (PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, etc.)
  4) Malignant vs non-malignant discrimination, with signal to capture subclones
  5) Cell-state axes: activation, cytotoxicity, exhaustion, proliferation, senescence, stress

Method suite
- Please run: HVG, Differential Expression (across major cell types and malignant vs non-malignant), scGeneFit, SpaPROS, and Random Forest feature importance.
- Integrate results into a unified candidate pool (target ~1600-1800 unique genes) with category tags. Then curate to exactly 1000 genes, ensuring balanced coverage for the objectives above and compatibility with spatial transcriptomics.

Guidance
- Use stratified DE contrasts based on the coarse annotations from step 1c (immune compartments and malignant vs non-malignant). If author-provided labels exist, harmonize and leverage them. For RF tasks, include:
  - Multi-class classifier for immune lineage resolution
  - Binary classifier for malignant vs non-malignant
  - State classifier(s) for activation/exhaustion (e.g., markers: IFNG, GZMB, CXCL13, TOX, PRDM1, MKI67, etc.)
- For pathway-aware selection (SpaPROS), seed with hallmark and Reactome pathways relevant to IO; ensure capture of cytokines/chemokines (CCL, CXCL, IL, IFN families) and receptors (CCR, CXCR, ILR, IFNAR/GR/γ)
- Deduplicate aliases; prefer current HGNC symbols; flag genes with low detectability in the dataset for possible replacement with proxy markers.

Deliverables
- Intermediate result files for each method (ranked lists with scores)
- Integrated candidate pool with tags (CSV/TSV)
- Final curated 1000-gene panel (CSV) with per-gene annotations:
  - Category (immune lineage marker, cytokine/chemokine, receptor, exhaustion/activation/cytotoxicity, cell cycle, DNA damage, stress, hypoxia, angiogenesis, EMT, oncogene/TSG, pathway readout)
  - Intended utility (e.g., Treg vs Th1, M1 vs M2, DC subset discrimination, malignant vs stromal)
- Summary README describing selection logic and known caveats
- Figures supporting separability and pathway coverage checks

Please keep outputs in your sub-workdir under method-specific subfolders and produce a concise summary file listing key paths for downstream agents.