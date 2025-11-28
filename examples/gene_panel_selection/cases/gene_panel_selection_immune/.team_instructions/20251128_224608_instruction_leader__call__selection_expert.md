Project: Human immune oncology gene profiling panel
Phase 1a: Quick dataset probe only (lightweight)

Context and goals summarized: build a 1000-gene immune-oncology panel enabling TME cell-type resolution, immune state (activation/exhaustion), cytokine/chemokine signaling, and cancer pathway profiling; final benchmarking included. CPU-only environment; ample RAM.

Input dataset
- AnnData path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad

Workdirs
- Project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Task (do not run heavy computations yet):
- Load the AnnData and output a quick structural summary to quick_probe.md in your workdir:
  - n_obs, n_vars
  - obs keys and a small value count for any likely cell-type/cluster annotations (e.g., cell_type, celltype, leiden, louvain, sample, patient, tissue)
  - var keys; count of mitochondrial/ribosomal genes if easily derivable
  - Sparsity of X; layers keys if any
  - Any preexisting embeddings keys in obsm
- Do NOT compute neighbors/UMAP or run selection yet. This is just to inform downsampling strategy.
- Save nothing else except the markdown summary and a tiny JSON with basic metadata (shape, candidate obs columns) for subsequent phases.
