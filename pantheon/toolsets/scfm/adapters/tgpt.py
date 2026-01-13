"""
tGPT Adapter

tGPT is a transcriptome-oriented GPT model using next-token prediction
for single-cell RNA-seq data. Trained on ~57M cells with a capacity-focused
architecture.

Reference: https://github.com/deeplearningplus/tGPT
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_tgpt_installed() -> tuple[bool, Optional[str]]:
    """Check if tGPT/transformers is available."""
    try:
        # tGPT uses HuggingFace transformers with GPT2 architecture
        from transformers import GPT2Model, PreTrainedTokenizerFast

        return True, "transformers"
    except ImportError:
        return False, None


class TGPTAdapter(BaseAdapter):
    """
    Adapter for tGPT foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space

    Key Features:
    - Next-token prediction architecture
    - Trained on ~57M cells
    - Human only
    - Capacity-focused design

    Requirements:
    - tGPT package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from tGPT repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("tgpt")
        if spec is None:
            raise ValueError("tGPT model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._tgpt_installed, self._tgpt_path = _check_tgpt_installed()

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
        Run tGPT model for embedding or integration task.

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
                "error": f"tGPT does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # tGPT requires GPU
        if device == "cpu":
            return {
                "error": "tGPT requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for tGPT package
        if not self._tgpt_installed:
            return {
                "error": "tGPT package not installed",
                "install": "git clone https://github.com/deeplearningplus/tGPT && pip install -e tGPT",
                "documentation": "https://github.com/deeplearningplus/tGPT",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (tGPT is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"tGPT only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load tGPT model: {str(e)}"}

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
        """Load tGPT model from HuggingFace."""
        if self._model is not None:
            return

        if not self._tgpt_installed:
            raise ImportError(
                "transformers package not installed. "
                "Install with: pip install transformers"
            )

        from transformers import GPT2Model, PreTrainedTokenizerFast

        # tGPT checkpoint on HuggingFace
        model_id = "lixiangchun/transcriptome-gpt-1024-8-16-64"

        try:
            self._tokenizer = PreTrainedTokenizerFast.from_pretrained(model_id)
            # Load GPT2 model with hidden states output
            self._model = GPT2Model.from_pretrained(
                model_id, output_hidden_states=True
            )
            self._model = self._model.to(device)
            self._model.eval()
        except Exception as e:
            raise ValueError(
                f"Failed to load tGPT model from HuggingFace: {str(e)}. "
                f"Model ID: {model_id}"
            )

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for tGPT.

        tGPT uses standard RNA-seq preprocessing.
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
        """Run tGPT inference to generate embeddings."""
        try:
            return self._run_tgpt_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"tGPT inference failed: {str(e)}")

    def _run_tgpt_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run tGPT inference using HuggingFace transformers."""
        import re
        import torch
        from scipy.sparse import issparse

        # tGPT expects gene names ranked by expression as input
        # Convert expression matrix to gene rankings per cell

        X = adata.X.toarray() if issparse(adata.X) else adata.X
        gene_names = list(adata.var_names)

        # Normalize gene names (replace - and . with _)
        gene_names_normalized = [re.sub(r"[-.]", "_", g) for g in gene_names]

        embeddings = []
        n_cells = X.shape[0]
        max_length = 64  # tGPT uses 64 tokens max

        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch_end = min(i + batch_size, n_cells)
                batch_sequences = []

                for j in range(i, batch_end):
                    # Get expression values for this cell
                    expr = X[j]

                    # Rank genes by expression (descending)
                    ranked_indices = np.argsort(expr)[::-1]

                    # Take top genes and create sequence of gene names
                    top_genes = [
                        gene_names_normalized[idx]
                        for idx in ranked_indices[:max_length]
                        if expr[idx] > 0
                    ]
                    sequence = " ".join(top_genes)
                    batch_sequences.append(sequence)

                # Tokenize batch
                batch_tokens = self._tokenizer(
                    batch_sequences,
                    max_length=max_length,
                    truncation=True,
                    padding=True,
                    return_tensors="pt",
                )
                batch_tokens = {k: v.to(device) for k, v in batch_tokens.items()}

                # Run model
                output = self._model(**batch_tokens)

                # Get embeddings from last hidden state
                # Average across sequence (excluding padding)
                hidden_states = output.last_hidden_state  # [batch, seq_len, hidden]
                attention_mask = batch_tokens["attention_mask"]

                # Masked mean pooling
                mask_expanded = attention_mask.unsqueeze(-1).expand(hidden_states.size())
                sum_hidden = (hidden_states * mask_expanded).sum(dim=1)
                sum_mask = mask_expanded.sum(dim=1).clamp(min=1e-9)
                batch_emb = (sum_hidden / sum_mask).cpu().numpy()

                embeddings.append(batch_emb)

        return np.vstack(embeddings)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """Write embeddings to AnnData."""
        output_keys = []

        key = self.spec.output_keys.embedding_key  # "X_tgpt"
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

        # tGPT is human-only, default to human
        return "human"
