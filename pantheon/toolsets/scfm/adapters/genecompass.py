"""
GeneCompass Adapter

GeneCompass is a large-scale foundation model for single-cell transcriptomics
trained on 120M cells with prior knowledge enhancement.

Reference: https://github.com/xCompass-AI/GeneCompass
Paper: bioRxiv 2023
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, GeneIDScheme, get_registry
from .base import BaseAdapter


def _check_genecompass_installed() -> tuple[bool, Optional[str]]:
    """Check if GeneCompass package is installed and return its location."""
    try:
        import genecompass
        return True, str(Path(genecompass.__file__).parent)
    except ImportError:
        return False, None


class GeneCompassAdapter(BaseAdapter):
    """
    Adapter for GeneCompass foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Trained on 120M cells (largest scale)
    - Prior-knowledge enhanced (gene regulatory networks)
    - Human and mouse support
    - Cross-species transfer learning

    Requirements:
    - GeneCompass package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from GeneCompass repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("genecompass")
        if spec is None:
            raise ValueError("GeneCompass model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._genecompass_installed, self._genecompass_path = _check_genecompass_installed()

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
        Run GeneCompass model for embedding or integration task.

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
                "error": f"GeneCompass does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # GeneCompass requires GPU
        if device == "cpu":
            return {
                "error": "GeneCompass requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for GeneCompass package
        if not self._genecompass_installed:
            return {
                "error": "GeneCompass package not installed",
                "install": "git clone https://github.com/xCompass-AI/GeneCompass && pip install -e GeneCompass",
                "documentation": "https://github.com/xCompass-AI/GeneCompass",
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
                "error": f"GeneCompass supports human and mouse, detected: '{species}'",
                "suggestion": "Use UCE for other species",
                "supported": ["human", "mouse"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load GeneCompass model: {str(e)}"}

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
        """Load GeneCompass model."""
        if self._model is not None:
            return

        if not self._genecompass_installed:
            raise ImportError("GeneCompass package not installed")

        self._model = "ready"

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for GeneCompass.

        GeneCompass uses:
        - Log-normalized expression
        - Prior knowledge integration (handled by model)
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
        """Run GeneCompass inference to generate embeddings."""
        try:
            return self._run_genecompass_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"GeneCompass inference failed: {str(e)}")

    def _run_genecompass_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run GeneCompass inference using direct Python API."""
        import torch
        from scipy.sparse import issparse

        # Check for checkpoint
        if self.checkpoint_dir is None:
            raise ValueError(
                "GeneCompass checkpoint directory not specified. "
                "Download from: https://github.com/xCompass-AI/GeneCompass (SciDB link)"
            )

        checkpoint_path = Path(self.checkpoint_dir)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint directory not found: {self.checkpoint_dir}"
            )

        # Detect species for proper tokenization
        species = self._detect_species(adata)

        # Try to use genecompass package if available
        if self._genecompass_installed:
            return self._run_genecompass_package(
                adata, device, batch_size, checkpoint_path, species
            )

        # Fallback: try manual model loading
        return self._run_genecompass_manual(
            adata, device, batch_size, checkpoint_path, species
        )

    def _run_genecompass_package(
        self,
        adata,
        device: str,
        batch_size: int,
        checkpoint_path: Path,
        species: str,
    ) -> np.ndarray:
        """Run GeneCompass using the official package."""
        import torch

        try:
            # GeneCompass package API (based on repo structure)
            from genecompass import GeneCompassModel
            from genecompass.data import DataProcessor

            # Load pre-trained model (Small or Base)
            model = GeneCompassModel.from_pretrained(str(checkpoint_path))
            model.to(device)
            model.eval()

            # Create data processor with species-specific tokenization
            processor = DataProcessor(species=species)

            # Tokenize the data
            tokenized = processor.tokenize(adata)

            # Generate embeddings
            embeddings = []
            n_cells = tokenized.shape[0] if hasattr(tokenized, "shape") else len(tokenized)

            with torch.no_grad():
                for i in range(0, n_cells, batch_size):
                    batch = tokenized[i : i + batch_size]
                    if not isinstance(batch, torch.Tensor):
                        batch = torch.tensor(batch, device=device)
                    else:
                        batch = batch.to(device)

                    emb = model.encode(batch)
                    embeddings.append(emb.cpu().numpy())

            return np.vstack(embeddings)

        except (ImportError, AttributeError) as e:
            # Try alternative API
            try:
                from genecompass.model import GeneCompass
                from genecompass.tokenizer import GeneTokenizer

                # Load tokenizer with species-specific vocabulary
                tokenizer = GeneTokenizer(species=species)

                # Load model
                model = GeneCompass.load(str(checkpoint_path))
                model.to(device)
                model.eval()

                # Tokenize and embed
                return self._tokenize_and_embed(
                    model, tokenizer, adata, device, batch_size
                )

            except Exception as inner_e:
                raise RuntimeError(
                    f"GeneCompass package installed but API usage failed: {str(e)}. "
                    f"Inner error: {str(inner_e)}. "
                    "Please check genecompass version compatibility."
                )

    def _run_genecompass_manual(
        self,
        adata,
        device: str,
        batch_size: int,
        checkpoint_path: Path,
        species: str,
    ) -> np.ndarray:
        """Run GeneCompass with manual model loading (fallback)."""
        import torch
        from scipy.sparse import issparse

        # Find checkpoint file
        ckpt_file = None
        for pattern in ["*.pth", "*.pt", "genecompass*.pth", "model*.pth"]:
            matches = list(checkpoint_path.glob(pattern))
            if matches:
                ckpt_file = matches[0]
                break

        # Also check for Small/Base model variants
        if ckpt_file is None:
            for variant in ["GeneCompass_Small", "GeneCompass_Base"]:
                variant_path = checkpoint_path / variant
                if variant_path.exists():
                    for pattern in ["*.pth", "*.pt"]:
                        matches = list(variant_path.glob(pattern))
                        if matches:
                            ckpt_file = matches[0]
                            break
                    if ckpt_file:
                        break

        if ckpt_file is None:
            raise FileNotFoundError(
                f"No checkpoint file (*.pth, *.pt) found in: {checkpoint_path}. "
                "Download GeneCompass_Small or GeneCompass_Base from: "
                "https://github.com/xCompass-AI/GeneCompass"
            )

        # Look for prior knowledge file
        prior_file = None
        for pattern in ["*tokens*.pickle", "*prior*.pickle"]:
            matches = list(checkpoint_path.glob(f"**/{pattern}"))
            if matches:
                prior_file = matches[0]
                break

        # GeneCompass needs the genecompass package for proper tokenization
        raise NotImplementedError(
            "GeneCompass manual inference requires the genecompass package. "
            "Install with: git clone https://github.com/xCompass-AI/GeneCompass && "
            "pip install -r requirements.txt"
        )

    def _tokenize_and_embed(
        self,
        model,
        tokenizer,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Tokenize AnnData and generate embeddings."""
        import torch
        from scipy.sparse import issparse

        X = adata.X
        if issparse(X):
            X = X.toarray()

        gene_names = adata.var_names.tolist()

        # Tokenize cells
        tokens = tokenizer.tokenize(X, gene_names)

        n_cells = len(tokens) if isinstance(tokens, list) else tokens.shape[0]
        embeddings = []

        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch = tokens[i : i + batch_size]
                if not isinstance(batch, torch.Tensor):
                    batch = torch.tensor(batch, dtype=torch.long, device=device)
                else:
                    batch = batch.to(device)

                # Get embeddings
                if hasattr(model, "encode"):
                    emb = model.encode(batch)
                elif hasattr(model, "get_cell_embeddings"):
                    emb = model.get_cell_embeddings(batch)
                else:
                    # Forward pass and extract embeddings
                    output = model(batch)
                    if isinstance(output, dict):
                        emb = output.get("cell_embedding", output.get("embeddings", output))
                    elif isinstance(output, tuple):
                        emb = output[0]
                    else:
                        emb = output

                embeddings.append(emb.cpu().numpy())

        return np.vstack(embeddings)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_genecompass"
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
