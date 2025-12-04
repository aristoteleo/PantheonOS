# Task: A mouse brain receptor profiling panel
Plex: 500 genes, with annotation, with genes grouped in major categories
Purpose: the gene panel should be able to identify and map all cell types, including excitatory and inhibitory neurons, glial cells, astrocytes, oligodendrocytes and vascular cells across the brain. Separately, the gene panel should enable users to profile the most relevant druggable receptor targets and their expression in the brain. As the primary goal of this gene panel is brain receptor profiling, it is of interest to include as many relevant druggable receptors as possible, while keeping a relatively small, but core set of marker genes for cell typing. Ideally, the gene panel should include multiple families of relevant druggable receptors in the brain.

# BEFORE DOING THE TASK

- **RETRIEVE  Data**
We will use the Allen Institute full mouse brain dataset.

First, check that the cache interface is installed:  
pip install "abc_atlas_access[notebooks] @ git+https://github.com/alleninstitute/abc_atlas_access.git"

Look online how to interact with the ABC project and understand their expression matrices and brain regions (but **do not download the full matrices, because they are very large**).  
Use the cache functions such as:
- abc_cache.list_directories()
- abc_cache.list_expression_matrix_files("WMB-10Xv3")
- abc_cache.list_metadata_files("WMB-taxonomy")

Learn how to navigate through the data and select cells to built the adata:
https://alleninstitute.github.io/abc_atlas_access/notebooks/getting_started.html
https://alleninstitute.github.io/abc_atlas_access/notebooks/general_accessing_10x_snRNASeq_tutorial.html 
https://alleninstitute.github.io/abc_atlas_access/notebooks/abc_atlas_selection_example.html

Matrices description:  
https://alleninstitute.github.io/abc_atlas_access/descriptions/WMB-10Xv3.html

Annotations (cell-type taxonomy):  
https://alleninstitute.github.io/abc_atlas_access/descriptions/WMB-taxonomy.html

Built some adatas with the 'WMB-10Xv3' whole mouse brain data **smartly** across all major brain regions and cell types, using the metadata only (not the full matrices). 

You should build **three Scanpy adata files**, each with <50,000 cells and proper cell-type labels with sufficient genes from the expression matrices with code.

Save these adata files, and use their paths to perform gene panel selection.

**THIS IS ONLY TO HAVE DATASETS** that you will use for gene panel selection. 

# DO THE TASK
Once you have the paths proceed to **Gene panel selection** as usual using the paths of the adatas you built
