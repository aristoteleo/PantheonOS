"""
UCE (Universal Cell Embeddings) Adapter

UCE is a zero-shot, cross-species foundation model that uses ESM-2 protein embeddings
for gene representations. It's ideal for embedding tasks without fine-tuning.

Reference: https://github.com/snap-stanford/UCE
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, get_registry
from .base import BaseAdapter


def _check_uce_installed() -> tuple[bool, Optional[str]]:
    """Check if UCE package is installed and return its location."""
    try:
        import uce
        return True, str(Path(uce.__file__).parent)
    except ImportError:
        return False, None


class UCEAdapter(BaseAdapter):
    """
    Adapter for Universal Cell Embeddings (UCE) model.

    Supports:
    - embed: Zero-shot cell embeddings
    - integrate: Batch integration via shared embedding space

    Requirements:
    - GPU with 16+ GB VRAM
    - UCE package installed (pip install git+https://github.com/snap-stanford/UCE.git)
    - ESM-2 weights (auto-downloaded)
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("uce")
        if spec is None:
            raise ValueError("UCE model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._uce_model = None
        self._uce_installed, self._uce_path = _check_uce_installed()
        self._species_to_name = {
            "human": "Homo sapiens",
            "mouse": "Mus musculus",
            "zebrafish": "Danio rerio",
            "frog": "Xenopus tropicalis",
            "pig": "Sus scrofa",
        }

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 100,
    ) -> dict[str, Any]:
        """
        Run UCE model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Not used for UCE (integration is implicit)
            label_key: Not used for UCE
            device: Device to use ('auto', 'cuda', 'cpu')
            batch_size: Batch size for inference (default: 100)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        if task not in [TaskType.EMBED, TaskType.INTEGRATE]:
            return {
                "error": f"UCE does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)
        if device == "cpu":
            return {
                "error": "UCE requires GPU. CPU fallback not supported.",
                "suggestion": "Use a GPU-enabled environment or try Geneformer (has CPU fallback)",
            }

        # Check for UCE package
        if not self._uce_installed:
            return {
                "error": "UCE package not installed",
                "install": "pip install git+https://github.com/snap-stanford/UCE.git",
                "documentation": "https://github.com/snap-stanford/UCE",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Detect species
        species = self._detect_species(adata)
        if species not in self._species_to_name:
            return {
                "error": f"Species '{species}' not supported by UCE",
                "supported": list(self._species_to_name.keys()),
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load UCE model: {str(e)}"}

        # Preprocess
        try:
            processed_adata = self._preprocess(adata, task)
        except Exception as e:
            return {"error": f"Preprocessing failed: {str(e)}"}

        # Run inference
        try:
            embeddings = self._run_inference(
                processed_adata,
                species=self._species_to_name[species],
                device=device,
                batch_size=batch_size,
            )
        except Exception as e:
            return {"error": f"Inference failed: {str(e)}"}

        # Write results
        output_keys = self._postprocess(adata, embeddings, task)
        self._add_provenance(adata, task, output_keys)

        # Save
        adata.write(output_path)

        return {
            "status": "success",
            "output_path": output_path,
            "output_keys": output_keys,
            "stats": {
                "n_cells": adata.n_obs,
                "embedding_dim": embeddings.shape[1],
                "species": species,
                "device": device,
            },
        }

    def _load_model(self, device: str):
        """
        Load UCE model.

        UCE model loading is handled by the evaluation script.
        This method validates the environment and caches model location.
        """
        if self._uce_model is not None:
            return

        # UCE uses its own model loading via eval_single_anndata.py
        # We just validate that the package is available
        if not self._uce_installed:
            raise ImportError("UCE package not installed")

        # Mark as "loaded" - actual loading happens in _run_inference
        self._uce_model = "ready"

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for UCE.

        UCE requires:
        - Log-normalized counts
        - Gene symbols (not Ensembl IDs)
        """
        import scanpy as sc

        # Work on a copy
        adata = adata.copy()

        # Ensure we have raw counts or normalized data
        if adata.raw is not None:
            # Use raw counts if available
            adata = adata.raw.to_adata()

        # Log-normalize if not already done
        if "log1p" not in adata.uns:
            sc.pp.normalize_total(adata, target_sum=1e4)
            sc.pp.log1p(adata)

        return adata

    def _run_inference(
        self,
        adata,
        species: str,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Run UCE inference to generate embeddings.

        Uses UCE's eval_single_anndata.py script via subprocess for robust execution.
        This ensures compatibility with UCE's complex preprocessing pipeline.

        Args:
            adata: Preprocessed AnnData object
            species: Species name (e.g., "Homo sapiens")
            device: Device string (e.g., "cuda")
            batch_size: Batch size for inference

        Returns:
            np.ndarray: Cell embeddings of shape (n_cells, embedding_dim)
        """
        import scanpy as sc

        # Create temporary directory for UCE intermediate files
        with tempfile.TemporaryDirectory(prefix="uce_") as temp_dir:
            temp_path = Path(temp_dir)

            # Save preprocessed data to temp file
            input_path = temp_path / "input.h5ad"
            adata.write(str(input_path))

            # Run UCE evaluation script
            embeddings = self._run_uce_script(
                input_path=str(input_path),
                output_dir=str(temp_path),
                species=species,
                batch_size=batch_size,
            )

            return embeddings

    def _run_uce_script(
        self,
        input_path: str,
        output_dir: str,
        species: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Execute UCE evaluation script and return embeddings.

        Args:
            input_path: Path to input .h5ad file
            output_dir: Directory for UCE output files
            species: Species name
            batch_size: Batch size for inference

        Returns:
            np.ndarray: Cell embeddings
        """
        import scanpy as sc

        # Find the UCE eval script
        if self._uce_path is None:
            raise ImportError("UCE package not found")

        uce_script = Path(self._uce_path) / "eval_single_anndata.py"

        # If the script doesn't exist in the expected location, try alternatives
        if not uce_script.exists():
            # Try using python -m uce.eval_single_anndata
            cmd = [
                "python", "-m", "uce.eval_single_anndata",
                "--adata_path", input_path,
                "--dir", output_dir,
                "--species", species,
                "--batch_size", str(batch_size),
            ]
        else:
            cmd = [
                "python", str(uce_script),
                "--adata_path", input_path,
                "--dir", output_dir,
                "--species", species,
                "--batch_size", str(batch_size),
            ]

        # Execute UCE
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout for large datasets
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"UCE execution failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("UCE execution timed out (>1 hour)")

        # Load results from output file
        # UCE outputs to {output_dir}/{dataset_name}.h5ad
        output_files = list(Path(output_dir).glob("*.h5ad"))

        # Filter to find the output file (not the input)
        output_files = [f for f in output_files if f.name != "input.h5ad"]

        if not output_files:
            # If no separate output, UCE may have modified input in place
            output_file = Path(input_path)
        else:
            output_file = output_files[0]

        # Read embeddings from output
        result_adata = sc.read_h5ad(str(output_file))

        if "X_uce" not in result_adata.obsm:
            raise RuntimeError(
                f"UCE output missing X_uce embeddings. "
                f"Available keys: {list(result_adata.obsm.keys())}"
            )

        return result_adata.obsm["X_uce"]

    def _run_inference_direct(
        self,
        adata,
        species: str,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Alternative: Run UCE inference using direct Python imports.

        This method attempts to use UCE's internal APIs directly.
        Falls back to subprocess method if imports fail.

        Note: This is more fragile as it depends on UCE's internal structure.
        """
        try:
            # Try importing UCE's evaluation module
            from uce.evaluate import AnndataProcessor
            from accelerate import Accelerator

            # Set up accelerator
            accelerator = Accelerator()

            # Create args namespace for UCE
            class Args:
                pass

            args = Args()
            args.adata_path = None  # We'll pass adata directly
            args.dir = tempfile.mkdtemp(prefix="uce_")
            args.species = species
            args.batch_size = batch_size
            args.nlayers = 4  # Default 4-layer model
            args.output_dim = self.spec.embedding_dim
            args.model_loc = None  # Use default model location
            args.multi_gpu = False
            args.filter = False
            args.skip = False

            # Process with UCE
            processor = AnndataProcessor(args, accelerator)
            processor.adata = adata
            processor.preprocess_anndata()
            processor.generate_idxs()
            processor.run_evaluation()

            # Get embeddings
            embeddings = processor.adata.obsm["X_uce"]

            # Clean up
            shutil.rmtree(args.dir, ignore_errors=True)

            return embeddings

        except ImportError:
            # Fall back to subprocess method
            return self._run_uce_script(
                input_path=str(Path(tempfile.mkdtemp()) / "input.h5ad"),
                output_dir=tempfile.mkdtemp(),
                species=species,
                batch_size=batch_size,
            )

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """
        Write embeddings to AnnData.

        Args:
            adata: AnnData object to update
            embeddings: Cell embeddings array (n_cells, embedding_dim)
            task: Task type

        Returns:
            List of output keys written
        """
        output_keys = []

        # Write embeddings
        key = self.spec.output_keys.embedding_key  # "X_uce"
        adata.obsm[key] = embeddings
        output_keys.append(f"obsm['{key}']")

        return output_keys

    def _detect_species(self, adata) -> str:
        """Detect species from AnnData"""
        # Check uns first
        if "species" in adata.uns:
            species = adata.uns["species"].lower()
            if "human" in species or "sapiens" in species:
                return "human"
            elif "mouse" in species or "musculus" in species:
                return "mouse"

        # Infer from gene naming
        gene_names = adata.var_names[:100].tolist()
        uppercase_count = sum(1 for g in gene_names if g.isupper())

        if uppercase_count > 50:
            return "human"
        return "mouse"
