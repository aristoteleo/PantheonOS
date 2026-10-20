"""
Program and CodebaseSnapshot data structures for evolution.

CodebaseSnapshot represents a multi-file codebase state.
Program represents an evolved version with metrics and lineage.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from fnmatch import fnmatch

from .utils.diff import apply_diff, generate_diff, parse_diff
from .utils.metrics import (
    compute_features,
    compute_fitness_score,
    feature_coordinates_to_bin,
)


@dataclass
class CodebaseSnapshot:
    """
    Represents a multi-file codebase state.

    Stores file contents and provides methods for diff application,
    workspace materialization, and directory loading.
    """

    files: Dict[str, str] = field(default_factory=dict)  # path -> content
    base_path: str = ""  # Original codebase root directory

    def apply_diff(self, diff_text: str) -> "CodebaseSnapshot":
        """
        Apply a diff (unified or SEARCH/REPLACE format) and return new snapshot.

        Args:
            diff_text: The diff text to apply

        Returns:
            New CodebaseSnapshot with changes applied
        """
        # Parse the diff
        changes = parse_diff(diff_text, self._get_default_file())

        # Apply changes
        new_files = apply_diff(self.files, changes)

        return CodebaseSnapshot(files=new_files, base_path=self.base_path)

    def _get_default_file(self) -> str:
        """Get default file path for SEARCH/REPLACE blocks without file markers."""
        if not self.files:
            return "main.py"

        # Prefer main.py or __init__.py if present
        for preferred in ["main.py", "__init__.py", "app.py", "index.py"]:
            if preferred in self.files:
                return preferred

        # Otherwise return first Python file
        for path in sorted(self.files.keys()):
            if path.endswith(".py"):
                return path

        # Fall back to first file
        return next(iter(self.files.keys()), "main.py")

    def to_workspace(self, workspace_path: str) -> None:
        """
        Write snapshot contents to a workspace directory.

        Args:
            workspace_path: Directory to write files to
        """
        workspace = Path(workspace_path)

        # Clear existing contents
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        # Write all files
        for file_path, content in self.files.items():
            full_path = workspace / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")

    def diff_from(self, other: "CodebaseSnapshot") -> str:
        """
        Generate unified diff from another snapshot to this one.

        Args:
            other: The other (parent) snapshot

        Returns:
            Unified diff string
        """
        return generate_diff(other.files, self.files)

    def get_file(self, path: str) -> Optional[str]:
        """Get content of a specific file."""
        return self.files.get(path)

    def set_file(self, path: str, content: str) -> "CodebaseSnapshot":
        """Return new snapshot with file added/updated."""
        new_files = dict(self.files)
        new_files[path] = content
        return CodebaseSnapshot(files=new_files, base_path=self.base_path)

    def delete_file(self, path: str) -> "CodebaseSnapshot":
        """Return new snapshot with file removed."""
        new_files = {k: v for k, v in self.files.items() if k != path}
        return CodebaseSnapshot(files=new_files, base_path=self.base_path)

    def total_lines(self) -> int:
        """Count total lines of code across all files."""
        return sum(len(content.split("\n")) for content in self.files.values())

    def total_bytes(self) -> int:
        """Count total bytes across all files."""
        return sum(len(content.encode("utf-8")) for content in self.files.values())

    def file_count(self) -> int:
        """Count number of files."""
        return len(self.files)

    def content_hash(self) -> str:
        """Generate a hash of all file contents for comparison."""
        hasher = hashlib.sha256()
        for path in sorted(self.files.keys()):
            hasher.update(path.encode("utf-8"))
            hasher.update(self.files[path].encode("utf-8"))
        return hasher.hexdigest()[:16]

    def to_summary(self, max_files: int = 5, max_lines_per_file: int = 50) -> str:
        """
        Generate a human-readable summary of the codebase.

        Args:
            max_files: Maximum number of files to include
            max_lines_per_file: Maximum lines to show per file

        Returns:
            Summary string
        """
        parts = []
        parts.append(f"Codebase: {self.file_count()} files, {self.total_lines()} lines\n")

        for i, (path, content) in enumerate(sorted(self.files.items())):
            if i >= max_files:
                parts.append(f"\n... and {self.file_count() - max_files} more files")
                break

            lines = content.split("\n")
            if len(lines) > max_lines_per_file:
                truncated = "\n".join(lines[:max_lines_per_file])
                parts.append(f"\n### {path} ({len(lines)} lines, truncated)\n```\n{truncated}\n...\n```")
            else:
                parts.append(f"\n### {path}\n```\n{content}\n```")

        return "\n".join(parts)

    @classmethod
    def from_directory(
        cls,
        path: str,
        include_patterns: List[str] = None,
        exclude_patterns: List[str] = None,
        max_file_size: int = 100 * 1024,  # 100KB default
    ) -> "CodebaseSnapshot":
        """
        Load a codebase snapshot from a directory.

        Args:
            path: Root directory path
            include_patterns: Glob patterns to include (default: ["**/*.py"])
            exclude_patterns: Glob patterns to exclude (default: common ignores)
            max_file_size: Maximum file size in bytes to include

        Returns:
            New CodebaseSnapshot
        """
        if include_patterns is None:
            include_patterns = ["**/*.py"]

        if exclude_patterns is None:
            exclude_patterns = [
                "**/__pycache__/**",
                "**/.git/**",
                "**/.venv/**",
                "**/venv/**",
                "**/node_modules/**",
                "**/*.pyc",
                "**/.pytest_cache/**",
                "**/.mypy_cache/**",
                "**/dist/**",
                "**/build/**",
                "**/*.egg-info/**",
            ]

        root = Path(path).resolve()
        files: Dict[str, str] = {}

        for pattern in include_patterns:
            for file_path in root.glob(pattern):
                if not file_path.is_file():
                    continue

                # Get relative path
                rel_path = str(file_path.relative_to(root))

                # Check exclude patterns
                excluded = False
                for exc_pattern in exclude_patterns:
                    if fnmatch(rel_path, exc_pattern) or fnmatch(str(file_path), exc_pattern):
                        excluded = True
                        break

                if excluded:
                    continue

                # Check file size
                if file_path.stat().st_size > max_file_size:
                    continue

                # Read file content
                try:
                    content = file_path.read_text(encoding="utf-8")
                    # Normalize path separators
                    rel_path = rel_path.replace("\\", "/")
                    files[rel_path] = content
                except (UnicodeDecodeError, IOError):
                    # Skip binary or unreadable files
                    continue

        return cls(files=files, base_path=str(root))

    @classmethod
    def from_single_file(cls, path: str, content: str) -> "CodebaseSnapshot":
        """Create snapshot from a single file."""
        return cls(files={path: content}, base_path="")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "files": self.files,
            "base_path": self.base_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodebaseSnapshot":
        """Deserialize from dictionary."""
        return cls(
            files=data.get("files", {}),
            base_path=data.get("base_path", ""),
        )


@dataclass
class Program:
    """
    Represents an evolved version of a codebase.

    Tracks code, metrics, lineage, and artifacts for evolution.
    """

    id: str
    snapshot: CodebaseSnapshot
    diff_from_parent: str = ""
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    llm_feedback: str = ""
    parent_id: Optional[str] = None
    generation: int = 0
    island_id: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    mutator_prompt_used: str = ""  # Store the mutator prompt for reproducibility
    analysis_prompt_used: str = ""  # Store the analyzer prompt
    analysis_used: str = ""  # Store analyzer's analysis output
    order: Optional[int] = None  # Sequential number assigned when added to database

    # Mutation summary fields
    mutation_summary: str = ""  # Description of the mutation (10-20 words)
    mutation_category: str = "other"  # Category: objective_function, optimization_method, etc.
    is_algorithmic: bool = True  # True for algorithm changes, False for code optimizations
    fitness_delta: Optional[float] = None  # Change in fitness score vs parent
    metrics_delta: Dict[str, float] = field(default_factory=dict)  # Per-metric changes

    def fitness_score(
        self,
        feature_dimensions: List[str] = None,
        metric_ranges: Dict[str, Tuple[float, float]] = None,
        function_weight: float = 1.0,
        llm_weight: float = 0.0,
    ) -> float:
        """
        Calculate fitness score from metrics.

        fitness = function_score × function_weight + llm_score × llm_weight

        Args:
            feature_dimensions: Feature dimensions to exclude from fitness
            metric_ranges: Optional dict of metric name -> (min, max) for normalization
            function_weight: Weight for function_score (default 1.0)
            llm_weight: Weight for llm_score (default 0.0)

        Returns:
            Fitness score (higher is better, 0.0 to 1.0 if normalized)
        """
        feature_dimensions = feature_dimensions or []
        return compute_fitness_score(
            self.metrics,
            feature_dimensions,
            metric_ranges,
            function_weight,
            llm_weight,
        )

    def feature_coordinates(
        self,
        feature_dimensions: List[str],
        reference_codes: List[str] = None,
    ) -> Dict[str, float]:
        """
        Calculate MAP-Elites feature coordinates.

        Args:
            feature_dimensions: List of feature dimension names
            reference_codes: Reference codes for diversity calculation

        Returns:
            Dict mapping feature names to values (0.0 to 1.0)
        """
        # First check if dimensions are in metrics (evaluation-based features)
        features: Dict[str, float] = {}
        code_dimensions = []

        for dim in feature_dimensions:
            if dim in self.metrics:
                # Use metric value directly (already in 0-1 range for scores)
                value = self.metrics[dim]
                # Clamp to 0-1 range
                features[dim] = max(0.0, min(1.0, float(value)))
            else:
                # Will compute from code
                code_dimensions.append(dim)

        # Compute remaining dimensions from code
        if code_dimensions:
            combined_code = "\n\n".join(self.snapshot.files.values())
            code_features = compute_features(
                combined_code,
                code_dimensions,
                reference_codes,
                language="python",
            )
            features.update(code_features)

        return features

    def feature_bin(
        self,
        feature_dimensions: List[str],
        num_bins: int = 10,
        reference_codes: List[str] = None,
        feature_ranges: Dict[str, Tuple[float, float]] = None,
    ) -> Tuple[int, ...]:
        """
        Get MAP-Elites grid bin for this program.

        Args:
            feature_dimensions: List of feature dimension names
            num_bins: Number of bins per dimension
            reference_codes: Reference codes for diversity calculation
            feature_ranges: Optional dict of feature name -> (min, max) for adaptive ranges

        Returns:
            Tuple of bin indices
        """
        coordinates = self.feature_coordinates(feature_dimensions, reference_codes)
        return feature_coordinates_to_bin(coordinates, feature_dimensions, num_bins, feature_ranges)

    def total_lines(self) -> int:
        """Total lines of code."""
        return self.snapshot.total_lines()

    def file_count(self) -> int:
        """Number of files."""
        return self.snapshot.file_count()

    def get_combined_code(self) -> str:
        """Get all code combined into a single string."""
        return "\n\n".join(
            f"# File: {path}\n{content}"
            for path, content in sorted(self.snapshot.files.items())
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "snapshot": self.snapshot.to_dict(),
            "diff_from_parent": self.diff_from_parent,
            "metrics": self.metrics,
            "artifacts": self.artifacts,
            "llm_feedback": self.llm_feedback,
            "parent_id": self.parent_id,
            "generation": self.generation,
            "island_id": self.island_id,
            "created_at": self.created_at,
            "mutator_prompt_used": self.mutator_prompt_used,
            "analysis_prompt_used": self.analysis_prompt_used,
            "analysis_used": self.analysis_used,
            "order": self.order,
            "mutation_summary": self.mutation_summary,
            "mutation_category": self.mutation_category,
            "is_algorithmic": self.is_algorithmic,
            "fitness_delta": self.fitness_delta,
            "metrics_delta": self.metrics_delta,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Program":
        """Deserialize from dictionary."""
        snapshot_data = data.get("snapshot", {})
        return cls(
            id=data["id"],
            snapshot=CodebaseSnapshot.from_dict(snapshot_data),
            diff_from_parent=data.get("diff_from_parent", ""),
            metrics=data.get("metrics", {}),
            artifacts=data.get("artifacts", {}),
            llm_feedback=data.get("llm_feedback", ""),
            parent_id=data.get("parent_id"),
            generation=data.get("generation", 0),
            island_id=data.get("island_id", 0),
            created_at=data.get("created_at", datetime.now().isoformat()),
            mutator_prompt_used=data.get("mutator_prompt_used", data.get("prompt_used", "")),
            analysis_prompt_used=data.get("analysis_prompt_used", ""),
            analysis_used=data.get("analysis_used", ""),
            order=data.get("order"),
            mutation_summary=data.get("mutation_summary", ""),
            mutation_category=data.get("mutation_category", "other"),
            is_algorithmic=data.get("is_algorithmic", True),
            fitness_delta=data.get("fitness_delta"),
            metrics_delta=data.get("metrics_delta", {}),
        )

    def save(self, path: str) -> None:
        """Save program to JSON file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Program":
        """Load program from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
