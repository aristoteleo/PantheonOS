"""Single-cell RNA-seq downstream analysis and visualization with omicverse integration"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .base import ScRNASeqBase
from ...utils.toolset import tool
from ...utils.log import logger



class ScRNASeqAnalysisToolSet(ScRNASeqBase):
    """Single-cell RNA-seq downstream analysis toolset with omicverse integration"""
    
    def __init__(
        self,
        name: str = "scrna_analysis",
        workspace_path: str = None,
        launch_directory: str = None,
        worker_params: dict = None,
        **kwargs
    ):
        super().__init__(name, workspace_path, launch_directory, worker_params, **kwargs)
    
    @tool
    def load_and_inspect_data(self, data_path: str, output_dir: str = None) -> Dict[str, Any]:
        """Load and inspect scRNA-seq data with comprehensive analysis"""
        
        data_path = Path(data_path)
        logger.info(f"\n📊 [bold cyan]Loading and inspecting data: {data_path.name}[/bold cyan]")
        
        if not data_path.exists():
            return {
                "status": "failed",
                "error": f"Data file not found: {data_path}"
            }
        
        # Prepare output directory
        if output_dir is None:
            output_dir = self.workspace_path / "analysis" / "data_inspection"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        inspection_results = {
            "data_path": str(data_path),
            "data_type": self._detect_data_type(data_path),
            "basic_info": {},
            "obs_columns": [],
            "var_info": {},
            "obsm_keys": [],
            "qc_metrics_present": False,
            "cell_type_annotations_present": False,
            "batch_info": {},
            "recommendations": []
        }
        
        try:
            # Import required packages
            import scanpy as sc
            import omicverse as ov
            
            # Load data based on file type
            logger.info("Loading data file...")
            if data_path.suffix.lower() == '.h5ad':
                adata = sc.read_h5ad(data_path)
            elif data_path.suffix.lower() == '.h5':
                adata = sc.read_10x_h5(data_path, genome=None, gex_only=True)
                adata.var_names_unique()
            elif data_path.suffix.lower() == '.csv':
                adata = sc.read_csv(data_path).T
            elif data_path.suffix.lower() == '.tsv':
                adata = sc.read_csv(data_path, delimiter='\t').T
            else:
                return {
                    "status": "failed",
                    "error": f"Unsupported file format: {data_path.suffix}"
                }
            
            logger.info("Examining data structure...")
            
            # Basic information
            basic_info = {
                "n_cells": adata.n_obs,
                "n_genes": adata.n_vars,
                "data_shape": f"({adata.n_obs}, {adata.n_vars})",
                "max_value": float(adata.X.max()) if hasattr(adata.X, 'max') else float(adata.X.toarray().max()),
                "data_type": "Normalized" if float(adata.X.max()) < 50 else "Raw counts",
                "sparse_format": str(type(adata.X))
            }
            
            # Observation (cell) metadata
            obs_columns = list(adata.obs.columns)
            
            # Variable (gene) information
            var_info = {
                "gene_symbols_present": 'gene_symbols' in adata.var.columns or adata.var.index.name == 'gene_symbols',
                "gene_ids_present": 'gene_ids' in adata.var.columns or 'ensembl_id' in adata.var.columns,
                "highly_variable_marked": 'highly_variable' in adata.var.columns,
                "var_columns": list(adata.var.columns)
            }
            
            # Check for mitochondrial and ribosomal genes
            var_info["mt_genes_detected"] = any(adata.var_names.str.startswith('MT-')) or any(adata.var_names.str.startswith('mt-'))
            var_info["rp_genes_detected"] = any(adata.var_names.str.contains('^RP[SL]', case=False))
            
            # Embedding information
            obsm_keys = list(adata.obsm.keys())
            
            # QC metrics detection
            qc_metrics_present = any(col in obs_columns for col in ['n_genes', 'n_counts', 'total_counts', 'n_genes_by_counts'])
            
            # Cell type annotations
            cell_type_annotations_present = any(col in obs_columns for col in ['cell_type', 'celltype', 'leiden', 'louvain', 'clusters'])
            
            # Batch information
            batch_info = {
                "batch_column_exists": 'batch' in obs_columns or 'sample' in obs_columns,
                "batch_columns": [col for col in obs_columns if 'batch' in col.lower() or col in ['sample', 'condition', 'treatment']]
            }
            
            if batch_info["batch_column_exists"]:
                batch_col = 'batch' if 'batch' in obs_columns else 'sample'
                batch_info["n_batches"] = len(adata.obs[batch_col].unique())
                batch_info["batch_values"] = list(adata.obs[batch_col].unique())
            
            inspection_results.update({
                "basic_info": basic_info,
                "obs_columns": obs_columns,
                "var_info": var_info,
                "obsm_keys": obsm_keys,
                "qc_metrics_present": qc_metrics_present,
                "cell_type_annotations_present": cell_type_annotations_present,
                "batch_info": batch_info
            })
            
            # Generate recommendations based on inspection
            self._generate_inspection_recommendations(inspection_results)
            
            # Display inspection summary
            self._display_inspection_summary(inspection_results)
            
            # Save inspection results
            results_file = output_dir / "data_inspection_results.json"
            with open(results_file, 'w') as f:
                json.dump(inspection_results, f, indent=2, default=str)
            
            return {
                "status": "success",
                "inspection_results": inspection_results,
                "output_dir": str(output_dir),
                "results_file": str(results_file)
            }
            
        except Exception as e:
            return {
                "status": "failed",
                "error": f"Data inspection failed: {str(e)}",
                "data_path": str(data_path)
            }
    
    def _detect_data_type(self, data_path: Path) -> str:
        """Detect the type of scRNA-seq data file"""
        suffix = data_path.suffix.lower()
        if suffix == ".h5ad":
            return "AnnData"
        elif suffix == ".h5":
            return "H5_10X"
        elif suffix in [".csv", ".tsv", ".txt"]:
            return "Expression_Matrix"
        elif suffix == ".mtx":
            return "Matrix_Market"
        else:
            return "Unknown"
    
    def _generate_inspection_recommendations(self, results: Dict):
        """Generate recommendations based on data inspection"""
        
        recommendations = []
        
        # QC recommendations
        if not results["qc_metrics_present"]:
            recommendations.append("Run QC analysis with omicverse.pp.qc to calculate quality metrics")
        
        # Preprocessing recommendations
        if results["basic_info"].get("data_type") == "Raw counts":
            recommendations.append("Data appears to be raw counts - run preprocessing with omicverse.pp.preprocess")
        
        # PCA recommendations
        if "X_pca" not in results["obsm_keys"]:
            recommendations.append("No PCA found - run omicverse.pp.pca for dimensionality reduction")
        
        # Batch correction recommendations
        if results["batch_info"]["batch_effects_likely"]:
            recommendations.append("Batch effects detected - consider batch correction with omicverse.single.batch_correction")
        
        # Clustering recommendations
        if not results["cell_type_annotations_present"]:
            recommendations.append("No cell type annotations found - run clustering and annotation workflow")
        
        results["recommendations"] = recommendations
    
    def _display_inspection_summary(self, results: Dict):
        """Display data inspection summary"""
        
        from rich.table import Table
        
        # Basic info table
        basic_table = Table(title="Dataset Overview")
        basic_table.add_column("Property", style="cyan")
        basic_table.add_column("Value", style="green")
        
        for key, value in results["basic_info"].items():
            basic_table.add_row(key.replace("_", " ").title(), str(value))
        
        logger.info("", rich=basic_table)
        
        # Data structure table
        structure_table = Table(title="Data Structure")
        structure_table.add_column("Component", style="cyan")
        structure_table.add_column("Status", style="green")
        structure_table.add_column("Details", style="dim")
        
        qc_status = "✅ Present" if results["qc_metrics_present"] else "❌ Missing"
        structure_table.add_row("QC Metrics", qc_status, f"{len(results['obs_columns'])} obs columns")
        
        embeddings_status = "✅ Present" if results["obsm_keys"] else "❌ Missing" 
        embeddings_detail = ", ".join(results["obsm_keys"]) if results["obsm_keys"] else "No embeddings"
        structure_table.add_row("Embeddings", embeddings_status, embeddings_detail)
        
        annotations_status = "✅ Present" if results["cell_type_annotations_present"] else "❌ Missing"
        structure_table.add_row("Cell Types", annotations_status, "")
        
        batch_status = "✅ Present" if results["batch_info"]["batch_column_exists"] else "❌ Missing"
        batch_detail = f"{results['batch_info']['n_batches']} batches" if results["batch_info"]["batch_column_exists"] else ""
        structure_table.add_row("Batch Info", batch_status, batch_detail)
        
        logger.info("", rich=structure_table)
        
        # Recommendations
        if results["recommendations"]:
            recommendations_text = "\n".join([f"• {rec}" for rec in results["recommendations"]])
            recommendations_panel = Panel(
                recommendations_text,
                title="Next Steps Recommendations",
                border_style="blue"
            )
            logger.info("", rich=recommendations_panel)
    
    @tool
    def run_scrna_workflow(self, workflow_type: str, description: str = None):
        """Run a specific workflow"""
        if workflow_type == "qc":
            return self.run_workflow_qc()
        elif workflow_type == "preprocessing":
            return self.run_workflow_preprocessing()
        elif workflow_type == "pca":
            return self.run_workflow_pca()
        elif workflow_type == "clustering":
            return self.run_workflow_clustering()
        elif workflow_type == "batch_correction":
            return self.run_workflow_batch_correction()
        elif workflow_type == "aucell":
            return self.run_workflow_aucell()
        elif workflow_type == "umap":
            return self.run_workflow_umap()
        elif workflow_type == "marker_from_desc":
            return self.run_workflow_marker_from_desc(description)
        elif workflow_type == "marker_from_data":
            return self.run_workflow_marker_from_data(description)
        elif workflow_type == "llm_anno":
            return self.run_workflow_llm_anno(description)
        else:
            return "Invalid workflow type"
    
    def run_workflow_qc(self):
        """Run QC workflow"""
        logger.info("Running QC workflow")
        return qc_response
    
    def run_workflow_preprocessing(self):
        """Run preprocessing workflow"""
        logger.info("Running preprocessing workflow")
        return preprocessing_response
    
    def run_workflow_pca(self):
        """Run PCA workflow"""
        logger.info("Running PCA workflow")
        return pca_response
    
    def run_workflow_batch_correction(self):
        """Run batch correction workflow"""
        logger.info("Running batch correction workflow")
        return batch_correction_response
    
    def run_workflow_clustering(self):
        """Run clustering workflow"""
        logger.info("Running clustering workflow")
        return clustering_response
    
    def run_workflow_umap(self):
        """Run UMAP workflow"""
        logger.info("Running UMAP workflow")
        return umap_response
    
    def run_workflow_marker_from_desc(self,description:str):
        """Run marker from data workflow"""
        logger.info("Running marker from data workflow")

        marker_from_data_response = f"""

        Get user data context (this should be provided by user in previous step)
        user_data_context = "{description}"  # User should provide this

        IMPORTANT: Generate 20 cell types based on actual biological knowledge of the tissue/context
        This replaces hardcoded PBMC examples with context-aware generation

        In real implementation, this should be dynamically generated based on the user data context
        expected_cell_types = dict()

        expected_cell_types['T Cells'] = ['CD3D', 'CD3E', ...]
        expected_cell_types['B Cells'] = ['MS4A1', 'CD79A', ...]

        Please generate the expected_cell_types dictionary in python format as return:
        """
        return marker_from_data_response
    
    def run_workflow_marker_from_data(self,):
        """Run marker from data workflow"""
        logger.info("Running marker from data workflow")
        marker_from_data_response = f"""

        First, find cluster-specific markers:
        ```python
        # MANDATORY: Check help for marker gene functions
        help(ov.single.get_celltype_marker)
        help(sc.get.aggregate)
        ```

        Then calculate the cluster-specific markers:
        ```python
        cluster_markers = None
        try:
            cluster_markers = ov.single.get_celltype_marker(adata,
                                                           clustertype='leiden',
                                                           log2fc_min=1,
                                                           pval_cutoff=0.05,
                                                           topgenenumber=10,
                                                           rank=True,
                                                           unique=False)
            print("✅ Cluster markers extracted with get_celltype_marker")
        except:
            try:
                sc.tl.rank_genes_groups(adata, groupby='leiden', method='wilcoxon')
                cluster_markers = {{}}
                for cluster in adata.obs['leiden'].cat.categories:
                    cluster_markers[cluster] = adata.uns['rank_genes_groups']['names'][cluster][:10].tolist()
                print("✅ Cluster markers extracted with scanpy")
            except Exception as e:
                print(f"❌ Marker detection failed: {{e}}")
        print(cluster_markers)
        adata.uns['cluster_markers'] = cluster_markers
        """
        return marker_from_data_response
    
    def run_workflow_aucell(self,cell_markers:dict):
        """Run AUCell workflow"""
        logger.info("Running AUCell workflow")
        aucell_response = f"""  

        Get cell markers from user (this should be provided by user in previous step)
        expected_cell_types = {cell_markers}

        First, check the function parameters:
        ```python
        # MANDATORY: Check help first for omicverse function
        help(ov.single.geneset_aucell)
        ```

        Then calculate AUCell scores:
        ```python
        print("\\n📊 Calculating AUCell scores for cell type markers...")

        # Calculate AUCell scores for each expected cell type
        for cell_type, markers in expected_cell_types.items():
            try:
                ov.single.geneset_aucell(adata, 
                                    geneset_name=cell_type,     # Cell type name
                                    geneset=markers,             # Marker gene list
                                    AUC_threshold=0.01,          # AUC threshold
                                    seed=42)                     # Random seed
                print(f"✅ AUCell score calculated for {{cell_type}}")
            except Exception as e:
                print(f"❌ AUCell failed for {{cell_type}}: {{e}}")

        print("\\n📈 AUCell scores added to adata.obs")
        ```
        """
        return aucell_response
    
    def run_workflow_llm_anno(self,description:str):
        """Run LLM annotation workflow"""
        logger.info("Running LLM annotation workflow")
        llm_anno_response = f"""

        First, analysis the first cluster:
        ```python

        for clust in adata.obs['leiden'].cat.categories:
            first_cluster = adata.obs['leiden'].cat.categories[0]
            print(f"\\n--- Processing Cluster {{first_cluster}} (1/{{total_clusters}}) ---")

            cluster = first_cluster
            print(f"\\n--- Analyzing Cluster {{cluster}} ---")

            print(f"Cluster markers:")
            cluster_markers = adata.uns['cluster_markers'][first_cluster]
            print(cluster_markers)

            print("📊 AUCell Score Analysis:")
            cluster_aucell_scores = {{}}
            for celltype_col in celltype_columns:
                if celltype_col in adata.obs.columns:
                    cluster_mean = adata.obs[adata.obs['leiden'] == cluster][celltype_col].mean()
                    other_clusters_mean = adata.obs[adata.obs['leiden'] != cluster][celltype_col].mean()
                    cluster_aucell_scores[celltype_col] = {{
                        'cluster_mean': cluster_mean,
                        'other_mean': other_clusters_mean,
                        'fold_enrichment': cluster_mean / (other_clusters_mean + 1e-6)
                    }}

            # Find top AUCell predictions
            top_aucell_types = sorted(cluster_aucell_scores.items(), 
                                    key=lambda x: x[1]['fold_enrichment'], 
                                    reverse=True)[:3]

            for i, (celltype_col, scores) in enumerate(top_aucell_types):
                celltype = celltype_col.replace('_AUCell', '').replace('AUCell_', '')
                print(f"  {{i+1}}. {{celltype}}: cluster={{scores['cluster_mean']:.3f}}, others={{scores['other_mean']:.3f}}, fold={{scores['fold_enrichment']:.2f}}x")

        ```

        Your task is to identify the most likely biological cell type for each cluster in {description}.
        First, interpret the marker genes in the context of known cell type–specific markers from public references (e.g., PanglaoDB, CellMarker, or literature).
        Then, cross-check with the AUCell scores: higher AUCell scores indicate stronger similarity to that cell type’s reference gene set.
        If marker gene evidence and AUCell score agree, assign that cell type.
        If they conflict, prioritize the marker gene interpretation but explain the discrepancy and possible reasons (e.g., mixed populations, doublets, transitional states).
        In ambiguous cases, provide the top 3 likely cell types ranked by confidence, with a brief explanation.
        Output the result as a dictionary with the following columns:

        Example:
        llm_anno_results={{
            "Cluster 0": "T Cells",
            "Cluster 1": "B Cells",
            ...
        }}

        Then, you need to annotate the cluster with the most likely cell type:
        ```python
        adata.obs['celltype_llm'] = adata.obs['leiden'].map(llm_anno_results)
        ```
        """
        return llm_anno_response
    
    
    

    









qc_response = f"""

First, check the function parameters:
```python
import omicverse as ov
# MANDATORY: Check help first before any omicverse function
help(ov.pp.qc)
```

Then run the actual QC:
```python
# Check if QC already done
if 'nUMIs' not in adata.obs.columns:
    print("\\n📊 Running Quality Control...")
    
    try:
        # Apply QC with actual omicverse parameters
        qc_tresh = dict(mito_perc=0.2, nUMIs=500, detected_genes=250)
        ov.pp.qc(adata, 
                mode='seurat',           # 'seurat' or 'mads'
                min_cells=3, 
                min_genes=200,
                mt_startswith='MT-',     # Mitochondrial gene prefix
                tresh=qc_tresh)
        print("✅ QC completed successfully")
        
        # Save QC results
        try:
            qc_dir = os.path.join(adata.uns['results_directory'], "02_quality_control")
            
            # Save QC parameters and statistics
            qc_stats = dict(
                pre_qc_cells=adata.n_obs,
                pre_qc_genes=adata.n_vars,
                qc_parameters=dict(
                    mode="seurat",
                    min_cells=3,
                    min_genes=200,
                    mt_startswith='MT-',
                    mito_threshold=qc_tresh.get('mito_perc', 0.2),
                    nUMIs_threshold=qc_tresh.get('nUMIs', 500),
                    detected_genes_threshold=qc_tresh.get('detected_genes', 250)
                ),
                timestamp=datetime.now().isoformat()
            ))
            
            
            import json
            with open(os.path.join(qc_dir, "qc_statistics.json"), 'w') as f:
                json.dump(qc_stats, f, indent=2)
            
            # Save post-QC data
            adata.write_h5ad(os.path.join(qc_dir, "post_qc_data.h5ad"))
            
            print(f"✅ QC results saved to {{qc_dir}}")
            
        except Exception as save_error:
            print(f"⚠️ Failed to save QC results: {{save_error}}")
        
    except Exception as e:
        print(f"❌ QC failed: {{e}}")
        
        
else:
    print("✅ QC already completed - skipping")
```
"""


preprocessing_response = f"""

First, check the function parameters:
```python
# MANDATORY: Check help first before any omicverse function  
import omicverse as ov
help(ov.pp.preprocess)
```

Then run preprocessing:
```python
# Check if preprocessing needed
needs_preprocessing = (
    ('highly_variable' or 'highly_variable_features') not in adata.var.columns or 
    'counts' not in adata.layers or
    adata.X.max() > 50  # Raw counts detected
)

if needs_preprocessing:
    try:
        # Use actual omicverse preprocess parameters
        adata = ov.pp.preprocess(adata, 
                                mode='shiftlog|pearson',    # normalization|HVG method
                                target_sum=50*1e4,          # Target sum for normalization
                                n_HVGs=2000,                # Number of HVGs
                                organism='human',           # 'human' or 'mouse'
                                no_cc=False)                # Remove cell cycle genes
        adata.var['highly_variable'] = adata.var['highly_variable_features']
        print("✅ Preprocessing completed successfully")
        
        # Save preprocessing results
        try:
            preprocess_dir = os.path.join(adata.uns['results_directory'], "03_preprocessing")
            
            # Save preprocessing parameters and statistics
            preprocess_stats = dict()
            preprocess_stats["preprocessing_parameters"] = dict(
                mode="shiftlog|pearson",
                target_sum=50*1e4,
                n_HVGs=2000,
                organism='human',
                no_cc=False)
            preprocess_stats["post_preprocessing"] = dict(
                n_cells=adata.n_obs,
                n_genes=adata.n_vars,
                n_hvgs=sum(adata.var['highly_variable']) if 'highly_variable' in adata.var.columns else 0,
                layers_available=list(adata.layers.keys()))
            preprocess_stats["timestamp"] = datetime.now().isoformat()
            preprocess_stats["hvg_genes"] = adata.var[adata.var['highly_variable']].index.tolist()
            preprocess_stats["layers_available"] = list(adata.layers.keys())
            preprocess_stats["timestamp"] = datetime.now().isoformat()
            preprocess_stats["hvg_genes"] = adata.var[adata.var['highly_variable']].index.tolist()
            preprocess_stats["layers_available"] = list(adata.layers.keys())
            preprocess_stats["timestamp"] = datetime.now().isoformat()
            
            
            # Save HVG list if available
            if 'highly_variable' in adata.var.columns:
                hvg_genes = adata.var[adata.var['highly_variable']].index.tolist()
                preprocess_stats["hvg_genes"] = hvg_genes
                
                # Save HVG list to separate file
                with open(os.path.join(preprocess_dir, "highly_variable_genes.txt"), 'w') as f:
                    for gene in hvg_genes:
                        f.write(f"{{gene}}\\n")
            
            # Save preprocessing statistics
            import json
            with open(os.path.join(preprocess_dir, "preprocessing_statistics.json"), 'w') as f:
                json.dump(preprocess_stats, f, indent=2)
            
            # Save preprocessed data
            adata.write_h5ad(os.path.join(preprocess_dir, "preprocessed_data.h5ad"))
            
            print(f"✅ Preprocessing results saved to {{preprocess_dir}}")
            
        except Exception as save_error:
            print(f"⚠️ Failed to save preprocessing results: {{save_error}}")
        
    except Exception as e:
        print(f"❌ Preprocessing failed: {{e}}")
        
else:
    print("✅ Data already preprocessed - skipping")
```
"""

pca_response = f"""

First, check the scaling function parameters:
```python
# MANDATORY: Check help first
import omicverse as ov
help(ov.pp.scale)
```

Then check PCA function parameters:
```python
# MANDATORY: Check help first

help(ov.pp.pca)
```

Now run scaling and PCA:
```python
# Check if scaling and PCA needed
needs_scaling = 'scaled' not in adata.layers
needs_pca = 'scaled|original|X_pca' not in adata.obsm.keys()

if needs_scaling:
    print("\\n🔢 Scaling data...")
    try:
        ov.pp.scale(adata,                    # Scale to unit variance and zero mean
                   max_value=10,              # Clip values above this
                   layers_add='scaled')       # Add to 'scaled' layer
        print("✅ Scaling completed successfully")
    except Exception as e:
        print(f"❌ Scaling failed: {{e}}")

if needs_pca:
    print("\\n🔢 Computing PCA...")
    try:
        ov.pp.pca(adata, 
                 n_pcs=50,                   # Number of principal components
                 layer='scaled',             # Use scaled data
                 inplace=True)               # Modify adata in place
        print("✅ PCA completed successfully")
        
        # Save PCA results
        try:
            pca_dir = os.path.join(adata.uns['results_directory'], "04_dimensionality_reduction")
            
            # Save PCA parameters and statistics
            pca_stats = dict()
            pca_stats["pca_parameters"] = dict(
                n_pcs=50,
                layer='scaled',
                inplace=True)
            pca_stats["scaling_parameters"] = dict(
                max_value=10,
                layers_add='scaled')
            pca_stats["results"] = dict(
                n_cells=adata.n_obs,
                n_genes=adata.n_vars,
                n_pcs_computed=adata.obsm['scaled|original|X_pca'].shape[1] if 'scaled|original|X_pca' in adata.obsm else 0,
                layers_available=list(adata.layers.keys()),
                obsm_keys=list(adata.obsm.keys()))
            pca_stats["timestamp"] = datetime.now().isoformat()
            
            # Add variance explained if available
            if 'pca' in adata.uns and 'variance_ratio' in adata.uns['pca']:
                variance_ratio = adata.uns['pca']['variance_ratio']
                pca_stats["variance_explained"] = dict(
                    variance_ratio=variance_ratio.tolist(),
                    cumulative_variance=variance_ratio.cumsum().tolist(),
                    n_components_80pct=int((variance_ratio.cumsum() < 0.8).sum() + 1),
                    n_components_90pct=int((variance_ratio.cumsum() < 0.9).sum() + 1))
            
            
            # Save PCA statistics
            import json
            with open(os.path.join(pca_dir, "pca_statistics.json"), 'w') as f:
                json.dump(pca_stats, f, indent=2)
            
            # Save post-PCA data
            adata.write_h5ad(os.path.join(pca_dir, "post_pca_data.h5ad"))
            
            print(f"✅ PCA results saved to {{pca_dir}}")
            
        except Exception as save_error:
            print(f"⚠️ Failed to save PCA results: {{save_error}}")
        
    except Exception as e:
        print(f"❌ PCA failed: {{e}}")
        

if not needs_scaling and not needs_pca:
    print("✅ Scaling and PCA already completed - skipping")
```
"""


batch_correction_response = f"""

```python
import omicverse as ov
# Check if batch correction needed and possible
batch_key = None
for potential_key in ['batch', 'sample', 'donor', 'condition']:
    if potential_key in adata.obs.columns:
        batch_key = potential_key
        break

has_corrected = any('harmony' in k or 'scanorama' in k or 'scVI' in k for k in adata.obsm.keys())

if batch_key and not has_corrected:
    print(f"\\n🔗 Applying Batch Correction using batch_key: {{batch_key}}...")
    
    # MANDATORY: Check help first
    help(ov.single.batch_correction)
    
    try:
        # Use actual omicverse batch_correction parameters
        ov.single.batch_correction(adata, 
                                 batch_key=batch_key,       # Batch column name
                                 use_rep='scaled|original|X_pca',  # Representation to use
                                 methods='harmony',         # 'harmony', 'combat', 'scanorama'
                                 n_pcs=50)                  # Number of PCs
        print("✅ Batch correction completed successfully")
    except Exception as e:
        print(f"❌ Batch correction failed: {{e}}")
        # Try with different method
        try:
            ov.single.batch_correction(adata, batch_key=batch_key, methods='combat')
            print("✅ Batch correction completed using Combat")
        except Exception as e2:
            print(f"❌ All batch correction methods failed: {{e2}}")
else:
    if not batch_key:
        print("✅ No batch information found - skipping batch correction")
    else:
        print("✅ Batch correction already completed - skipping")
```

"""


clustering_response = f"""

First, check the embedding of cells in obsm:
```python 
print(adata.obsm.keys())
if 'X_harmony' in adata.obsm.keys():
    use_rep = 'X_harmony'
elif 'X_scanorama' in adata.obsm.keys():
    use_rep = 'X_scanorama'
elif 'X_scVI' in adata.obsm.keys():
    use_rep = 'X_scVI'
else:
    use_rep = 'X_pca'
```


```python
# Check if clustering needed
needs_neighbors = 'neighbors' not in adata.uns.keys()
needs_clustering = 'leiden' not in adata.obs.columns

if needs_neighbors:
    print("\\n🎯 Computing neighborhood graph...")
    # Use scanpy directly for neighbors (no help() needed for non-omicverse functions)
    try:
        sc.pp.neighbors(adata, 
                       n_neighbors=15,              # Number of neighbors
                       use_rep=use_rep)  # Use PCA representation
        print("✅ Neighborhood graph computed successfully")
    except Exception as e:
        print(f"❌ Neighbors computation failed: {{e}}")
        # Try with default representation
        try:
            sc.pp.neighbors(adata, n_neighbors=15, use_rep=use_rep)
            print("✅ Neighbors computed with default representation")
        except Exception as e2:
            print(f"❌ Neighbors computation still failed: {{e2}}")

# Alternative: Use omicverse clustering
if needs_clustering:
    print("\\n🎯 Running clustering...")
    
    # MANDATORY: Check help first for omicverse clustering
    help(ov.utils.cluster)
    
    try:
        # Use omicverse clustering function with actual parameters
        ov.utils.cluster(adata, 
                        method='leiden',         # 'leiden', 'louvain', 'kmeans', 'GMM'
                        use_rep=use_rep,        # Representation to use
                        random_state=1024,      # Random seed
                        resolution=0.5,         # Resolution parameter
                        key_added='leiden')     # Output column name
        print("✅ Omicverse clustering completed successfully")
        
        # Save clustering and UMAP results
        try:
            clustering_dir = os.path.join(adata.uns['results_directory'], "06_clustering")
            
            # Save clustering parameters and statistics
            clustering_stats = dict()
            clustering_stats["clustering_parameters"] = dict(
                method="omicverse.utils.cluster",
                algorithm="leiden",
                use_rep=use_rep,
                random_state=1024,
                resolution=0.5,
                key_added="leiden")
            clustering_stats["neighbors_parameters"] = dict(
                n_neighbors=15,
                use_rep=use_rep)
            clustering_stats["results"] = dict(
                n_cells=adata.n_obs,
                n_clusters=len(adata.obs['leiden'].cat.categories) if 'leiden' in adata.obs.columns else 0,
                cluster_sizes=adata.obs['leiden'].value_counts().to_dict(),
                embeddings_available=list(adata.obsm.keys()),
            )
            clustering_stats["timestamp"] = datetime.now().isoformat()

            
            # Save clustering statistics
            import json
            with open(os.path.join(clustering_dir, "clustering_statistics.json"), 'w') as f:
                json.dump(clustering_stats, f, indent=2)
            
            # Save post-clustering data with UMAP
            adata.write_h5ad(os.path.join(clustering_dir, "post_clustering_data.h5ad"))
            
        except Exception as save_error:
            print(f"⚠️ Failed to save clustering results: {{save_error}}")
            
    except Exception as e:
        print(f"❌ Omicverse clustering failed: {{e}}")
        # Fallback to scanpy leiden clustering
        try:
            sc.tl.leiden(adata, resolution=0.5, random_state=0, key_added='leiden')
            print("✅ Scanpy clustering completed successfully")
        except Exception as e2:
            print(f"❌ All clustering attempts failed: {{e2}}")

if not needs_neighbors and not needs_clustering:
    print("✅ Neighbors and clustering already completed - skipping")
```

"""

umap_response = f"""

First, check the UMAP function parameters:
```python
# MANDATORY: Check help first
help(sc.tl.umap)
```

Then run UMAP:
```python
# Check if UMAP needed
needs_umap = 'X_umap' not in adata.obsm.keys()

if needs_umap:
    print("\\n🎯 Computing UMAP...")
    try:
        sc.tl.umap(adata, random_state=0)
        print("✅ UMAP computed successfully")
    except Exception as e:
        print(f"❌ UMAP computation failed: {{e}}")
```

"""

