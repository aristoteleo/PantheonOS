Project: Human immune-oncology gene panel selection
Step 1 only: Dataset inspection, QC, and (if needed) downsampling/gene subsetting

Inputs
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Environment info: see /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md (GPU available; dataset ~2.85 GiB)

What to do (high-level)
- Load and inspect the dataset structure (cells, genes, key obs/var annotations)
- Perform QC and filtering with your standard criteria; record thresholds used
- If dataset >500k cells, perform downsampling; if >30000 genes, perform gene subsetting
- Save an inspection/QC summary markdown and figures into your workdir
- If downsampling/subsetting performed, save the new adata path and report it clearly; this becomes the input for selection algorithms later

Deliverables
- inspection_qc_summary.md
- figures (QC plots)
- paths for any downsampled/subsetted adata files

Proceed autonomously; no further user input required at this step.