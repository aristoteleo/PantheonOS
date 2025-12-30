"""
Hybrid evaluation system for evolved programs.

Combines function-based evaluation with LLM feedback.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from pantheon.utils.log import logger

from .config import EvolutionConfig
from .program import CodebaseSnapshot, Program


@dataclass
class EvaluationResult:
    """Result of evaluating a program."""

    success: bool
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    llm_feedback: str = ""
    error: Optional[str] = None
    execution_time: float = 0.0


class HybridEvaluator:
    """
    Hybrid evaluator combining function evaluation and LLM feedback.

    Evaluation flow:
    1. Function evaluation: Run user-provided evaluator, get metrics
    2. LLM feedback: Analyze code quality, get suggestions
    3. Combined score: Weighted merge of both results
    """

    def __init__(
        self,
        evaluator_code: str,
        feedback_agent: Optional[Any] = None,  # Agent type
        function_weight: float = 0.7,
        llm_weight: float = 0.3,
        max_parallel: int = 4,
        timeout: int = 120,
        workspace_base: Optional[str] = None,
    ):
        """
        Initialize hybrid evaluator.

        Args:
            evaluator_code: Python code defining evaluate(workspace_path) function
            feedback_agent: Agent for LLM feedback (created if None)
            function_weight: Weight for function evaluation (0-1)
            llm_weight: Weight for LLM feedback (0-1)
            max_parallel: Maximum concurrent evaluations
            timeout: Evaluation timeout in seconds
            workspace_base: Base directory for evaluation workspaces
        """
        self.evaluator_code = evaluator_code
        self.feedback_agent = feedback_agent
        self.function_weight = function_weight
        self.llm_weight = llm_weight
        self.max_parallel = max_parallel
        self.timeout = timeout
        self.workspace_base = workspace_base or tempfile.mkdtemp(prefix="evolution_")
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._workspace_counter = 0
        self._python_interpreter = None

    async def _get_python_interpreter(self):
        """Lazy-load Python interpreter toolset."""
        if self._python_interpreter is None:
            try:
                from pantheon.toolsets.python import PythonInterpreterToolSet
                self._python_interpreter = PythonInterpreterToolSet("evolution_evaluator")
            except ImportError:
                logger.warning("PythonInterpreterToolSet not available, using subprocess")
        return self._python_interpreter

    async def _get_feedback_agent(self):
        """Lazy-create feedback agent if needed."""
        if self.feedback_agent is None and self.llm_weight > 0:
            try:
                from pantheon.agent import Agent
                self.feedback_agent = Agent(
                    name="code-reviewer",
                    instructions=CODE_REVIEWER_PROMPT,
                    model="normal",
                )
            except ImportError:
                logger.warning("Agent not available, LLM feedback disabled")
                self.llm_weight = 0
                self.function_weight = 1.0
        return self.feedback_agent

    def _get_workspace_path(self) -> str:
        """Get a unique workspace path."""
        self._workspace_counter += 1
        return str(Path(self.workspace_base) / f"workspace_{self._workspace_counter}")

    async def evaluate(
        self,
        program: Program,
        workspace_path: Optional[str] = None,
    ) -> EvaluationResult:
        """
        Evaluate a single program.

        Args:
            program: Program to evaluate
            workspace_path: Optional specific workspace path

        Returns:
            EvaluationResult with metrics and feedback
        """
        async with self._semaphore:
            workspace = workspace_path or self._get_workspace_path()

            try:
                # Write code to workspace
                program.snapshot.to_workspace(workspace)

                # Run evaluations in parallel
                tasks = []

                if self.function_weight > 0:
                    tasks.append(self._run_function_evaluation(workspace))

                if self.llm_weight > 0:
                    tasks.append(self._get_llm_feedback(program))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                func_result: Dict[str, Any] = {}
                llm_result: Dict[str, Any] = {}

                idx = 0
                if self.function_weight > 0:
                    if isinstance(results[idx], Exception):
                        func_result = {"error": str(results[idx])}
                    else:
                        func_result = results[idx]
                    idx += 1

                if self.llm_weight > 0:
                    if isinstance(results[idx], Exception):
                        llm_result = {"error": str(results[idx])}
                    else:
                        llm_result = results[idx]

                # Calculate combined score
                func_score = func_result.get("combined_score", 0.0)
                llm_score = llm_result.get("score", 50) / 100.0  # Normalize to 0-1

                combined_score = (
                    func_score * self.function_weight +
                    llm_score * self.llm_weight
                )

                # Build metrics dict
                metrics = {
                    "combined_score": combined_score,
                    "function_score": func_score,
                    "llm_score": llm_score,
                }
                # Include other metrics from function evaluation
                for k, v in func_result.items():
                    if k not in ["combined_score", "error"] and isinstance(v, (int, float)):
                        metrics[k] = v

                # Build artifacts
                artifacts = {
                    "llm_feedback": llm_result.get("summary", ""),
                    "issues": llm_result.get("issues", []),
                    "suggestions": llm_result.get("suggestions", []),
                }
                if "error" in func_result:
                    artifacts["evaluation_error"] = func_result["error"]
                if "stderr" in func_result:
                    artifacts["stderr"] = func_result["stderr"]

                return EvaluationResult(
                    success=True,
                    metrics=metrics,
                    artifacts=artifacts,
                    llm_feedback=llm_result.get("summary", ""),
                )

            except Exception as e:
                logger.error(f"Evaluation failed: {e}")
                return EvaluationResult(
                    success=False,
                    error=str(e),
                    metrics={"combined_score": 0.0},
                )

    async def evaluate_batch(
        self,
        programs: List[Program],
    ) -> List[EvaluationResult]:
        """
        Evaluate multiple programs in parallel.

        Args:
            programs: List of programs to evaluate

        Returns:
            List of EvaluationResults
        """
        tasks = [self.evaluate(program) for program in programs]
        return await asyncio.gather(*tasks)

    async def _run_function_evaluation(self, workspace_path: str) -> Dict[str, Any]:
        """
        Run the user-provided evaluation function.

        Args:
            workspace_path: Path to the code workspace

        Returns:
            Dict with metrics from evaluation
        """
        interpreter = await self._get_python_interpreter()

        if interpreter:
            return await self._run_with_interpreter(workspace_path, interpreter)
        else:
            return await self._run_with_subprocess(workspace_path)

    async def _run_with_interpreter(
        self,
        workspace_path: str,
        interpreter,
    ) -> Dict[str, Any]:
        """Run evaluation using PythonInterpreterToolSet."""
        eval_code = f'''
import sys
import os

# Add workspace to path
sys.path.insert(0, r"{workspace_path}")
os.chdir(r"{workspace_path}")

# User evaluator code
{self.evaluator_code}

# Run evaluation
try:
    result = evaluate(r"{workspace_path}")
    if not isinstance(result, dict):
        result = {{"combined_score": float(result) if result else 0.0}}
except Exception as e:
    result = {{"error": str(e), "combined_score": 0.0}}
'''
        try:
            response = await asyncio.wait_for(
                interpreter.run_python_code(eval_code, result_var_name="result"),
                timeout=self.timeout,
            )

            if response.get("success"):
                result = response.get("result", {})
                if isinstance(result, dict):
                    return result
                else:
                    return {"combined_score": float(result) if result else 0.0}
            else:
                error = response.get("stderr", "") or response.get("error", "Unknown error")
                return {"error": error, "combined_score": 0.0}

        except asyncio.TimeoutError:
            return {"error": "Evaluation timed out", "combined_score": 0.0}
        except Exception as e:
            return {"error": str(e), "combined_score": 0.0}

    async def _run_with_subprocess(self, workspace_path: str) -> Dict[str, Any]:
        """Run evaluation using subprocess (fallback)."""
        import subprocess

        eval_script = f'''
import sys
import os
import json

sys.path.insert(0, r"{workspace_path}")
os.chdir(r"{workspace_path}")

{self.evaluator_code}

try:
    result = evaluate(r"{workspace_path}")
    if not isinstance(result, dict):
        result = {{"combined_score": float(result) if result else 0.0}}
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{"error": str(e), "combined_score": 0.0}}))
'''

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", eval_script,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workspace_path,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

            if stdout:
                try:
                    return json.loads(stdout.decode())
                except json.JSONDecodeError:
                    pass

            if stderr:
                return {"error": stderr.decode(), "combined_score": 0.0}

            return {"error": "No output from evaluator", "combined_score": 0.0}

        except asyncio.TimeoutError:
            return {"error": "Evaluation timed out", "combined_score": 0.0}
        except Exception as e:
            return {"error": str(e), "combined_score": 0.0}

    async def _get_llm_feedback(self, program: Program) -> Dict[str, Any]:
        """
        Get LLM feedback on the code.

        Args:
            program: Program to analyze

        Returns:
            Dict with score, issues, suggestions, summary
        """
        agent = await self._get_feedback_agent()
        if agent is None:
            return {"score": 50, "summary": "LLM feedback not available"}

        try:
            # Build code summary
            code_summary = program.snapshot.to_summary(max_files=5, max_lines_per_file=100)

            # Build prompt
            prompt = f"""Analyze this codebase and provide feedback:

{code_summary}

{"Changes from parent:" if program.diff_from_parent else ""}
```diff
{program.diff_from_parent[:3000] if program.diff_from_parent else "Initial version"}
```

Provide your assessment in JSON format with:
- score: 0-100 quality score
- issues: list of specific issues found
- suggestions: list of improvement suggestions
- summary: brief overall assessment
"""

            response = await asyncio.wait_for(
                agent.run(prompt),
                timeout=60,
            )

            # Parse JSON from response
            return self._parse_llm_response(response.content)

        except asyncio.TimeoutError:
            return {"score": 50, "summary": "LLM feedback timed out"}
        except Exception as e:
            logger.warning(f"LLM feedback failed: {e}")
            return {"score": 50, "summary": f"LLM feedback error: {e}"}

    def _parse_llm_response(self, content: str) -> Dict[str, Any]:
        """Parse LLM response to extract JSON."""
        # Try to find JSON in response
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Try to extract fields manually
        result = {
            "score": 50,
            "issues": [],
            "suggestions": [],
            "summary": content[:500] if content else "",
        }

        # Look for score
        score_match = re.search(r'"score"\s*:\s*(\d+)', content)
        if score_match:
            result["score"] = int(score_match.group(1))

        return result


# Default code reviewer prompt
CODE_REVIEWER_PROMPT = """You are an expert code reviewer. Analyze the given code and provide:
1. A quality score (0-100)
2. Specific issues found
3. Concrete improvement suggestions

Consider:
- Code correctness and potential bugs
- Performance and efficiency
- Code structure and organization
- Best practices and patterns
- Error handling

Output JSON format:
{
    "score": 85,
    "issues": ["Issue 1 description", "Issue 2 description"],
    "suggestions": ["Suggestion 1", "Suggestion 2"],
    "summary": "Brief overall assessment"
}
"""


class FunctionEvaluator:
    """
    Simple function-based evaluator without LLM feedback.

    For cases where only metric evaluation is needed.
    """

    def __init__(
        self,
        evaluator_func: Callable[[str], Dict[str, float]],
        timeout: int = 60,
        workspace_base: Optional[str] = None,
    ):
        """
        Initialize with evaluator function.

        Args:
            evaluator_func: Function that takes workspace path and returns metrics dict
            timeout: Evaluation timeout in seconds
            workspace_base: Base directory for workspaces
        """
        self.evaluator_func = evaluator_func
        self.timeout = timeout
        self.workspace_base = workspace_base or tempfile.mkdtemp(prefix="evolution_")
        self._workspace_counter = 0

    def _get_workspace_path(self) -> str:
        """Get a unique workspace path."""
        self._workspace_counter += 1
        return str(Path(self.workspace_base) / f"workspace_{self._workspace_counter}")

    async def evaluate(self, program: Program) -> EvaluationResult:
        """Evaluate a program using the function."""
        workspace = self._get_workspace_path()

        try:
            program.snapshot.to_workspace(workspace)

            # Run evaluator in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            metrics = await asyncio.wait_for(
                loop.run_in_executor(None, self.evaluator_func, workspace),
                timeout=self.timeout,
            )

            if not isinstance(metrics, dict):
                metrics = {"combined_score": float(metrics) if metrics else 0.0}

            return EvaluationResult(
                success=True,
                metrics=metrics,
            )

        except asyncio.TimeoutError:
            return EvaluationResult(
                success=False,
                error="Evaluation timed out",
                metrics={"combined_score": 0.0},
            )
        except Exception as e:
            return EvaluationResult(
                success=False,
                error=str(e),
                metrics={"combined_score": 0.0},
            )


# Import sys for subprocess fallback
import sys
