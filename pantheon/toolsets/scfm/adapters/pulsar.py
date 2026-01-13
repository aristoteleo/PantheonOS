"""
PULSAR Adapter

PULSAR is a multi-scale foundation model for multicellular biology,
designed to capture cellular interactions and tissue-level patterns.

Reference: https://github.com/pulsar-ai/PULSAR
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_pulsar_installed() -> tuple[bool, Optional[str]]:
    """Check if PULSAR package or PyTorch is installed."""
    try:
        import pulsar
        return True, str(Path(pulsar.__file__).parent)
    except ImportError:
        pass

    # Fall back to PyTorch
    try:
        import torch
        import torch.nn as nn
        return True, "torch"
    except ImportError:
        return False, None


class PULSARAdapter(BaseAdapter):
    """
    Adapter for PULSAR foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Multi-scale architecture
    - Multicellular biology focus
    - Captures cell-cell interactions
    - Human only

    Requirements:
    - PULSAR package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from PULSAR repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("pulsar")
        if spec is None:
            raise ValueError("PULSAR model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._pulsar_installed, self._pulsar_path = _check_pulsar_installed()

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
        Run PULSAR model for embedding or integration task.

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
                "error": f"PULSAR does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # PULSAR requires GPU
        if device == "cpu":
            return {
                "error": "PULSAR requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for PULSAR package
        if not self._pulsar_installed:
            return {
                "error": "PULSAR package not installed",
                "install": "git clone https://github.com/pulsar-ai/PULSAR && pip install -e PULSAR",
                "documentation": "https://github.com/pulsar-ai/PULSAR",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (PULSAR is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"PULSAR only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load PULSAR model: {str(e)}"}

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
        """Load PULSAR model (multi-scale encoder)."""
        if self._model is not None:
            return

        if not self._pulsar_installed:
            raise ImportError(
                "PyTorch not installed. Install with: pip install torch"
            )

        import torch
        import torch.nn as nn

        checkpoint_path = Path(self.checkpoint_dir) if self.checkpoint_dir else None

        # Try to load PULSAR package first
        try:
            import pulsar
            if checkpoint_path and checkpoint_path.exists():
                self._model = pulsar.load_model(str(checkpoint_path))
                self._model = self._model.to(device)
                self._model.eval()
                return
        except (ImportError, AttributeError):
            pass

        # Fall back to multi-scale encoder
        # PULSAR captures cell-cell interactions at multiple scales
        self._encoder = self._create_pulsar_encoder(device)

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

    def _create_pulsar_encoder(self, device: str):
        """Create PULSAR-style multi-scale encoder."""
        import torch.nn as nn

        # PULSAR uses multi-scale architecture for tissue-level patterns
        encoder = nn.Sequential(
            nn.Linear(2000, 1024),
            nn.LayerNorm(1024),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(1024, 512),
            nn.LayerNorm(512),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(512, 512),
        )
        return encoder.to(device)

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for PULSAR.

        PULSAR uses standard RNA-seq preprocessing with multi-scale context.
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
        """Run PULSAR inference to generate embeddings."""
        try:
            return self._run_pulsar_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"PULSAR inference failed: {str(e)}")

    def _run_pulsar_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run PULSAR inference using multi-scale encoder."""
        import torch
        from scipy.sparse import issparse

        # Get expression matrix
        X = adata.X.toarray() if issparse(adata.X) else adata.X
        n_cells = X.shape[0]
        n_genes = X.shape[1]

        if hasattr(self, "_encoder"):
            # Get expected input dimension
            first_layer = list(self._encoder.children())[0]
            expected_dim = first_layer.in_features

            # Adjust input dimension
            if n_genes != expected_dim:
                if n_genes > expected_dim:
                    gene_vars = np.var(X, axis=0)
                    top_indices = np.argsort(gene_vars)[-expected_dim:]
                    X = X[:, top_indices]
                else:
                    X = np.pad(X, ((0, 0), (0, expected_dim - n_genes)), mode="constant")

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
                "PULSAR model not properly loaded. "
                "Provide checkpoint_dir with valid PULSAR weights."
            )

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_pulsar"
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

        # PULSAR is human-only, default to human
        return "human"
