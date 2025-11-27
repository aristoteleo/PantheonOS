# Project: Immune-oncology 1000-gene panel (Human TME)
Root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Selection expert workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Biologist workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist
Reporter workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter
Dataset (initial): /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad (bioRxiv: 10.1101/2024.01.17.576110)

## Goals
- Curate a 1000-gene panel enabling: immune cell resolution, tumor/normal separation, cancer pathway readouts, cytokine/chemokine states, cell states (activation, exhaustion, proliferation, senescence, stress) and spatial deployment (Vizgen-style).

## Checklist

- [x] Create project workdirs and initial notes
- [x] Environment audit and setup (system_manager)
- [x] Dataset QC and understanding, apply downsampling rules if needed (<=50k cells, <=3k genes) and save downsampled adata path (selection_expert)
- [ ] Gene panel selection methods (selection_expert)
  - [ ] HVG-based selection
  - [ ] Differential Expression across major cell types/states and malignant vs non-malignant
  - [ ] SpaPROS panel for spatial TME structure
  - [ ] scGeneFit panel for classification separability
  - [ ] Random Forest-based feature importance (cell types + malignant vs non-malignant)
  - [ ] Aggregate, deduplicate, score and rank genes; generate Venn/UpSet diagrams
- [ ] Biological curation to finalize a 1000-gene panel grouped in categories (selection_expert)
  - [ ] Ensure coverage: immune lineages, cytokine/chemokine axes, exhaustion/activation, cell cycle, DNA damage, hypoxia/angiogenesis/EMT, oncogenes/tumor suppressors, major signaling (MAPK, PI3K, JAK-STAT, TGF-β, WNT)
  - [ ] Ensure human HGNC symbols and remove aliases/duplicates; spatial assay deployability
  - [ ] Produce per-gene annotations and relevance scores
- [ ] Biological interpretation of final panel and key findings (biologist)
- [ ] Final report as PDF with methods, figures (including Venn), tables, and curated panel (reporter)

## Deliverables
- Downsampled adata path and QC plots
- Per-method gene lists and metrics
- Venn/UpSet figure and overlap stats
- Final curated 1000-gene panel CSV with annotations and categories
- Summary figures: UMAP separability using panel, confusion matrices, pathway coverage
- Biologist interpretation notes
- Report: report.pdf
