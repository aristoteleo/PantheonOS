"""Base utilities and configuration for single-cell RNA-seq analysis"""

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from ...utils.log import logger
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from ...utils.toolset import ToolSet


class ScRNASeqBase(ToolSet):
    """Base class for single-cell RNA-seq analysis toolsets"""
    
    def __init__(
        self,
        name: str = "scrna_base",
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
    
    def _run_command(
        self, 
        cmd: List[str], 
        cwd: Optional[Path] = None,
        timeout: int = 3600,
        **kwargs
    ) -> Dict[str, Any]:
        """Universal command execution pattern for scRNA tools"""
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or self.workspace_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
                **kwargs
            )
            return {
                "status": "success",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "command": " ".join(cmd)
            }
        except subprocess.CalledProcessError as e:
            return {
                "status": "failed",
                "stdout": e.stdout,
                "stderr": e.stderr,
                "returncode": e.returncode,
                "error": str(e),
                "command": " ".join(cmd),
                "recovery_suggestions": [
                    "Check input files exist and are readable",
                    "Verify Python environment and packages",
                    "Check available disk space and memory",
                    "Review parameter settings"
                ]
            }
        except subprocess.TimeoutExpired as e:
            return {
                "status": "timeout",
                "error": f"Command timed out after {timeout} seconds",
                "command": " ".join(cmd),
                "recovery_suggestions": [
                    f"Increase timeout beyond {timeout} seconds",
                    "Check system resources",
                    "Consider running on smaller dataset"
                ]
            }
    
    def _prepare_output_dir(self, output_dir: Optional[str] = None, prefix: str = "scrna") -> Path:
        """Prepare output directory and return Path object"""
        if output_dir is None:
            output_dir = self.workspace_path / "results"
        
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Generate timestamped output name for unique runs
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_path / f"{prefix}_{timestamp}"
        
        return output_file
    
    def _merge_params(self, user_params: Dict, step: str = "default") -> Dict:
        """Merge user parameters with defaults"""
        # Get step-specific defaults
        defaults = self.pipeline_config.get("default_params", {}).copy()
        step_defaults = self.pipeline_config.get(f"{step}_params", {})
        defaults.update(step_defaults)
        
        # Merge with user params (user params override)
        params = defaults.copy()
        if user_params:
            params.update(user_params)
        
        return params
    
    def _display_table(self, data: Dict[str, Any], title: str = "Results"):
        """Display results in formatted table"""
        table = Table(title=title)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        
        for key, value in data.items():
            # Format complex values
            if isinstance(value, (list, tuple)):
                value = f"{len(value)} items"
            elif isinstance(value, dict):
                value = f"{len(value)} entries"
            elif isinstance(value, Path):
                value = str(value)
            
            table.add_row(str(key).replace("_", " ").title(), str(value))
        
        logger.info("", rich=table)
    
    async def _check_python_environment(self) -> Dict[str, Any]:
        """Check Python environment and required packages by listing installed packages"""
        try:
            # Import and initialize Python toolset for testing
            from ...python.python_interpreter import PythonInterpreterToolSet
            import uuid
            
            # Use a unique client_id for this environment check session
            unique_client_id = f"env_check_{uuid.uuid4().hex[:8]}"
            python_toolset = PythonInterpreterToolSet("env_check", enable_code_validation=False)
            
            # Check Python version
            python_version_result = await python_toolset.run_python_code(
                "import sys; print(f'Python {sys.version.split()[0]}')", 
                context_variables={"client_id": unique_client_id}
            )
            
            if python_version_result.get("stderr"):
                return {
                    "python_version": None,
                    "packages": {},
                    "environment_ready": False,
                    "error": "Python environment not accessible"
                }
            
            python_version = python_version_result["stdout"].strip()
            
            # Get list of installed packages with versions
            package_list_code = """
import sys
try:
    # Try importlib.metadata first (Python 3.8+)
    from importlib import metadata
    installed = {}
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        version = dist.version
        installed[name.lower()] = version
    
    # Print in a parseable format
    for name, version in installed.items():
        print(f"{name}=={version}")
        
except ImportError:
    # Fallback to pkg_resources (older Python versions)
    try:
        import pkg_resources
        installed = {}
        for dist in pkg_resources.working_set:
            installed[dist.project_name.lower()] = dist.version
        
        for name, version in installed.items():
            print(f"{name}=={version}")
    except ImportError:
        print("ERROR: Cannot list packages")
"""
            
            package_list_result = await python_toolset.run_python_code(
                package_list_code,
                context_variables={"client_id": unique_client_id}
            )
            
            if package_list_result.get("stderr") or "ERROR:" in package_list_result.get("stdout", ""):
                # Fallback to individual package checks if listing fails
                return await self._check_packages_individually(python_toolset, unique_client_id, python_version)
            
            # Parse installed packages
            installed_packages = {}
            for line in package_list_result["stdout"].strip().split('\n'):
                if line and '==' in line:
                    name, version = line.split('==', 1)
                    installed_packages[name.lower()] = version
            
            # Check required packages against installed packages
            packages_status = {}
            required_packages = self.pipeline_config["tools"]["python_packages"]
            
            for package in required_packages:
                package_lower = package.lower()
                
                # Check common package name variations
                possible_names = [
                    package_lower,
                    package_lower.replace('-', '_'),
                    package_lower.replace('_', '-'),
                ]
                
                # Special cases for common packages
                name_mappings = {
                    'omicverse': ['omicverse', 'omics-verse'],
                    'pertpy': ['pertpy', 'pert-py'],
                    'scanpy': ['scanpy', 'scan-py'],
                }
                
                if package_lower in name_mappings:
                    possible_names.extend(name_mappings[package_lower])
                
                found_version = None
                for possible_name in possible_names:
                    if possible_name in installed_packages:
                        found_version = installed_packages[possible_name]
                        break
                
                if found_version:
                    packages_status[package] = {
                        "installed": True,
                        "version": found_version
                    }
                else:
                    packages_status[package] = {
                        "installed": False,
                        "error": f"Package '{package}' not found in installed packages"
                    }
            
            # Clean up the Python toolset
            try:
                if unique_client_id in python_toolset.clientid_to_interpreterid:
                    interpreter_id = python_toolset.clientid_to_interpreterid[unique_client_id]
                    if interpreter_id in python_toolset.interpreters:
                        await python_toolset.delete_interpreter(interpreter_id)
                        del python_toolset.clientid_to_interpreterid[unique_client_id]
            except Exception:
                pass  # Ignore cleanup errors
            
            return {
                "python_version": python_version,
                "packages": packages_status,
                "environment_ready": all(pkg["installed"] for pkg in packages_status.values())
            }
            
        except Exception as e:
            return {
                "python_version": None,
                "packages": {},
                "environment_ready": False,
                "error": f"Environment check failed: {str(e)}"
            }
    
    async def _check_packages_individually(self, python_toolset, unique_client_id: str, python_version: str) -> Dict[str, Any]:
        """Fallback method to check packages individually if listing fails"""
        packages_status = {}
        required_packages = self.pipeline_config["tools"]["python_packages"]
        
        for package in required_packages:
            try:
                # Try to get package version without importing
                version_check_code = f"""
try:
    from importlib import metadata
    dist = metadata.distribution('{package}')
    print(dist.version)
except ImportError:
    try:
        import pkg_resources
        dist = pkg_resources.get_distribution('{package}')
        print(dist.version)
    except:
        print("NOT_FOUND")
except:
    print("NOT_FOUND")
"""
                
                version_result = await python_toolset.run_python_code(
                    version_check_code,
                    context_variables={"client_id": unique_client_id}
                )
                
                if not version_result.get("stderr"):
                    version_output = version_result["stdout"].strip()
                    if version_output and version_output != "NOT_FOUND":
                        packages_status[package] = {
                            "installed": True,
                            "version": version_output
                        }
                    else:
                        packages_status[package] = {
                            "installed": False,
                            "error": f"Package '{package}' not found"
                        }
                else:
                    packages_status[package] = {
                        "installed": False,
                        "error": f"Check failed: {version_result['stderr']}"
                    }
                    
            except Exception as e:
                packages_status[package] = {
                    "installed": False,
                    "error": f"Check failed: {str(e)}"
                }
        
        return {
            "python_version": python_version,
            "packages": packages_status,
            "environment_ready": all(pkg["installed"] for pkg in packages_status.values())
        }
    
    def _calculate_md5(self, file_path: Path) -> str:
        """Calculate MD5 checksum of a file"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()