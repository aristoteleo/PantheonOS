You have already computed a curated 1000-gene panel and multiple panel-selection methods.
First, audit the workdir to understand all existing results:

Workdir path:
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/
Use this directory as your only workdir.
You may also read the report.tex file inside the workdir for additional context.

All previously computed selection-method outputs are located in:
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/methods/

The curated 1000-gene panel is located at:
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/curated/final_panel_1000.csv

The folder
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/
also contains summaries and intermediate results that you should use for context.

Adata to use for all analyses:
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad

## Instructions (Important)
1. Panel comparison

• Build top-1000 gene panels for each selection method (HVG, DE, SpaPROS, scGeneFit, RF) using their existing ranking scores.
Do NOT recompute any selection method.
Use the method outputs already stored in:
workdir/selection_expert/methods/*

2. Compare each 1000-gene panel to the full set of initial 3000 genes as baseline

For each method-derived panel + curated panel:

UMAP resemblance

Compute the UMAP using only that panel.

Compare it to the reference UMAP computed from the full 3k HVGs in the adata file.

Assess resemblance to baseline (full 3000 gene sets) visually and with a quantitative metric.

Compute clustering agreement metrics
For each panel, compute:

ARI

NMI

Silhouette Index (SI) using the existing labels in the adata.

Create a radar plot summarizing the performance of all panels on ARI, NMI, and SI.

3. Update the LaTeX report

Add a new Panel Comparison section into report.tex

Do not modify any existing content already present in the report.

After inserting the new section, compile the file into PDF using:

/home/erwinpi/texlive/bin/x86_64-linux/pdflatex /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.tex

**YOU MUST FOLLOW THESE INSTRUCTIONS EXACTLY**