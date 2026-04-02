"""
Evolution ToolSet - Expose evolution functionality to Agents.

Allows Agents to run evolutionary code optimization on codebases.

Refactored to support:
- Async execution (background evolution with evolution_id tracking)
- Session management (EvolutionManager for multi-evolution coordination)
- Tool visibility control (Agent-visible vs Frontend-only tools)
- Progress polling (frontend queries status via evolution_id)
"""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pantheon.toolset import tool, ToolSet
from pantheon.utils.log import logger


# ============================================================================
# Session Management
# ============================================================================

class EvolutionSession:
    """Single evolution session state with self-managed persistence"""
    
    def __init__(self, evolution_id: str, config_dict: Dict[str, Any], workspace_path: Optional[str] = None):
        # === Identification ===
        self.evolution_id = evolution_id
        self.workspace_path = workspace_path
        
        # === Type ===
        self.evolution_type: str = "code"  # "code" | "codebase"
        
        # === Status ===
        self.status = "pending"  # pending/running/completed/failed/cancelled/paused
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.error: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        
        # === Input Config ===
        self.evaluator_code: str = ""
        self.objective: str = config_dict.get("objective", "")
        self.max_iter: int = config_dict.get("max_iterations", 100)
        self.num_islands: int = config_dict.get("num_islands", 3)
        self.mutator_model: str = config_dict.get("mutator_model", "normal")
        
        # === Unified File Storage ===
        self.files: Dict[str, str] = {}  # File path -> Content (Current/Final)
        self.initial_files: Dict[str, str] = {}  # Original input files (Immutable)
        
        # === Codebase Specific ===
        self.codebase_path: Optional[str] = None
        self.include_patterns: Optional[List[str]] = None
        self.output_path: Optional[str] = None
        
        # === Progress Data ===
        self.current_iter: int = 0
        self.best_score: float = 0.0
        self.score_history: List[float] = []
        self.failed_iterations: int = 0
        self.last_error: Optional[str] = None  # Most recent iteration error
        
        # === Result Data ===
        self.initial_score: Optional[float] = None
        self.improvement: Optional[float] = None
        self.summary: Optional[str] = None
        
        # === Compatibility ===
        self.config_dict = config_dict  # Keep for other configurations

        # === Caching ===
        self._cached_database = None
        self._last_db_load_time = 0.0
        self._live_database = None  # In-memory database during active evolution

    def get_database(self, force_reload: bool = False):
        """Get the evolution database. Uses live in-memory DB if available, otherwise loads from disk."""
        # Prefer live in-memory database (set during active evolution)
        if self._live_database is not None:
            return self._live_database

        from pantheon.evolution import EvolutionDatabase

        if not self.workspace_path:
            return None

        db_path = Path(self.workspace_path)
        state_file = db_path / "evolution_state.json"

        if not state_file.exists():
            return None

        should_reload = force_reload or self._cached_database is None

        if not should_reload:
            try:
                mtime = state_file.stat().st_mtime
                if mtime > self._last_db_load_time:
                    should_reload = True
            except OSError:
                should_reload = True

        if should_reload:
            try:
                self._cached_database = EvolutionDatabase.load(str(db_path))
                self._last_db_load_time = time.time()
            except Exception as e:
                logger.warning(f"Failed to load evolution database: {e}")
                return None

        return self._cached_database
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize session to dictionary"""
        return {
            "evolution_id": self.evolution_id,
            "evolution_type": self.evolution_type,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            # Input
            "evaluator_code": self.evaluator_code,
            "objective": self.objective,
            "max_iter": self.max_iter,
            "num_islands": self.num_islands,
            "mutator_model": self.mutator_model,
            # Unified file storage
            "files": self.files,
            "initial_files": self.initial_files,  # Original input files
            # Codebase-specific
            "codebase_path": self.codebase_path,
            "include_patterns": self.include_patterns,
            "output_path": self.output_path,
            # Progress
            "current_iter": self.current_iter,
            "best_score": self.best_score,
            "score_history": self.score_history,
            "failed_iterations": self.failed_iterations,
            "last_error": self.last_error,
            # Result
            "initial_score": self.initial_score,
            "improvement": self.improvement,
            "summary": self.summary,
            # Compatibility
            "config_dict": self.config_dict,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], workspace_path: Optional[str] = None) -> "EvolutionSession":
        """Deserialize session from dictionary"""
        session = cls(
            evolution_id=data["evolution_id"],
            config_dict=data.get("config_dict", {}),
            workspace_path=workspace_path,
        )
        # Type
        session.evolution_type = data.get("evolution_type", "code")
        # Status
        session.status = data.get("status", "pending")
        session.created_at = data.get("created_at", time.time())
        session.started_at = data.get("started_at")
        session.completed_at = data.get("completed_at")
        session.error = data.get("error")
        # Input
        session.evaluator_code = data.get("evaluator_code", "")
        session.objective = data.get("objective", "")
        session.max_iter = data.get("max_iter", 100)
        session.num_islands = data.get("num_islands", 3)
        session.mutator_model = data.get("mutator_model", "normal")
        # Unified file storage
        session.files = data.get("files", {})
        session.initial_files = data.get("initial_files", {})
        # Codebase-specific
        session.codebase_path = data.get("codebase_path")
        session.include_patterns = data.get("include_patterns")
        session.output_path = data.get("output_path")
        # Progress
        session.current_iter = data.get("current_iter", 0)
        session.best_score = data.get("best_score", 0.0)
        session.score_history = data.get("score_history", [])
        session.failed_iterations = data.get("failed_iterations", 0)
        session.last_error = data.get("last_error")
        # Result
        session.initial_score = data.get("initial_score")
        session.improvement = data.get("improvement")
        session.summary = data.get("summary")
        return session
    
    def to_status_dict(self) -> Dict[str, Any]:
        """Build status dictionary for API responses"""
        return {
            "evolution_id": self.evolution_id,
            "evolution_type": self.evolution_type,
            "status": self.status,
            "objective": self.objective,
            "iteration": self.current_iter,
            "max_iterations": self.max_iter,
            "best_score": self.best_score,
            "initial_score": self.initial_score,
            "improvement": self.improvement,
            "file_count": len(self.files),
            "islands": self.num_islands,
            "model": self.mutator_model,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
    
    def to_config_dict(self) -> Dict[str, Any]:
        """Build config dictionary for API responses"""
        return {
            "objective": self.objective,
            "iterations": self.max_iter,
            "islands": self.num_islands,
            "model": self.mutator_model,
        }
    
    def to_stats_dict(self, db, stats: Dict[str, Any]) -> Dict[str, Any]:
        """Build stats dictionary from database and session data"""
        return {
            "total_programs": stats.get("total_programs", 0),
            "total_iterations": self.current_iter,
            "improvements": db.total_improved,
            "best_score": self.best_score,
            "initial_score": self.initial_score or 0,
            "improvement_pct": self.improvement or 0,
            "avg_score": stats.get("avg_fitness", 0),
            "num_islands": self.num_islands,
            "archive_size": stats.get("archive_size", 0),
            "feature_dimensions": list(db.config.feature_dimensions) if db.config.feature_dimensions else [],
            "feature_ranges": stats.get("feature_ranges", {}),
            "config": self.to_config_dict(),
        }
    
    def save(self) -> None:
        """Save session state to disk"""
        if not self.workspace_path:
            return
        
        session_file = Path(self.workspace_path) / "session_state.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, workspace_path: str) -> Optional["EvolutionSession"]:
        """Load session state from disk"""
        session_file = Path(workspace_path) / "session_state.json"
        
        if not session_file.exists():
            return None
        
        try:
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data, workspace_path)
        except Exception as e:
            logger.warning(f"Failed to load session from {session_file}: {e}")
            return None
    
    def update_progress(self, iteration: int, best_score: float, auto_save: bool = True) -> None:
        """Update progress and optionally auto-save"""
        self.current_iter = iteration
        self.best_score = best_score
        
        # Update score_history
        # Ensure we have enough entries (fill with 0 if needed)
        while len(self.score_history) < iteration:
            self.score_history.append(0.0)
        
        # Update or append the score for this iteration
        if iteration > 0:
            if len(self.score_history) >= iteration:
                self.score_history[iteration - 1] = best_score
            else:
                self.score_history.append(best_score)
        
        if auto_save:
            self.save()


class EvolutionManager:
    """Global evolution session manager (singleton)"""
    
    _instance = None
    
    def __init__(self):
        self._sessions: Dict[str, EvolutionSession] = {}
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            # Restore sessions from disk on first initialization
            cls._instance._restore_sessions()
        return cls._instance
    
    def create_session(self, evolution_id: str, config_dict: Dict[str, Any], workspace_path: Optional[str] = None) -> EvolutionSession:
        session = EvolutionSession(evolution_id, config_dict, workspace_path)
        self._sessions[evolution_id] = session
        session.save()  # Auto-save on creation
        return session
    
    def get_session(self, evolution_id: str) -> Optional[EvolutionSession]:
        return self._sessions.get(evolution_id)
    
    def list_sessions(
        self,
        status_filter: Optional[str] = None,
        limit: int = 100,
    ) -> List[EvolutionSession]:
        """List sessions with optional status filter"""
        sessions = list(self._sessions.values())
        
        if status_filter:
            sessions = [s for s in sessions if s.status == status_filter]
        
        # Sort by creation time (newest first)
        sessions = sorted(sessions, key=lambda s: s.created_at, reverse=True)
        
        return sessions[:limit]
    
    def _restore_sessions(self):
        """Restore sessions from evolution_state.json files in workspaces"""
        # This will be called by EvolutionToolSet after workdir is set
        pass
    
    def restore_from_workdir(self, workdir: Path):
        """Scan workdir for evolution workspaces and restore sessions"""
        if not workdir.exists():
            return
        
        logger.info(f"Restoring Evolution sessions from {workdir}")
        restored_count = 0
        
        for evolution_dir in workdir.iterdir():
            if not evolution_dir.is_dir():
                continue
            
            workspace_path = str(evolution_dir)
            
            # Load from session_state.json
            session = EvolutionSession.load(workspace_path)
            
            if session:
                self._sessions[session.evolution_id] = session
                restored_count += 1
        
        if restored_count > 0:
            logger.info(f"Restored {restored_count} Evolution session(s)")


def success_response(data: Any) -> Dict[str, Any]:
    """Create a successful API response with unified format"""
    return {
        "success": True,
        "data": data
    }


def error_response(
    code: str,
    message: str,
    details: Any = None
) -> Dict[str, Any]:
    """Create an error API response with unified format"""
    error = {
        "code": code,
        "message": message
    }
    if details is not None:
        error["details"] = details
    
    return {
        "success": False,
        "error": error
    }


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
            default_islands: Default number of MAP-Elites islands
        """
        super().__init__(name, **kwargs)
        
        # Use fixed default path for evolution data persistence
        if workdir:
            self.workdir = Path(workdir).expanduser().resolve()
        else:
            from pantheon.settings import get_settings
            settings = get_settings()
            self.workdir = settings.pantheon_dir / "evolution"
        
        # Ensure workdir exists
        self.workdir.mkdir(parents=True, exist_ok=True)
        
        self.default_iterations = default_iterations
        self.default_islands = default_islands
        
        # Global session manager
        self.manager = EvolutionManager.get_instance()
        
        # Restore sessions from disk
        self.manager.restore_from_workdir(self.workdir)
    
    def _get_workspace_path(self, evolution_id: str) -> str:
        """Get workspace path for an evolution (convention: {workdir}/{evolution_id})"""
        return str(self.workdir / evolution_id)
    
    def _estimate_timeout(self, iterations: int) -> int:
        """Estimate timeout for synchronous mode based on iterations"""
        # Assume ~12 seconds per iteration on average
        base_time = iterations * 12
        with_buffer = int(base_time * 1.3)  # Add 30% buffer
        return max(60, min(with_buffer, 300))  # Clamp to 60-300 seconds

    @tool(exclude=True)
    async def evolve_code(
        self,
        code: str,
        evaluator_code: str,
        objective: str,
        iterations: Optional[int] = None,
        islands: Optional[int] = None,
        model: str = "normal",
        async_mode: bool = True,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evolve and optimize code using evolutionary algorithms with LLM-guided mutation.

        Use when: Optimizing single-file code. For multi-file projects, use evolve_codebase.

        Args:
            code: Initial code to optimize (string)
            
            evaluator_code: Python function: evaluate(workspace_path: str) -> Dict[str, float]
                MUST return individual metrics (0-1 range) AND a "fitness_weights" dict.
                Do NOT compute combined_score yourself — the engine computes fitness automatically.
                Example:
                ```python
                def evaluate(workspace_path):
                    import sys; sys.path.insert(0, workspace_path)
                    from main import func
                    correct = float(func(test_data) == expected)  # 0 or 1
                    speed = 1.0 / (1.0 + elapsed)  # 0-1 scale
                    return {
                        "correctness": correct,
                        "speed": speed,
                        "fitness_weights": {  # REQUIRED
                            "correctness": 0.7,
                            "speed": 0.3,
                        },
                    }
                ```
            
            objective: Optimization goal (e.g., "Optimize for speed", "Reduce memory usage")
            
            iterations: Max iterations (default: 50)
                Recommended: 10-20 for experiments, 50-100 for production
            
            islands: Evolution islands for diversity (default: 3, recommended: 3-5)
            
            model: Mutation model quality level (default: "normal", options: "high", "normal", "low")
            
            async_mode: Run in background (default: True, RECOMMENDED for iterations > 20)
                True: Returns evolution_id immediately, monitor via get_evolution_status(evolution_id)
                False: Waits for completion (only for quick tests <20 iterations)
            
            timeout: Timeout seconds for sync mode (default: auto-estimate)

        Returns:
            dict:
                Async: {"success": bool, "evolution_id": str, "status": "running", "estimated_time": str}
                Sync: {"success": bool, "evolution_id": str, "status": str, "best_score": float, "improvement": float, "summary": str}
            
            Key: SAVE evolution_id for tracking. Monitor via get_evolution_status(evolution_id) or Evolution UI (🧬).
        """
        # Generate evolution ID
        evolution_id = str(uuid.uuid4())
        iterations = iterations or self.default_iterations
        islands = islands or self.default_islands
        
        # Smart timeout estimation for sync mode
        if timeout is None and not async_mode:
            timeout = self._estimate_timeout(iterations)
        
        # Prepare config
        from pantheon.evolution import EvolutionConfig
        
        workspace_path = self._get_workspace_path(evolution_id)
        config = EvolutionConfig(
            max_iterations=iterations,
            num_islands=islands,
            mutator_model=model,
            workspace_path=workspace_path,
            db_path=workspace_path,  # Enable checkpoint persistence
        )
        
        # Create session with extended config for list_evolutions
        config_dict = config.to_dict()
        config_dict["objective"] = objective  # Save for list_evolutions
        session = self.manager.create_session(evolution_id, config_dict, workspace_path)
        
        # Save input data (unified model)
        session.evolution_type = "code"  # ✅ Set type
        session.files = {"main.py": code}  # ✅ Unified file storage
        session.initial_files = {"main.py": code}  # ✅ Save original input
        session.evaluator_code = evaluator_code
        session.objective = objective
        session.save()
        
        if async_mode:
            # Async mode: start background task and return immediately
            session.task = asyncio.create_task(
                self._run_evolution_background(
                    evolution_id, code, evaluator_code, objective, config
                )
            )
            return {
                "success": True,
                "evolution_id": evolution_id,
                "status": "running",
                "estimated_time": f"~{iterations * 12 // 60} minutes",
                "message": (
                    f"✅ Evolution started successfully!\n\n"
                    f"🆔 Evolution ID: {evolution_id}\n"
                    f"⏱️ Estimated time: ~{iterations * 12 // 60} minutes\n\n"
                    f"📊 To monitor progress:\n"
                    f"1. Click the DNA icon (🧬) in left sidebar\n"
                    f"2. Find evolution in the list\n"
                    f"3. Click to view details\n\n"
                    f"Or use: get_evolution_status('{evolution_id}')"
                ),
            }
        else:
            # Sync mode: wait for completion (with timeout)
            try:
                result = await asyncio.wait_for(
                    self._run_evolution(evolution_id, code, evaluator_code, objective, config),
                    timeout=timeout
                )
                session = self.manager.get_session(evolution_id)
                return {
                    "success": True,
                    "evolution_id": evolution_id,
                    "status": "completed",
                    "file_count": len(session.files),
                    "best_score": result.best_score,
                    "improvement": session.improvement,
                    "summary": session.summary,
                }
            except asyncio.TimeoutError:
                # Timeout: convert to async mode
                session.task = asyncio.create_task(
                    self._run_evolution_background(
                        evolution_id, code, evaluator_code, objective, config
                    )
                )
                return {
                    "success": True,
                    "evolution_id": evolution_id,
                    "status": "running",
                    "message": f"Timeout after {timeout}s, continuing in background.",
                }

    @tool(exclude=True)
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
        async_mode: bool = True,
    ) -> Dict[str, Any]:
        """
        Evolve and optimize an entire codebase (multiple files).

        Use when: Optimizing multi-file projects, libraries, or applications.
        For single-file optimization, use evolve_code instead.

        Args:
            codebase_path: Path to directory containing the codebase (must exist)
            
            evaluator_code: Python function: evaluate(workspace_path: str) -> Dict[str, float]
                MUST return individual metrics (0-1 range) AND a "fitness_weights" dict.
                Do NOT compute combined_score yourself — the engine computes fitness automatically.
                Should run project-level tests/benchmarks across all files.
                Example:
                ```python
                def evaluate(workspace_path):
                    import subprocess, sys
                    sys.path.insert(0, workspace_path)

                    # Run test suite
                    result = subprocess.run(
                        ["python", "-m", "pytest", workspace_path],
                        capture_output=True, timeout=30, cwd=workspace_path
                    )
                    tests_pass = float(result.returncode == 0)

                    # Run benchmarks
                    from benchmarks import run_benchmark
                    perf_score = run_benchmark()  # Returns 0-1

                    return {
                        "tests_passed": tests_pass,
                        "performance": perf_score,
                        "fitness_weights": {  # REQUIRED
                            "tests_passed": 0.7,
                            "performance": 0.3,
                        },
                    }
                ```
            
            objective: Optimization goal (e.g., "Optimize library performance")
            
            include_patterns: Glob patterns for files to include (default: ["**/*.py"])
                Examples: ["src/**/*.py"], ["lib/*.py", "utils/*.py"]
            
            iterations: Max iterations (default: 50, recommended: 20-100 for codebases)
            
            islands: Evolution islands (default: 3, recommended: 3-5)
            
            model: Mutation model quality level (default: "normal", options: "high", "normal", "low")
            
            output_path: Optional path to save optimized codebase
            
            async_mode: Run in background (default: True, HIGHLY RECOMMENDED for codebases)

        Returns:
            dict:
                Async: {"success": bool, "evolution_id": str, "status": "running", "file_count": int, "estimated_time": str}
                Sync: {"success": bool, "evolution_id": str, "status": str, "file_count": int, "best_score": float, "improvement": float, "summary": str}
            
            Key: ALWAYS use async_mode=True for codebases. Monitor via get_evolution_status(evolution_id).
        """
        from pantheon.evolution import EvolutionConfig, CodebaseSnapshot
        
        # Generate evolution_id
        evolution_id = str(uuid.uuid4())
        iterations = iterations or self.default_iterations
        islands = islands or self.default_islands
        include_patterns = include_patterns or ["**/*.py"]
        
        # Load codebase
        codebase_path = Path(codebase_path).expanduser().resolve()
        if not codebase_path.is_dir():
            return error_response("INVALID_INPUT", f"Codebase path not found: {codebase_path}")
        
        try:
            initial_snapshot = CodebaseSnapshot.from_directory(
                str(codebase_path),
                include_patterns=include_patterns,
            )
        except Exception as e:
            return error_response("INTERNAL_ERROR", f"Failed to load codebase: {e}")
        
        logger.info(
            f"Loaded codebase: {initial_snapshot.file_count()} files, "
            f"{initial_snapshot.total_lines()} lines"
        )
        
        # Prepare config
        workspace_path = self._get_workspace_path(evolution_id)
        config = EvolutionConfig(
            max_iterations=iterations,
            num_islands=islands,
            mutator_model=model,
            workspace_path=workspace_path,
            db_path=workspace_path,
        )
        
        # Create session (unified)
        config_dict = config.to_dict()
        config_dict["objective"] = objective
        session = self.manager.create_session(evolution_id, config_dict, workspace_path)
        
        # Set type and data (unified model)
        session.evolution_type = "codebase"  # ✅ Set type
        session.files = initial_snapshot.files  # ✅ Unified file storage
        session.initial_files = dict(initial_snapshot.files)  # ✅ Save original input (deep copy)
        session.codebase_path = str(codebase_path)  # ✅ Codebase-specific metadata
        session.include_patterns = include_patterns
        session.output_path = output_path
        session.evaluator_code = evaluator_code
        session.objective = objective
        session.save()
        
        # Async/sync execution
        if async_mode:
            session.task = asyncio.create_task(
                self._run_evolution_codebase_background(
                    evolution_id, initial_snapshot, evaluator_code, objective, config, output_path
                )
            )
            return {
                "success": True,
                "evolution_id": evolution_id,
                "status": "running",
                "file_count": initial_snapshot.file_count(),
                "estimated_time": f"~{iterations * 15 // 60} minutes",
                "message": (
                    f"✅ Codebase evolution started successfully!\n\n"
                    f"🆔 Evolution ID: {evolution_id}\n"
                    f"📁 Files: {initial_snapshot.file_count()}\n"
                    f"⏱️ Estimated time: ~{iterations * 15 // 60} minutes\n\n"
                    f"📊 To monitor progress:\n"
                    f"1. Click the DNA icon (🧬) in left sidebar\n"
                    f"2. Find evolution in the list\n"
                    f"3. Click to view details\n\n"
                    f"Or use: get_evolution_status('{evolution_id}')"
                ),
            }
        else:
            # Sync execution
            try:
                result = await self._run_evolution_codebase(
                    evolution_id, initial_snapshot, evaluator_code, objective, config, output_path
                )
                return {
                    "success": True,
                    "evolution_id": evolution_id,
                    "status": "completed",
                    "file_count": len(session.files),
                    "best_score": result.best_score,
                    "improvement": session.improvement,
                    "summary": session.summary,
                }
            except Exception as e:
                logger.error(f"Codebase evolution failed: {e}")
                return {
                    "success": False,
                    "evolution_id": evolution_id,
                    "error": str(e),
                }
    
    
    # ===== Internal Methods =====
    
    async def _run_evolution(
        self,
        evolution_id: str,
        code: str,
        evaluator_code: str,
        objective: str,
        config,
    ):
        """Execute evolution (wait for completion)"""
        from pantheon.evolution import EvolutionTeam
        
        session = self.manager.get_session(evolution_id)
        session.status = "running"
        session.started_at = time.time()
        
        try:
            # Define progress callback to update session state in real-time
            def on_progress(iteration: int, best_score: float, error: str = None):
                """Called after each iteration (success or failure)"""
                session.update_progress(iteration, best_score, auto_save=True)
                if error:
                    session.failed_iterations += 1
                    session.last_error = error
                logger.info(f"Evolution {evolution_id} progress: iteration={iteration}, score={best_score:.4f}")
            
            team = EvolutionTeam(config=config)
            # Expose in-memory database for real-time visualization
            session._live_database = team.database

            def on_progress_with_db(iteration: int, best_score: float, error: str = None):
                session._live_database = team.database
                on_progress(iteration, best_score, error)

            result = await team.evolve(
                initial_code=code,
                evaluator_code=evaluator_code,
                objective=objective,
                progress_callback=on_progress_with_db,
            )

            # Clear live database, fall back to disk
            session._live_database = None

            session.status = "completed"
            session.completed_at = time.time()
            session.best_score = result.best_score
            session.current_iter = result.total_iterations
            
            # Save result data (unified model)
            session.files = {"main.py": result.best_code}  # ✅ Unified file storage
            
            # Calculate initial_score from score_history
            if session.score_history:
                session.initial_score = session.score_history[0]
                
                # Calculate improvement percentage
                if session.initial_score > 0:
                    session.improvement = ((session.best_score - session.initial_score) / session.initial_score) * 100
                else:
                    session.improvement = 0.0
                
                # Generate summary
                session.summary = (
                    f"Evolution completed in {result.total_iterations} iterations. "
                    f"Score improved from {session.initial_score:.4f} to {session.best_score:.4f} "
                    f"({session.improvement:+.1f}%)."
                )
            else:
                session.initial_score = 0.0
                session.improvement = 0.0
                session.summary = f"Evolution completed in {result.total_iterations} iterations."
            
            session.save()
            
            # Auto-generate HTML report
            try:
                html_path = Path(config.db_path) / "evolution_report.html"
                result.save_html_report(str(html_path))
                logger.info(f"HTML report saved to {html_path}")
            except Exception as e:
                logger.warning(f"Failed to generate HTML report: {e}")
            
            return result
            
        except Exception as e:
            session.status = "failed"
            session.error = str(e)
            logger.error(f"Evolution {evolution_id} failed: {e}")
            raise

    async def _run_evolution_background(self, evolution_id, code, evaluator_code, objective, config):
        """Run evolution in background (non-blocking)"""
        try:
            result = await self._run_evolution(evolution_id, code, evaluator_code, objective, config)
            logger.info(f"Evolution {evolution_id} completed: score={result.best_score:.4f}")
        except asyncio.CancelledError:
            logger.info(f"Evolution {evolution_id} cancelled")
            session = self.manager.get_session(evolution_id)
            if session:
                session.status = "cancelled"
        except Exception as e:
            logger.error(f"Evolution {evolution_id} failed: {e}")
    async def _run_evolution_codebase(
        self, evolution_id, initial_snapshot, evaluator_code, objective, config, output_path
    ):
        """Execute codebase evolution (wait for completion)"""
        from pantheon.evolution import EvolutionTeam
        
        session = self.manager.get_session(evolution_id)
        session.status = "running"
        session.started_at = time.time()
        
        try:
            # Define progress callback
            def on_progress(iteration, best_score, error=None):
                session.update_progress(iteration, best_score, auto_save=True)
                if error:
                    session.failed_iterations += 1
                    session.last_error = error
                logger.info(f"Codebase evolution {evolution_id} progress: iteration={iteration}, score={best_score:.4f}")
            
            team = EvolutionTeam(config=config)
            session._live_database = team.database

            def on_progress_with_db(iteration, best_score, error=None):
                session._live_database = team.database
                on_progress(iteration, best_score, error)

            result = await team.evolve(
                initial_code=initial_snapshot,
                evaluator_code=evaluator_code,
                objective=objective,
                progress_callback=on_progress_with_db,
            )

            session._live_database = None

            # Save result (unified model)
            session.status = "completed"
            session.completed_at = time.time()
            session.best_score = result.best_score
            session.current_iter = result.total_iterations
            
            # ✅ Unified file storage
            session.files = result.best_program.snapshot.files
            
            # Calculate result data
            if session.score_history:
                session.initial_score = session.score_history[0]
                
                if session.initial_score > 0:
                    session.improvement = ((session.best_score - session.initial_score) / session.initial_score) * 100
                else:
                    session.improvement = 0.0
                
                session.summary = (
                    f"Codebase evolution completed in {result.total_iterations} iterations. "
                    f"Score improved from {session.initial_score:.4f} to {session.best_score:.4f} "
                    f"({session.improvement:+.1f}%). "
                    f"Evolved {len(session.files)} files."
                )
            else:
                session.initial_score = 0.0
                session.improvement = 0.0
                session.summary = f"Codebase evolution completed in {result.total_iterations} iterations."
            
            # Save to output path if requested
            if output_path:
                output_dir = Path(output_path).expanduser().resolve()
                result.best_program.snapshot.to_workspace(str(output_dir))
                session.output_path = str(output_dir)
                logger.info(f"Best codebase saved to {output_dir}")
            
            session.save()
            
            # Auto-generate HTML report
            try:
                html_path = Path(config.db_path) / "evolution_report.html"
                result.save_html_report(str(html_path))
                logger.info(f"HTML report saved to {html_path}")
            except Exception as e:
                logger.warning(f"Failed to generate HTML report: {e}")
            
            return result
            
        except Exception as e:
            session.status = "failed"
            session.error = str(e)
            session.save()
            logger.error(f"Codebase evolution {evolution_id} failed: {e}")
            raise

    async def _run_evolution_codebase_background(
        self, evolution_id, initial_snapshot, evaluator_code, objective, config, output_path
    ):
        """Run codebase evolution in background (non-blocking)"""
        try:
            result = await self._run_evolution_codebase(
                evolution_id, initial_snapshot, evaluator_code, objective, config, output_path
            )
            logger.info(f"Codebase evolution {evolution_id} completed: score={result.best_score:.4f}")
        except asyncio.CancelledError:
            logger.info(f"Codebase evolution {evolution_id} cancelled")
            session = self.manager.get_session(evolution_id)
            if session:
                session.status = "cancelled"
                session.save()
        except Exception as e:
            logger.error(f"Codebase evolution {evolution_id} failed: {e}")

    # ===== Frontend-only Tools =====

    @tool(exclude=True)
    async def get_evolution_status(
        self,
        evolution_id: str,
        include_code: bool = False,
    ) -> Dict[str, Any]:
        """
        Query evolution status by ID - Track progress of async evolutions.

        Use when: Monitoring long-running evolutions started with async_mode=True.

        Args:
            evolution_id: Unique evolution identifier from evolve_code/evolve_codebase
            
            include_code: Whether to include files and evaluator_code (default: False)
                False: Lightweight polling, returns progress metrics only
                True: Complete data including input/output files (use when completed)

        Returns:
            dict:
                Basic (include_code=False):
                    {"evolution_id": str, "evolution_type": str, "status": str, "iteration": int, 
                     "max_iterations": int, "best_score": float, "score_history": list, 
                     "improvements_found": int, "file_count": int, "error": str|null, 
                     "has_html_report": bool, "config": dict}
                
                Complete (include_code=True):
                    {...basic fields..., 
                     "input": {"evaluator_code": str, "files": dict, "codebase_path": str, "include_patterns": list},
                     "result": {"initial_score": float, "improvement": float, "summary": str, "files": dict, "output_path": str}}
            
            Status values: "pending", "running", "completed", "failed", "cancelled", "not_found"

        Usage:
            # Start evolution
            result = await evolve_code(..., async_mode=True)
            evo_id = result["evolution_id"]
            
            # Poll status
            status = await get_evolution_status(evo_id)
            progress = status["iteration"] / status["max_iterations"] * 100
            
            # Get full results when completed
            if status["status"] == "completed":
                full = await get_evolution_status(evo_id, include_code=True)
                optimized_code = full["result"]["files"]["main.py"]
        """
        # Get session from manager (single source of truth)
        session = self.manager.get_session(evolution_id)
        
        if not session:
            return {
                "evolution_id": evolution_id,
                "status": "not_found",
            }
        
        # Check if HTML report exists
        has_html_report = False
        if session.workspace_path:
            html_path = Path(session.workspace_path) / "evolution_report.html"
            has_html_report = html_path.exists()
        
        # Build basic response
        response = {
            "evolution_id": evolution_id,
            "evolution_type": session.evolution_type,  # ✅ New: type indicator
            "status": session.status,
            "iteration": session.current_iter,
            "max_iterations": session.max_iter,
            "best_score": session.best_score,
            "score_history": session.score_history,
            "error": session.error,
            "failed_iterations": session.failed_iterations,
            "last_error": session.last_error,
            "file_count": len(session.files),
            "has_html_report": has_html_report,
        }
        
        # Calculate improvements
        if session.score_history:
            response["improvements_found"] = len([
                s for i, s in enumerate(session.score_history) 
                if i > 0 and s > session.score_history[i-1]
            ])
        
        # Add config
        response["config"] = {
            "objective": session.objective,
            "iterations": session.max_iter,
            "islands": session.num_islands,
            "model": session.mutator_model,
        }
        
        # Complete mode: include code and result data
        if include_code:
            response["input"] = {
                "evaluator_code": session.evaluator_code,
                "files": session.initial_files if session.initial_files else session.files,  # Use initial_files for display
            }
            
            # Codebase-specific metadata
            if session.evolution_type == "codebase":
                response["input"]["codebase_path"] = session.codebase_path
                response["input"]["include_patterns"] = session.include_patterns
            
            # Add result data if available
            if session.initial_score is not None:
                response["result"] = {
                    "initial_score": session.initial_score,
                    "improvement": session.improvement,
                    "summary": session.summary,
                    "files": session.files  # Add optimized code files
                }
                
                # Codebase-specific result
                if session.evolution_type == "codebase":
                    response["result"]["output_path"] = session.output_path
        
        return response
    
    @tool(exclude=True)
    async def get_evolution_monitor_data(
        self,
        evolution_id: str,
        include_history_limit: int = 20
    ) -> Dict[str, Any]:
        """
        Get all data needed for Evolution Monitor (aggregated API).
        
        This combines status, stats, and recent history into a single request
        to reduce HTTP overhead and improve data consistency.
        
        Args:
            evolution_id: Evolution ID
            include_history_limit: Number of recent history points to include (default: 20)
        
        Returns:
            Unified response with status, stats, and recent history
        """
        session = self.manager.get_session(evolution_id)
        
        if not session:
            return error_response(
                "NOT_FOUND",
                f"Evolution {evolution_id} not found"
            )
        
        try:
            # Build status data using helper method + extra fields
            status_data = session.to_status_dict()
            status_data.update({
                "score_history": session.score_history,
                "improvements_found": len([
                    s for i, s in enumerate(session.score_history)
                    if i > 0 and s > session.score_history[i-1]
                ]) if session.score_history else 0,
                "config": session.to_config_dict(),
            })
            
            # Get stats data
            stats_data = None
            recent_history = []
            
            db = session.get_database()
            if db:
                # Build stats using helper method
                stats = db.get_statistics()
                stats_data = session.to_stats_dict(db, stats)
                
                # Get recent history
                if db.programs:
                    programs = sorted(
                        db.programs.values(),
                        key=lambda p: p.generation
                    )[-include_history_limit:]
                    
                    for prog in programs:
                        history_point = {
                            "order": prog.generation,
                            "program_id": prog.id,
                        }
                        # Add all metrics
                        history_point.update(prog.metrics)
                        recent_history.append(history_point)
            
            return success_response({
                "status": status_data,
                "stats": stats_data,
                "recent_history": recent_history
            })
        except Exception as e:
            logger.error(f"Failed to get monitor data for {evolution_id}: {e}")
            return error_response(
                "INTERNAL_ERROR",
                "Failed to get monitor data",
                details=str(e)
            )
    
    @tool(exclude=True)
    async def cancel_evolution(
        self,
        evolution_id: str,
    ) -> Dict[str, Any]:
        """
        Cancel a running evolution.

        Args:
            evolution_id: Evolution ID to cancel

        Returns:
            dict: Result with success status
        """
        session = self.manager.get_session(evolution_id)
        if not session:
            return error_response(
                "NOT_FOUND",
                f"Evolution {evolution_id} not found"
            )
        
        if session.task and not session.task.done():
            session.task.cancel()
            session.status = "cancelled"
            return {
                "success": True,
                "evolution_id": evolution_id,
                "message": "Evolution cancelled",
            }
        else:
            return error_response(
                "INVALID_STATE",
                "Evolution not running or already finished",
                details={"status": session.status}
            )
    
    @tool(exclude=True)
    async def list_evolutions(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        List evolution sessions with pagination and filtering.
        
        Args:
            limit: Maximum number of results to return (default: 50)
            offset: Number of results to skip (default: 0)
            status_filter: Filter by status (optional)
            sort_by: Field to sort by (default: "created_at")
            sort_order: Sort order "asc" or "desc" (default: "desc")
        
        Returns:
            Unified response with paginated evolution list
        """
        try:
            all_sessions = list(self.manager._sessions.values())
            
            # Filter by status if specified
            if status_filter:
                all_sessions = [s for s in all_sessions if s.status == status_filter]
            
            # Sort
            reverse = (sort_order == "desc")
            try:
                all_sessions.sort(
                    key=lambda s: getattr(s, sort_by, 0),
                    reverse=reverse
                )
            except Exception:
                # Fallback to created_at if sort_by field doesn't exist
                all_sessions.sort(key=lambda s: s.created_at, reverse=True)
            
            # Pagination
            total = len(all_sessions)
            paginated = all_sessions[offset:offset + limit]
            
            evolutions = []
            for s in paginated:
                evolutions.append(s.to_status_dict())
            
            return success_response({
                "evolutions": evolutions,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total
            })
        except Exception as e:
            logger.error(f"Failed to list evolutions: {e}")
            return error_response(
                "INTERNAL_ERROR",
                "Failed to list evolutions",
                details=str(e)
            )
    
    @tool(exclude=True)
    async def get_evolution_html_report(
        self,
        evolution_id: str,
    ) -> Dict[str, Any]:
        """
        Get HTML visualization report of evolution
        
        Args:
            evolution_id: Evolution ID
        
        Returns:
            {
                "success": bool,
                "html": str,  # HTML content (if successful)
                "error": str  # Error message (if failed)
            }
        """
        session = self.manager.get_session(evolution_id)
        
        if not session:
            return {
                "success": False,
                "error": f"Evolution {evolution_id} not found"
            }
        
        if not session.workspace_path:
            return {
                "success": False,
                "error": "Evolution workspace not available"
            }
        
        html_path = Path(session.workspace_path) / "evolution_report.html"
        
        if not html_path.exists():
            return {
                "success": False,
                "error": "HTML report not found. Evolution may not be completed yet."
            }
        
        try:
            html_content = html_path.read_text(encoding='utf-8')
            return {
                "success": True,
                "html": html_content
            }
        except Exception as e:
            logger.error(f"Failed to read HTML report: {e}")
            return {
                "success": False,
                "error": f"Failed to read HTML report: {str(e)}"
            }
    
    @tool(exclude=True)
    async def get_evolution_visualization_data(
        self,
        evolution_id: str,
        data_type: str,
        since_order: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get evolution visualization data for real-time frontend rendering.
        
        This tool provides access to the same data used by the HTML visualizer,
        but in a structured format suitable for incremental updates.
        
        Args:
            evolution_id: Evolution ID
            data_type: Type of visualization data to retrieve:
                - "tree": Evolution tree structure (parent-child relationships)
                - "history": Score history over iterations
                - "heatmap": MAP-Elites heatmap data
                - "programs": Detailed program information
                - "stats": Summary statistics
            since_order: Optional. If provided, only return data for programs
                        with order > since_order (for incremental updates)
        
        Returns:
            {
                "success": bool,
                "data": {...},  # Data structure depends on data_type
                "latest_order": int,  # Highest order in current data
                "total_programs": int,  # Total number of programs
                "error": str  # Error message if failed
            }
        """
        from pantheon.evolution import EvolutionDatabase
        from pantheon.evolution.visualizer import EvolutionVisualizer
        
        session = self.manager.get_session(evolution_id)
        
        if not session:
            return {
                "success": False,
                "error": f"Evolution {evolution_id} not found"
            }
        
        if not session.workspace_path:
            return {
                "success": False,
                "error": "Evolution workspace not available"
            }
        
        try:
            # Load database from workspace using caching
            database = session.get_database()
            
            if not database:
                # Evolution hasn't started yet or no checkpoint saved
                return {
                    "success": True,
                    "data": None,
                    "latest_order": 0,
                    "total_programs": 0,
                    "message": "No evolution data available yet"
                }
            
            visualizer = EvolutionVisualizer(database, objective=session.objective)
            
            # Get latest order from database
            latest_order = 0
            if database.programs:
                latest_order = max((p.order for p in database.programs.values() if p.order is not None), default=0)
            
            # Prepare data based on type
            data = None
            if data_type == "tree":
                # Always return full tree structure to ensure D3.js can render correctly.
                # Incremental tree updates are tricky because we need the parent structure.
                # Given typical tree sizes, sending the full tree JSON is acceptable.
                tree_data = visualizer.build_tree_data()
                # Include best_program_id for frontend to locate best node
                data = {
                    "tree": tree_data,
                    "best_program_id": session.best_program_id if hasattr(session, 'best_program_id') else None
                }
            elif data_type == "history":
                data = visualizer.get_score_history()
            elif data_type == "heatmap":
                data = visualizer.get_map_elites_data()
            elif data_type == "programs":
                data = visualizer.get_programs_data()
            elif data_type == "stats":
                data = visualizer.get_summary_stats()
            else:
                return {
                    "success": False,
                    "error": f"Unknown data_type: {data_type}"
                }
            
            # Filter incremental ONLY for non-tree types where it makes sense
            if since_order is not None and data_type != "tree" and data_type != "stats":
                if data_type == "programs" and isinstance(data, dict):
                    # Filter programs by order
                    # Need to check order from visualizer or database
                    filtered_data = {}
                    for prog_id, prog_data in data.items():
                        prog = database.programs.get(prog_id)
                        if prog and prog.order is not None and prog.order > since_order:
                            filtered_data[prog_id] = prog_data
                    data = filtered_data
                elif data_type == "history" and isinstance(data, list):
                    # History is ordered by iteration, not strictly program order
                    # But usually we just append new history points.
                    # History format: [{"iteration": 0, "score": 0.5, "order": 10}, ...]
                    # Check if 'order' field exists in history points
                    if data and "order" in data[0]:
                         data = [d for d in data if d.get("order", 0) > since_order]
                    else:
                        # Fallback: if no order in history, return all (or implement iteration based filtering)
                        # Current visualizer.get_score_history might not include 'order'.
                        # Let's check visualizer.py implementation if needed.
                        # For now, let's assume client handles deduplication or valid "since_order" is sufficient if "order" is present.
                        pass
                # Heatmap acts as a grid, incremental update is also tricky unless we track cell updates.
                # For now, let's return full heatmap to be safe, or just filter map_elites_data list if it has order.
                elif data_type == "heatmap" and isinstance(data, list):
                     # Map elites data usually: [{"coords": [x,y], "score": ..., "program_id": ..., "order": ...}]
                     # If order is present:
                     if data and "order" in data[0]:
                         data = [d for d in data if d.get("order", 0) > since_order]
            
            return {
                "success": True,
                "data": data,
                "latest_order": latest_order,
                "total_programs": len(database.programs),
            }
            
        except Exception as e:
            logger.exception(f"Error generating visualization data: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # ===== Unified Tools (LLM-facing) =====

    @tool
    async def evolve(
        self,
        type: str,
        evaluator_code: str,
        objective: str,
        code: Optional[str] = None,
        codebase_path: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        iterations: Optional[int] = None,
        islands: Optional[int] = None,
        model: str = "normal",
        output_path: Optional[str] = None,
        async_mode: bool = True,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evolve and optimize code using evolutionary algorithms with LLM-guided mutation.

        Args:
            type: Evolution type:
                - "code": Optimize a single code string (requires `code` arg)
                - "codebase": Optimize a multi-file project (requires `codebase_path` arg)

            code: Initial code to optimize (required when type="code")

            codebase_path: Path to directory containing the codebase (required when type="codebase")

            evaluator_code: Python function: evaluate(workspace_path: str) -> Dict[str, float]
                MUST return individual metrics (0-1 range) AND a "fitness_weights" dict.
                Example:
                ```python
                def evaluate(workspace_path):
                    import sys; sys.path.insert(0, workspace_path)
                    from main import func
                    correct = float(func(test_data) == expected)
                    speed = 1.0 / (1.0 + elapsed)
                    return {
                        "correctness": correct,
                        "speed": speed,
                        "fitness_weights": {"correctness": 0.7, "speed": 0.3},
                    }
                ```

            objective: Optimization goal (e.g., "Optimize for speed")

            include_patterns: For codebase type: glob patterns for files (default: ["**/*.py"])

            iterations: Max iterations (default: 50)

            islands: Evolution islands for diversity (default: 3)

            model: Mutation model quality (default: "normal", options: "high", "normal", "low")

            output_path: For codebase type: path to save optimized codebase

            async_mode: Run in background (default: True, RECOMMENDED for iterations > 20)

            timeout: Timeout seconds for sync mode (default: auto-estimate)

        Returns:
            dict with evolution_id and status
        """
        if type == "code":
            if not code:
                return error_response("INVALID_INPUT", "'code' is required when type='code'")
            return await self.evolve_code(
                code=code,
                evaluator_code=evaluator_code,
                objective=objective,
                iterations=iterations,
                islands=islands,
                model=model,
                async_mode=async_mode,
                timeout=timeout,
            )
        elif type == "codebase":
            if not codebase_path:
                return error_response("INVALID_INPUT", "'codebase_path' is required when type='codebase'")
            return await self.evolve_codebase(
                codebase_path=codebase_path,
                evaluator_code=evaluator_code,
                objective=objective,
                include_patterns=include_patterns,
                iterations=iterations,
                islands=islands,
                model=model,
                output_path=output_path,
                async_mode=async_mode,
            )
        else:
            return error_response("INVALID_INPUT", f"Unknown type '{type}'. Must be 'code' or 'codebase'.")

    @tool
    async def evolution_manage(
        self,
        evolution_id: str,
        action: str = "status",
        include_code: bool = False,
    ) -> Dict[str, Any]:
        """
        Manage and monitor evolution sessions.

        Args:
            evolution_id: Unique evolution identifier from evolve()
            action: Operation to perform:
                - "status": Get evolution status and progress (default)
                - "cancel": Cancel a running evolution
            include_code: For status action: include files and evaluator_code (default: False)

        Returns:
            dict with evolution status or cancellation result

        Examples:
            # Check progress
            evolution_manage(evolution_id, action="status")

            # Get full results when completed
            evolution_manage(evolution_id, action="status", include_code=True)

            # Cancel a running evolution
            evolution_manage(evolution_id, action="cancel")
        """
        if action == "status":
            return await self.get_evolution_status(
                evolution_id=evolution_id,
                include_code=include_code,
            )
        elif action == "cancel":
            return await self.cancel_evolution(
                evolution_id=evolution_id,
            )
        else:
            return error_response(
                "INVALID_INPUT",
                f"Unknown action '{action}'. Must be 'status' or 'cancel'."
            )
