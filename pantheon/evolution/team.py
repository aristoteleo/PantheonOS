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
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pantheon.utils.log import logger

from .config import EvolutionConfig
from .database import EvolutionDatabase
from .evaluator import EvaluationResult, HybridEvaluator
from .program import CodebaseSnapshot, Program
from .prompt_builder import (
    EvolutionPromptBuilder,
    MUTATION_SYSTEM_PROMPT_CODEBASE,
    MUTATION_SYSTEM_PROMPT_SIMPLE,
    SUMMARIZER_SYSTEM_PROMPT,
)
from .result import EvolutionResult, IterationResult
from .utils.diff import parse_diff, apply_diff


def think(thought: str) -> str:
    """
    Use this tool to think through problems step by step.

    Args:
        thought: Your reasoning, analysis, or intermediate thoughts

    Returns:
        Acknowledgment that thinking was recorded
    """
    return "Thought recorded. Continue your analysis."


def compute_deltas(
    parent: Program,
    child: Program,
    feature_dimensions: List[str],
    metric_ranges: Dict[str, Tuple[float, float]] = None,
    function_weight: float = 1.0,
    llm_weight: float = 0.0,
) -> tuple:
    """
    Compute fitness and metrics deltas between parent and child.

    Args:
        parent: Parent program
        child: Child program
        feature_dimensions: Feature dimensions for fitness calculation
        metric_ranges: Optional dict of metric name -> (min, max) for normalization
        function_weight: Weight for function_score (default 1.0)
        llm_weight: Weight for llm_score (default 0.0)

    Returns:
        Tuple of (fitness_delta, metrics_delta)
        - fitness_delta: Change in fitness score
        - metrics_delta: Dict of per-metric changes (missing metrics treated as 0)
    """
    # fitness_delta uses the new fitness formula
    fitness_delta = (
        child.fitness_score(feature_dimensions, metric_ranges, function_weight, llm_weight)
        - parent.fitness_score(feature_dimensions, metric_ranges, function_weight, llm_weight)
    )

    # metrics_delta records per-metric changes (only numeric values)
    all_keys = set(parent.metrics.keys()) | set(child.metrics.keys())
    metrics_delta = {}
    for key in all_keys:
        child_val = child.metrics.get(key, 0.0)
        parent_val = parent.metrics.get(key, 0.0)
        # Only compute delta for numeric values
        if isinstance(child_val, (int, float)) and isinstance(parent_val, (int, float)):
            metrics_delta[key] = child_val - parent_val

    return fitness_delta, metrics_delta


def extract_cost_from_response(response) -> float:
    """
    Extract LLM cost from an agent response.

    Args:
        response: AgentResponse from agent.run()

    Returns:
        Cost in USD, or 0.0 if not available
    """
    try:
        if response and response.details and response.details.messages:
            # Find the last assistant message which contains cost info
            for msg in reversed(response.details.messages):
                if msg.get("role") == "assistant" and "_metadata" in msg:
                    return msg.get("_metadata", {}).get("current_cost", 0.0)
    except Exception:
        pass
    return 0.0


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
        analyzer: Optional[Any] = None,  # Agent for code analysis
        critic: Optional[Any] = None,  # Agent
        database: Optional[EvolutionDatabase] = None,
        config: Optional[EvolutionConfig] = None,
    ):
        """
        Initialize evolution team.

        Args:
            mutator: Agent for generating mutations (created if None)
            evaluator: HybridEvaluator or Agent for evaluation
            analyzer: Agent for code analysis (created if None when use_analyzer=True)
            critic: Optional critic agent for failure analysis
            database: Program database (created if None)
            config: Evolution configuration (created if None)
        """
        self.config = config or EvolutionConfig()

        # Configure log level from config
        if self.config.log_level:
            from pantheon.utils.log import set_level
            set_level(self.config.log_level)

        self.database = database or EvolutionDatabase(config=self.config)
        self.prompt_builder = EvolutionPromptBuilder(
            max_code_length=self.config.max_code_length,
            max_top_programs=self.config.num_top_programs,
            max_inspirations=self.config.num_inspirations,
        )

        # Agents (lazy-initialized)
        self._mutator = mutator
        self._evaluator = evaluator
        self._analyzer = analyzer
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
                # Use simplified prompt when analyzer is enabled
                system_prompt = (
                    MUTATION_SYSTEM_PROMPT_SIMPLE
                    if self.config.use_analyzer
                    else MUTATION_SYSTEM_PROMPT_CODEBASE
                )
                self._mutator = Agent(
                    name="code-mutator",
                    instructions=system_prompt,
                    model=self.config.mutator_model,
                    use_memory=False,  # Prevent context accumulation across iterations
                )
            except ImportError:
                raise RuntimeError("Pantheon Agent not available for mutation")
        return self._mutator

    def _create_analyzer(self, generation: int):
        """
        Create analyzer agent with generation-appropriate system prompt.

        The analyzer's optimization direction (exploration vs exploitation) is
        probabilistically determined based on generation. Early generations favor
        algorithm-level exploration; later generations favor implementation-level
        exploitation.

        If user provided a custom analyzer at init time, returns that instead
        (with direction="custom" and probability=0.0).

        Args:
            generation: Current program generation for adaptive prompt selection

        Returns:
            Tuple of (analyzer_agent, direction, exploration_probability)
        """
        # If user provided custom analyzer, use it without adaptive prompts
        if self._analyzer is not None:
            return self._analyzer, "custom", 0.0

        from pantheon.agent import Agent

        # Get adaptive system prompt based on generation
        system_prompt, direction, exploration_prob = self.prompt_builder.get_analyzer_system_prompt(
            generation=generation,
            initial_prob=self.config.analyzer_exploration_initial,
            final_prob=self.config.analyzer_exploration_final,
            decay_generations=self.config.analyzer_exploration_decay_generations,
        )

        analyzer = Agent(
            name="code-analyzer",
            instructions=system_prompt,
            model=self.config.analyzer_model,
            tools=[think],  # Add thinking tool for deeper reasoning
            use_memory=False,  # Prevent context accumulation across iterations
        )

        return analyzer, direction, exploration_prob

    def _create_summarizer(self):
        """
        Create summarizer agent for extracting exploration directions.

        The summarizer is a lightweight agent that extracts structured
        direction information from analyzer output.

        Returns:
            Summarizer agent
        """
        from pantheon.agent import Agent

        return Agent(
            name="direction-summarizer",
            instructions=SUMMARIZER_SYSTEM_PROMPT,
            model="low",  # Use low-cost model for summarization
            use_memory=False,
        )

    async def _extract_direction(
        self,
        analysis_text: str,
        diff_text: str = "",
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Extract exploration direction from analyzer output and diff using summarizer.

        The summarizer analyzes both the proposed changes (analysis) and the actual
        code changes (diff) to determine what was actually implemented.

        Args:
            analysis_text: The analyzer's output text with proposed changes
            diff_text: The actual code diff that was applied
            timeout: Timeout for summarizer call

        Returns:
            Dict with keys: direction, category, is_algorithmic, match_confidence
            Returns default values if extraction fails
        """
        default_result = {
            "direction": "No clear direction proposed",
            "category": "other",
            "is_algorithmic": False,
            "match_confidence": "low",
        }

        if not analysis_text or len(analysis_text.strip()) < 20:
            return default_result

        try:
            summarizer = self._create_summarizer()

            # Build prompt with both analysis and diff
            prompt_parts = ["## ANALYSIS (proposed changes):", analysis_text]

            if diff_text and diff_text.strip():
                # Truncate diff if too long
                max_diff_len = 3000
                if len(diff_text) > max_diff_len:
                    diff_text = diff_text[:max_diff_len] + "\n... (truncated)"
                prompt_parts.append("\n## DIFF (actual code changes):")
                prompt_parts.append(diff_text)
            else:
                prompt_parts.append("\n## DIFF (actual code changes):")
                prompt_parts.append("(No code changes were made)")

            prompt_parts.append("\n## Task:")
            prompt_parts.append("Identify which proposed direction was actually implemented in the diff.")

            prompt = "\n".join(prompt_parts)

            response = await asyncio.wait_for(
                summarizer.run(prompt, update_memory=False),
                timeout=timeout
            )

            # Parse JSON from response
            content = response.content.strip()
            # Handle potential markdown code blocks
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])  # Remove first and last lines

            result = json.loads(content)

            # Validate required fields
            if "direction" not in result:
                result["direction"] = default_result["direction"]
            if "category" not in result:
                result["category"] = default_result["category"]
            if "is_algorithmic" not in result:
                result["is_algorithmic"] = default_result["is_algorithmic"]
            if "match_confidence" not in result:
                result["match_confidence"] = default_result["match_confidence"]

            return result

        except asyncio.TimeoutError:
            logger.debug("Summarizer timeout, using default direction")
            return default_result
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse summarizer JSON: {e}")
            return default_result
        except Exception as e:
            logger.debug(f"Direction extraction failed: {e}")
            return default_result

    def _classify_result(
        self,
        score_delta: float,
        has_error: bool = False,
    ) -> str:
        """
        Classify the result of an exploration attempt.

        Args:
            score_delta: Score change (child - parent)
            has_error: Whether evaluation had an error

        Returns:
            One of: "success", "marginal", "neutral", "failure", "error"
        """
        if has_error:
            return "error"

        # Thresholds for classification
        if score_delta > 0.01:  # > +1%
            return "success"
        elif score_delta > 0:  # 0 to +1%
            return "marginal"
        elif score_delta > -0.01:  # -1% to 0
            return "neutral"
        else:  # < -1%
            return "failure"

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
        progress_callback: Optional[callable] = None,
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
            progress_callback: Optional callback(iteration: int, best_score: float) for progress updates
            **kwargs: Additional arguments

        Returns:
            EvolutionResult with best program and history
        """
        max_iterations = max_iterations or self.config.max_iterations
        self.objective = objective
        self.evaluator_code = evaluator_code
        self.progress_callback = progress_callback  # Store callback for use in checkpoints

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

                # Find initial_program (order=0) and best_program from database
                initial_program = None
                best_program = None
                best_program_id = self.database.best_program_id

                for prog in self.database.programs.values():
                    if prog.order == 0:
                        initial_program = prog
                    if prog.id == best_program_id:
                        best_program = prog

                if initial_program is None:
                    # Fallback: use first program added
                    initial_program = min(self.database.programs.values(), key=lambda p: p.order)
                if best_program is None:
                    # Fallback: find program with highest fitness
                    best_program = max(
                        self.database.programs.values(),
                        key=lambda p: p.fitness_score(
                            self.config.feature_dimensions,
                            self.database.metric_ranges,
                            self.config.function_weight,
                            self.config.llm_weight,
                        )
                    )
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

            # Keep fitness_weights for dynamic function_score calculation
            fitness_weights = initial_program.metrics.get("fitness_weights")
            if fitness_weights:
                # Update metric_ranges (for normalization in fitness_score)
                self.database._update_metric_ranges(initial_program.metrics)

            self.database.add(initial_program)

            initial_score = initial_program.fitness_score(
                self.config.feature_dimensions,
                self.database.metric_ranges,
                self.config.function_weight,
                self.config.llm_weight,
            )
            result.score_history.append(initial_score)
            result.best_score_history.append(initial_score)
            best_score = initial_score
            best_program = initial_program  # Track best program for consistent fitness comparison

            logger.info(f"Initial program score: {initial_score:.4f}")

        # Evolution loop - use workers if num_workers > 1
        if self.config.num_workers > 1:
            # Parallel worker-based evolution
            logger.info(f"Starting parallel evolution with {self.config.num_workers} workers")

            # Shared atomic counter for iteration numbers
            iteration_lock = asyncio.Lock()
            iteration_state = {"next": start_iteration}

            async def get_next_iteration():
                """Atomically get and increment iteration counter."""
                async with iteration_lock:
                    val = iteration_state["next"]
                    iteration_state["next"] += 1
                    return val

            result_queue = asyncio.Queue()

            # Start workers
            workers = [
                asyncio.create_task(
                    self._worker(i, get_next_iteration, max_iterations, result_queue)
                )
                for i in range(self.config.num_workers)
            ]

            # Collect results as they come in
            completed_iterations = 0
            target_iterations = max_iterations - start_iteration

            while completed_iterations < target_iterations:
                try:
                    iter_result = await asyncio.wait_for(result_queue.get(), timeout=300)
                    result.iteration_results.append(iter_result)
                    completed_iterations += 1

                    # Track scores
                    result.score_history.append(iter_result.child_score)

                    is_new_best = False
                    # Recompute best_program's fitness using current metric_ranges
                    # This ensures consistent comparison as ranges expand during evolution
                    current_best_score = best_program.fitness_score(
                        self.config.feature_dimensions,
                        self.database.metric_ranges,
                        self.config.function_weight,
                        self.config.llm_weight,
                    )
                    if iter_result.child_score > current_best_score:
                        best_score = iter_result.child_score
                        best_program = self.database.programs[iter_result.child_id]
                        generations_without_improvement = 0
                        is_new_best = True
                    else:
                        generations_without_improvement += 1

                    result.best_score_history.append(best_score)

                    # Log every iteration with clear progress
                    progress_pct = completed_iterations / target_iterations * 100
                    status = "★ NEW BEST" if is_new_best else ("✓ accepted" if iter_result.accepted else "✗ rejected")
                    # Compute initial_program's score with current metric_ranges for consistent comparison
                    current_initial_score = initial_program.fitness_score(
                        self.config.feature_dimensions,
                        self.database.metric_ranges,
                        self.config.function_weight,
                        self.config.llm_weight,
                    )
                    logger.info(
                        f"[{completed_iterations}/{target_iterations}] ({progress_pct:.0f}%) "
                        f"iter={iter_result.iteration} initial={current_initial_score:.4f} "
                        f"score={iter_result.child_score:.4f} best={best_score:.4f} {status}"
                    )

                    # Periodic summary (every 10 iterations)
                    if completed_iterations % 10 == 0:
                        stats = self.database.get_statistics()
                        logger.info(
                            f"=== Summary: {completed_iterations}/{target_iterations} complete, "
                            f"initial={current_initial_score:.4f}, best={best_score:.4f}, "
                            f"avg={stats['avg_fitness']:.4f}, programs={stats['total_programs']} ==="
                        )

                    # Trigger progress callback on every iteration (independent of checkpoint)
                    if self.progress_callback:
                        self.progress_callback(
                            start_iteration + completed_iterations,
                            best_score
                        )

                    # Periodic checkpoint
                    if self.config.db_path and completed_iterations % self.config.checkpoint_interval == 0:
                        self._save_checkpoint(
                            self.config.db_path,
                            start_iteration + completed_iterations,
                            best_score,
                            result.score_history,
                            result.best_score_history,
                            generations_without_improvement,
                        )

                    # Early stopping check
                    if generations_without_improvement >= self.config.early_stop_generations:
                        logger.info(
                            f"Early stopping: no improvement for "
                            f"{generations_without_improvement} iterations"
                        )
                        break

                except asyncio.TimeoutError:
                    logger.warning("Waiting for worker results...")
                    continue

            # Cancel remaining workers
            for worker in workers:
                worker.cancel()

            # Wait for workers to finish
            await asyncio.gather(*workers, return_exceptions=True)

        else:
            # Sequential evolution (original behavior)
            for iteration in range(start_iteration, max_iterations):
                try:
                    iter_result = await self._run_iteration(iteration, max_iterations)
                    result.iteration_results.append(iter_result)

                    # Track scores
                    result.score_history.append(iter_result.child_score)

                    # Recompute best_program's fitness using current metric_ranges
                    # This ensures consistent comparison as ranges expand during evolution
                    current_best_score = best_program.fitness_score(
                        self.config.feature_dimensions,
                        self.database.metric_ranges,
                        self.config.function_weight,
                        self.config.llm_weight,
                    )
                    if iter_result.child_score > current_best_score:
                        best_score = iter_result.child_score
                        best_program = self.database.programs[iter_result.child_id]
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
                        # Compute initial_program's score with current metric_ranges for consistent comparison
                        current_initial_score = initial_program.fitness_score(
                            self.config.feature_dimensions,
                            self.database.metric_ranges,
                            self.config.function_weight,
                            self.config.llm_weight,
                        )
                        logger.info(
                            f"--- Progress: {iteration}/{max_iterations}, initial={current_initial_score:.4f}, "
                            f"best={best_score:.4f}, avg={stats['avg_fitness']:.4f}, "
                            f"programs={stats['total_programs']} ---"
                        )

                    # Trigger progress callback on every iteration (independent of checkpoint)
                    if self.progress_callback:
                        self.progress_callback(iteration, best_score)

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
            # Compute final iteration number
            final_iteration = start_iteration + len(result.iteration_results) - 1
            
            # Trigger final progress callback
            if self.progress_callback:
                self.progress_callback(final_iteration, best_score)
            
            self._save_checkpoint(
                self.config.db_path,
                final_iteration,
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
            "max_iterations": self.config.max_iterations,
            "num_islands": self.config.num_islands,
            "mutator_model": self.config.mutator_model,
            "created_at": time.time(),  # For session restoration
        }

        state_path = Path(path) / "evolution_state.json"
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)

        logger.info(f"Checkpoint saved: iteration {iteration}, {len(self.database.programs)} programs")

    async def _run_iteration(
        self,
        iteration: int,
        max_iterations: int = 0,
        worker_id: Optional[int] = None,
    ) -> IterationResult:
        """
        Run a single evolution iteration.

        Args:
            iteration: Current iteration number
            max_iterations: Total iterations for logging progress
            worker_id: Worker ID for parallel execution (None for sequential)

        Returns:
            IterationResult with details
        """
        iter_start = time.time()
        log_prefix = f"[Worker {worker_id}]" if worker_id is not None else f"[{iteration + 1}/{max_iterations}]"
        logger.info(f"{log_prefix} Starting iteration...")

        # Sample parent and inspirations (thread-safe)
        parent, inspirations = await self.database.sample_async(
            num_inspirations=self.config.num_inspirations,
        )

        parent_score = parent.fitness_score(
            self.config.feature_dimensions,
            self.database.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )

        # Get top programs for reference (thread-safe)
        async with self.database._lock:
            top_programs = self.database.get_top_programs(
                n=self.config.num_top_programs,
            )

        # Apply probability filtering for context sections
        use_top_programs = random.random() < self.config.top_programs_probability
        use_inspirations = random.random() < self.config.inspirations_probability

        effective_top_programs = top_programs if use_top_programs else None
        effective_inspirations = inspirations if use_inspirations else None

        # Build prompt (with or without analyzer)
        analysis_text = ""  # Store analyzer output for program record
        analysis_prompt = ""  # Store analyzer prompt for program record
        analyzer_direction = ""  # Track exploration vs exploitation direction
        extracted_direction = None  # Direction info from summarizer
        iteration_cost = 0.0  # Track LLM cost for this iteration
        if self.config.use_analyzer:
            # === Analyzer Phase (full context) ===
            analysis_start = time.time()
            try:
                # Create analyzer with generation-adaptive prompt
                analyzer, analyzer_direction, exploration_prob = self._create_analyzer(
                    generation=parent.generation
                )

                # Build evolution history from sibling and ancestor summaries
                sibling_summaries = self.database.get_sibling_summaries(parent.id)
                ancestor_summaries = self.database.get_ancestor_summaries(parent.id)
                evolution_history_text = self.prompt_builder.build_evolution_history_section(
                    sibling_summaries=sibling_summaries,
                    ancestor_summaries=ancestor_summaries,
                    parent_order=parent.order or 0,
                    max_siblings=self.config.evolution_history_max_siblings,
                    max_ancestors=self.config.evolution_history_max_ancestors,
                    max_chars=self.config.evolution_history_max_chars,
                )

                analysis_prompt = self.prompt_builder.build_analysis_prompt(
                    parent=parent,
                    objective=self.objective,
                    top_programs=effective_top_programs,
                    inspirations=effective_inspirations,
                    artifacts=parent.artifacts,
                    iteration=iteration,
                    exploration_history=evolution_history_text,  # Always include history
                    metric_ranges=self.database.metric_ranges,
                    feature_dimensions=self.config.feature_dimensions,
                    function_weight=self.config.function_weight,
                    llm_weight=self.config.llm_weight,
                )
                # analysis_prompt is already stored above for program record
                analysis_response = await asyncio.wait_for(
                    analyzer.run(analysis_prompt, update_memory=False),
                    timeout=self.config.analyzer_timeout
                )
                analysis_text = analysis_response.content
                iteration_cost += extract_cost_from_response(analysis_response)
                analysis_time = time.time() - analysis_start
                logger.info(
                    f"{log_prefix} Analysis ({analyzer_direction}, p={exploration_prob:.2f}): "
                    f"{analysis_time:.1f}s (${iteration_cost:.4f})"
                )
                # Note: Direction extraction moved to after mutation to include diff
            except asyncio.TimeoutError:
                analysis_time = time.time() - analysis_start
                logger.warning(f"{log_prefix} Analyzer timeout after {analysis_time:.1f}s, skipping iteration")
                return IterationResult(
                    iteration=iteration,
                    parent_id=parent.id,
                    child_id=parent.id,
                    parent_score=parent_score,
                    child_score=parent_score,
                    improvement=0,
                    accepted=False,
                    mutation_time=0,
                    evaluation_time=0,
                    total_time=time.time() - iter_start,
                    error="analyzer_timeout",
                )
            except Exception as e:
                logger.warning(f"{log_prefix} Analyzer failed: {e}, skipping iteration")
                return IterationResult(
                    iteration=iteration,
                    parent_id=parent.id,
                    child_id=parent.id,
                    parent_score=parent_score,
                    child_score=parent_score,
                    improvement=0,
                    accepted=False,
                    mutation_time=0,
                    evaluation_time=0,
                    total_time=time.time() - iter_start,
                    error=f"analyzer_failed: {e}",
                )

            # === Mutator Phase (code + instructions only) ===
            prompt = self.prompt_builder.build_simple_mutation_prompt(
                parent=parent,
                analysis=analysis_text,
            )
        else:
            # Original behavior: mutator gets full context
            prompt = self.prompt_builder.build_mutation_prompt(
                parent=parent,
                objective=self.objective,
                top_programs=effective_top_programs,
                inspirations=effective_inspirations,
                artifacts=parent.artifacts,
                iteration=iteration,
                metric_ranges=self.database.metric_ranges,
                feature_dimensions=self.config.feature_dimensions,
                function_weight=self.config.function_weight,
                llm_weight=self.config.llm_weight,
            )

        # Generate mutation with timeout
        mutation_start = time.time()
        mutator = await self._ensure_mutator()

        try:
            response = await asyncio.wait_for(
                mutator.run(prompt, update_memory=False),
                timeout=self.config.mutation_timeout
            )
            mutation_cost = extract_cost_from_response(response)
            iteration_cost += mutation_cost
            mutation_time = time.time() - mutation_start
            logger.info(f"{log_prefix} Mutation: {mutation_time:.1f}s (${mutation_cost:.4f})")
        except asyncio.TimeoutError:
            mutation_time = time.time() - mutation_start
            logger.warning(f"{log_prefix} Mutation timeout after {mutation_time:.1f}s")
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
                llm_cost=iteration_cost,
            )

        # Apply mutation
        mutation_applied = False  # Track if code actually changed
        try:
            child_snapshot = self._apply_mutation(parent.snapshot, response.content)
            default_file = next(iter(parent.snapshot.files.keys()), "main.py")
            changes = parse_diff(response.content, default_file)
            logger.info(f"{log_prefix} Applied {len(changes)} change(s)")
        except Exception as e:
            logger.warning(f"{log_prefix} Failed to apply mutation: {e}")
            child_snapshot = parent.snapshot

        # Check if code actually changed
        diff_from_parent = child_snapshot.diff_from(parent.snapshot)
        if diff_from_parent and diff_from_parent.strip():
            mutation_applied = True
        else:
            logger.warning(f"{log_prefix} Mutation produced no code changes (SEARCH blocks may not have matched)")

        # Create child program
        child = Program(
            id=str(uuid.uuid4())[:8],
            snapshot=child_snapshot,
            diff_from_parent=diff_from_parent,
            parent_id=parent.id,
            generation=parent.generation + 1,
            mutator_prompt_used=prompt if self.config.save_prompts else "",
            analysis_prompt_used=analysis_prompt if self.config.save_prompts else "",
            analysis_used=analysis_text if self.config.save_prompts else "",
        )

        # Evaluate child
        eval_start = time.time()
        evaluator = await self._ensure_evaluator()
        eval_result = await evaluator.evaluate(child)
        eval_time = time.time() - eval_start

        child.metrics = eval_result.metrics
        child.artifacts = eval_result.artifacts
        child.llm_feedback = eval_result.llm_feedback

        # Extract fitness_weights from evaluator (keep it for dynamic function_score calculation)
        fitness_weights = child.metrics.get("fitness_weights")
        if fitness_weights:
            # Update metric_ranges first (for normalization)
            self.database._update_metric_ranges(child.metrics)

        child_score = child.fitness_score(
            self.config.feature_dimensions,
            self.database.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )
        improvement = child_score - parent_score

        # Log evaluation results
        logger.info(f"{log_prefix} Evaluation: {eval_time:.1f}s, score: {child_score:.4f}")

        # Log key metrics if available
        metric_parts = []
        for key in ['mixing_score', 'speed_score', 'function_score']:
            if key in child.metrics:
                metric_parts.append(f"{key.replace('_score', '')}={child.metrics[key]:.3f}")
        if metric_parts:
            logger.info(f"{log_prefix} Metrics: {', '.join(metric_parts)}")

        # Extract direction using summarizer (always, not just exploration mode)
        if analysis_text and mutation_applied:
            extracted_direction = await self._extract_direction(
                analysis_text,
                diff_text=diff_from_parent or "",
                timeout=self.config.summarizer_timeout,
            )
            # Store mutation summary in child program
            if extracted_direction.get("direction") not in ("No clear direction proposed", "No implementation found"):
                child.mutation_summary = extracted_direction.get("direction", "")
                child.mutation_category = extracted_direction.get("category", "other")
                child.is_algorithmic = extracted_direction.get("is_algorithmic", True)

            logger.debug(
                f"{log_prefix} Direction extracted: '{extracted_direction.get('direction', 'N/A')[:50]}...' "
                f"(confidence: {extracted_direction.get('match_confidence', 'N/A')})"
            )

        # Compute fitness and metrics deltas
        child.fitness_delta, child.metrics_delta = compute_deltas(
            parent,
            child,
            self.config.feature_dimensions,
            self.database.metric_ranges,
            self.config.function_weight,
            self.config.llm_weight,
        )

        # Add to database (thread-safe)
        accepted = await self.database.add_async(child)

        total_time = time.time() - iter_start

        # Log result
        result_str = "✓ Improved" if improvement > 0 else "✗ No improvement"
        accepted_str = "(accepted)" if accepted else "(rejected)"
        cost_str = f"${iteration_cost:.4f}" if iteration_cost > 0 else ""
        logger.info(f"{log_prefix} Result: {result_str} ({improvement:+.4f}) {accepted_str} [{total_time:.1f}s] {cost_str}")

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
            llm_cost=iteration_cost,
        )

    async def _worker(
        self,
        worker_id: int,
        get_next_iteration,
        max_iterations: int,
        result_queue: asyncio.Queue,
    ) -> None:
        """
        Single worker that runs evolution iterations independently.

        Args:
            worker_id: Identifier for this worker
            get_next_iteration: Async function to get next iteration number
            max_iterations: Stop when counter reaches this value
            result_queue: Queue to put iteration results
        """
        while True:
            # Get next iteration number atomically
            iteration = await get_next_iteration()

            if iteration >= max_iterations:
                break

            logger.info(f"[Worker {worker_id}] Starting iteration {iteration + 1}/{max_iterations}")

            try:
                iter_result = await self._run_iteration(iteration, max_iterations, worker_id=worker_id)
                await result_queue.put(iter_result)
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Iteration {iteration} failed: {e}")
                await result_queue.put(IterationResult(
                    iteration=iteration,
                    parent_id="",
                    child_id="",
                    parent_score=0,
                    child_score=0,
                    improvement=0,
                    accepted=False,
                    error=str(e),
                ))

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
