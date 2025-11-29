Caller: selection_expert. Please open the Allen Brain Cell Atlas access documentation for python (abc_atlas_access) and specifically the tutorial: https://alleninstitute.github.io/abc_atlas_access/notebooks/consensus_mouse_clustering_analysis_and_annotation.html. Extract concrete Python usage snippets for:
- Initializing the cache object
- Listing datasets and directories (list_directories)
- Listing expression matrix files for WMB-10Xv3
- Listing metadata files for WMB-taxonomy
- Loading taxonomy metadata (cell annotations: cell type/class and region annotations)
- Loading a subset of raw counts for a selected set of cells into an AnnData (without downloading full matrices)
- Any helper for selecting cells by region/class
Return succinct code examples and note field names present in the taxonomy metadata (e.g., cell type labels, region labels, relevant keys). Save the snippets in a markdown file in our agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/abc_usage_snippets.md