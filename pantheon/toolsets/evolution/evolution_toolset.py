"""
Evolution ToolSet - Expose evolution functionality to Agents.

Allows Agents to run evolutionary code optimization on codebases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pantheon.toolset import tool, ToolSet
from pantheon.utils.log import logger


class EvolutionToolSet(ToolSet):
    """
    ToolSet for evolutionary code optimization.

    Allows Agents to evolve codebases through iterative LLM-guided
    mutations and evaluations.

    Example:
        ```python
        from pantheon.agent import Agent
        from pantheon.toolsets.evolution import EvolutionToolSet

        agent = Agent(
            name="optimizer",
            instructions="You help users optimize their code.",
        )
        agent.toolset(EvolutionToolSet("evolve"))

        response = await agent.run("Optimize this sorting function...")
        ```
    """

    def __init__(
        self,
        name: str = "evolution",
        workdir: Optional[str] = None,
        default_iterations: int = 50,
        default_islands: int = 3,
        **kwargs,
    ):
        """
        Initialize the Evolution ToolSet.

        Args:
            name: Name of the toolset
            workdir: Working directory for evolution workspaces
            default_iterations: Default number of evolution iterations
            default_islands: Default number of evolution islands
            **kwargs: Additional arguments passed to ToolSet
        """
        super().__init__(name, **kwargs)
        self.workdir = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        self.default_iterations = default_iterations
        self.default_islands = default_islands

    @tool
    async def evolve_code(
        self,
        code: str,
        evaluator_code: str,
        objective: str,
        iterations: Optional[int] = None,
        islands: Optional[int] = None,
        model: str = "normal",
    ) -> Dict[str, Any]:
        """
        Evolve and optimize code using evolutionary algorithms.

        This tool runs an evolutionary optimization loop that:
        1. Generates mutations of the code using an LLM
        2. Evaluates each mutation using the provided evaluator
        3. Keeps the best-performing variants
        4. Repeats until convergence or max iterations

        Args:
            code: The initial code to optimize (single file content)
            evaluator_code: Python code defining an `evaluate(workspace_path)` function
                that returns a dict with at least a "combined_score" key (0-1 scale).
                Example:
                ```python
                def evaluate(workspace_path):
                    import time
                    exec(open(f"{workspace_path}/main.py").read())
                    # ... run tests or benchmarks ...
                    return {"combined_score": 0.85, "speed": 1.2}
                ```
            objective: Natural language description of the optimization goal.
                Example: "Optimize for speed while maintaining correctness"
            iterations: Maximum number of evolution iterations (default: 50)
            islands: Number of evolution islands for diversity (default: 3)
            model: Model to use for mutation generation

        Returns:
            dict: Evolution results containing:
                - success: Whether evolution completed successfully
                - best_code: The best code found
                - best_score: The best score achieved
                - initial_score: The initial score before evolution
                - improvement: Score improvement (best - initial)
                - total_iterations: Number of iterations run
                - summary: Human-readable summary
        """
        try:
            from pantheon.evolution import EvolutionTeam, EvolutionConfig

            iterations = iterations or self.default_iterations
            islands = islands or self.default_islands

            config = EvolutionConfig(
                max_iterations=iterations,
                num_islands=islands,
                mutator_model=model,
                workspace_path=str(self.workdir / "evolution_workspace"),
            )

            team = EvolutionTeam(config=config)
            result = await team.evolve(
                initial_code=code,
                evaluator_code=evaluator_code,
                objective=objective,
            )

            initial_score = result.score_history[0] if result.score_history else 0

            return {
                "success": True,
                "best_code": result.best_code,
                "best_score": result.best_score,
                "initial_score": initial_score,
                "improvement": result.best_score - initial_score,
                "total_iterations": result.total_iterations,
                "improvements_found": result.improvements,
                "summary": result.get_summary(),
            }

        except Exception as e:
            logger.error(f"Evolution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "best_code": code,
                "best_score": 0,
            }

    @tool
    async def evolve_codebase(
        self,
        codebase_path: str,
        evaluator_code: str,
        objective: str,
        include_patterns: Optional[List[str]] = None,
        iterations: Optional[int] = None,
        islands: Optional[int] = None,
        model: str = "normal",
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evolve and optimize an entire codebase.

        Similar to evolve_code but works on multi-file codebases.

        Args:
            codebase_path: Path to the directory containing the codebase
            evaluator_code: Python code defining an `evaluate(workspace_path)` function
            objective: Natural language description of the optimization goal
            include_patterns: Glob patterns for files to include (default: ["**/*.py"])
            iterations: Maximum number of evolution iterations
            islands: Number of evolution islands
            model: Model to use for mutation generation
            output_path: Optional path to save the best result

        Returns:
            dict: Evolution results containing:
                - success: Whether evolution completed successfully
                - best_score: The best score achieved
                - initial_score: The initial score
                - improvement: Score improvement
                - total_iterations: Number of iterations run
                - files_evolved: List of files in the evolved codebase
                - output_path: Path where best code was saved (if output_path provided)
                - summary: Human-readable summary
        """
        try:
            from pantheon.evolution import (
                EvolutionTeam,
                EvolutionConfig,
                CodebaseSnapshot,
            )

            include_patterns = include_patterns or ["**/*.py"]
            iterations = iterations or self.default_iterations
            islands = islands or self.default_islands

            # Load codebase
            codebase_path = Path(codebase_path).expanduser().resolve()
            if not codebase_path.is_dir():
                return {
                    "success": False,
                    "error": f"Codebase path not found: {codebase_path}",
                }

            initial_snapshot = CodebaseSnapshot.from_directory(
                str(codebase_path),
                include_patterns=include_patterns,
            )

            logger.info(
                f"Loaded codebase: {initial_snapshot.file_count()} files, "
                f"{initial_snapshot.total_lines()} lines"
            )

            config = EvolutionConfig(
                max_iterations=iterations,
                num_islands=islands,
                mutator_model=model,
                workspace_path=str(self.workdir / "evolution_workspace"),
            )

            team = EvolutionTeam(config=config)
            result = await team.evolve(
                initial_code=initial_snapshot,
                evaluator_code=evaluator_code,
                objective=objective,
            )

            initial_score = result.score_history[0] if result.score_history else 0

            response = {
                "success": True,
                "best_score": result.best_score,
                "initial_score": initial_score,
                "improvement": result.best_score - initial_score,
                "total_iterations": result.total_iterations,
                "improvements_found": result.improvements,
                "files_evolved": (
                    list(result.best_program.snapshot.files.keys())
                    if result.best_program
                    else []
                ),
                "summary": result.get_summary(),
            }

            # Save output if requested
            if output_path and result.best_program:
                output_dir = Path(output_path).expanduser().resolve()
                result.best_program.snapshot.to_workspace(str(output_dir))
                response["output_path"] = str(output_dir)
                logger.info(f"Best codebase saved to {output_dir}")

            return response

        except Exception as e:
            logger.error(f"Codebase evolution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "best_score": 0,
            }

    @tool
    async def get_evolution_status(
        self,
        database_path: str,
    ) -> Dict[str, Any]:
        """
        Get the status of a saved evolution database.

        Useful for resuming or analyzing previous evolution runs.

        Args:
            database_path: Path to the saved evolution database

        Returns:
            dict: Database statistics including:
                - total_programs: Number of programs in database
                - best_score: Best score found
                - avg_fitness: Average fitness across all programs
                - num_islands: Number of islands
                - generations: Maximum generation reached
        """
        try:
            from pantheon.evolution import EvolutionDatabase

            db = EvolutionDatabase()
            db.load(database_path)

            stats = db.get_statistics()
            best = db.get_best_program()

            return {
                "success": True,
                "total_programs": stats["total_programs"],
                "best_score": stats["best_fitness"],
                "avg_fitness": stats["avg_fitness"],
                "num_islands": stats["num_islands"],
                "archive_size": stats["archive_size"],
                "best_program_id": best.id if best else None,
                "best_generation": best.generation if best else 0,
            }

        except Exception as e:
            logger.error(f"Failed to load evolution database: {e}")
            return {
                "success": False,
                "error": str(e),
            }
