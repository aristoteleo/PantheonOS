"""Single-cell RNA-seq upstream processing and data preparation"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .base import ScRNASeqBase
from ...utils.toolset import tool
from ...utils.log import logger


class ScRNASeqUpstreamToolSet(ScRNASeqBase):
    """Single-cell RNA-seq upstream processing toolset"""
    
    def __init__(
        self,
        name: str = "scrna_upstream",
        workspace_path: str = None,
        launch_directory: str = None,
        worker_params: dict = None,
        **kwargs
    ):
        super().__init__(name, workspace_path, launch_directory, worker_params, **kwargs)
    
    @tool
    async def check_dependencies(self) -> Dict[str, Any]:
        """Check Python environment and required packages for scRNA-seq analysis"""
        logger.info("")
        logger.info("\n" + "="*70)
        logger.info("🧬 [bold cyan]Single-cell RNA-seq Environment Check[/bold cyan]")
        logger.info("="*70)
        
        env_status = await self._check_python_environment()
        
        # Display Python version
        if env_status["python_version"]:
            logger.info(f"✅ Python: {env_status['python_version']}")
        else:
            logger.error("❌ Python: Not found or not accessible")
            return {
                "status": "failed",
                "error": "Python3 is required but not found",
                "environment_ready": False
            }
        
        # Display package status
        missing_packages = []
        for package, status in env_status["packages"].items():
            if status["installed"]:
                version = status.get("version", "unknown")
                logger.info(f"✅ {package}: {version}")
            else:
                logger.warning(f"❌ {package}: Not installed")
                missing_packages.append(package)
        
        if missing_packages:
            logger.info("\n📦 [yellow]Missing packages can be installed with:[/yellow]")
            install_cmd = f"pip install {' '.join(missing_packages)}"
            logger.info(f"[dim]{install_cmd}[/dim]")
            
            return {
                "status": "partially_ready",
                "missing_packages": missing_packages,
                "install_command": install_cmd,
                "environment_ready": False,
                "python_environment": env_status
            }
        
        logger.success("✅ All required packages are installed!")
        return {
            "status": "ready",
            "missing_packages": [],
            "environment_ready": True,
            "python_environment": env_status
        }
    
    @tool
    async def install_missing_packages(self, packages: List[str] = None) -> Dict[str, Any]:
        """Install missing Python packages for scRNA-seq analysis"""
        
        if packages is None:
            # Get missing packages from dependency check
            dep_check = await self.check_dependencies()
            if dep_check["status"] == "ready":
                return {
                    "status": "success",
                    "message": "All packages already installed"
                }
            packages = dep_check.get("missing_packages", [])
        
        if not packages:
            return {
                "status": "success", 
                "message": "No packages to install"
            }
        
        logger.info(f"\n📦 [bold cyan]Installing missing packages: {', '.join(packages)}[/bold cyan]")
        
        installation_results = {}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
        ) as progress:
            
            for package in packages:
                task = progress.add_task(f"Installing {package}...", total=1)
                
                try:
                    result = subprocess.run(
                        ["pip", "install", package],
                        capture_output=True,
                        text=True,
                        timeout=300  # 5 minute timeout per package
                    )
                    
                    if result.returncode == 0:
                        installation_results[package] = {
                            "status": "success",
                            "output": result.stdout
                        }
                        progress.update(task, description=f"✅ {package} installed")
                    else:
                        installation_results[package] = {
                            "status": "failed",
                            "error": result.stderr,
                            "output": result.stdout
                        }
                        progress.update(task, description=f"❌ {package} failed")
                        
                except subprocess.TimeoutExpired:
                    installation_results[package] = {
                        "status": "timeout",
                        "error": "Installation timed out after 5 minutes"
                    }
                    progress.update(task, description=f"⏰ {package} timed out")
                    
                except Exception as e:
                    installation_results[package] = {
                        "status": "error",
                        "error": str(e)
                    }
                    progress.update(task, description=f"❌ {package} error")
                
                progress.advance(task)
        
        # Summary
        successful = [pkg for pkg, result in installation_results.items() 
                     if result["status"] == "success"]
        failed = [pkg for pkg, result in installation_results.items() 
                 if result["status"] != "success"]
        
        if failed:
            logger.info(f"\n⚠️ [yellow]Some packages failed to install: {', '.join(failed)}[/yellow]")
            logger.info("Try installing them manually or check your Python environment")
            
        if successful:
            logger.info(f"\n✅ [green]Successfully installed: {', '.join(successful)}[/green]")
        
        return {
            "status": "completed",
            "installation_results": installation_results,
            "successful": successful,
            "failed": failed
        }
    
    @tool
    def scan_folder(self, folder_path: str) -> Dict[str, Any]:
        """Scan folder for scRNA-seq data files and structure"""
        
        folder_path = Path(folder_path).resolve()
        logger.info(f"\n🔍 [bold cyan]Scanning folder: {folder_path}[/bold cyan]")
        
        if not folder_path.exists():
            return {
                "status": "failed",
                "error": f"Folder does not exist: {folder_path}"
            }
        
        scan_results = {
            "folder_path": str(folder_path),
            "total_files": 0,
            "file_types": {},
            "potential_inputs": [],
            "data_structure": {},
            "recommendations": []
        }
        
        # Scan all files
        supported_extensions = [
            ".h5", ".h5ad", ".mtx", ".csv", ".tsv", ".txt",
            ".fastq", ".fq", ".fastq.gz", ".fq.gz"
        ]
        
        all_files = []
        for ext in supported_extensions:
            files = list(folder_path.rglob(f"*{ext}"))
            all_files.extend(files)
        
        scan_results["total_files"] = len(all_files)
        
        # Categorize files
        for file_path in all_files:
            ext = "".join(file_path.suffixes)
            if ext not in scan_results["file_types"]:
                scan_results["file_types"][ext] = []
            scan_results["file_types"][ext].append(str(file_path.relative_to(folder_path)))
        
        # Look for common scRNA-seq data patterns
        self._identify_data_patterns(folder_path, scan_results)
        
        # Generate recommendations
        self._generate_scan_recommendations(scan_results)
        
        # Display results
        self._display_scan_results(scan_results)
        
        return {
            "status": "success",
            "scan_results": scan_results
        }
    
    def _identify_data_patterns(self, folder_path: Path, scan_results: Dict):
        """Identify common scRNA-seq data patterns"""
        
        # Look for 10X format (matrix.mtx, barcodes.tsv, features.tsv)
        mtx_files = list(folder_path.rglob("*matrix.mtx*"))
        for mtx_file in mtx_files:
            mtx_dir = mtx_file.parent
            barcodes = list(mtx_dir.glob("*barcodes*"))
            features = list(mtx_dir.glob("*features*")) or list(mtx_dir.glob("*genes*"))
            
            if barcodes and features:
                scan_results["potential_inputs"].append({
                    "type": "10X_format",
                    "directory": str(mtx_dir.relative_to(folder_path)),
                    "files": {
                        "matrix": str(mtx_file.relative_to(folder_path)),
                        "barcodes": str(barcodes[0].relative_to(folder_path)),
                        "features": str(features[0].relative_to(folder_path))
                    }
                })
        
        # Look for H5 files (10X or AnnData)
        h5_files = list(folder_path.rglob("*.h5"))
        for h5_file in h5_files:
            scan_results["potential_inputs"].append({
                "type": "H5_file",
                "file": str(h5_file.relative_to(folder_path)),
                "size_mb": round(h5_file.stat().st_size / (1024*1024), 2)
            })
        
        # Look for H5AD files (AnnData)
        h5ad_files = list(folder_path.rglob("*.h5ad"))
        for h5ad_file in h5ad_files:
            scan_results["potential_inputs"].append({
                "type": "AnnData_file",
                "file": str(h5ad_file.relative_to(folder_path)),
                "size_mb": round(h5ad_file.stat().st_size / (1024*1024), 2)
            })
        
        # Look for CSV/TSV expression matrices
        expression_files = list(folder_path.rglob("*expression*")) + \
                          list(folder_path.rglob("*counts*")) + \
                          list(folder_path.rglob("*matrix*"))
        
        for expr_file in expression_files:
            if expr_file.suffix in [".csv", ".tsv", ".txt"]:
                scan_results["potential_inputs"].append({
                    "type": "Expression_matrix",
                    "file": str(expr_file.relative_to(folder_path)),
                    "size_mb": round(expr_file.stat().st_size / (1024*1024), 2)
                })
    
    def _generate_scan_recommendations(self, scan_results: Dict):
        """Generate recommendations based on scan results"""
        
        if not scan_results["potential_inputs"]:
            scan_results["recommendations"].append(
                "No recognized scRNA-seq data formats found. "
                "Ensure data is in 10X format, H5/H5AD, or expression matrix format."
            )
            return
        
        # Recommend based on data types found
        input_types = [inp["type"] for inp in scan_results["potential_inputs"]]
        
        if "AnnData_file" in input_types:
            scan_results["recommendations"].append(
                "Found AnnData (.h5ad) files - these are ready for analysis with scanpy/omicverse"
            )
        
        if "10X_format" in input_types:
            scan_results["recommendations"].append(
                "Found 10X format data - can be loaded directly for analysis"
            )
        
        if "H5_file" in input_types:
            scan_results["recommendations"].append(
                "Found H5 files - check if these are 10X format or AnnData format"
            )
        
        if "Expression_matrix" in input_types:
            scan_results["recommendations"].append(
                "Found expression matrices - may need metadata/barcode files for complete analysis"
            )
        
        # General recommendations
        scan_results["recommendations"].extend([
            "Use load_data() to read the data and inspect its structure",
            "Run quality_control() to assess data quality",
            "Check for batch effects and metadata availability"
        ])
    
    def _display_scan_results(self, scan_results: Dict):
        """Display scan results in formatted tables"""
        
        from rich.table import Table
        
        # File types summary
        if scan_results["file_types"]:
            file_table = Table(title="File Types Found")
            file_table.add_column("Extension", style="cyan")
            file_table.add_column("Count", style="green")
            file_table.add_column("Examples", style="dim")
            
            for ext, files in scan_results["file_types"].items():
                examples = ", ".join(files[:3])
                if len(files) > 3:
                    examples += f" ... (+{len(files)-3} more)"
                file_table.add_row(ext, str(len(files)), examples)
            
            logger.info("", rich=file_table)
        
        # Potential inputs
        if scan_results["potential_inputs"]:
            input_table = Table(title="Potential Data Inputs")
            input_table.add_column("Type", style="cyan")
            input_table.add_column("Location", style="green")
            input_table.add_column("Details", style="dim")
            
            for inp in scan_results["potential_inputs"]:
                if inp["type"] == "10X_format":
                    location = inp["directory"]
                    details = "matrix.mtx + barcodes + features"
                elif inp["type"] in ["H5_file", "AnnData_file", "Expression_matrix"]:
                    location = inp["file"]
                    details = f"{inp.get('size_mb', 0):.1f} MB"
                else:
                    location = str(inp.get("file", inp.get("directory", "")))
                    details = ""
                
                input_table.add_row(inp["type"], location, details)
            
            logger.info("", rich=input_table)
        
        # Recommendations
        if scan_results["recommendations"]:
            recommendations_text = "\n".join([f"• {rec}" for rec in scan_results["recommendations"]])
            recommendations_panel = Panel(
                recommendations_text,
                title="Recommendations",
                border_style="blue"
            )
            logger.info("", rich=recommendations_panel)
    
    @tool
    def init(self, project_name: str = None, create_structure: bool = True) -> Dict[str, Any]:
        """Initialize scRNA-seq project structure"""
        
        if project_name is None:
            project_name = f"scrna_project_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        project_path = self.workspace_path / project_name
        
        logger.info(f"\n🚀 [bold cyan]Initializing scRNA-seq project: {project_name}[/bold cyan]")
        
        if project_path.exists():
            return {
                "status": "failed",
                "error": f"Project directory already exists: {project_path}"
            }
        
        # Create project structure
        if create_structure:
            project_dirs = self.pipeline_config["project_structure"]["dirs"]
            
            try:
                project_path.mkdir(parents=True)
                
                for dir_name in project_dirs:
                    (project_path / dir_name).mkdir()
                    
                # Create config file
                config = {
                    "project_name": project_name,
                    "created": datetime.now().isoformat(),
                    "workspace_path": str(project_path),
                    "pipeline_config": self.pipeline_config
                }
                
                config_file = project_path / "project_config.json"
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=2)
                
                # Create README
                readme_content = f"""# {project_name}

Single-cell RNA-seq Analysis Project

## Project Structure
```
{project_name}/
├── raw_data/       # Input expression files
├── processed/      # QC and preprocessed data  
├── analysis/       # Downstream analysis results
├── plots/          # Visualizations
├── reports/        # HTML/PDF reports
├── annotations/    # Cell type annotations
├── markers/        # Marker genes
├── logs/          # Processing logs
└── project_config.json
```

## Quick Start
1. Place your scRNA-seq data in the `raw_data/` directory
2. Use the pantheon bio scrna tools to analyze your data
3. Results will be organized in the respective directories

Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                
                readme_file = project_path / "README.md"
                with open(readme_file, 'w') as f:
                    f.write(readme_content)
                
                logger.info(f"✅ [green]Project structure created successfully![/green]")
                logger.info(f"[dim]Project path: {project_path}[/dim]")
                
                return {
                    "status": "success",
                    "project_name": project_name,
                    "project_path": str(project_path),
                    "directories_created": project_dirs,
                    "config_file": str(config_file),
                    "readme_file": str(readme_file)
                }
                
            except Exception as e:
                return {
                    "status": "failed",
                    "error": f"Failed to create project structure: {str(e)}"
                }
        else:
            # Just create the main directory
            try:
                project_path.mkdir(parents=True)
                
                return {
                    "status": "success",
                    "project_name": project_name,
                    "project_path": str(project_path),
                    "directories_created": [project_name]
                }
                
            except Exception as e:
                return {
                    "status": "failed",
                    "error": f"Failed to create project directory: {str(e)}"
                }