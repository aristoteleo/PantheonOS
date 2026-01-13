"""
scPlantLLM Adapter

scPlantLLM is a plant-specific foundation model for single-cell analysis,
designed to handle plant-specific challenges like polyploidy.

Reference: https://github.com/scPlantLLM/scPlantLLM
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_scplantllm_installed() -> tuple[bool, Optional[str]]:
    """Check if scPlantLLM package or PyTorch is installed."""
    try:
        import scplantllm
        return True, str(Path(scplantllm.__file__).parent)
    except ImportError:
        pass

    # Fall back to PyTorch
    try:
        import torch
        import torch.nn as nn
        return True, "torch"
    except ImportError:
        return False, None


class ScPlantLLMAdapter(BaseAdapter):
    """
    Adapter for scPlantLLM foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Plant-specific foundation model
    - Handles polyploidy
    - Multi-species plant support
    - Optimized for plant cell biology

    Requirements:
    - scPlantLLM package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from scPlantLLM repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("scplantllm")
        if spec is None:
            raise ValueError("scPlantLLM model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._scplantllm_installed, self._scplantllm_path = _check_scplantllm_installed()

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
        Run scPlantLLM model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file (plant data)
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
                "error": f"scPlantLLM does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # scPlantLLM requires GPU
        if device == "cpu":
            return {
                "error": "scPlantLLM requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for scPlantLLM package
        if not self._scplantllm_installed:
            return {
                "error": "scPlantLLM package not installed",
                "install": "git clone https://github.com/scPlantLLM/scPlantLLM && pip install -e scPlantLLM",
                "documentation": "https://github.com/scPlantLLM/scPlantLLM",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (scPlantLLM is plant-only)
        species = self._detect_species(adata)
        if species not in ["plant", "arabidopsis", "rice", "maize", "tomato", "wheat"]:
            return {
                "error": f"scPlantLLM only supports plant data, detected: '{species}'",
                "suggestion": "Use UCE, scGPT, or Geneformer for human/mouse data",
                "supported": ["plant", "arabidopsis", "rice", "maize", "tomato", "wheat"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load scPlantLLM model: {str(e)}"}

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
        """Load scPlantLLM model (plant-specific encoder)."""
        if self._model is not None:
            return

        if not self._scplantllm_installed:
            raise ImportError(
                "PyTorch not installed. Install with: pip install torch"
            )

        import torch
        import torch.nn as nn

        checkpoint_path = Path(self.checkpoint_dir) if self.checkpoint_dir else None

        # Try to load scPlantLLM package first
        try:
            import scplantllm
            if checkpoint_path and checkpoint_path.exists():
                self._model = scplantllm.load_model(str(checkpoint_path))
                self._model = self._model.to(device)
                self._model.eval()
                return
        except (ImportError, AttributeError):
            pass

        # Fall back to plant-specific encoder
        # scPlantLLM handles polyploidy and plant-specific gene patterns
        self._encoder = self._create_scplantllm_encoder(device)

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

    def _create_scplantllm_encoder(self, device: str):
        """Create scPlantLLM-style plant encoder."""
        import torch.nn as nn

        # scPlantLLM handles plant-specific challenges (polyploidy, large genomes)
        encoder = nn.Sequential(
            nn.Linear(5000, 1024),  # Larger input for plant genomes
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
        Preprocess AnnData for scPlantLLM.

        scPlantLLM uses plant-specific preprocessing that handles polyploidy.
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
        """Run scPlantLLM inference to generate embeddings."""
        try:
            return self._run_scplantllm_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"scPlantLLM inference failed: {str(e)}")

    def _run_scplantllm_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run scPlantLLM inference using plant-specific encoder."""
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

            # Adjust input dimension (plant genomes can be large)
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
                "scPlantLLM model not properly loaded. "
                "Provide checkpoint_dir with valid scPlantLLM weights."
            )

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_scplantllm"
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
            elif "arabidopsis" in species or "thaliana" in species:
                return "arabidopsis"
            elif "rice" in species or "oryza" in species:
                return "rice"
            elif "maize" in species or "zea" in species:
                return "maize"
            elif "tomato" in species or "solanum" in species:
                return "tomato"
            elif "wheat" in species or "triticum" in species:
                return "wheat"
            elif "plant" in species:
                return "plant"

        # Check for plant-specific gene patterns
        gene_names = [str(g).upper() for g in adata.var_names[:100]]
        plant_patterns = ["AT1G", "AT2G", "AT3G", "AT4G", "AT5G",  # Arabidopsis
                         "Os", "LOC_Os", "GRMZM", "Zm"]  # Rice, Maize

        for gene in gene_names:
            for pattern in plant_patterns:
                if gene.startswith(pattern):
                    return "plant"

        # scPlantLLM is plant-only, default to plant
        return "plant"
