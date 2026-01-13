"""
scBERT Adapter

scBERT is a large-scale pre-trained model for single-cell analysis
using Performer architecture for full-genome attention.

Reference: https://github.com/TencentAILabHealthcare/scBERT
Paper: Nature Machine Intelligence 2022
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, GeneIDScheme, get_registry
from .base import BaseAdapter


def _check_scbert_installed() -> tuple[bool, Optional[str]]:
    """Check if scBERT package is installed and return its location."""
    try:
        import scbert
        return True, str(Path(scbert.__file__).parent)
    except ImportError:
        try:
            # Alternative import
            from performer_pytorch import Performer
            return True, None
        except ImportError:
            return False, None


class ScBERTAdapter(BaseAdapter):
    """
    Adapter for scBERT foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (200-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Performer architecture (linear attention)
    - Full-genome attention (no gene vocabulary limit)
    - Human only
    - Good for rare cell type detection

    Requirements:
    - scBERT package or performer_pytorch
    - GPU with 8-16 GB VRAM (CPU fallback available)
    - Model checkpoint from scBERT repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("scbert")
        if spec is None:
            raise ValueError("scBERT model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._scbert_installed, self._scbert_path = _check_scbert_installed()

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
        Run scBERT model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (integration)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda', 'cpu')
            batch_size: Batch size for inference (default: 64)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.INTEGRATE]
        if task not in supported_tasks:
            if task == TaskType.ANNOTATE:
                return {
                    "error": "scBERT annotation requires fine-tuning",
                    "suggestion": "Use pre-trained embedding + classifier, or provide fine-tuned checkpoint",
                    "documentation": "https://github.com/TencentAILabHealthcare/scBERT",
                    "supported_tasks": ["embed", "integrate"],
                }
            return {
                "error": f"scBERT does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # Check for scBERT package
        if not self._scbert_installed:
            return {
                "error": "scBERT package not installed",
                "install": "pip install performer-pytorch  # or clone from https://github.com/TencentAILabHealthcare/scBERT",
                "documentation": "https://github.com/TencentAILabHealthcare/scBERT",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (scBERT is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"scBERT only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load scBERT model: {str(e)}"}

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
        """Load scBERT model."""
        if self._model is not None:
            return

        if not self._scbert_installed:
            raise ImportError("scBERT package not installed")

        # Mark as ready - actual loading happens in inference
        self._model = "ready"

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for scBERT.

        scBERT uses:
        - Log-normalized expression
        - Gene selection (HVGs or full genome)
        """
        import scanpy as sc

        # Work on a copy
        adata = adata.copy()

        # Ensure we have raw counts
        if adata.raw is not None:
            adata = adata.raw.to_adata()

        # Standard normalization
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)

        return adata

    def _run_inference(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Run scBERT inference to generate embeddings.

        Args:
            adata: Preprocessed AnnData object
            device: Device string (e.g., "cuda", "cpu")
            batch_size: Batch size for inference

        Returns:
            np.ndarray: Cell embeddings of shape (n_cells, 200)
        """
        try:
            return self._run_scbert_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"scBERT inference failed: {str(e)}")

    def _run_scbert_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run scBERT inference using direct Python API."""
        import torch
        from scipy.sparse import issparse

        # Check for checkpoint
        if self.checkpoint_dir is None:
            raise ValueError(
                "scBERT checkpoint directory not specified. "
                "Download from: https://github.com/TencentAILabHealthcare/scBERT"
            )

        checkpoint_path = Path(self.checkpoint_dir)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint directory not found: {self.checkpoint_dir}"
            )

        # Find checkpoint file
        ckpt_file = None
        for pattern in ["*.pth", "*.pt", "panglao_pretrain.pth", "model.pth"]:
            matches = list(checkpoint_path.glob(pattern))
            if matches:
                ckpt_file = matches[0]
                break

        if ckpt_file is None:
            raise FileNotFoundError(
                f"No checkpoint file (*.pth, *.pt) found in: {self.checkpoint_dir}"
            )

        # Try to import performer_pytorch
        try:
            from performer_pytorch import PerformerLM
        except ImportError:
            raise ImportError(
                "performer_pytorch not installed. Install with: pip install performer-pytorch"
            )

        # scBERT model configuration (from paper/repo)
        # Uses 7 expression bins, dim=200, depth=6, heads=10
        NUM_TOKENS = 7  # Expression level bins
        DIM = 200  # Embedding dimension
        DEPTH = 6
        HEADS = 10
        MAX_SEQ_LEN = adata.n_vars + 1  # Genes + CLS token

        # Build model
        model = PerformerLM(
            num_tokens=NUM_TOKENS,
            dim=DIM,
            depth=DEPTH,
            max_seq_len=MAX_SEQ_LEN,
            heads=HEADS,
            local_attn_heads=0,
            g2v_position_emb=False,  # Will handle separately if gene2vec available
        )

        # Load checkpoint
        ckpt = torch.load(str(ckpt_file), map_location=device)
        if "model_state_dict" in ckpt:
            model.load_state_dict(ckpt["model_state_dict"], strict=False)
        else:
            model.load_state_dict(ckpt, strict=False)

        model.to(device)
        model.eval()

        # Bin expression values into discrete tokens (0-6)
        X = adata.X
        if issparse(X):
            X = X.toarray()

        # Normalize to [0, 1] range, then bin to 7 levels
        X_min = X.min(axis=1, keepdims=True)
        X_max = X.max(axis=1, keepdims=True)
        X_range = X_max - X_min
        X_range[X_range == 0] = 1  # Avoid division by zero
        X_norm = (X - X_min) / X_range

        # Bin into 7 discrete tokens (0-6)
        binned = np.clip(np.floor(X_norm * NUM_TOKENS).astype(np.int64), 0, NUM_TOKENS - 1)

        # Add CLS token (using 0 or a special token)
        n_cells = binned.shape[0]
        cls_token = np.zeros((n_cells, 1), dtype=np.int64)
        binned_with_cls = np.concatenate([cls_token, binned], axis=1)

        # Batch inference
        embeddings = []
        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch = torch.tensor(
                    binned_with_cls[i : i + batch_size],
                    dtype=torch.long,
                    device=device,
                )

                # Get embeddings (return_encodings=True gives embeddings before output)
                output = model(batch, return_encodings=True)

                # Mean pool over sequence length to get cell embedding
                cell_emb = output.mean(dim=1)
                embeddings.append(cell_emb.cpu().numpy())

        return np.vstack(embeddings)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        if task == TaskType.EMBED:
            key = self.spec.output_keys.embedding_key  # "X_scBERT"
            adata.obsm[key] = embeddings
            output_keys.append(f"obsm['{key}']")
        elif task == TaskType.INTEGRATE:
            key = self.spec.output_keys.embedding_key
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

        # scBERT is human-only, default to human
        return "human"
