Complete the human immune oncology panel to 1000 genes with annotation and grouped categories, then benchmark it.

Workdir
- Project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Agent:   /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Dataset and algorithms
- Use the dataset and method outputs already produced in your workdir.
- Use the recommended separability subpanel (tables/recommended_subpanel_500.csv) as the core; you may refine as needed based on your internal logic.

Panel goals (high-level)
- Final panel size: 1000 genes (plex 1000).
- The panel must enable: cataloging all major cell types in the tumor microenvironment; resolving immune cell subtypes; profiling exhaustion/activation states; characterizing cytokine/chemokine programs; and profiling key cancer signaling pathways and oncogenes to distinguish cancer cell states/stages.

Biological coverage expectations (categories; you decide exact composition)
- Core lineage markers: T/NK, B/plasma, myeloid (mono/macro/DC), granulocytes, epithelial/tumor, endothelial, fibroblast/pericyte.
- Antigen processing/presentation and HLA machinery (class I/II, B2M, TAP1/2).
- T-cell states and checkpoints (activation/exhaustion/naive/memory/effector): PDCD1, CTLA4, LAG3, TIGIT, HAVCR2, TOX/EOMES/TCF7, cytotoxicity (PRF1, GZMB, NKG7), co-stimulation (CD27/CD28/ICOS/CD40LG, etc.).
- Cytokines/chemokines and receptors (IL/IFN/TNF families; CCR/CXCR/IFNAR/ILRs) with ligand-receptor coverage for interaction inference.
- Cancer signaling pathways: RTK (EGFR/ERBB2/PDGF/KIT), RAS/RAF/MAPK, PI3K/AKT/mTOR, JAK/STAT, TGF-β, WNT, NOTCH, Hedgehog, NF-κB, Hippo; DNA damage/repair and cell cycle; EMT/hypoxia/metabolism.
- Additional TME programs: antigen presentation, interferon-stimulated genes, adhesion/trafficking, ECM/CAF markers, hypoxia/metabolic rewiring.

Identifier policy
- Standardize outputs to HGNC gene symbols as primary identifiers; include Ensembl IDs as a secondary column if available from var.

Deliverables
- Final 1000-gene panel with annotations and grouping:
  - Save as CSV and XLSX: tables/final_panel_1000_annotated.csv and .xlsx (columns: gene_symbol, ensembl_id, category, subcategory, methods_present, consensus/relevance_score, brief_biological_rationale; add ligand/receptor flag if applicable).
  - Save a machine-friendly simple list: tables/final_panel_1000_symbols.txt
  - Save a README: tables/final_panel_README.md describing categories and curation logic.
  - Save category counts figure: figures/final_panel_category_counts.png
- Benchmark and comparison to method panels:
  - Complete ARI vs panel size curves (100→1000) across methods and for the final panel; save figures/ari_vs_size_all_methods.png and per-method CSVs in tables/.
  - Provide benchmarking with dataset splitting strategy, ARI/NMI/Silhouette boxplots, UMAP comparisons, and quantitative UMAP similarity; save figures and tables; write benchmark_summary.md.
  - Include an updated UpSet that includes the final panel vs each method panel: figures/upset_with_final_panel.png

Notes
- Keep computation efficient (you may use the downsample you created for benchmarking curves if needed, but document exactly what was used). The full, original AnnData remains the biological reference for context lookups.
- Please avoid over-representation of ribosomal/mitochondrial genes; ensure practical panel diversity and coverage of the above programs.
- Ensure the final panel is balanced for the intended immune-oncology use cases.

When complete
- Summarize key highlights and caveats in methods_overview.md and update it with a “Final panel” section including paths to all final assets.