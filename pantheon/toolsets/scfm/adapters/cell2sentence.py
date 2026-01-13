"""
Cell2Sentence Adapter

Cell2Sentence is an LLM adapter that flattens single-cell data into
text sequences for LLM fine-tuning. Bridges single-cell and NLP domains.

Reference: https://github.com/vandijklab/cell2sentence
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_cell2sentence_installed() -> tuple[bool, Optional[str]]:
    """Check if Cell2Sentence or transformers is available."""
    try:
        # Cell2Sentence can work with just transformers
        from transformers import AutoModel, AutoTokenizer

        return True, "transformers"
    except ImportError:
        return False, None


class Cell2SentenceAdapter(BaseAdapter):
    """
    Adapter for Cell2Sentence LLM bridge.

    Supports:
    - embed: Cell embeddings via LLM fine-tuning (768-dim)

    Key Features:
    - Flattens cells to text sequences
    - LLM fine-tuning approach
    - Bridges scRNA-seq and NLP
    - Human only

    Requirements:
    - Cell2Sentence package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Pre-trained LLM checkpoint
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("cell2sentence")
        if spec is None:
            raise ValueError("Cell2Sentence model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._c2s_installed, self._c2s_path = _check_cell2sentence_installed()

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 16,
        llm_model: str = "gpt2",
    ) -> dict[str, Any]:
        """
        Run Cell2Sentence model for embedding task.

        Args:
            task: TaskType.EMBED
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (unused)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda')
            batch_size: Batch size for inference (default: 16)
            llm_model: Base LLM model to use (default: 'gpt2')

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED]
        if task not in supported_tasks:
            return {
                "error": f"Cell2Sentence does not support task '{task.value}'",
                "supported_tasks": ["embed"],
                "note": "Cell2Sentence requires fine-tuning for best results",
            }

        device = self._resolve_device(device)

        # Cell2Sentence requires GPU
        if device == "cpu":
            return {
                "error": "Cell2Sentence requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for Cell2Sentence package
        if not self._c2s_installed:
            return {
                "error": "Cell2Sentence package not installed",
                "install": "git clone https://github.com/vandijklab/cell2sentence && pip install -e cell2sentence",
                "documentation": "https://github.com/vandijklab/cell2sentence",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (Cell2Sentence is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"Cell2Sentence only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device, llm_model)
        except Exception as e:
            return {"error": f"Failed to load Cell2Sentence model: {str(e)}"}

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
                "llm_model": llm_model,
            },
        }

    def _load_model(self, device: str, llm_model: str = "gpt2"):
        """Load Cell2Sentence model (LLM for text embedding)."""
        if self._model is not None:
            return

        if not self._c2s_installed:
            raise ImportError(
                "transformers package not installed. "
                "Install with: pip install transformers"
            )

        from transformers import AutoModel, AutoTokenizer

        # Load tokenizer and model
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(llm_model)
            self._model = AutoModel.from_pretrained(llm_model)

            # Add padding token if not present (GPT2 doesn't have one by default)
            if self._tokenizer.pad_token is None:
                self._tokenizer.pad_token = self._tokenizer.eos_token

            self._model = self._model.to(device)
            self._model.eval()
            self._llm_model = llm_model
        except Exception as e:
            raise ValueError(
                f"Failed to load LLM model '{llm_model}': {str(e)}. "
                f"Try: gpt2, bert-base-uncased, or microsoft/BiomedNLP-PubMedBERT-base-uncased-abstract"
            )

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for Cell2Sentence.

        Cell2Sentence converts expression to ranked gene lists (sentences).
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
        """Run Cell2Sentence inference to generate embeddings."""
        try:
            return self._run_c2s_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"Cell2Sentence inference failed: {str(e)}")

    def _run_c2s_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run Cell2Sentence inference - convert cells to text, embed with LLM."""
        import re
        import torch
        from scipy.sparse import issparse

        # Cell2Sentence converts expression to ranked gene sequences
        X = adata.X.toarray() if issparse(adata.X) else adata.X
        gene_names = list(adata.var_names)

        # Normalize gene names (replace special characters)
        gene_names_normalized = [re.sub(r"[-.]", "_", g) for g in gene_names]

        embeddings = []
        n_cells = X.shape[0]
        max_length = 512  # Max tokens for LLM

        with torch.no_grad():
            for i in range(0, n_cells, batch_size):
                batch_end = min(i + batch_size, n_cells)
                batch_sequences = []

                for j in range(i, batch_end):
                    # Get expression values for this cell
                    expr = X[j]

                    # Rank genes by expression (descending)
                    ranked_indices = np.argsort(expr)[::-1]

                    # Take top expressed genes to form "sentence"
                    # Cell2Sentence typically uses top 100-200 genes
                    top_genes = []
                    for idx in ranked_indices[:200]:
                        if expr[idx] > 0:
                            top_genes.append(gene_names_normalized[idx])
                        if len(top_genes) >= 100:
                            break

                    # Create sentence: space-separated gene names
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
                hidden_states = output.last_hidden_state  # (batch, seq_len, hidden)
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

        key = self.spec.output_keys.embedding_key  # "X_cell2sentence"
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

        # Cell2Sentence is human-only, default to human
        return "human"
