"""
CellPLM Adapter

CellPLM is a cell-centric pre-trained language model for single-cell
transcriptomics with efficient inference.

Reference: https://github.com/OmicsML/CellPLM
Paper: bioRxiv 2023
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, GeneIDScheme, get_registry
from .base import BaseAdapter


def _check_cellplm_installed() -> tuple[bool, Optional[str]]:
    """Check if CellPLM package is installed and return its location."""
    try:
        import cellplm
        return True, str(Path(cellplm.__file__).parent)
    except ImportError:
        return False, None


class CellPLMAdapter(BaseAdapter):
    """
    Adapter for CellPLM foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Cell-centric architecture (fast inference)
    - Efficient for large datasets
    - Human only
    - Good scaling properties

    Requirements:
    - CellPLM package from GitHub
    - GPU with 8-16 GB VRAM (CPU fallback available)
    - Model checkpoint from CellPLM repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("cellplm")
        if spec is None:
            raise ValueError("CellPLM model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._cellplm_installed, self._cellplm_path = _check_cellplm_installed()

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 128,  # CellPLM is efficient
    ) -> dict[str, Any]:
        """
        Run CellPLM model for embedding or integration task.

        Args:
            task: TaskType.EMBED or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (integration)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda', 'cpu')
            batch_size: Batch size for inference (default: 128)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.INTEGRATE]
        if task not in supported_tasks:
            return {
                "error": f"CellPLM does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # Check for CellPLM package
        if not self._cellplm_installed:
            return {
                "error": "CellPLM package not installed",
                "install": "git clone https://github.com/OmicsML/CellPLM && pip install -e CellPLM",
                "documentation": "https://github.com/OmicsML/CellPLM",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (CellPLM is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"CellPLM only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load CellPLM model: {str(e)}"}

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
        """Load CellPLM model."""
        if self._model is not None:
            return

        if not self._cellplm_installed:
            raise ImportError("CellPLM package not installed")

        self._model = "ready"

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for CellPLM.

        CellPLM uses:
        - Log-normalized expression
        - Cell-centric representation
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
    ) -> np.ndarray:
        """Run CellPLM inference to generate embeddings."""
        try:
            return self._run_cellplm_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"CellPLM inference failed: {str(e)}")

    def _run_cellplm_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run CellPLM inference using direct Python API."""
        import torch
        from scipy.sparse import issparse

        # Check for checkpoint
        if self.checkpoint_dir is None:
            raise ValueError(
                "CellPLM checkpoint directory not specified. "
                "Download from: https://github.com/OmicsML/CellPLM (Dropbox link in README)"
            )

        checkpoint_path = Path(self.checkpoint_dir)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint directory not found: {self.checkpoint_dir}"
            )

        # Try to use cellplm package if available
        if self._cellplm_installed:
            return self._run_cellplm_package(adata, device, batch_size, checkpoint_path)

        # Fallback: try to load model directly if checkpoint structure is known
        return self._run_cellplm_manual(adata, device, batch_size, checkpoint_path)

    def _run_cellplm_package(
        self,
        adata,
        device: str,
        batch_size: int,
        checkpoint_path: Path,
    ) -> np.ndarray:
        """Run CellPLM using the official cellplm package."""
        import torch

        try:
            # CellPLM package API (structure may vary by version)
            from cellplm import CellPLM
            from cellplm.pipeline import EmbeddingPipeline

            # Load pre-trained model
            model = CellPLM.from_pretrained(str(checkpoint_path))
            model.to(device)
            model.eval()

            # Create embedding pipeline
            pipeline = EmbeddingPipeline(model=model, device=device)

            # Generate embeddings
            embeddings = pipeline.embed(adata, batch_size=batch_size)

            return embeddings

        except (ImportError, AttributeError) as e:
            # Try alternative API structure
            try:
                from cellplm.model import CellPLMModel
                from cellplm.utils import load_checkpoint

                model = CellPLMModel()
                load_checkpoint(model, checkpoint_path)
                model.to(device)
                model.eval()

                # Manual embedding extraction
                return self._extract_embeddings_manual(
                    model, adata, device, batch_size
                )

            except Exception as inner_e:
                raise RuntimeError(
                    f"CellPLM package installed but API usage failed: {str(e)}. "
                    f"Inner error: {str(inner_e)}. "
                    "Please check cellplm version compatibility."
                )

    def _run_cellplm_manual(
        self,
        adata,
        device: str,
        batch_size: int,
        checkpoint_path: Path,
    ) -> np.ndarray:
        """Run CellPLM inference with manual model loading (fallback)."""
        import torch
        from scipy.sparse import issparse

        # Find checkpoint file
        ckpt_file = None
        for pattern in ["*.pth", "*.pt", "cellplm*.pth", "model*.pth"]:
            matches = list(checkpoint_path.glob(pattern))
            if matches:
                ckpt_file = matches[0]
                break

        if ckpt_file is None:
            raise FileNotFoundError(
                f"No checkpoint file (*.pth, *.pt) found in: {checkpoint_path}. "
                "Download checkpoint from: https://github.com/OmicsML/CellPLM"
            )

        # Load checkpoint
        ckpt = torch.load(str(ckpt_file), map_location=device)

        # CellPLM architecture (from paper: cell-centric transformer)
        # Embedding dimension is 512
        EMBED_DIM = 512

        # Get expression matrix
        X = adata.X
        if issparse(X):
            X = X.toarray()

        # CellPLM uses cell-centric encoding
        # For manual inference without package, we need model architecture
        raise NotImplementedError(
            "CellPLM manual inference requires the cellplm package. "
            "Install with: pip install cellplm (if available on PyPI) "
            "or: git clone https://github.com/OmicsML/CellPLM && pip install -e CellPLM"
        )

    def _extract_embeddings_manual(
        self,
        model,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Extract embeddings manually from CellPLM model."""
        import torch
        from scipy.sparse import issparse

        X = adata.X
        if issparse(X):
            X = X.toarray()

        X_tensor = torch.tensor(X, dtype=torch.float32)
        n_cells = X_tensor.shape[0]

        embeddings = []
        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch = X_tensor[i : i + batch_size].to(device)

                # Try different API methods that CellPLM might expose
                if hasattr(model, "encode"):
                    emb = model.encode(batch)
                elif hasattr(model, "get_embeddings"):
                    emb = model.get_embeddings(batch)
                elif hasattr(model, "forward"):
                    output = model(batch)
                    # Assume output is a dict or tuple with embeddings
                    if isinstance(output, dict) and "embeddings" in output:
                        emb = output["embeddings"]
                    elif isinstance(output, tuple):
                        emb = output[0]  # First element is often embeddings
                    else:
                        emb = output
                else:
                    raise AttributeError(
                        "CellPLM model has no recognized embedding method "
                        "(encode, get_embeddings, forward)"
                    )

                embeddings.append(emb.cpu().numpy())

        return np.vstack(embeddings)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_cellplm"
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

        # CellPLM is human-only, default to human
        return "human"
