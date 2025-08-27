"""Base utilities and configuration for single-cell ATAC-seq analysis"""

import os
import subprocess
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from ...utils.log import logger
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from ...utils.toolset import ToolSet


class ScATACSeqBase(ToolSet):
    """Base class for single-cell ATAC-seq analysis toolsets"""
    
    def __init__(
        self,
        name: str = "scatac_base",
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
        """Initialize scATAC-seq toolset configuration"""
        return {
            "file_extensions": {
                "input": [".fastq", ".fq", ".fastq.gz", ".fq.gz", ".fastq.bz2"],
                "output": [".h5", ".h5ad", ".mtx", ".tsv", ".csv"],
                "reference": [".fa", ".fasta", ".gtf", ".gff3", ".bed"],
                "fragments": [".fragments.tsv.gz", ".fragments.tsv"],
                "peaks": [".bed", ".narrowPeak", ".broadPeak"],
                "cellranger": [".cloupe", ".h5", ".csv"]
            },
            "tools": {
                "required": ["cellranger-atac"],
                "optional": ["python3", "R", "bedtools", "samtools"],
                "python_packages": ["scanpy", "anndata", "pandas", "numpy"],
                "r_packages": ["Signac", "Seurat", "ArchR", "GenomicRanges"],
                "alternatives": {
                    "cellranger-atac": ["snapatac2", "archR"],
                    "peak_calling": ["macs2", "genrich"]
                }
            },
            "default_params": {
                "threads": os.cpu_count() or 4,
                "memory": "64G",  # scATAC typically needs more memory
                "min_cells": 3,
                "min_peaks": 200,
                "max_peaks": 100000,
                "mito_threshold": 20.0,
                "expected_cells": 10000
            },
            "references": {
                "human": {
                    "GRCh38": {
                        "url": "https://cf.10xgenomics.com/supp/cell-arc/refdata-cellranger-arc-GRCh38-2024-A.tar.gz",
                        "version": "2024-A",
                        "size": 18000000000  # ~18GB
                    }
                },
                "mouse": {
                    "GRCm39": {
                        "url": "https://cf.10xgenomics.com/supp/cell-arc/refdata-cellranger-arc-GRCm39-2024-A.tar.gz",
                        "version": "2024-A", 
                        "size": 15000000000  # ~15GB
                    }
                }
            },
            "cellranger_atac": {
                "version": "2.2.0",
                "url": "https://cf.10xgenomics.com/releases/cell-atac/cellranger-atac-2.2.0.tar.gz?Expires=1754781116&Key-Pair-Id=APKAI7S6A5RYOXBWRPDA&Signature=CpWHJGFySfIjcIVycZq9ItmUeT-CmGU2bHbOZOYW49-VhaUou~bgj8Lb09pNHIqo3exFSrrNyvpyzA2QAh2fOmbE3UkTbZxEcaCJJcWdr1ReywanHar8qo1z~DKMRSzz~DW0sIudre4-YUE98DFqnC92EiguwIElMfBsnNoD-jjTiIaPM6K6MoQyBtpjaLTC2T37OplIOMUwfD-DN44YMdGDxlOjKaIaZ3B78HLqv-sGkAOvig~7EgcZs9auVSlB2G93qYZXTjo8q~5r0tmtWNjhYRzWrrN4OVgpavSxmbKDh4YX111p-EPWVxViOEUAq436ab7~NuDymm44IAR4Pg__",
                "size": 527868928,  # ~503MB actual size
                "md5": "c616198bd9aaca393f99c71e07ae46b6",  # Actual MD5 checksum
                "mirrors": [
                    "https://cf.10xgenomics.com/releases/cell-atac/cellranger-atac-2.2.0.tar.gz",
                    # Additional mirrors can be added here for fallback
                ],
                "install_dir": "software"  # Default installation directory
            },
            "project_structure": {
                "dirs": [
                    "raw_data",      # Input FASTQ files
                    "references",    # Genome references
                    "cellranger",    # cellranger-atac outputs
                    "filtered",      # QC-filtered data
                    "analysis",      # Downstream analysis
                    "plots",         # Visualizations
                    "reports",       # HTML/PDF reports
                    "logs"          # Processing logs
                ]
            }
        }
    
    def _get_cache_dir(self) -> Path:
        """Get cache directory for references and downloads"""
        cache_dir = self.launch_directory / ".pantheon" / "cache" / "scatac"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
    
    def _run_command(
        self, 
        cmd: List[str], 
        cwd: Optional[Path] = None,
        timeout: int = 3600,
        **kwargs
    ) -> Dict[str, Any]:
        """Universal command execution pattern for scATAC tools"""
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
                    "Verify cellranger-atac installation",
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
    
    def _prepare_output_dir(self, output_dir: Optional[str] = None, prefix: str = "scatac") -> Path:
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
    
    def _check_cellranger_atac(self) -> Dict[str, Any]:
        """Check cellranger-atac installation"""
        try:
            result = subprocess.run(
                ["cellranger-atac", "--version"],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Parse version from output
            version_line = result.stdout.strip().split('\n')[0]
            version = version_line.split()[-1] if version_line else "unknown"
            
            return {
                "installed": True,
                "version": version,
                "path": subprocess.run(["which", "cellranger-atac"], 
                                     capture_output=True, text=True).stdout.strip()
            }
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {
                "installed": False,
                "version": None,
                "path": None,
                "install_instructions": [
                    "Download from 10X Genomics website", 
                    "Add to PATH environment variable",
                    "Verify installation with: cellranger-atac --version"
                ]
            }
    
    def _calculate_md5(self, file_path: Path) -> str:
        """Calculate MD5 checksum of a file"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def _verify_installation(self, binary_path: Path) -> Dict[str, Any]:
        """Verify cellranger-atac installation by running version check"""
        try:
            result = subprocess.run(
                [str(binary_path), "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                version_output = result.stdout.strip()
                return {
                    "valid": True,
                    "version_output": version_output,
                    "executable": True
                }
            else:
                return {
                    "valid": False,
                    "error": result.stderr.strip(),
                    "executable": False
                }
        except subprocess.TimeoutExpired:
            return {
                "valid": False,
                "error": "Installation verification timed out",
                "executable": False
            }
        except Exception as e:
            return {
                "valid": False,
                "error": f"Verification failed: {str(e)}",
                "executable": False
            }