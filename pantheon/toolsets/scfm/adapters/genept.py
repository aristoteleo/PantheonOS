"""
GenePT Adapter

GenePT uses GPT-3.5 embeddings of gene descriptions to create cell embeddings.
It's an API-based approach that doesn't require local GPU.

Reference: https://github.com/yiqunchen/GenePT
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional
import os

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_openai_available() -> tuple[bool, Optional[str]]:
    """Check if OpenAI API is available."""
    try:
        import openai
        api_key = os.environ.get("OPENAI_API_KEY")
        return api_key is not None, api_key
    except ImportError:
        return False, None


class GenePTAdapter(BaseAdapter):
    """
    Adapter for GenePT embedding approach.

    Supports:
    - embed: Cell embeddings via GPT-3.5 gene descriptions (1536-dim)

    Key Features:
    - Uses GPT-3.5 embeddings of gene descriptions
    - API-based (no local GPU required)
    - Human only
    - Leverages biomedical knowledge in LLMs

    Requirements:
    - OpenAI API key
    - openai Python package
    - Internet connection
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("genept")
        if spec is None:
            raise ValueError("GenePT model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._openai_available, self._api_key = _check_openai_available()

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 32,
        api_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run GenePT model for embedding task.

        Args:
            task: TaskType.EMBED
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (unused)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use (ignored, API-based)
            batch_size: Batch size for API calls (default: 32)
            api_key: OpenAI API key (or use OPENAI_API_KEY env var)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED]
        if task not in supported_tasks:
            return {
                "error": f"GenePT does not support task '{task.value}'",
                "supported_tasks": ["embed"],
            }

        # Check for OpenAI availability
        effective_api_key = api_key or self._api_key
        if not effective_api_key:
            return {
                "error": "OpenAI API key not found",
                "suggestion": "Set OPENAI_API_KEY environment variable or pass api_key parameter",
                "install": "pip install openai && export OPENAI_API_KEY=<your_key>",
            }

        # Check for openai package
        try:
            import openai
        except ImportError:
            return {
                "error": "openai package not installed",
                "install": "pip install openai",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (GenePT is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"GenePT only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Preprocess
        try:
            processed_adata = self._preprocess(adata, task)
        except Exception as e:
            return {"error": f"Preprocessing failed: {str(e)}"}

        # Run inference
        try:
            embeddings = self._run_inference(
                processed_adata,
                batch_size=batch_size,
                api_key=effective_api_key,
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
                "method": "api",
            },
        }

    def _load_model(self, device: str):
        """GenePT is API-based, no model loading needed."""
        pass  # No-op for API-based model

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for GenePT.

        GenePT needs normalized expression for gene weighting.
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
        batch_size: int,
        api_key: str,
    ) -> np.ndarray:
        """Run GenePT inference to generate embeddings."""
        try:
            return self._run_genept_api(adata, batch_size, api_key)
        except Exception as e:
            raise RuntimeError(f"GenePT inference failed: {str(e)}")

    def _run_genept_api(
        self,
        adata,
        batch_size: int,
        api_key: str,
    ) -> np.ndarray:
        """Run GenePT using pre-computed gene embeddings."""
        # GenePT approach:
        # 1. Load pre-computed GPT embeddings for gene descriptions
        # 2. Aggregate gene embeddings weighted by expression
        # 3. Return cell-level embeddings

        from scipy.sparse import issparse

        # Load pre-computed gene embeddings
        gene_embeddings, gene_names = self._load_gene_embeddings()

        # Get expression matrix
        X = adata.X.toarray() if issparse(adata.X) else adata.X

        # Map adata genes to embedding genes
        adata_genes = list(adata.var_names)
        gene_to_idx = {g.upper(): i for i, g in enumerate(gene_names)}

        # Find matching genes
        matched_indices = []
        matched_emb_indices = []
        for i, gene in enumerate(adata_genes):
            gene_upper = gene.upper()
            if gene_upper in gene_to_idx:
                matched_indices.append(i)
                matched_emb_indices.append(gene_to_idx[gene_upper])

        if len(matched_indices) < 100:
            raise ValueError(
                f"Only {len(matched_indices)} genes matched between data and embeddings. "
                f"GenePT requires gene symbols. Check gene ID format."
            )

        # Extract matched expression and embeddings
        X_matched = X[:, matched_indices]  # (n_cells, n_matched_genes)
        emb_matched = gene_embeddings[matched_emb_indices]  # (n_matched_genes, 1536)

        # Compute cell embeddings as weighted average
        # cell_emb[i] = sum(expr[i,g] * gene_emb[g]) / sum(expr[i,g])
        embeddings = []
        n_cells = X_matched.shape[0]

        for i in range(0, n_cells, batch_size):
            batch_end = min(i + batch_size, n_cells)
            batch_X = X_matched[i:batch_end]  # (batch, n_genes)

            # Normalize expression per cell (avoid division by zero)
            row_sums = batch_X.sum(axis=1, keepdims=True)
            row_sums = np.maximum(row_sums, 1e-10)
            batch_X_norm = batch_X / row_sums

            # Weighted sum: (batch, n_genes) @ (n_genes, 1536) = (batch, 1536)
            batch_emb = batch_X_norm @ emb_matched
            embeddings.append(batch_emb)

        return np.vstack(embeddings)

    def _load_gene_embeddings(self) -> tuple[np.ndarray, list[str]]:
        """Load pre-computed gene embeddings from checkpoint directory."""
        if self.checkpoint_dir is None:
            raise ValueError(
                "GenePT requires pre-computed gene embeddings. "
                "Download from: https://github.com/yiqunchen/GenePT"
            )

        checkpoint_path = Path(self.checkpoint_dir)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint directory not found: {self.checkpoint_dir}"
            )

        # Look for embedding files
        emb_file = None
        names_file = None

        for pattern in ["gene_embeddings.npy", "embeddings.npy", "*.npy"]:
            matches = list(checkpoint_path.glob(pattern))
            if matches:
                emb_file = matches[0]
                break

        for pattern in ["gene_names.txt", "genes.txt", "*.txt"]:
            matches = list(checkpoint_path.glob(pattern))
            if matches:
                names_file = matches[0]
                break

        # Also check for combined pickle/npz format
        if emb_file is None:
            for pattern in ["*.pkl", "*.pickle", "*.npz"]:
                matches = list(checkpoint_path.glob(pattern))
                if matches:
                    return self._load_combined_embeddings(matches[0])

        if emb_file is None:
            raise FileNotFoundError(
                f"No embedding file found in {checkpoint_path}. "
                "Expected: gene_embeddings.npy or similar"
            )

        # Load embeddings
        embeddings = np.load(emb_file)

        # Load gene names
        if names_file is not None:
            with open(names_file, "r") as f:
                gene_names = [line.strip() for line in f]
        else:
            # Try to load from npz if it contains both
            raise FileNotFoundError(
                f"No gene names file found in {checkpoint_path}. "
                "Expected: gene_names.txt or similar"
            )

        return embeddings, gene_names

    def _load_combined_embeddings(self, filepath: Path) -> tuple[np.ndarray, list[str]]:
        """Load embeddings from combined file (pkl/npz)."""
        import pickle

        if filepath.suffix in [".pkl", ".pickle"]:
            with open(filepath, "rb") as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                # Assume dict format: {gene_name: embedding}
                gene_names = list(data.keys())
                embeddings = np.array([data[g] for g in gene_names])
                return embeddings, gene_names
            else:
                raise ValueError(f"Unknown pickle format in {filepath}")

        elif filepath.suffix == ".npz":
            data = np.load(filepath, allow_pickle=True)
            if "embeddings" in data and "gene_names" in data:
                return data["embeddings"], list(data["gene_names"])
            else:
                raise ValueError(f"Expected 'embeddings' and 'gene_names' keys in {filepath}")

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_genept"
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

        # GenePT is human-only, default to human
        return "human"
