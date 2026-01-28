Project: Human immune-oncology gene panel selection
Goal: Build a 1000-gene panel (with annotation and grouping into major categories) to:
- catalog all cell types in the tumor microenvironment (TME)
- resolve immune cell types and states (activation, exhaustion, cytotoxicity, regulatory)
- characterize cytokine/cytokine-receptor and chemokine axes
- profile key cancer signaling pathways and oncogenes to stage cancer cells
- include checkpoint molecules and antigen presentation machinery
- enable downstream benchmarking (ARI/NMI/SI) and UMAP comparisons

Dataset
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Source: bioRxiv 2024 preprint DOI 10.1101/2024.01.17.576110

Workdirs
- Project root workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Environment context
- See environment.md in project root for hardware/software details and dataset size.
- You can use GPU if beneficial but not mandatory. Dataset is ~2.85 GiB; downsampling is allowed per your rules if >500k cells, and gene subsetting if >30k genes. If you downsample, save the new adata path and use it for all pre-established selection algorithms.

What to do (high-level)
1) Inspect dataset, run QC, determine cells/genes, and downsample if needed. Save inspection summary and figures into your workdir.
2) Run your pre-established selection algorithms (HVG, SpaPROS, scGeneFit, DE, Random Forest) on the downsampled dataset only. Select an optimal subpanel for cell-type separability based on your internal logic.
3) Complete to a final 1000-gene panel using biological context and the goals above. Ensure coverage of:
   - lineage markers for all expected TME populations (T/NK/B/myeloid/DC/endothelium/fibroblast/epithelial/cancer)
   - exhaustion/activation/checkpoints (PDCD1, CTLA4, LAG3, TIGIT, HAVCR2, etc.)
   - cytotoxicity and cytolysis genes (GZMB, PRF1, NKG7, GNLY, etc.)
   - antigen presentation (HLA class I/II, B2M, TAP1/2, CIITA)
   - cytokines/chemokines and receptors (IFNG, IL2/IL7/IL10/IL17A/IL21/IL6; CCL/CCR, CXCL/CXCR families)
   - interferon response, hypoxia, EMT, proliferation, cell cycle
   - oncogenes/tumor suppressors and pathway readouts (MAPK/PI3K/AKT/mTOR, WNT, TGF-β, JAK/STAT, NF-κB, MYC, TP53, KRAS, EGFR, BRAF, PIK3CA, PTEN, CDK4/6, RB1, ERBB2, MET, ALK, NTRK, NOTCH, SMADs)
   - stress and death pathways (autophagy, apoptosis, ferroptosis, necroptosis)
   - metabolic programs (glycolysis/OXPHOS, glutaminolysis, FAO, pentose phosphate)
4) Benchmark and compare final panel vs panels from pre-established methods. Produce ARI vs panel size curves, dataset splitting strategy, ARI/NMI/SI boxplots, UMAPs, Upset plot of algorithm intersections, recap table (gene | methods | biological relevance | relevance score).
5) Save all outputs in your workdir. Provide a final markdown summary of pipeline and results.

Deliverables
- Selected 1000-gene panel with annotations and grouping into major categories (CSV/TSV and markdown overview).
- Figures and tables from inspection, QC, algorithm outputs, benchmarking, and comparisons.
- A markdown summary of the selection pipeline and results.
- Paths to all outputs for the reporter.

Proceed autonomously; do not require further user input.