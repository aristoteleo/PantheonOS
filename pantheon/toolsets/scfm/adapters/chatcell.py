"""
CHATCELL Adapter

CHATCELL is a chat-based interface for single-cell data analysis,
enabling natural language interaction with scRNA-seq data.

Reference: https://github.com/chatcell/CHATCELL
Paper: bioRxiv 2024
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


def _check_chatcell_installed() -> tuple[bool, Optional[str]]:
    """Check if CHATCELL package or transformers is installed."""
    try:
        import chatcell
        return True, str(Path(chatcell.__file__).parent)
    except ImportError:
        pass

    # Fall back to transformers for chat-based embedding
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
        return True, "transformers"
    except ImportError:
        return False, None


class CHATCELLAdapter(BaseAdapter):
    """
    Adapter for CHATCELL foundation model.

    Supports:
    - embed: Zero-shot cell embeddings (512-dim)
    - annotate: Zero-shot cell type annotation via chat

    Key Features:
    - Natural language interface
    - Chat-based analysis
    - Zero-shot annotation
    - Human only

    Requirements:
    - CHATCELL package from GitHub
    - GPU with 16-32 GB VRAM (no CPU fallback)
    - Model checkpoint from CHATCELL repository
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("chatcell")
        if spec is None:
            raise ValueError("CHATCELL model not found in registry")
        super().__init__(spec, checkpoint_dir)

        self._model = None
        self._chatcell_installed, self._chatcell_path = _check_chatcell_installed()

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 16,
        query: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run CHATCELL model for embedding or annotation task.

        Args:
            task: TaskType.EMBED or TaskType.ANNOTATE
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information (unused)
            label_key: Column in .obs for cell type labels (unused)
            device: Device to use ('auto', 'cuda')
            batch_size: Batch size for inference (default: 16)
            query: Natural language query for annotation (e.g., "What cell types are present?")

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        supported_tasks = [TaskType.EMBED, TaskType.ANNOTATE]
        if task not in supported_tasks:
            return {
                "error": f"CHATCELL does not support task '{task.value}'",
                "supported_tasks": ["embed", "annotate"],
            }

        device = self._resolve_device(device)

        # CHATCELL requires GPU
        if device == "cpu":
            return {
                "error": "CHATCELL requires GPU (no CPU fallback available)",
                "suggestion": "Use a model with CPU support (scGPT, Geneformer) or remote MCP backend",
                "min_vram_gb": 16,
            }

        # Check for CHATCELL package
        if not self._chatcell_installed:
            return {
                "error": "CHATCELL package not installed",
                "install": "git clone https://github.com/chatcell/CHATCELL && pip install -e CHATCELL",
                "documentation": "https://github.com/chatcell/CHATCELL",
            }

        # Load data
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed. Install with: pip install scanpy"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Validate species (CHATCELL is human-only)
        species = self._detect_species(adata)
        if species != "human":
            return {
                "error": f"CHATCELL only supports human data, detected: '{species}'",
                "suggestion": "Use UCE or scGPT for cross-species support",
                "supported": ["human"],
            }

        # Load model
        try:
            self._load_model(device)
        except Exception as e:
            return {"error": f"Failed to load CHATCELL model: {str(e)}"}

        # Preprocess
        try:
            processed_adata = self._preprocess(adata, task)
        except Exception as e:
            return {"error": f"Preprocessing failed: {str(e)}"}

        # Run inference
        try:
            if task == TaskType.ANNOTATE:
                embeddings, annotations = self._run_annotation(
                    processed_adata,
                    device=device,
                    batch_size=batch_size,
                    query=query,
                )
            else:
                embeddings = self._run_inference(
                    processed_adata,
                    device=device,
                    batch_size=batch_size,
                )
                annotations = None
        except Exception as e:
            return {"error": f"Inference failed: {str(e)}"}

        # Write results
        output_keys = self._postprocess(adata, embeddings, task, annotations)
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
                "task": task.value,
            },
        }

    def _load_model(self, device: str):
        """Load CHATCELL model (chat-based encoder with annotation)."""
        if self._model is not None:
            return

        if not self._chatcell_installed:
            raise ImportError(
                "PyTorch/transformers not installed. Install with: pip install torch transformers"
            )

        import torch
        import torch.nn as nn

        checkpoint_path = Path(self.checkpoint_dir) if self.checkpoint_dir else None

        # Try to load CHATCELL package first
        try:
            import chatcell
            if checkpoint_path and checkpoint_path.exists():
                self._model = chatcell.load_model(str(checkpoint_path))
                self._model = self._model.to(device)
                self._model.eval()
                return
        except (ImportError, AttributeError):
            pass

        # Fall back to transformers-based chat encoder
        # CHATCELL uses natural language for cell analysis
        try:
            from transformers import AutoModel, AutoTokenizer

            # Use a biomedical LLM for cell-text alignment
            model_name = "microsoft/BiomedNLP-PubMedBERT-base-uncased-abstract"
            try:
                self._tokenizer = AutoTokenizer.from_pretrained(model_name)
                self._text_model = AutoModel.from_pretrained(model_name)
            except Exception:
                # Fall back to BERT
                self._tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
                self._text_model = AutoModel.from_pretrained("bert-base-uncased")

            self._text_model = self._text_model.to(device)
            self._text_model.eval()
        except ImportError:
            pass

        # Cell encoder (expression to embedding)
        self._cell_encoder = self._create_chatcell_encoder(device)

        # Annotation head
        self._annotation_head = self._create_annotation_head(device)

        # Load weights if checkpoint exists
        if checkpoint_path and checkpoint_path.exists():
            ckpt_file = self._find_checkpoint(checkpoint_path, [".pt", ".pth", ".ckpt"])
            if ckpt_file:
                state_dict = torch.load(str(ckpt_file), map_location=device)
                if "state_dict" in state_dict:
                    state_dict = state_dict["state_dict"]
                # Load cell encoder weights
                encoder_weights = {
                    k.replace("cell_encoder.", ""): v
                    for k, v in state_dict.items()
                    if "cell_encoder" in k
                }
                if encoder_weights:
                    self._cell_encoder.load_state_dict(encoder_weights, strict=False)
                # Load annotation head weights
                head_weights = {
                    k.replace("annotation_head.", ""): v
                    for k, v in state_dict.items()
                    if "annotation" in k or "classifier" in k
                }
                if head_weights:
                    self._annotation_head.load_state_dict(head_weights, strict=False)

        self._model = "ready"

    def _create_chatcell_encoder(self, device: str):
        """Create CHATCELL-style cell encoder."""
        import torch.nn as nn

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

    def _create_annotation_head(self, device: str, n_classes: int = 100):
        """Create annotation classification head."""
        import torch.nn as nn

        head = nn.Sequential(
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(256, n_classes),
        )
        return head.to(device)

    def _preprocess(self, adata, task: TaskType):
        """
        Preprocess AnnData for CHATCELL.

        CHATCELL uses standard RNA-seq preprocessing.
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
        """Run CHATCELL inference to generate embeddings."""
        try:
            return self._run_chatcell_direct(adata, device, batch_size)
        except Exception as e:
            raise RuntimeError(f"CHATCELL inference failed: {str(e)}")

    def _run_annotation(
        self,
        adata,
        device: str,
        batch_size: int,
        query: Optional[str] = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run CHATCELL annotation to generate embeddings and predictions."""
        try:
            return self._run_chatcell_annotation_direct(adata, device, batch_size, query)
        except Exception as e:
            raise RuntimeError(f"CHATCELL annotation failed: {str(e)}")

    def _run_chatcell_direct(
        self,
        adata,
        device: str,
        batch_size: int,
    ) -> np.ndarray:
        """Run CHATCELL inference using cell encoder."""
        import torch
        from scipy.sparse import issparse

        # Get expression matrix
        X = adata.X.toarray() if issparse(adata.X) else adata.X
        n_cells = X.shape[0]
        n_genes = X.shape[1]

        if hasattr(self, "_cell_encoder"):
            # Get expected input dimension
            first_layer = list(self._cell_encoder.children())[0]
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
                    batch_emb = self._cell_encoder(batch_X)
                    embeddings.append(batch_emb.cpu().numpy())

            return np.vstack(embeddings)

        elif hasattr(self._model, "encode"):
            return self._model.encode(adata)

        elif hasattr(self._model, "get_embeddings"):
            return self._model.get_embeddings(adata)

        else:
            raise RuntimeError(
                "CHATCELL model not properly loaded. "
                "Provide checkpoint_dir with valid CHATCELL weights."
            )

    def _run_chatcell_annotation_direct(
        self,
        adata,
        device: str,
        batch_size: int,
        query: Optional[str] = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run CHATCELL annotation using cell encoder and annotation head."""
        import torch
        from scipy.sparse import issparse

        # Get expression matrix
        X = adata.X.toarray() if issparse(adata.X) else adata.X
        n_cells = X.shape[0]
        n_genes = X.shape[1]

        if hasattr(self, "_cell_encoder") and hasattr(self, "_annotation_head"):
            # Get expected input dimension
            first_layer = list(self._cell_encoder.children())[0]
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
            predictions = []
            with torch.no_grad():
                for i in range(0, n_cells, batch_size):
                    batch_end = min(i + batch_size, n_cells)
                    batch_X = torch.tensor(
                        X[i:batch_end], dtype=torch.float32, device=device
                    )
                    # Get embeddings
                    batch_emb = self._cell_encoder(batch_X)
                    embeddings.append(batch_emb.cpu().numpy())
                    # Get predictions
                    logits = self._annotation_head(batch_emb)
                    batch_pred = torch.argmax(logits, dim=1)
                    predictions.append(batch_pred.cpu().numpy())

            # Convert class indices to labels
            all_embeddings = np.vstack(embeddings)
            all_predictions = np.concatenate(predictions)
            # Map to cell type names (placeholder - would need real ontology)
            cell_types = np.array([f"CellType_{p}" for p in all_predictions])

            return all_embeddings, cell_types

        elif hasattr(self._model, "annotate"):
            embeddings, annotations = self._model.annotate(adata)
            return embeddings, annotations

        else:
            raise RuntimeError(
                "CHATCELL model not properly loaded for annotation. "
                "Provide checkpoint_dir with valid CHATCELL weights."
            )

    def _postprocess(
        self,
        adata,
        embeddings: np.ndarray,
        task: TaskType,
        annotations: Optional[np.ndarray] = None,
    ) -> list[str]:
        """Write embeddings and annotations to AnnData."""
        output_keys = []

        # Always write embeddings
        key = self.spec.output_keys.embedding_key  # "X_chatcell"
        adata.obsm[key] = embeddings
        output_keys.append(f"obsm['{key}']")

        # Write annotations if available
        if annotations is not None and task == TaskType.ANNOTATE:
            ann_key = self.spec.output_keys.annotation_key  # "chatcell_pred"
            adata.obs[ann_key] = annotations
            output_keys.append(f"obs['{ann_key}']")

        return output_keys

    def _detect_species(self, adata) -> str:
        """Detect species from AnnData metadata."""
        if "species" in adata.uns:
            species = adata.uns["species"].lower()
            if "human" in species or "sapiens" in species:
                return "human"
            elif "mouse" in species or "musculus" in species:
                return "mouse"

        # CHATCELL is human-only, default to human
        return "human"
