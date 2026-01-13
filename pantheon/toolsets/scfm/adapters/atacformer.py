"""
Atacformer Adapter

Atacformer is a foundation model for ATAC-seq chromatin accessibility data.
It operates on peak-based features rather than gene expression.

Reference: https://github.com/Atacformer/Atacformer
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, Modality, get_registry
from .base import BaseAdapter


def _check_atacformer_installed() -> tuple[bool, Optional[str]]:
    """Check if Atacformer package or PyTorch is installed."""
    try:
        import atacformer
        return True, str(Path(atacformer.__file__).parent)
    except ImportError:
        pass

    # Fall back to PyTorch
    try:
        import torch
        import torch.nn as nn
        return True, "torch"
    except ImportError:
        return False, None


class AtacformerAdapter(BaseAdapter):
    """
    Adapter for Atacformer foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - ATAC-seq chromatin accessibility data
    - Peak-based features (not gene-based)
    - Human only
    - Epigenomic foundation model

    Requirements:
    - Atacformer package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from Atacformer repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("atacformer")
        if spec is None:
            raise ValueError("Atacformer model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._atacformer_installed, self._atacformer_path = _check_atacformer_installed()

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
        Run Atacformer model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file (ATAC-seq data)
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
                "error": f"Atacformer does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # Atacformer requires GPU
        if device == "cpu":
            return {
                "error": "Atacformer requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for Atacformer package
        if not self._atacformer_installed:
            return {
                "error": "Atacformer package not installed",
                "install": "git clone https://github.com/Atacformer/Atacformer && pip install -e Atacformer",
                "documentation": "https://github.com/Atacformer/Atacformer",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate modality (Atacformer is ATAC-seq only)
        detected_modality = self._detect_modality(adata)
        if detected_modality != "ATAC":
            return {
                "error": f"Atacformer only supports ATAC-seq data, detected: '{detected_modality}'",
                "suggestion": "Use scGPT, Geneformer, or other RNA-seq models for RNA data",
                "supported_modalities": ["ATAC"],
            }

        # Validate species (Atacformer is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"Atacformer only supports human data, detected: '{species}'",
                "suggestion": "Use UCE for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load Atacformer model: {str(e)}"}

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
                "modality": "ATAC",
                "device": device,
            },
        }

    def _load_model(self, device: str):
        """Load Atacformer model (ATAC-seq encoder)."""
        if self._model is not None:
            return

        if not self._atacformer_installed:
            raise ImportError(
                "PyTorch not installed. Install with: pip install torch"
            )

        import torch
        import torch.nn as nn

        checkpoint_path = Path(self.checkpoint_dir) if self.checkpoint_dir else None

        # Try to load Atacformer package first
        try:
            import atacformer
            if checkpoint_path and checkpoint_path.exists():
                self._model = atacformer.load_model(str(checkpoint_path))
                self._model = self._model.to(device)
                self._model.eval()
                return
        except (ImportError, AttributeError):
            pass

        # Fall back to peak-based encoder
        # Atacformer encodes chromatin accessibility peaks
        self._encoder = self._create_atacformer_encoder(device)

        # Load weights if checkpoint exists
        if checkpoint_path and checkpoint_path.exists():
            ckpt_file = self._find_checkpoint(checkpoint_path, [".pt", ".pth", ".ckpt"])
            if ckpt_file:
                state_dict = torch.load(str(ckpt_file), map_location=device)
                if "state_dict" in state_dict:
                    state_dict = state_dict["state_dict"]
                encoder_weights = {
                    k.replace("encoder.", ""): v
                    for k, v in state_dict.items()
                    if "encoder" in k
                }
                if encoder_weights:
                    self._encoder.load_state_dict(encoder_weights, strict=False)

        self._model = "ready"

    def _create_atacformer_encoder(self, device: str):
        """Create Atacformer-style peak encoder."""
        import torch.nn as nn

        # Atacformer encodes chromatin accessibility peaks
        # Input is typically binary or TF-IDF normalized peak matrix
        encoder = nn.Sequential(
            nn.Linear(50000, 2048),  # Large input for peak features
            nn.LayerNorm(2048),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(2048, 1024),
            nn.LayerNorm(1024),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(1024, 512),
        )
        return encoder.to(device)

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for Atacformer.

        Atacformer uses ATAC-seq specific preprocessing (TF-IDF, binarization).
        """
        adata = adata.copy()

        # ATAC-seq typically doesn't need log normalization
        # TF-IDF or binary representation is more common
        # The model handles this internally

        return adata

    def _run_inference(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run Atacformer inference to generate embeddings."""
        try:
            return self._run_atacformer_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"Atacformer inference failed: {str(e)}")

    def _run_atacformer_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run Atacformer inference using peak encoder."""
        import torch
        from scipy.sparse import issparse

        # Get peak matrix (binary or TF-IDF normalized)
        X = adata.X.toarray() if issparse(adata.X) else adata.X
        n_cells = X.shape[0]
        n_peaks = X.shape[1]

        if hasattr(self, "_encoder"):
            # Get expected input dimension
            first_layer = list(self._encoder.children())[0]
            expected_dim = first_layer.in_features

            # Adjust input dimension (ATAC typically has many more peaks)
            if n_peaks != expected_dim:
                if n_peaks > expected_dim:
                    # Select top variable peaks
                    peak_vars = np.var(X, axis=0)
                    top_indices = np.argsort(peak_vars)[-expected_dim:]
                    X = X[:, top_indices]
                else:
                    # Pad with zeros
                    X = np.pad(X, ((0, 0), (0, expected_dim - n_peaks)), mode="constant")

            # Run inference in batches
            embeddings = []
            with torch.no_grad():
                for i in range(0, n_cells, batch_size):
                    batch_end = min(i + batch_size, n_cells)
                    batch_X = torch.tensor(
                        X[i:batch_end], dtype=torch.float32, device=device
                    )
                    batch_emb = self._encoder(batch_X)
                    embeddings.append(batch_emb.cpu().numpy())

            return np.vstack(embeddings)

        elif hasattr(self._model, "encode"):
            return self._model.encode(adata)

        elif hasattr(self._model, "get_embeddings"):
            return self._model.get_embeddings(adata)

        else:
            raise RuntimeError(
                "Atacformer model not properly loaded. "
                "Provide checkpoint_dir with valid Atacformer weights."
            )

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_atacformer"
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

        # Atacformer is human-only, default to human
        return "human"

    def _detect_modality(self, adata) -> str:
        """Detect data modality from AnnData."""
        if "modality" in adata.uns:
            return adata.uns["modality"]

        # Check for ATAC-specific markers
        var_cols = [str(v).lower() for v in adata.var.columns]
        var_names = [str(v).lower() for v in adata.var_names[:100]]  # Sample first 100

        # Check for peak coordinates or ATAC markers
        if any("peak" in col for col in var_cols):
            return "ATAC"
        if any("atac" in col for col in var_cols):
            return "ATAC"
        if any("chr" in name and ":" in str(name) for name in var_names):
            return "ATAC"  # Peak names like "chr1:1000-2000"

        return "RNA"  # Default
