"""
Evolution team for coordinating multi-agent code evolution.

EvolutionTeam orchestrates:
- Mutator agent for generating code changes
- Evaluator for scoring programs
- Optional critic agent for analysis
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pantheon.utils.log import logger

from .config import EvolutionConfig
from .database import EvolutionDatabase
from .evaluator import EvaluationResult, HybridEvaluator
from .program import CodebaseSnapshot, Program
from .prompt_builder import EvolutionPromptBuilder, MUTATION_SYSTEM_PROMPT_CODEBASE
from .result import EvolutionResult, IterationResult
from .utils.diff import parse_diff, apply_diff


class EvolutionTeam:
    """
    Evolution team coordinating mutation, evaluation, and selection.

    Implements the core evolution loop:
    1. Sample parent and inspirations from database
    2. Build mutation prompt
    3. Generate mutation via LLM
    4. Apply diff to create child program
    5. Evaluate child
    6. Add to database (MAP-Elites decides if kept)
    7. Repeat
    """

    def __init__(
        self,
        mutator: Optional[Any] = None,  # Agent
        evaluator: Optional[Union[HybridEvaluator, Any]] = None,  # HybridEvaluator or Agent
        critic: Optional[Any] = None,  # Agent
        database: Optional[EvolutionDatabase] = None,
        config: Optional[EvolutionConfig] = None,
    ):
        """
        Initialize evolution team.

        Args:
            mutator: Agent for generating mutations (created if None)
            evaluator: HybridEvaluator or Agent for evaluation
            critic: Optional critic agent for failure analysis
            database: Program database (created if None)
            config: Evolution configuration (created if None)
        """
        self.config = config or EvolutionConfig()
        self.database = database or EvolutionDatabase(config=self.config)
        self.prompt_builder = EvolutionPromptBuilder(
            max_code_length=self.config.max_code_length,
            max_top_programs=self.config.num_top_programs,
            max_inspirations=self.config.num_inspirations,
        )

        # Agents (lazy-initialized)
        self._mutator = mutator
        self._evaluator = evaluator
        self._critic = critic

        # State
        self.objective: str = ""
        self.evaluator_code: str = ""
        self._initialized = False

    async def _ensure_mutator(self):
        """Ensure mutator agent is initialized."""
        if self._mutator is None:
            try:
                from pantheon.agent import Agent
                self._mutator = Agent(
                    name="code-mutator",
                    instructions=MUTATION_SYSTEM_PROMPT_CODEBASE,
                    model=self.config.mutator_model,
                    use_memory=False,  # Prevent context accumulation across iterations
                )
            except ImportError:
                raise RuntimeError("Pantheon Agent not available for mutation")
        return self._mutator

    async def _ensure_evaluator(self):
        """Ensure evaluator is initialized."""
        if self._evaluator is None:
            self._evaluator = HybridEvaluator(
                evaluator_code=self.evaluator_code,
                function_weight=self.config.function_weight,
                llm_weight=self.config.llm_weight,
                max_parallel=self.config.max_parallel_evaluations,
                timeout=self.config.evaluation_timeout,
                workspace_base=self.config.workspace_path,
                feedback_max_lines_per_file=self.config.feedback_max_lines_per_file,
            )
        return self._evaluator

    async def evolve(
        self,
        initial_code: Union[str, CodebaseSnapshot],
        evaluator_code: str,
        objective: str,
        max_iterations: Optional[int] = None,
        initial_path: Optional[str] = None,
        resume_from: Optional[str] = None,
        **kwargs,
    ) -> EvolutionResult:
        """
        Run the evolution loop.

        Args:
            initial_code: Initial code string or CodebaseSnapshot
            evaluator_code: Python code defining evaluate(workspace_path) function
            objective: Natural language optimization objective
            max_iterations: Override config max_iterations
            initial_path: Path for loading initial codebase (if initial_code is path)
            resume_from: Path to resume from (directory with evolution_state.json)
            **kwargs: Additional arguments

        Returns:
            EvolutionResult with best program and history
        """
        max_iterations = max_iterations or self.config.max_iterations
        self.objective = objective
        self.evaluator_code = evaluator_code

        # Initialize result
        result = EvolutionResult(
            config_used=self.config.to_dict(),
        )

        # Check if resuming from checkpoint
        start_iteration = 0
        best_score = 0
        generations_without_improvement = 0

        if resume_from:
            resume_path = Path(resume_from)
            state_file = resume_path / "evolution_state.json"

            if state_file.exists():
                logger.info(f"Resuming evolution from {resume_from}")

                # Load database
                self.database = EvolutionDatabase.load(str(resume_path))

                # Load evolution state
                with open(state_file, "r") as f:
                    state = json.load(f)

                start_iteration = state.get("current_iteration", 0) + 1
                best_score = state.get("best_score", 0)
                result.score_history = state.get("score_history", [])
                result.best_score_history = state.get("best_score_history", [])
                generations_without_improvement = state.get("generations_without_improvement", 0)

                # Restore objective and evaluator if not provided
                if not objective:
                    self.objective = state.get("objective", "")
                if not evaluator_code:
                    self.evaluator_code = state.get("evaluator_code", "")

                logger.info(f"Resumed from iteration {start_iteration}, best_score={best_score:.4f}")
                logger.info(f"Database has {len(self.database.programs)} programs")
            else:
                logger.warning(f"No evolution_state.json found in {resume_from}, starting fresh")
                resume_from = None

        if not resume_from:
            # Create initial snapshot
            if isinstance(initial_code, CodebaseSnapshot):
                initial_snapshot = initial_code
            elif initial_path:
                initial_snapshot = CodebaseSnapshot.from_directory(initial_path)
            else:
                # Single file code
                initial_snapshot = CodebaseSnapshot.from_single_file("main.py", initial_code)

            # Create and evaluate initial program
            initial_program = Program(
                id=str(uuid.uuid4())[:8],
                snapshot=initial_snapshot,
                generation=0,
            )

            logger.info(f"Starting evolution with {initial_program.file_count()} files, "
                       f"{initial_program.total_lines()} lines")

            # Evaluate initial program
            evaluator = await self._ensure_evaluator()
            eval_result = await evaluator.evaluate(initial_program)

            initial_program.metrics = eval_result.metrics
            initial_program.artifacts = eval_result.artifacts
            initial_program.llm_feedback = eval_result.llm_feedback

            self.database.add(initial_program)

            initial_score = initial_program.fitness_score(self.config.feature_dimensions)
            result.score_history.append(initial_score)
            result.best_score_history.append(initial_score)
            best_score = initial_score

            logger.info(f"Initial program score: {initial_score:.4f}")

        # Evolution loop
        for iteration in range(start_iteration, max_iterations):
            iter_start = time.time()

            try:
                iter_result = await self._run_iteration(iteration, max_iterations)
                result.iteration_results.append(iter_result)

                # Track scores
                result.score_history.append(iter_result.child_score)

                if iter_result.child_score > best_score:
                    best_score = iter_result.child_score
                    generations_without_improvement = 0
                    logger.info(
                        f"  ★ New best score: {best_score:.4f} "
                        f"(+{iter_result.improvement:.4f})"
                    )
                else:
                    generations_without_improvement += 1

                result.best_score_history.append(best_score)

                # Periodic logging
                if self.config.log_iterations and iteration % 10 == 0 and iteration > 0:
                    stats = self.database.get_statistics()
                    logger.info(
                        f"--- Progress: {iteration}/{max_iterations}, best={best_score:.4f}, "
                        f"avg={stats['avg_fitness']:.4f}, programs={stats['total_programs']} ---"
                    )

                # Periodic migration
                if iteration > 0 and iteration % self.config.migration_interval == 0:
                    self.database.migrate()

                # Periodic checkpoint
                if self.config.db_path and iteration % self.config.checkpoint_interval == 0:
                    self._save_checkpoint(
                        self.config.db_path,
                        iteration,
                        best_score,
                        result.score_history,
                        result.best_score_history,
                        generations_without_improvement,
                    )

                # Early stopping
                if generations_without_improvement >= self.config.early_stop_generations:
                    logger.info(
                        f"Early stopping: no improvement for "
                        f"{generations_without_improvement} generations"
                    )
                    break

            except Exception as e:
                logger.error(f"Iteration {iteration} failed: {e}")
                result.errors.append(f"Iteration {iteration}: {e}")
                result.iteration_results.append(
                    IterationResult(
                        iteration=iteration,
                        parent_id="",
                        child_id="",
                        parent_score=0,
                        child_score=0,
                        improvement=0,
                        accepted=False,
                        error=str(e),
                    )
                )

        # Finalize result
        result.total_iterations = len(result.iteration_results)
        result.best_program = self.database.get_best_program()
        result.best_score = best_score
        result.database = self.database
        result.finalize()

        # Save final checkpoint
        if self.config.db_path:
            self._save_checkpoint(
                self.config.db_path,
                iteration,
                best_score,
                result.score_history,
                result.best_score_history,
                generations_without_improvement,
            )

        logger.info(result.get_summary())

        return result

    def _save_checkpoint(
        self,
        path: str,
        iteration: int,
        best_score: float,
        score_history: List[float],
        best_score_history: List[float],
        generations_without_improvement: int,
    ) -> None:
        """Save evolution checkpoint including state for resume."""
        # Save database
        self.database.save(path)

        # Save evolution state
        state = {
            "current_iteration": iteration,
            "best_score": best_score,
            "score_history": score_history,
            "best_score_history": best_score_history,
            "generations_without_improvement": generations_without_improvement,
            "objective": self.objective,
            "evaluator_code": self.evaluator_code,
        }

        state_path = Path(path) / "evolution_state.json"
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)

        logger.info(f"Checkpoint saved: iteration {iteration}, {len(self.database.programs)} programs")

    async def _run_iteration(self, iteration: int, max_iterations: int = 0) -> IterationResult:
        """
        Run a single evolution iteration.

        Args:
            iteration: Current iteration number
            max_iterations: Total iterations for logging progress

        Returns:
            IterationResult with details
        """
        iter_start = time.time()
        logger.info(f"[{iteration + 1}/{max_iterations}] Starting iteration...")

        # Sample parent and inspirations
        parent, inspirations = self.database.sample(
            num_inspirations=self.config.num_inspirations,
        )

        parent_score = parent.fitness_score(self.config.feature_dimensions)

        # Get top programs for reference
        top_programs = self.database.get_top_programs(
            n=self.config.num_top_programs,
        )

        # Build mutation prompt
        prompt = self.prompt_builder.build_mutation_prompt(
            parent=parent,
            objective=self.objective,
            top_programs=top_programs,
            inspirations=inspirations,
            artifacts=parent.artifacts,
            iteration=iteration,
        )

        # Generate mutation with timeout
        mutation_start = time.time()
        mutator = await self._ensure_mutator()

        try:
            response = await asyncio.wait_for(
                mutator.run(prompt, update_memory=False),
                timeout=self.config.mutation_timeout
            )
            mutation_time = time.time() - mutation_start
            logger.info(f"  Mutation: {mutation_time:.1f}s")
        except asyncio.TimeoutError:
            mutation_time = time.time() - mutation_start
            logger.warning(f"  Mutation timeout after {mutation_time:.1f}s, using parent unchanged")
            return IterationResult(
                iteration=iteration,
                parent_id=parent.id,
                child_id=parent.id,
                parent_score=parent_score,
                child_score=parent_score,
                improvement=0,
                accepted=False,
                mutation_time=mutation_time,
                evaluation_time=0,
                total_time=time.time() - iter_start,
                error="mutation_timeout",
            )

        # Apply mutation
        num_changes = 0
        try:
            child_snapshot = self._apply_mutation(parent.snapshot, response.content)
            # Count changes
            default_file = next(iter(parent.snapshot.files.keys()), "main.py")
            changes = parse_diff(response.content, default_file)
            num_changes = len(changes)
            logger.info(f"  Applied {num_changes} change(s)")
        except Exception as e:
            logger.warning(f"  Failed to apply mutation: {e}")
            child_snapshot = parent.snapshot

        # Create child program
        child = Program(
            id=str(uuid.uuid4())[:8],
            snapshot=child_snapshot,
            diff_from_parent=child_snapshot.diff_from(parent.snapshot),
            parent_id=parent.id,
            generation=parent.generation + 1,
            prompt_used=prompt if self.config.save_prompts else "",
        )

        # Evaluate child
        eval_start = time.time()
        evaluator = await self._ensure_evaluator()
        eval_result = await evaluator.evaluate(child)
        eval_time = time.time() - eval_start

        child.metrics = eval_result.metrics
        child.artifacts = eval_result.artifacts
        child.llm_feedback = eval_result.llm_feedback

        child_score = child.fitness_score(self.config.feature_dimensions)
        improvement = child_score - parent_score

        # Log evaluation results
        logger.info(f"  Evaluation: {eval_time:.1f}s, score: {child_score:.4f}")

        # Log key metrics if available
        metrics = child.metrics
        metric_parts = []
        for key in ['mixing_score', 'speed_score', 'combined_score']:
            if key in metrics:
                metric_parts.append(f"{key.replace('_score', '')}={metrics[key]:.3f}")
        if metric_parts:
            logger.info(f"  Metrics: {', '.join(metric_parts)}")

        # Add to database (MAP-Elites decides if kept)
        accepted = self.database.add(child)

        total_time = time.time() - iter_start

        # Log result
        result_str = "✓ Improved" if improvement > 0 else "✗ No improvement"
        accepted_str = "(accepted)" if accepted else "(rejected)"
        logger.info(f"  Result: {result_str} ({improvement:+.4f}) {accepted_str} [{total_time:.1f}s]")

        return IterationResult(
            iteration=iteration,
            parent_id=parent.id,
            child_id=child.id,
            parent_score=parent_score,
            child_score=child_score,
            improvement=improvement,
            accepted=accepted,
            mutation_time=mutation_time,
            evaluation_time=eval_time,
            total_time=total_time,
        )

    def _apply_mutation(
        self,
        parent_snapshot: CodebaseSnapshot,
        mutation_response: str,
    ) -> CodebaseSnapshot:
        """
        Apply LLM mutation response to parent snapshot.

        Args:
            parent_snapshot: Parent codebase snapshot
            mutation_response: LLM response with SEARCH/REPLACE blocks

        Returns:
            New CodebaseSnapshot with mutations applied
        """
        # Parse changes from response
        default_file = next(iter(parent_snapshot.files.keys()), "main.py")
        changes = parse_diff(mutation_response, default_file)

        if not changes:
            logger.warning("No valid changes parsed from mutation response")
            return parent_snapshot

        # Apply changes
        new_files = apply_diff(parent_snapshot.files, changes)

        return CodebaseSnapshot(
            files=new_files,
            base_path=parent_snapshot.base_path,
        )


async def evolve(
    initial_code: Union[str, CodebaseSnapshot],
    evaluator_code: str,
    objective: str,
    config: Optional[EvolutionConfig] = None,
    **kwargs,
) -> EvolutionResult:
    """
    Convenience function to run evolution.

    Args:
        initial_code: Initial code or CodebaseSnapshot
        evaluator_code: Evaluation function code
        objective: Optimization objective
        config: Evolution configuration
        **kwargs: Additional arguments

    Returns:
        EvolutionResult
    """
    team = EvolutionTeam(config=config)
    return await team.evolve(
        initial_code=initial_code,
        evaluator_code=evaluator_code,
        objective=objective,
        **kwargs,
    )
