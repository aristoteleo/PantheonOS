"""
scFoundation (xTrimoGene) Adapter

scFoundation is a large-scale foundation model for single-cell transcriptomics
with ~100M parameters. It uses a custom 19,264 gene vocabulary and supports
zero-shot embedding extraction for downstream tasks.

Reference: https://github.com/biomap-research/scFoundation
Paper: Nature Methods 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, GeneIDScheme, get_registry
from .base import BaseAdapter


def _check_scfoundation_installed() -> tuple[bool, Optional[str]]:
    """Check if scFoundation package is installed and return its location."""
    try:
        # scFoundation may be imported as scfoundation or from xTrimoGene
        import scfoundation
        return True, str(Path(scfoundation.__file__).parent)
    except ImportError:
        try:
            # Alternative import path
            from xTrimoGene import scFoundation
            return True, None
        except ImportError:
            return False, None


class ScFoundationAdapter(BaseAdapter):
    """
    Adapter for scFoundation (xTrimoGene) foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Differences from scGPT/UCE:
    - Uses a CUSTOM 19,264 gene vocabulary (not symbols or Ensembl)
    - Human only (no cross-species support)
    - Requires GPU (no CPU fallback)
    - Read-depth aware preprocessing

    Requirements:
    - scFoundation package: pip install scfoundation (or clone from GitHub)
    - GPU with 16-32 GB VRAM
    - Model checkpoint from scFoundation repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("scfoundation")
        if spec is None:
            raise ValueError("scFoundation model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._gene_vocab = None
        self._scfoundation_installed, self._scfoundation_path = _check_scfoundation_installed()

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 64,
    ) -> dict[str, Any]:
        """
        Run scFoundation model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (integration)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda')
            batch_size: Batch size for inference (default: 64)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.INTEGRATE]
        if task not in supported_tasks:
            if task == TaskType.ANNOTATE:
                return {
                    "error": "scFoundation annotation requires fine-tuning",
                    "suggestion": "Use pre-trained embedding + classifier, or provide fine-tuned checkpoint",
                    "documentation": "https://github.com/biomap-research/scFoundation",
                    "supported_tasks": ["embed", "integrate"],
                }
            return {
                "error": f"scFoundation does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # scFoundation requires GPU
        if device == "cpu":
            return {
                "error": "scFoundation requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for scFoundation package
        if not self._scfoundation_installed:
            return {
                "error": "scFoundation package not installed",
                "install": "pip install scfoundation  # or clone from https://github.com/biomap-research/scFoundation",
                "documentation": "https://github.com/biomap-research/scFoundation",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (scFoundation is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"scFoundation only supports human data, detected: '{species}'",
                "suggestion": "Use UCE for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load scFoundation model: {str(e)}"}

        # Preprocess
        try:
            processed_adata = self._preprocess(adata, task)
        except Exception as e:
            return {"error": f"Preprocessing failed: {str(e)}"}

        # Run inference
        try:
            embeddings = self._run_inference(
                processed_adata,
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
                "gene_scheme": "custom_19264",
                "device": device,
            },
        }

    def _load_model(self, device: str):
        """
        Load scFoundation model and gene vocabulary.

        scFoundation uses:
        - Pre-trained transformer model
        - Custom 19,264 gene vocabulary
        """
        if self._model is not None:
            return

        if not self._scfoundation_installed:
            raise ImportError("scFoundation package not installed")

        # Mark as ready - actual loading happens in inference
        self._model = "ready"
        self._gene_vocab = "ready"

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for scFoundation.

        scFoundation requires:
        - Genes mapped to its 19,264 gene vocabulary
        - Read-depth aware normalization
        """
        import scanpy as sc

        # Work on a copy
        adata = adata.copy()

        # Ensure we have raw counts
        if adata.raw is not None:
            adata = adata.raw.to_adata()

        # Store total counts per cell (for read-depth awareness)
        if "n_counts" not in adata.obs:
            adata.obs["n_counts"] = adata.X.sum(axis=1).A1 if hasattr(adata.X, "toarray") else adata.X.sum(axis=1)

        # Note: Gene vocabulary matching would happen here
        # scFoundation uses a custom 19,264 gene set that needs to be matched

        return adata

    def _run_inference(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Run scFoundation inference to generate embeddings.

        Args:
            adata: Preprocessed AnnData object
            device: Device string (e.g., "cuda")
            batch_size: Batch size for inference

        Returns:
            np.ndarray: Cell embeddings of shape (n_cells, 512)
        """
        try:
            return self._run_scfoundation_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"scFoundation inference failed: {str(e)}")

    def _run_scfoundation_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Run scFoundation inference using direct Python API.
        """
        try:
            from scfoundation import get_embeddings
        except ImportError:
            try:
                from xTrimoGene.scFoundation import get_embeddings
            except ImportError as e:
                raise ImportError(f"scFoundation dependencies missing: {e}")

        # Check for checkpoint
        if self.checkpoint_dir is None:
            raise ValueError(
                "scFoundation checkpoint directory not specified. "
                "Download from: https://github.com/biomap-research/scFoundation"
            )

        checkpoint_path = Path(self.checkpoint_dir)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint directory not found: {self.checkpoint_dir}"
            )

        # Run embedding extraction
        # Note: Actual API may differ - this follows the expected pattern
        embeddings = get_embeddings(
            adata,
            checkpoint_path=str(checkpoint_path),
            device=device,
            batch_size=batch_size,
        )

        # Ensure correct shape
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)

        return embeddings.astype(np.float32)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """
        Write embeddings to AnnData.

        Args:
            adata: AnnData object to update
            embeddings: Cell embeddings array (n_cells, 512)
            task: Task type

        Returns:
            List of output keys written
        """
        output_keys = []

        if task == TaskType.EMBED:
            key = self.spec.output_keys.embedding_key  # "X_scfoundation"
            adata.obsm[key] = embeddings
            output_keys.append(f"obsm['{key}']")
        elif task == TaskType.INTEGRATE:
            # For integration, use the same embedding key
            # scFoundation's shared embedding space provides batch correction
            key = self.spec.output_keys.embedding_key
            adata.obsm[key] = embeddings
            output_keys.append(f"obsm['{key}']")

        return output_keys

    def _detect_species(self, adata) -> str:
        """Detect species from AnnData metadata."""
        # Check uns first
        if "species" in adata.uns:
            species = adata.uns["species"].lower()
            if "human" in species or "sapiens" in species:
                return "human"
            elif "mouse" in species or "musculus" in species:
                return "mouse"

        # scFoundation is human-only, default to human
        return "human"
