

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from ...utils.log import logger
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from ...utils.toolset import ToolSet

class SpatialBase(ToolSet):
    """Base class for spatial analysis toolsets"""
    
    def __init__(
        self,
        name: str = "spatial_base",
        workspace_path: str = None,
        launch_directory: str = None,
        worker_params: dict = None,
        **kwargs
    ):
        super().__init__(name, worker_params, **kwargs)
        # workspace_path is the data analysis workspace
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        # launch_directory is where pantheon was originally launched - used for software installation
        self.launch_directory = Path(launch_directory) if launch_directory else Path.cwd()
        self.pipeline_config = self._initialize_config()

    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize scRNA-seq toolset configuration"""
        return {
            "file_extensions": {
                "input": [".fastq", ".fq", ".fastq.gz", ".fq.gz", ".fastq.bz2"],
                "output": [".h5", ".h5ad", ".mtx", ".tsv", ".csv"],
                "reference": [".fa", ".fasta", ".gtf", ".gff3"],
                "expression": [".h5", ".mtx", ".csv", ".tsv"]
            },
            "tools": {
                "required": ["python3"],
                "optional": ["cellranger", "R", "jupyter"],
                "python_packages": [
                    "scanpy", "anndata", "pandas", "numpy", "scipy",
                    "matplotlib", "seaborn", "omicverse", "pertpy"
                ],
                "r_packages": ["Seurat", "SingleCellExperiment", "scater", "scran"],
                "alternatives": {
                    "preprocessing": ["scanpy", "seurat", "scater"],
                    "annotation": ["scanpy", "celltypist", "singler"],
                    "trajectory": ["scanpy", "scvelo", "cellrank"]
                }
            },
            "default_params": {
                "threads": os.cpu_count() or 4,
                "memory": "32G",
                "min_genes": 200,
                "min_cells": 3,
                "max_genes": 5000,
                "mito_threshold": 20.0,
                "ribo_threshold": 50.0,
                "n_top_genes": 2000,
                "n_pcs": 50,
                "resolution": 0.5,
                "expected_cells": 10000
            },
            "omicverse_params": {
                "qc": {
                    "min_genes": 200,
                    "min_cells": 3,
                    "max_genes": 5000,
                    "mt_gene_regex": "^MT-",
                    "rp_gene_regex": "^RP[SL]"
                },
                "preprocess": {
                    "target_sum": 1e4,
                    "n_top_genes": 2000,
                    "log1p": True,
                    "copy": False
                },
                "pca": {
                    "n_comps": 50,
                    "svd_solver": "arpack",
                    "random_state": 0
                },
                "batch_correction": {
                    "methods": ["scVI", "harmony"],
                    "batch_key": "batch",
                    "default_method": "scVI"
                },
                "clustering": {
                    "resolution": 0.5,
                    "algorithm": "leiden",
                    "random_state": 0
                }
            },
            "pertpy_params": {
                "de_methods": ["wilcoxon", "t-test", "logistic_regression"],
                "default_de_method": "wilcoxon",
                "pseudobulk": {
                    "groupby": ["cell_type", "condition"],
                    "min_cells": 10
                }
            },
            "cell_ontology": {
                "broad_cell_types": [
                    "T cell", "B cell", "NK cell", "Monocyte", "Macrophage",
                    "Dendritic cell", "Neutrophil", "Eosinophil", "Basophil",
                    "Epithelial cell", "Endothelial cell", "Fibroblast",
                    "Smooth muscle cell", "Pericyte", "Stem cell",
                    "Neural cell", "Glial cell", "Hepatocyte", "Cardiomyocyte",
                    "Erythrocyte"
                ]
            },
            "project_structure": {
                "dirs": [
                    "raw_data",      # Input expression files
                    "processed",     # QC and preprocessed data
                    "analysis",      # Downstream analysis
                    "plots",         # Visualizations
                    "reports",       # HTML/PDF reports
                    "annotations",   # Cell type annotations
                    "markers",       # Marker genes
                    "logs"          # Processing logs
                ]
            }
        }
    
    def _get_cache_dir(self) -> Path:
        """Get cache directory for references and downloads"""
        cache_dir = self.launch_directory / ".pantheon" / "cache" / "scrna"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
        