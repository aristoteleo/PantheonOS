Caller: selection_expert

Task: Please ensure the analysis environment has the following Python packages installed and usable by the Jupyter kernel used in our tools.

Packages (with suggested versions):
- scanpy>=1.10
- anndata>=0.10
- numpy>=1.23
- scipy>=1.10
- pandas>=2.0
- matplotlib>=3.7
- seaborn>=0.12
- scikit-learn>=1.2
- umap-learn>=0.5.5
- pynndescent>=0.5
- numba>=0.57
- statsmodels>=0.14
- python-igraph>=0.10
- louvain>=0.8
- leidenalg>=0.10
- harmonypy (optional, for batch correction)

Please:
1) Install or update the packages in the active environment used by the Jupyter kernel.
2) Verify that `import scanpy as sc; import anndata as ad` work in a quick test with the same kernel that `notebook` tool uses.
3) Report back the environment status and versions.