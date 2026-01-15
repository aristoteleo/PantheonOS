"""
Base Adapter for Single-Cell Foundation Models

Defines the interface that all model adapters must implement.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..registry import ModelSpec, TaskType


class BaseAdapter(ABC):
    """
    Base class for foundation model adapters.

    Each adapter must implement:
    - run(): Execute the model for a given task
    - _preprocess(): Prepare data for model input
    - _postprocess(): Write results to AnnData
    """

    def __init__(self, spec: ModelSpec, checkpoint_dir: Optional[str] = None):
        """
        Initialize adapter with model specification.

        Args:
            spec: Model specification from registry
            checkpoint_dir: Path to model checkpoints (optional, uses default cache)
        """
        self.spec = spec
        self.checkpoint_dir = checkpoint_dir or None
        if self.checkpoint_dir is None:
            resolved = self._resolve_checkpoint_dir(require=False)
            if resolved is not None:
                self.checkpoint_dir = str(resolved)
        self._model = None
        self._tokenizer = None

    @property
    def name(self) -> str:
        return self.spec.name

    @abstractmethod
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
        Execute the model for a given task.

        Args:
            task: Task type (embed, annotate, integrate)
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad file
            batch_key: Column in .obs for batch information
            label_key: Column in .obs for cell type labels
            device: Device to use ('auto', 'cuda', 'cpu')
            batch_size: Batch size for inference

        Returns:
            Dictionary with output_path, output_keys, and statistics
        """
        pass

    @abstractmethod
    def _load_model(self, device: str):
        """Load model and tokenizer from checkpoint"""
        pass

    @abstractmethod
    def _preprocess(self, adata, task: TaskType):
        """Preprocess AnnData for model input"""
        pass

    @abstractmethod
    def _postprocess(self, adata, embeddings, task: TaskType) -> list[str]:
        """
        Write results to AnnData and return output keys.

        Returns:
            List of keys written to adata (obsm/obs)
        """
        pass

    def _add_provenance(self, adata, task: TaskType, output_keys: list[str], backend: str = "local"):
        """Add provenance information to adata.uns"""
        import json
        import os

        # Allow subprocess runners to override backend without changing adapter call sites
        backend = os.environ.get("SCFM_BACKEND") or backend

        provenance = {
            "model_name": self.spec.name,
            "version": self.spec.version,
            "task": task.value,
            "output_keys": output_keys,
            "timestamp": datetime.now().isoformat(),
            "backend": backend,
        }

        if "scfm" not in adata.uns or not isinstance(adata.uns.get("scfm"), dict):
            adata.uns["scfm"] = {}
        scfm_uns = adata.uns["scfm"]

        # Remove legacy keys that can break h5ad serialization (e.g., list[dict])
        legacy_runs = scfm_uns.get("runs")
        if isinstance(legacy_runs, list) and any(isinstance(x, dict) for x in legacy_runs):
            scfm_uns.pop("runs", None)
        if isinstance(scfm_uns.get("latest"), dict):
            scfm_uns.pop("latest", None)

        # Store provenance as JSON strings for HDF5 compatibility
        run_info_str = json.dumps(provenance, ensure_ascii=False)
        runs = scfm_uns.get("runs_json")
        if runs is None:
            runs = []
        runs = list(runs)  # Ensure it's a mutable list
        runs.append(run_info_str)
        scfm_uns["runs_json"] = runs
        scfm_uns["latest_json"] = run_info_str

    def _resolve_device(self, device: str) -> str:
        """Resolve 'auto' device to actual device"""
        if device != "auto":
            return device

        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass

        return "cpu"

    def _resolve_checkpoint_dir(self, require: bool = True) -> Optional[Path]:
        """
        Resolve checkpoint directory from multiple sources.

        Priority:
        1. Constructor parameter (self.checkpoint_dir)
        2. Model-specific env var (SCFM_CHECKPOINT_DIR_<MODEL>)
        3. Base env var + model subfolder (SCFM_CHECKPOINT_DIR/<model_name>)

        Args:
            require: If True, raise error when not found

        Returns:
            Path to checkpoint directory, or None if not required
        """
        import os

        # Priority 1: Constructor parameter
        if self.checkpoint_dir:
            path = Path(self.checkpoint_dir)
            if path.exists():
                return path
            if require:
                raise FileNotFoundError(f"Checkpoint directory not found: {self.checkpoint_dir}")

        # Priority 2: Model-specific env var
        model_env = f"SCFM_CHECKPOINT_DIR_{self.spec.name.upper().replace('-', '_')}"
        if model_path := os.environ.get(model_env):
            path = Path(model_path)
            if path.exists():
                return path

        # Priority 3: Base env var + model subfolder
        if base_dir := os.environ.get("SCFM_CHECKPOINT_DIR"):
            path = Path(base_dir) / self.spec.name.lower()
            if path.exists():
                return path

        if require:
            raise ValueError(
                f"{self.spec.name} checkpoint not found. Set one of:\n"
                f"  - checkpoint_dir parameter\n"
                f"  - {model_env} environment variable\n"
                f"  - SCFM_CHECKPOINT_DIR with {self.spec.name.lower()}/ subfolder"
            )
        return None

    def _find_checkpoint(self, checkpoint_path: Path, extensions: list[str]) -> Path:
        """
        Find checkpoint file in directory.

        Args:
            checkpoint_path: Directory to search
            extensions: List of extensions to try (e.g., [".pt", ".pth", ".ckpt"])

        Returns:
            Path to checkpoint file

        Raises:
            FileNotFoundError: If no checkpoint found
        """
        matches: list[Path] = []
        for ext in extensions:
            for pattern in [f"*{ext}", f"**/*{ext}"]:
                matches.extend([m for m in checkpoint_path.glob(pattern) if m.is_file()])

        if matches:
            matches = sorted({m.resolve(): m for m in matches}.values(), key=lambda p: str(p))
            for preferred in ["model", "checkpoint", "best"]:
                for m in matches:
                    if preferred in m.stem.lower():
                        return m
            return matches[0]

        raise FileNotFoundError(
            f"No checkpoint found in {checkpoint_path}\n"
            f"Searched for extensions: {extensions}"
        )
