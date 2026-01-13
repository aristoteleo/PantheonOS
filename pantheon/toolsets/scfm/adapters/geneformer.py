"""
Geneformer Adapter

Geneformer is a context-aware, attention-based deep learning model pretrained
on a large-scale corpus of ~30 million single cell transcriptomes. It uses
rank value encoding and transfer learning for diverse downstream tasks.

Reference: https://github.com/ctheodoris/Geneformer
Paper: Nature 2023
"""

import tempfile
import shutil
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import ModelSpec, TaskType, GeneIDScheme, get_registry
from .base import BaseAdapter


def _check_geneformer_installed() -> tuple[bool, Optional[str]]:
    """Check if Geneformer package is installed and return its location."""
    try:
        import geneformer
        return True, str(Path(geneformer.__file__).parent)
    except ImportError:
        return False, None


class GeneformerAdapter(BaseAdapter):
    """
    Adapter for Geneformer foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - integrate: Batch integration via shared embedding space
    - annotate: Cell type annotation (requires fine-tuning)

    Key Differences from UCE/scGPT:
    - Requires Ensembl gene IDs (ENSG...), NOT gene symbols
    - Two-stage inference: tokenization → embedding extraction
    - Human only (limited cross-species support)
    - CPU fallback available (slower but works)

    Requirements:
    - Geneformer package: pip install geneformer (or clone from GitHub)
    - Model checkpoint from HuggingFace: ctheodoris/Geneformer
    - git-lfs for downloading model weights
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("geneformer")
        if spec is None:
            raise ValueError("Geneformer model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._tokenizer = None
        self._geneformer_installed, self._geneformer_path = _check_geneformer_installed()

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
        Run Geneformer model for embedding, annotation, or integration task.

        Args:
            task: TaskType.EMBED, TaskType.ANNOTATE, or TaskType.INTEGRATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (integration)
            label_key: Column in .obs for cell type labels (annotation fine-tuning)
            device: Device to use ('auto', 'cuda', 'cpu')
            batch_size: Batch size for inference (default: 32)

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.INTEGRATE]
        if task not in supported_tasks:
            if task == TaskType.ANNOTATE:
                return {
                    "error": "Geneformer annotation requires fine-tuning",
                    "suggestion": "Use pre-trained embedding + classifier, or provide fine-tuned checkpoint",
                    "documentation": "https://geneformer.readthedocs.io/en/latest/",
                    "supported_tasks": ["embed", "integrate"],
                }
            return {
                "error": f"Geneformer does not support task '{task.value}'",
                "supported_tasks": ["embed", "integrate"],
            }

        device = self._resolve_device(device)

        # Check for Geneformer package
        if not self._geneformer_installed:
            return {
                "error": "Geneformer package not installed",
                "install": "pip install geneformer  # or clone from https://github.com/ctheodoris/Geneformer",
                "documentation": "https://geneformer.readthedocs.io/",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (Geneformer is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"Geneformer only supports human data, detected: '{species}'",
                "suggestion": "Use UCE for cross-species support",
                "supported": ["human"],
            }

        # Validate gene ID scheme
        gene_scheme = self._detect_gene_scheme(adata)
        if gene_scheme != GeneIDScheme.ENSEMBL:
            return {
                "error": f"Geneformer requires Ensembl gene IDs (ENSG...), detected: '{gene_scheme.value}'",
                "suggestion": "Convert gene symbols to Ensembl IDs using biomart or similar",
                "example": "Gene names should be like 'ENSG00000141510' (not 'TP53')",
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load Geneformer model: {str(e)}"}

        # Preprocess
        try:
            processed_adata = self._preprocess(adata, task)
        except Exception as e:
            return {"error": f"Preprocessing failed: {str(e)}"}

        # Run inference (two-stage: tokenize → embed)
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
                "gene_scheme": gene_scheme.value,
                "device": device,
            },
        }

    def _load_model(self, device: str):
        """
        Load Geneformer model and tokenizer.

        Geneformer uses:
        - TranscriptomeTokenizer for rank-based encoding
        - Pre-trained transformer model for embedding extraction
        """
        if self._model is not None:
            return

        if not self._geneformer_installed:
            raise ImportError("Geneformer package not installed")

        # Mark as ready - actual loading happens in inference
        self._model = "ready"
        self._tokenizer = "ready"

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for Geneformer.

        Geneformer requires:
        - Raw counts in .X
        - Ensembl gene IDs (ENSG...)
        - Version suffixes stripped (ENSG00000141510.15 → ENSG00000141510)
        """
        import scanpy as sc

        # Work on a copy
        adata = adata.copy()

        # Ensure we have raw counts
        if adata.raw is not None:
            adata = adata.raw.to_adata()

        # Strip Ensembl version suffixes if present
        # e.g., ENSG00000141510.15 → ENSG00000141510
        new_var_names = []
        for gene in adata.var_names:
            if gene.startswith("ENSG") and "." in gene:
                new_var_names.append(gene.split(".")[0])
            else:
                new_var_names.append(gene)
        adata.var_names = new_var_names

        # Geneformer expects total counts per cell for rank normalization
        # The tokenizer handles the actual normalization
        if "n_counts" not in adata.obs:
            adata.obs["n_counts"] = adata.X.sum(axis=1).A1 if hasattr(adata.X, "toarray") else adata.X.sum(axis=1)

        return adata

    def _run_inference(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Run Geneformer inference to generate embeddings.

        Geneformer uses a two-stage process:
        1. Tokenization: Convert expression to rank-based tokens
        2. Embedding: Extract hidden states from transformer

        Args:
            adata: Preprocessed AnnData object
            device: Device string (e.g., "cuda", "cpu")
            batch_size: Batch size for inference

        Returns:
            np.ndarray: Cell embeddings of shape (n_cells, 512)
        """
        try:
            return self._run_geneformer_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"Geneformer inference failed: {str(e)}")

    def _run_geneformer_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """
        Run Geneformer inference using direct Python API.

        Two-stage process:
        1. TranscriptomeTokenizer → tokenized dataset
        2. EmbExtractor → cell embeddings
        """
        try:
            from geneformer import TranscriptomeTokenizer, EmbExtractor
        except ImportError as e:
            raise ImportError(f"Geneformer dependencies missing: {e}")

        # Check for checkpoint
        if self.checkpoint_dir is None:
            raise ValueError(
                "Geneformer checkpoint directory not specified. "
                "Download from: https://huggingface.co/ctheodoris/Geneformer"
            )

        checkpoint_path = Path(self.checkpoint_dir)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint directory not found: {self.checkpoint_dir}"
            )

        # Create temporary directory for intermediate files
        with tempfile.TemporaryDirectory(prefix="geneformer_") as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            token_dir = temp_path / "tokenized"
            output_dir = temp_path / "output"

            input_dir.mkdir()
            token_dir.mkdir()
            output_dir.mkdir()

            # Save AnnData to input directory
            input_file = input_dir / "data.h5ad"
            adata.write(str(input_file))

            # Stage 1: Tokenize
            # Geneformer expects specific column names
            custom_attr_dict = {
                "cell_type": "celltype" if "celltype" in adata.obs else None,
                "batch": "batch" if "batch" in adata.obs else None,
            }
            # Remove None values
            custom_attr_dict = {k: v for k, v in custom_attr_dict.items() if v is not None}

            tokenizer = TranscriptomeTokenizer(
                custom_attr_name_dict=custom_attr_dict if custom_attr_dict else None,
                nproc=1,
                model_input_size=2048,  # Default context length
            )

            tokenizer.tokenize_data(
                str(input_dir),
                str(token_dir),
                "data",  # Dataset name prefix
                file_format="h5ad",
            )

            # Find tokenized dataset
            token_files = list(token_dir.glob("*.dataset"))
            if not token_files:
                raise RuntimeError("Tokenization failed - no .dataset files produced")

            # Stage 2: Extract embeddings
            embex = EmbExtractor(
                model_type="Pretrained",
                num_classes=0,  # 0 for embedding extraction (not classification)
                emb_mode="cell",  # Cell-level embeddings
                cell_emb_style="mean_pool",  # Average over tokens
                filter_data=None,
                max_ncells=None,
                emb_layer=-1,  # Last layer
                emb_label=None,
                labels_to_plot=None,
                forward_batch_size=batch_size,
                nproc=1,
            )

            # Determine device for extraction
            if device == "cpu":
                use_gpu = False
            else:
                use_gpu = True

            # Extract embeddings
            embs = embex.extract_embs(
                str(checkpoint_path),
                str(token_files[0]),
                str(output_dir),
                "geneformer_embs",
                output_torch_embs=False,
            )

            # Load embeddings from output
            # Geneformer saves embeddings as a dictionary or DataFrame
            if isinstance(embs, dict):
                # Extract the embedding array
                if "embs" in embs:
                    embeddings = np.array(embs["embs"])
                else:
                    # Try to stack all values
                    embeddings = np.vstack(list(embs.values()))
            elif hasattr(embs, "values"):
                embeddings = embs.values
            else:
                embeddings = np.array(embs)

            # Ensure correct shape
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)

            return embeddings.astype(np.float32)

    def _postprocess(self, adata, embeddings: np.ndarray, task: TaskType) -> list[str]:
        """
        Write embeddings to AnnData.

        Args:
            adata: AnnData object to update
            embeddings: Cell embeddings array (n_cells, 512)
            task: Task type

        Returns:
            List of output keys written
        """
        output_keys = []

        if task == TaskType.EMBED:
            key = self.spec.output_keys.embedding_key  # "X_geneformer"
            adata.obsm[key] = embeddings
            output_keys.append(f"obsm['{key}']")
        elif task == TaskType.INTEGRATE:
            # For integration, use the same embedding key
            # Geneformer's shared embedding space provides batch correction
            key = self.spec.output_keys.embedding_key
            adata.obsm[key] = embeddings
            output_keys.append(f"obsm['{key}']")

        return output_keys

    def _detect_species(self, adata) -> str:
        """Detect species from AnnData metadata."""
        # Check uns first
        if "species" in adata.uns:
            species = adata.uns["species"].lower()
            if "human" in species or "sapiens" in species:
                return "human"
            elif "mouse" in species or "musculus" in species:
                return "mouse"

        # Geneformer is human-only, so check for Ensembl human pattern
        gene_names = adata.var_names[:100].tolist()
        ensembl_human_count = sum(1 for g in gene_names if g.startswith("ENSG"))

        if ensembl_human_count > 50:
            return "human"

        # Check for mouse Ensembl pattern
        ensembl_mouse_count = sum(1 for g in gene_names if g.startswith("ENSMUSG"))
        if ensembl_mouse_count > 50:
            return "mouse"

        # Default to human for Geneformer
        return "human"

    def _detect_gene_scheme(self, adata) -> GeneIDScheme:
        """Detect gene ID scheme from AnnData."""
        gene_names = adata.var_names[:100].tolist()

        # Check for Ensembl pattern (human or mouse)
        ensembl_count = sum(1 for g in gene_names if g.startswith("ENS"))
        if ensembl_count > 50:
            return GeneIDScheme.ENSEMBL

        # Check for gene symbols (uppercase = human, mixed = mouse)
        uppercase_count = sum(1 for g in gene_names if g.isupper() and not g.startswith("ENS"))
        if uppercase_count > 50:
            return GeneIDScheme.SYMBOL

        return GeneIDScheme.CUSTOM
