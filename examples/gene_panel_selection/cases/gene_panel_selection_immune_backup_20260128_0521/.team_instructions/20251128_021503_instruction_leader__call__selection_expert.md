Project: Human immune oncology panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Background:
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Dataset source: bioRxiv Preprint (2024) — DOI: 10.1101/2024.01.17.576110
- Hardware/software context: see environment summary at /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md

Goal (Phase 1 — dataset understanding):
Please perform dataset inspection and QC with the following high-level guidance:
- Load the dataset, check basic stats (cells, genes), modality (scRNA vs spatial), batches, key metadata fields.
- Execute standard QC and structure inspection; initial embedding/clustering to gauge cell-type structure.
- If dataset has >50,000 cells, downsample to a representative subset (retain diversity across batches/tissue if present). If it has >3,000 genes, create a reduced gene set suitable for fast algorithm runs.
- Save any downsampled/filtered AnnData to disk in your workdir and report their paths. The downsampled adata will be the only input for the pre-established selection algorithms later; keep the original for biological context in panel curation.
- Produce a brief markdown summary and figures of QC and structure; save them in your workdir.

Return:
- Paths to: any downsampled adata, QC summary markdown, and key figures (UMAP, QC plots).