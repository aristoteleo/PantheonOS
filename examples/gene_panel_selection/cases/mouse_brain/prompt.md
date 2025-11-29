# Task: A mouse brain receptor profiling panel
Plex: 500 genes, with annotation, with genes grouped in major categories
Purpose: the gene panel should be able to identify and map all cell types, including excitatory and inhibitory neurons, glial cells, astrocytes, oligodendrocytes and vascular cells across the brain. Separately, the gene panel should enable users to profile the most relevant druggable receptor targets and their expression in the brain. As the primary goal of this gene panel is brain receptor profiling, it is of interest to include as many relevant druggable receptors as possible, while keeping a relatively small, but core set of marker genes for cell typing. Ideally, the gene panel should include multiple families of relevant druggable receptors in the brain.

# Dataset
We will use the Allen Institute full mouse brain dataset.

First, check that the cache interface is installed:  
pip install "abc_atlas_access[notebooks] @ git+https://github.com/alleninstitute/abc_atlas_access.git"

Look online how to interact with the ABC project and understand their expression matrices and brain regions (but **do not download the full matrices, because they are very large**).  
Use the cache functions such as:
- abc_cache.list_directories()
- abc_cache.list_expression_matrix_files("WMB-10Xv3")
- abc_cache.list_metadata_files("WMB-taxonomy")

Learn how to navigate through the data using the official notebook:  
https://alleninstitute.github.io/abc_atlas_access/notebooks/consensus_mouse_clustering_analysis_and_annotation.html

Subsample the 'WMB-10Xv3' whole mouse brain data **smartly** across all major brain regions and cell types, using the metadata only (not the full matrices).  

Using these subsampled cells together with the Allen annotations, build **three Scanpy adata files**, each with <50,000 cells and proper cell-type labels.  
Save these adata files, and use their paths to perform gene panel selection.

Matrices description:  
https://alleninstitute.github.io/abc_atlas_access/descriptions/WMB-10Xv3.html

Annotations (cell-type taxonomy):  
https://alleninstitute.github.io/abc_atlas_access/descriptions/WMB-taxonomy.html