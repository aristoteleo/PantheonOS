"""
scPRINT Adapter

scPRINT is a foundation model focused on protein-coding genes for
robust batch integration in single-cell RNA-seq data. Trained on ~22M cells.

Reference: https://github.com/scprint/scPRINT
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_scprint_installed() -> tuple[bool, Optional[str]]:
    """Check if scPRINT package is installed and return its location."""
    try:
        import scprint
        return True, str(Path(scprint.__file__).parent)
    except ImportError:
        return False, None


class ScPRINTAdapter(BaseAdapter):
    """
    Adapter for scPRINT foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Focus on protein-coding genes
    - Robust batch integration
    - Human only
    - Trained on ~22M cells

    Requirements:
    - scPRINT package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from scPRINT repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("scprint")
        if spec is None:
            raise ValueError("scPRINT model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._scprint_installed, self._scprint_path = _check_scprint_installed()

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 32,
    ) -> dict[str, Any]:
        """
        Run scPRINT model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (integration)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda')
            batch_size: Batch size for inference (default: 32)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.INTEGRATE]
        if task not in supported_tasks:
            return {
                "error": f"scPRINT does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # scPRINT requires GPU
        if device == "cpu":
            return {
                "error": "scPRINT requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for scPRINT package
        if not self._scprint_installed:
            return {
                "error": "scPRINT package not installed",
                "install": "git clone https://github.com/scprint/scPRINT && pip install -e scPRINT",
                "documentation": "https://github.com/scprint/scPRINT",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (scPRINT is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"scPRINT only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load scPRINT model: {str(e)}"}

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
                "device": device,
            },
        }

    def _load_model(self, device: str):
        """Load scPRINT model."""
        if self._model is not None:
            return

        if not self._scprint_installed:
            raise ImportError("scPRINT package not installed")

        from scprint import scPrint

        checkpoint_path = Path(self.checkpoint_dir) if self.checkpoint_dir else None

        if checkpoint_path is None:
            # Try to load from HuggingFace hub
            try:
                # scPRINT checkpoints are on HuggingFace
                self._model = scPrint.load_from_checkpoint(
                    "cantinilab/scPRINT",
                    precpt_gene_emb=None,
                    transformer="normal" if device == "cpu" else "flash",
                )
            except Exception:
                raise ValueError(
                    "scPRINT checkpoint not found. Download from HuggingFace "
                    "or specify checkpoint_dir parameter."
                )
        else:
            # Load from local checkpoint
            ckpt_file = self._find_checkpoint(checkpoint_path, [".ckpt", ".pt"])
            self._model = scPrint.load_from_checkpoint(
                str(ckpt_file),
                precpt_gene_emb=None,
                transformer="normal" if device == "cpu" else "flash",
            )

        self._model = self._model.to(device)
        self._model.eval()

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for scPRINT.

        scPRINT uses standard RNA-seq preprocessing with focus on protein-coding genes.
        """
        import scanpy as sc

        adata = adata.copy()

        if adata.raw is not None:
            adata = adata.raw.to_adata()

        # Standard preprocessing
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)

        return adata

    def _run_inference(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run scPRINT inference to generate embeddings."""
        try:
            return self._run_scprint_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"scPRINT inference failed: {str(e)}")

    def _run_scprint_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run scPRINT inference using direct Python API."""
        import torch
        from scipy.sparse import issparse

        # scPRINT uses Embedder class for generating embeddings
        try:
            from scprint import Embedder

            embedder = Embedder(
                batch_size=batch_size,
                how="mean",  # Aggregation method
                max_len=2048,  # Max sequence length
                add_zero_genes=True,
            )

            # Run embedding - Embedder modifies adata in place
            embedder(self._model, adata=adata)

            # Extract embeddings from adata.obsm
            emb_key = "X_scprint"
            if emb_key in adata.obsm:
                return adata.obsm[emb_key]
            else:
                # Fallback to scprint_emb or other keys
                for key in ["scprint_emb", "X_emb", "embeddings"]:
                    if key in adata.obsm:
                        return adata.obsm[key]
                raise KeyError("No embedding key found in adata.obsm after Embedder")

        except ImportError:
            # Fallback to manual inference if Embedder not available
            return self._run_manual_inference(adata, device, batch_size)

    def _run_manual_inference(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Manual inference fallback when Embedder class not available."""
        import torch
        from scipy.sparse import issparse

        X = adata.X.toarray() if issparse(adata.X) else adata.X

        embeddings = []
        n_cells = X.shape[0]

        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch_end = min(i + batch_size, n_cells)
                batch_X = torch.tensor(
                    X[i:batch_end], dtype=torch.float32, device=device
                )

                # Get embeddings from model encoder
                if hasattr(self._model, "encode"):
                    emb = self._model.encode(batch_X)
                elif hasattr(self._model, "get_cell_embeddings"):
                    emb = self._model.get_cell_embeddings(batch_X)
                elif hasattr(self._model, "forward"):
                    # Use forward pass and extract hidden states
                    output = self._model(batch_X)
                    if isinstance(output, tuple):
                        emb = output[0]  # First element is typically embeddings
                    else:
                        emb = output
                else:
                    raise RuntimeError(
                        "Could not find embedding method in scPRINT model"
                    )

                if isinstance(emb, torch.Tensor):
                    emb = emb.cpu().numpy()
                embeddings.append(emb)

        return np.vstack(embeddings)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_scprint"
        adata.obsm[key] = embeddings
        output_keys.append(f"obsm['{key}']")

        return output_keys

    def _detect_species(self, adata) -> str:
        """Detect species from AnnData metadata."""
        if "species" in adata.uns:
            species = adata.uns["species"].lower()
            if "human" in species or "sapiens" in species:
                return "human"
            elif "mouse" in species or "musculus" in species:
                return "mouse"

        # scPRINT is human-only, default to human
        return "human"
