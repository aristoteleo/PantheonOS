"""
Evolution database with MAP-Elites support.

Stores evolved programs with quality-diversity archiving and multi-island evolution.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from pantheon.utils.log import logger

from .config import EvolutionConfig
from .program import CodebaseSnapshot, Program
from pantheon.evolution.utils.metrics import feature_coordinates_to_bin


@dataclass
class EvolutionDatabase:
    """
    Program database with MAP-Elites and island-based evolution.

    Supports:
    - Multi-island populations for diversity
    - MAP-Elites grid for quality-diversity
    - Elite archive for exploitation
    - Various sampling strategies
    """

    config: EvolutionConfig = field(default_factory=EvolutionConfig)

    # Storage
    programs: Dict[str, Program] = field(default_factory=dict)
    islands: List[Set[str]] = field(default_factory=list)
    # Dynamic bin storage: island_id -> {program_id -> {dim: value}}
    island_coordinates: List[Dict[str, Dict[str, float]]] = field(default_factory=list)
    archive: Set[str] = field(default_factory=set)
    best_program_id: Optional[str] = None

    # Statistics
    total_added: int = 0
    total_improved: int = 0

    # Observed feature ranges: {feature_name: (min_value, max_value)}
    feature_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    # Observed metric ranges for normalization: {metric_name: (min_value, max_value)}
    metric_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    # Sequence counter for program ordering
    _next_order: int = 0

    # Bin cache: program_id -> bin_tuple (invalidated when ranges change)
    _bin_cache: Dict[str, Tuple[int, ...]] = field(default_factory=dict, repr=False)
    _cache_version: int = field(default=0, repr=False)
    _ranges_version: int = field(default=0, repr=False)

    # Thread safety lock for async operations
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self):
        """Initialize islands if not already set."""
        if not self.islands:
            self.islands = [set() for _ in range(self.config.num_islands)]
            self.island_coordinates = [{} for _ in range(self.config.num_islands)]

    def add(
        self,
        program: Program,
        target_island: Optional[int] = None,
        reference_codes: Optional[List[str]] = None,
    ) -> bool:
        """
        Add a program to the database.

        Places program in MAP-Elites grid and updates archive if elite.
        Uses dynamic bin calculation based on current feature ranges.

        Args:
            program: Program to add
            target_island: Specific island to add to (random if None)
            reference_codes: Reference codes for diversity calculation

        Returns:
            True if program was added (might replace existing)
        """
        # Assign sequential order number
        program.order = self._next_order
        self._next_order += 1

        self.total_added += 1

        # Store program
        self.programs[program.id] = program

        # Determine target island
        if target_island is None:
            target_island = random.randint(0, self.config.num_islands - 1)
        program.island_id = target_island

        # Add to island population
        self.islands[target_island].add(program.id)

        # Calculate feature coordinates (stored for dynamic bin computation)
        coords = program.feature_coordinates(self.config.feature_dimensions, reference_codes)

        # Store coordinates for dynamic bin calculation
        self.island_coordinates[target_island][program.id] = coords

        # Update observed feature ranges (may invalidate cache)
        self._update_feature_ranges(program)

        # Update observed metric ranges for normalization
        self._update_metric_ranges(program.metrics)

        # Compute current bin dynamically
        feature_bin = self._compute_bin(coords)

        # Cache the bin for this program
        self._bin_cache[program.id] = feature_bin

        # MAP-Elites: find the best existing program in this bin
        existing_best_id = self._get_best_in_bin(target_island, feature_bin)

        added = False
        new_fitness = program.fitness_score(
            self.config.feature_dimensions,
            self.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )

        if existing_best_id is None or existing_best_id == program.id:
            # Empty bin or we are the only one
            added = True
        else:
            # Compare fitness with the best in bin
            existing = self.programs.get(existing_best_id)
            if existing:
                old_fitness = existing.fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                )
                if new_fitness > old_fitness:
                    added = True
                    self.total_improved += 1

        # Update best program
        self._update_best(program)

        # Update archive
        self._update_archive(program)

        # Log if improvement
        if added and self.config.log_improvements:
            logger.debug(
                f"Added program {program.id[:8]} to island {target_island}, "
                f"bin {feature_bin}, fitness {new_fitness:.4f}"
            )

        return added

    def _update_best(self, program: Program) -> None:
        """Update best program tracking."""
        if self.best_program_id is None:
            self.best_program_id = program.id
            return

        best = self.programs.get(self.best_program_id)
        if best is None:
            self.best_program_id = program.id
            return

        new_fitness = program.fitness_score(
            self.config.feature_dimensions,
            self.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )
        best_fitness = best.fitness_score(
            self.config.feature_dimensions,
            self.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )

        if new_fitness > best_fitness:
            self.best_program_id = program.id
            logger.info(
                f"New best program: {program.id[:8]} with fitness {new_fitness:.4f} "
                f"(previous: {best_fitness:.4f})"
            )

    def _update_archive(self, program: Program) -> None:
        """Update elite archive to keep top X% of programs by fitness."""
        self.archive.add(program.id)

        # Calculate dynamic archive size based on ratio
        total_programs = len(self.programs)
        target_size = max(1, int(total_programs * self.config.archive_ratio))

        # Trim archive if over target size
        if len(self.archive) > target_size:
            # Remove lowest fitness programs
            archive_programs = [
                (pid, self.programs[pid].fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                ))
                for pid in self.archive
                if pid in self.programs
            ]
            archive_programs.sort(key=lambda x: x[1], reverse=True)

            self.archive = set(pid for pid, _ in archive_programs[:target_size])

    def _update_feature_ranges(self, program: Program) -> bool:
        """
        Update observed min/max for each feature dimension.

        Returns:
            True if any range changed, False otherwise
        """
        coords = program.feature_coordinates(self.config.feature_dimensions)
        changed = False
        for dim, value in coords.items():
            if dim not in self.feature_ranges:
                self.feature_ranges[dim] = (value, value)
                changed = True
            else:
                old_min, old_max = self.feature_ranges[dim]
                new_min, new_max = min(old_min, value), max(old_max, value)
                if new_min != old_min or new_max != old_max:
                    self.feature_ranges[dim] = (new_min, new_max)
                    changed = True
        if changed:
            self._invalidate_bin_cache()
        return changed

    def _update_metric_ranges(self, metrics: Dict[str, float]) -> bool:
        """
        Update observed min/max for each metric.

        Used for normalizing metrics when computing fitness scores.

        Args:
            metrics: Dict of metric name -> value

        Returns:
            True if any range changed, False otherwise
        """
        # Absolute-fitness mode: pin every metric's normalization range to a fixed [0, 1] and never
        # widen it. Since normalization is (value - min) / (max - min) = value, fitness then equals
        # the raw evaluator score instead of a min-max value relative to the archive — so the champion
        # is not pinned to 1.0 and a score above 1.0 (beating the target) still increases fitness
        # (the range branch does not clamp). Seed once per metric.
        if getattr(self.config, "fitness_absolute", False):
            changed = False
            for metric_name, value in metrics.items():
                if not isinstance(value, (int, float)) or metric_name == 'error':
                    continue
                if metric_name not in self.metric_ranges:
                    self.metric_ranges[metric_name] = (0.0, 1.0)
                    changed = True
            return changed

        changed = False
        for metric_name, value in metrics.items():
            # Skip non-numeric values and error field
            if not isinstance(value, (int, float)) or metric_name == 'error':
                continue
            if metric_name not in self.metric_ranges:
                self.metric_ranges[metric_name] = (float(value), float(value))
                changed = True
            else:
                old_min, old_max = self.metric_ranges[metric_name]
                new_min = min(old_min, float(value))
                new_max = max(old_max, float(value))
                if new_min != old_min or new_max != old_max:
                    self.metric_ranges[metric_name] = (new_min, new_max)
                    changed = True
        return changed

    def get_normalized_metrics(self, metrics: Dict[str, float]) -> Dict[str, float]:
        """
        Normalize metrics to [0, 1] using observed ranges.

        Args:
            metrics: Dict of metric name -> value

        Returns:
            Dict of metric name -> normalized value (0-1)
        """
        normalized = {}
        for name, value in metrics.items():
            if not isinstance(value, (int, float)):
                continue
            if name in self.metric_ranges:
                min_val, max_val = self.metric_ranges[name]
                range_size = max_val - min_val
                if range_size > 1e-8:
                    normalized[name] = (float(value) - min_val) / range_size
                else:
                    normalized[name] = 0.5  # All values are the same
            else:
                normalized[name] = float(value)  # No range info, keep original
        return normalized

    def compute_function_score(
        self,
        metrics: Dict[str, Any],
        fitness_weights: Optional[Dict[str, float]] = None,
    ) -> float:
        """
        Compute function_score from normalized metrics with weights.

        Only metrics in fitness_weights participate in the calculation.
        Metrics are normalized using observed min/max ranges before weighting.

        Args:
            metrics: Raw metrics from evaluator
            fitness_weights: Weight for each metric (from evaluator)

        Returns:
            Normalized weighted fitness score (0.0 to 1.0)
        """
        # Weights must be provided by evaluator
        if not fitness_weights:
            return 0.0

        # Normalize and compute weighted sum
        total_weight = 0.0
        weighted_sum = 0.0

        for metric_name, weight in fitness_weights.items():
            if metric_name not in metrics:
                continue
            value = metrics[metric_name]
            if not isinstance(value, (int, float)):
                continue

            # Normalize using observed range
            if metric_name in self.metric_ranges:
                min_val, max_val = self.metric_ranges[metric_name]
                range_size = max_val - min_val
                if range_size > 1e-8:
                    normalized = (float(value) - min_val) / range_size
                else:
                    normalized = 0.5  # All values are the same
            else:
                # First sample, clamp to [0, 1]
                normalized = max(0.0, min(1.0, float(value)))

            weighted_sum += weight * normalized
            total_weight += weight

        if total_weight < 1e-8:
            return 0.0

        return weighted_sum / total_weight

    def _invalidate_bin_cache(self) -> None:
        """Invalidate the bin cache when feature ranges change."""
        self._ranges_version += 1

    def _compute_bin(self, coords: Dict[str, float]) -> Tuple[int, ...]:
        """
        Compute bin for given coordinates using current feature ranges.

        Args:
            coords: Feature coordinates {dim: value}

        Returns:
            Bin tuple
        """
        effective_ranges = self.get_effective_feature_ranges()
        return feature_coordinates_to_bin(
            coords,
            self.config.feature_dimensions,
            self.config.feature_bins,
            effective_ranges,
        )

    def _get_cached_bin(self, prog_id: str, coords: Dict[str, float]) -> Tuple[int, ...]:
        """
        Get bin for a program, using cache if valid.

        Args:
            prog_id: Program ID (used as cache key)
            coords: Feature coordinates

        Returns:
            Bin tuple
        """
        # Check if cache is still valid
        if self._cache_version != self._ranges_version:
            self._bin_cache.clear()
            self._cache_version = self._ranges_version

        if prog_id not in self._bin_cache:
            self._bin_cache[prog_id] = self._compute_bin(coords)
        return self._bin_cache[prog_id]

    def _find_programs_in_bin(
        self,
        island_id: int,
        target_bin: Tuple[int, ...],
    ) -> List[str]:
        """
        Find all programs currently in the specified bin.

        Args:
            island_id: Island to search
            target_bin: Target bin coordinates

        Returns:
            List of program IDs in the bin
        """
        result = []
        for prog_id, coords in self.island_coordinates[island_id].items():
            if self._get_cached_bin(prog_id, coords) == target_bin:
                result.append(prog_id)
        return result

    def _get_best_in_bin(
        self,
        island_id: int,
        target_bin: Tuple[int, ...],
    ) -> Optional[str]:
        """
        Get the best program (by fitness) in the specified bin.

        Args:
            island_id: Island to search
            target_bin: Target bin coordinates

        Returns:
            Program ID of the best program, or None if bin is empty
        """
        candidates = self._find_programs_in_bin(island_id, target_bin)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda pid: self.programs[pid].fitness_score(
                self.config.feature_dimensions,
                self.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )
        )

    def iter_filled_bins(self, island_id: int) -> Iterator[Tuple[Tuple[int, ...], str]]:
        """
        Iterate over all filled bins and their best programs.

        This dynamically computes bins using current feature ranges.

        Args:
            island_id: Island to iterate

        Yields:
            Tuples of (bin_coords, best_program_id)
        """
        bin_to_best: Dict[Tuple[int, ...], Tuple[str, float]] = {}

        for prog_id, coords in self.island_coordinates[island_id].items():
            bin_coords = self._get_cached_bin(prog_id, coords)
            fitness = self.programs[prog_id].fitness_score(
                self.config.feature_dimensions,
                self.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )

            if bin_coords not in bin_to_best:
                bin_to_best[bin_coords] = (prog_id, fitness)
            else:
                existing_id, existing_fitness = bin_to_best[bin_coords]
                if fitness > existing_fitness:
                    bin_to_best[bin_coords] = (prog_id, fitness)

        for bin_coords, (prog_id, _) in bin_to_best.items():
            yield bin_coords, prog_id

    def get_feature_range(self, dim: str) -> Tuple[float, float]:
        """
        Get effective range for a feature dimension with padding.

        Returns:
            Tuple of (min_value, max_value) with padding applied
        """
        if not self.config.feature_range_adaptive or dim not in self.feature_ranges:
            # Use default 0-1 range
            return (0.0, 1.0)

        min_val, max_val = self.feature_ranges[dim]
        range_size = max_val - min_val

        # Add padding
        padding = range_size * self.config.feature_range_padding
        padded_min = max(0.0, min_val - padding)
        padded_max = min(1.0, max_val + padding)

        # Ensure minimum range to avoid division by zero
        if padded_max - padded_min < 0.01:
            padded_min = max(0.0, min_val - 0.05)
            padded_max = min(1.0, max_val + 0.05)

        return (padded_min, padded_max)

    def get_effective_feature_ranges(self) -> Dict[str, Tuple[float, float]]:
        """Get effective ranges for all feature dimensions."""
        return {
            dim: self.get_feature_range(dim)
            for dim in self.config.feature_dimensions
        }

    def sample(
        self,
        num_inspirations: int = 2,
        island_id: Optional[int] = None,
    ) -> Tuple[Program, List[Program]]:
        """
        Sample a parent program and inspiration programs.

        Uses exploration/exploitation ratio to balance sampling strategy.

        Args:
            num_inspirations: Number of inspiration programs to sample
            island_id: Specific island to sample from (random if None)

        Returns:
            Tuple of (parent_program, list_of_inspirations)
        """
        if not self.programs:
            raise ValueError("Cannot sample from empty database")

        # Select parent using strategy
        parent = self._sample_parent(island_id)

        # Sample inspirations (diverse programs)
        inspirations = self._sample_inspirations(num_inspirations, exclude={parent.id})

        return parent, inspirations

    async def add_async(
        self,
        program: Program,
        target_island: Optional[int] = None,
        reference_codes: Optional[List[str]] = None,
    ) -> bool:
        """
        Thread-safe async version of add().

        Args:
            program: Program to add
            target_island: Specific island to add to (random if None)
            reference_codes: Reference codes for diversity calculation

        Returns:
            True if program was added (might replace existing)
        """
        async with self._lock:
            return self.add(program, target_island, reference_codes)

    async def sample_async(
        self,
        num_inspirations: int = 2,
        island_id: Optional[int] = None,
    ) -> Tuple[Program, List[Program]]:
        """
        Thread-safe async version of sample().

        Args:
            num_inspirations: Number of inspiration programs to sample
            island_id: Specific island to sample from (random if None)

        Returns:
            Tuple of (parent_program, list_of_inspirations)
        """
        async with self._lock:
            return self.sample(num_inspirations, island_id)

    def _sample_parent(self, island_id: Optional[int] = None) -> Program:
        """Sample a parent program using configured strategy."""
        rand_val = random.random()

        if rand_val < self.config.exploration_ratio:
            # Random sampling for exploration
            return self._sample_random(island_id)
        elif rand_val < self.config.exploration_ratio + self.config.exploitation_ratio:
            # Elite sampling for exploitation
            return self._sample_from_archive()
        else:
            # Fitness-weighted sampling
            return self._sample_weighted(island_id)

    def _sample_random(self, island_id: Optional[int] = None) -> Program:
        """Sample random program from population."""
        if island_id is not None and self.islands[island_id]:
            program_id = random.choice(list(self.islands[island_id]))
        else:
            program_id = random.choice(list(self.programs.keys()))
        return self.programs[program_id]

    def _sample_from_archive(self) -> Program:
        """Sample from elite archive with fitness-weighted probability."""
        if not self.archive:
            return self._sample_random()

        # Calculate fitness weights for archive members
        weights = []
        valid_candidates = []
        for pid in self.archive:
            program = self.programs.get(pid)
            if program:
                fitness = program.fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                )
                # Use fitness as weight (higher fitness = higher probability)
                # Add small epsilon to avoid zero weights
                weights.append(max(fitness, 0.001))
                valid_candidates.append(pid)

        if not valid_candidates:
            return self._sample_random()

        # Weighted random selection
        selected_id = random.choices(valid_candidates, weights=weights, k=1)[0]
        return self.programs[selected_id]

    def _sample_weighted(self, island_id: Optional[int] = None) -> Program:
        """Sample program weighted by fitness."""
        if island_id is not None and self.islands[island_id]:
            candidates = list(self.islands[island_id])
        else:
            candidates = list(self.programs.keys())

        if not candidates:
            return self._sample_random()

        # Calculate fitness weights
        weights = []
        valid_candidates = []
        for pid in candidates:
            program = self.programs.get(pid)
            if program:
                fitness = program.fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                )
                # Add small epsilon to avoid zero weights
                weights.append(max(fitness, 0.001))
                valid_candidates.append(pid)

        if not valid_candidates:
            return self._sample_random()

        # Weighted random selection
        total = sum(weights)
        weights = [w / total for w in weights]
        selected_id = random.choices(valid_candidates, weights=weights, k=1)[0]
        return self.programs[selected_id]

    def _sample_inspirations(
        self,
        num: int,
        exclude: Optional[Set[str]] = None,
    ) -> List[Program]:
        """Sample diverse inspiration programs using dynamic bins."""
        exclude = exclude or set()
        inspirations = []

        # Sample from different islands for diversity
        available_islands = list(range(self.config.num_islands))
        random.shuffle(available_islands)

        for island_id in available_islands:
            if len(inspirations) >= num:
                break

            island_programs = self.islands[island_id] - exclude
            if island_programs:
                # Get filled bins dynamically
                filled_bins = list(self.iter_filled_bins(island_id))
                if filled_bins:
                    # Sample from different bins for diversity
                    random.shuffle(filled_bins)
                    for bin_coords, pid in filled_bins:
                        if len(inspirations) >= num:
                            break
                        if pid not in exclude and pid in self.programs:
                            inspirations.append(self.programs[pid])
                            exclude.add(pid)

        # If still need more, sample randomly
        remaining = num - len(inspirations)
        if remaining > 0:
            all_ids = set(self.programs.keys()) - exclude
            sample_ids = random.sample(list(all_ids), min(remaining, len(all_ids)))
            for pid in sample_ids:
                if pid in self.programs:
                    inspirations.append(self.programs[pid])

        return inspirations

    def get_best_program(self) -> Optional[Program]:
        """Get the best program found so far."""
        if self.best_program_id:
            return self.programs.get(self.best_program_id)
        return None

    def get_children(self, parent_id: str) -> List[Program]:
        """
        Get all direct children of a program.

        Args:
            parent_id: ID of the parent program

        Returns:
            List of child programs
        """
        return [p for p in self.programs.values() if p.parent_id == parent_id]

    def get_sibling_summaries(
        self,
        parent_id: str,
        exclude_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get mutation summary info for sibling programs (same parent).

        Used for constructing evolution history in prompts.
        fitness_delta is computed dynamically using current metric_ranges.

        Args:
            parent_id: ID of the parent program
            exclude_id: Program ID to exclude from results

        Returns:
            List of dicts with: summary, category, is_algorithmic, fitness_delta, metrics_delta, order
        """
        children = self.get_children(parent_id)
        parent = self.programs.get(parent_id)
        parent_fitness = parent.fitness_score(
            self.config.feature_dimensions,
            self.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        ) if parent else 0.0

        result = []
        for child in children:
            if exclude_id and child.id == exclude_id:
                continue
            if child.mutation_summary:  # Only include programs with summaries
                # Compute fitness_delta dynamically
                child_fitness = child.fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                )
                fitness_delta = child_fitness - parent_fitness

                # Compute metrics_delta dynamically
                metrics_delta = {}
                parent_metrics = parent.metrics if parent else {}
                for k, v in child.metrics.items():
                    if k in parent_metrics and isinstance(v, (int, float)):
                        metrics_delta[k] = v - parent_metrics[k]

                result.append({
                    "summary": child.mutation_summary,
                    "category": child.mutation_category,
                    "is_algorithmic": child.is_algorithmic,
                    "fitness_delta": fitness_delta,
                    "metrics_delta": metrics_delta,
                    "order": child.order,
                })
        return result

    def get_ancestor_chain(self, program_id: str) -> List[Program]:
        """
        Get the complete ancestor chain from root to the given program.

        Args:
            program_id: ID of the program

        Returns:
            List of Programs from root to target (excluding root, excluding target)
        """
        chain = []
        current_id = program_id

        # Traverse up to root
        while current_id is not None:
            if current_id not in self.programs:
                break
            program = self.programs[current_id]
            if program.parent_id is not None:  # Don't include root
                chain.insert(0, program)
            current_id = program.parent_id

        # Remove the target itself if it was added
        if chain and chain[-1].id == program_id:
            chain.pop()

        return chain

    def get_ancestor_summaries(self, program_id: str) -> List[Dict[str, Any]]:
        """
        Get mutation summary info for ancestor chain.

        Used for constructing evolution history in prompts.
        fitness_delta is computed dynamically using current metric_ranges.

        Args:
            program_id: ID of the program

        Returns:
            List of dicts with: summary, category, is_algorithmic, fitness_delta, metrics_delta, order, generation
        """
        chain = self.get_ancestor_chain(program_id)
        result = []
        for prog in chain:
            # Compute fitness_delta dynamically
            prog_fitness = prog.fitness_score(
                self.config.feature_dimensions,
                self.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )
            if prog.parent_id and prog.parent_id in self.programs:
                parent = self.programs[prog.parent_id]
                parent_fitness = parent.fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                )
                fitness_delta = prog_fitness - parent_fitness

                # Compute metrics_delta dynamically
                metrics_delta = {}
                for k, v in prog.metrics.items():
                    if k in parent.metrics and isinstance(v, (int, float)):
                        metrics_delta[k] = v - parent.metrics[k]
            else:
                # Root program, no delta
                fitness_delta = None
                metrics_delta = {}

            result.append({
                "summary": prog.mutation_summary or f"Generation {prog.generation} mutation",
                "category": prog.mutation_category,
                "is_algorithmic": prog.is_algorithmic,
                "fitness_delta": fitness_delta,
                "metrics_delta": metrics_delta,
                "order": prog.order,
                "generation": prog.generation,
            })
        return result

    def get_top_programs(
        self,
        n: int = 5,
        metric: str = "fitness",
        island_id: Optional[int] = None,
    ) -> List[Program]:
        """
        Get top N programs by metric.

        Args:
            n: Number of programs to return
            metric: Metric name to sort by (default: "fitness" for dynamic fitness score)
            island_id: Specific island (all if None)

        Returns:
            List of top programs
        """
        if island_id is not None:
            candidates = [
                self.programs[pid]
                for pid in self.islands[island_id]
                if pid in self.programs
            ]
        else:
            candidates = list(self.programs.values())

        # Sort by metric
        def get_metric(p: Program) -> float:
            if metric == "fitness":
                return p.fitness_score(
                    self.config.feature_dimensions,
                    self.metric_ranges,
                    self.config.function_weight,
                    self.config.llm_weight,
                )
            return p.metrics.get(metric, 0.0)

        candidates.sort(key=get_metric, reverse=True)
        return candidates[:n]

    def migrate(self, migration_rate: Optional[float] = None) -> int:
        """
        Perform island migration.

        Copies top programs between islands for genetic diversity.

        Args:
            migration_rate: Fraction of programs to migrate (uses config if None)

        Returns:
            Number of programs migrated
        """
        if self.config.num_islands < 2:
            return 0

        migration_rate = migration_rate or self.config.migration_rate
        migrated = 0

        for source_island in range(self.config.num_islands):
            # Get top programs from source island
            top_programs = self.get_top_programs(
                n=max(1, int(len(self.islands[source_island]) * migration_rate)),
                island_id=source_island,
            )

            # Migrate to next island (ring topology)
            target_island = (source_island + 1) % self.config.num_islands

            for program in top_programs:
                # Don't duplicate, just add reference to target island
                self.islands[target_island].add(program.id)
                migrated += 1

        logger.debug(f"Migration complete: {migrated} programs migrated")
        return migrated

    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics."""
        fitness_values = [
            p.fitness_score(
                self.config.feature_dimensions,
                self.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )
            for p in self.programs.values()
        ]

        # Count unique filled bins per island (dynamically computed)
        filled_bin_counts = []
        for island_id in range(self.config.num_islands):
            filled_bins = set(bin_coords for bin_coords, _ in self.iter_filled_bins(island_id))
            filled_bin_counts.append(len(filled_bins))

        return {
            "total_programs": len(self.programs),
            "total_added": self.total_added,
            "total_improved": self.total_improved,
            "archive_size": len(self.archive),
            "num_islands": self.config.num_islands,
            "island_sizes": [len(island) for island in self.islands],
            "filled_bin_counts": filled_bin_counts,
            "best_fitness": max(fitness_values) if fitness_values else 0.0,
            "avg_fitness": sum(fitness_values) / len(fitness_values) if fitness_values else 0.0,
            "min_fitness": min(fitness_values) if fitness_values else 0.0,
        }

    def save(self, path: str) -> None:
        """
        Save database to directory.

        Args:
            path: Directory to save to
        """
        save_dir = Path(path)
        save_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata
        metadata = {
            "config": self.config.to_dict(),
            "best_program_id": self.best_program_id,
            "archive": list(self.archive),
            "islands": [list(island) for island in self.islands],
            "total_added": self.total_added,
            "total_improved": self.total_improved,
            "feature_ranges": {k: list(v) for k, v in self.feature_ranges.items()},
            "metric_ranges": {k: list(v) for k, v in self.metric_ranges.items()},
            "next_order": self._next_order,
        }
        with open(save_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        # Save programs
        programs_dir = save_dir / "programs"
        programs_dir.mkdir(exist_ok=True)

        for program_id, program in self.programs.items():
            program.save(str(programs_dir / f"{program_id}.json"))

        # Save island coordinates (for dynamic bin calculation)
        with open(save_dir / "island_coordinates.json", "w", encoding="utf-8") as f:
            json.dump(self.island_coordinates, f, indent=2)

        logger.info(f"Saved database with {len(self.programs)} programs to {path}")

    @classmethod
    def load(cls, path: str) -> "EvolutionDatabase":
        """
        Load database from directory.

        Args:
            path: Directory to load from

        Returns:
            Loaded EvolutionDatabase
        """
        load_dir = Path(path)

        # Load metadata
        with open(load_dir / "metadata.json", "r", encoding="utf-8") as f:
            metadata = json.load(f)

        config = EvolutionConfig.from_dict(metadata.get("config", {}))
        db = cls(config=config)

        db.best_program_id = metadata.get("best_program_id")
        db.archive = set(metadata.get("archive", []))
        db.islands = [set(island) for island in metadata.get("islands", [])]
        db.total_added = metadata.get("total_added", 0)
        db.total_improved = metadata.get("total_improved", 0)
        # Restore feature ranges
        feature_ranges_data = metadata.get("feature_ranges", {})
        db.feature_ranges = {k: tuple(v) for k, v in feature_ranges_data.items()}
        # Restore metric ranges
        metric_ranges_data = metadata.get("metric_ranges", {})
        db.metric_ranges = {k: tuple(v) for k, v in metric_ranges_data.items()}
        # Restore sequence counter
        db._next_order = metadata.get("next_order", 0)

        # Load programs
        programs_dir = load_dir / "programs"
        if programs_dir.exists():
            for program_file in programs_dir.glob("*.json"):
                program = Program.load(str(program_file))
                db.programs[program.id] = program

        # Try to load island_coordinates (new format)
        coordinates_path = load_dir / "island_coordinates.json"
        if coordinates_path.exists():
            with open(coordinates_path, "r", encoding="utf-8") as f:
                db.island_coordinates = json.load(f)
        else:
            # Backward compatibility: rebuild coordinates from programs
            logger.info("Rebuilding island_coordinates from programs (legacy format)")
            db._rebuild_coordinates_from_programs()

        # Ensure correct number of islands
        while len(db.islands) < config.num_islands:
            db.islands.append(set())
        while len(db.island_coordinates) < config.num_islands:
            db.island_coordinates.append({})

        # Backward compatibility: rebuild metric_ranges if empty
        if not db.metric_ranges and db.programs:
            logger.info("Rebuilding metric_ranges from programs (legacy format)")
            db._rebuild_metric_ranges_from_programs()

        # Recalculate best_program_id using current fitness calculation
        # This fixes issues with legacy databases where fitness was calculated incorrectly
        db._recalculate_best_program()

        logger.info(f"Loaded database with {len(db.programs)} programs from {path}")
        return db

    def _rebuild_coordinates_from_programs(self) -> None:
        """
        Rebuild island_coordinates from stored programs.

        Used for backward compatibility with old database format.
        """
        # Initialize island_coordinates
        self.island_coordinates = [{} for _ in range(self.config.num_islands)]

        for prog_id, program in self.programs.items():
            island_id = program.island_id
            if island_id is None:
                island_id = 0

            # Ensure island exists
            while island_id >= len(self.island_coordinates):
                self.island_coordinates.append({})

            # Calculate and store coordinates
            coords = program.feature_coordinates(self.config.feature_dimensions)
            self.island_coordinates[island_id][prog_id] = coords

        logger.info(f"Rebuilt coordinates for {len(self.programs)} programs")

    def _rebuild_metric_ranges_from_programs(self) -> None:
        """
        Rebuild metric_ranges from stored programs.

        Used for backward compatibility with old database format.
        """
        self.metric_ranges = {}
        for program in self.programs.values():
            self._update_metric_ranges(program.metrics)
        logger.info(f"Rebuilt metric_ranges from {len(self.programs)} programs")

    def _recalculate_best_program(self) -> None:
        """
        Recalculate best_program_id using current fitness calculation.

        This fixes issues with legacy databases where fitness was calculated
        incorrectly (e.g., using fallback logic that gave high scores to
        failed evaluations).
        """
        if not self.programs:
            self.best_program_id = None
            return

        best_id = None
        best_fitness = -float('inf')

        for prog_id, program in self.programs.items():
            fitness = program.fitness_score(
                self.config.feature_dimensions,
                self.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )
            if fitness > best_fitness:
                best_fitness = fitness
                best_id = prog_id

        old_best = self.best_program_id
        self.best_program_id = best_id

        if old_best != best_id:
            logger.info(
                f"Recalculated best_program_id: {old_best[:8] if old_best else 'None'} -> "
                f"{best_id[:8] if best_id else 'None'} (fitness: {best_fitness:.4f})"
            )
