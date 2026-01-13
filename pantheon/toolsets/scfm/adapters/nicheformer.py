"""
Nicheformer Adapter

Nicheformer is a foundation model for spatial transcriptomics that
captures cell niches and spatial context.

Reference: https://github.com/theislab/nicheformer
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, GeneIDScheme, get_registry
from .base import BaseAdapter


def _check_nicheformer_installed() -> tuple[bool, Optional[str]]:
    """Check if Nicheformer package is installed and return its location."""
    try:
        import nicheformer
        return True, str(Path(nicheformer.__file__).parent)
    except ImportError:
        return False, None


class NicheformerAdapter(BaseAdapter):
    """
    Adapter for Nicheformer foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space
    - spatial: Spatial niche analysis

    Key Features:
    - Spatial transcriptomics focus
    - Captures cell niche context
    - Human and mouse support
    - Multi-tissue compatibility

    Requirements:
    - Nicheformer package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from Nicheformer repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("nicheformer")
        if spec is None:
            raise ValueError("Nicheformer model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._nicheformer_installed, self._nicheformer_path = _check_nicheformer_installed()

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
        Run Nicheformer model for embedding, integration, or spatial task.

        Args:
            task: TaskType.EMBED, TaskType.INTEGRATE, or TaskType.SPATIAL
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (integration)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda')
            batch_size: Batch size for inference (default: 32)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.INTEGRATE, TaskType.SPATIAL]
        if task not in supported_tasks:
            return {
                "error": f"Nicheformer does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate", "spatial"],
            }

        device = self._resolve_device(device)

        # Nicheformer requires GPU
        if device == "cpu":
            return {
                "error": "Nicheformer requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for Nicheformer package
        if not self._nicheformer_installed:
            return {
                "error": "Nicheformer package not installed",
                "install": "git clone https://github.com/theislab/nicheformer && pip install -e nicheformer",
                "documentation": "https://github.com/theislab/nicheformer",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species
        species = self._detect_species(adata)
        if species not in ["human", "mouse"]:
            return {
                "error": f"Nicheformer supports human and mouse, detected: '{species}'",
                "suggestion": "Use UCE for other species",
                "supported": ["human", "mouse"],
            }

        # Check for spatial coordinates if spatial task
        if task == TaskType.SPATIAL:
            if "spatial" not in adata.obsm and "X_spatial" not in adata.obsm:
                return {
                    "error": "Spatial task requires spatial coordinates in adata.obsm['spatial'] or adata.obsm['X_spatial']",
                    "suggestion": "Add spatial coordinates or use 'embed' task instead",
                }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load Nicheformer model: {str(e)}"}

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
                task=task,
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
                "has_spatial": "spatial" in adata.obsm or "X_spatial" in adata.obsm,
                "device": device,
            },
        }

    def _load_model(self, device: str):
        """Load Nicheformer model."""
        if self._model is not None:
            return

        if not self._nicheformer_installed:
            raise ImportError("Nicheformer package not installed")

        import torch

        # Try to import nicheformer model class
        try:
            from nicheformer.models import NicheformerModel
        except ImportError:
            try:
                from nicheformer import Nicheformer as NicheformerModel
            except ImportError:
                raise ImportError(
                    "Could not import NicheformerModel. "
                    "Ensure nicheformer is properly installed."
                )

        checkpoint_path = Path(self.checkpoint_dir) if self.checkpoint_dir else None

        if checkpoint_path is None:
            raise ValueError(
                "Nicheformer checkpoint directory not specified. "
                "Download from: https://data.mendeley.com/preview/87gm9hrgm8"
            )

        # Find checkpoint file
        ckpt_file = self._find_checkpoint(checkpoint_path, [".ckpt", ".pt", ".pth"])

        # Load model
        try:
            # Try PyTorch Lightning loading
            self._model = NicheformerModel.load_from_checkpoint(str(ckpt_file))
        except (AttributeError, TypeError):
            # Fallback to standard PyTorch loading
            self._model = NicheformerModel()
            state_dict = torch.load(str(ckpt_file), map_location=device)
            if "state_dict" in state_dict:
                state_dict = state_dict["state_dict"]
            self._model.load_state_dict(state_dict)

        self._model = self._model.to(device)
        self._model.eval()

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for Nicheformer.

        Nicheformer uses:
        - Log-normalized expression
        - Spatial coordinates (for spatial task)
        """
        import scanpy as sc

        adata = adata.copy()

        if adata.raw is not None:
            adata = adata.raw.to_adata()

        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)

        return adata

    def _run_inference(
        self,
        adata,
        device: str,
        batch_size: int,
        task: TaskType = TaskType.EMBED,
    ) -> np.ndarray:
        """Run Nicheformer inference to generate embeddings."""
        try:
            return self._run_nicheformer_direct(adata, device, batch_size, task)
        except Exception as e:
            raise RuntimeError(f"Nicheformer inference failed: {str(e)}")

    def _run_nicheformer_direct(
        self,
        adata,
        device: str,
        batch_size: int,
        task: TaskType,
    ) -> np.ndarray:
        """Run Nicheformer inference using direct Python API."""
        import torch
        from scipy.sparse import issparse

        X = adata.X.toarray() if issparse(adata.X) else adata.X

        # Get spatial coordinates if available
        spatial = None
        if task == TaskType.SPATIAL or "spatial" in adata.obsm:
            spatial = adata.obsm.get("spatial", adata.obsm.get("X_spatial"))

        embeddings = []
        n_cells = X.shape[0]

        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch_end = min(i + batch_size, n_cells)
                batch_X = torch.tensor(
                    X[i:batch_end], dtype=torch.float32, device=device
                )

                # Prepare spatial context if available
                batch_spatial = None
                if spatial is not None:
                    batch_spatial = torch.tensor(
                        spatial[i:batch_end], dtype=torch.float32, device=device
                    )

                # Run inference based on model API
                if hasattr(self._model, "encode"):
                    if batch_spatial is not None:
                        emb = self._model.encode(batch_X, spatial=batch_spatial)
                    else:
                        emb = self._model.encode(batch_X)
                elif hasattr(self._model, "get_embeddings"):
                    if batch_spatial is not None:
                        emb = self._model.get_embeddings(batch_X, spatial=batch_spatial)
                    else:
                        emb = self._model.get_embeddings(batch_X)
                elif hasattr(self._model, "forward"):
                    # Use forward pass
                    if batch_spatial is not None:
                        output = self._model(batch_X, spatial=batch_spatial)
                    else:
                        output = self._model(batch_X)

                    # Extract embeddings from output
                    if isinstance(output, dict):
                        emb = output.get("embeddings", output.get("z", output.get("cell_emb")))
                    elif isinstance(output, tuple):
                        emb = output[0]
                    else:
                        emb = output
                else:
                    raise RuntimeError(
                        "Could not find embedding method in Nicheformer model"
                    )

                if isinstance(emb, torch.Tensor):
                    emb = emb.cpu().numpy()
                embeddings.append(emb)

        return np.vstack(embeddings)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_nicheformer"
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

        # Sample gene names for species detection
        gene_names = adata.var_names[:50].tolist()
        human_markers = {"ACTB", "GAPDH", "CD4", "CD8A"}
        mouse_markers = {"Actb", "Gapdh", "Cd4", "Cd8a"}

        gene_set = set(gene_names)
        if len(human_markers & gene_set) > len(mouse_markers & gene_set):
            return "human"
        elif len(mouse_markers & gene_set) > 0:
            return "mouse"

        return "human"  # Default
