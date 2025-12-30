"""
Evaluator ToolSet - Code evaluation tools for Agents.

Provides tools for evaluating codebases during evolution or standalone.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from pantheon.toolset import tool, ToolSet
from pantheon.utils.log import logger


class EvaluatorToolSet(ToolSet):
    """
    ToolSet for evaluating code quality and performance.

    Provides tools for running evaluators, computing metrics,
    and getting LLM feedback on code.

    Example:
        ```python
        from pantheon.agent import Agent
        from pantheon.toolsets.evolution import EvaluatorToolSet

        agent = Agent(
            name="code-reviewer",
            instructions="You analyze and evaluate code quality.",
        )
        agent.toolset(EvaluatorToolSet("evaluator"))
        ```
    """

    def __init__(
        self,
        name: str = "evaluator",
        workdir: Optional[str] = None,
        timeout: int = 120,
        **kwargs,
    ):
        """
        Initialize the Evaluator ToolSet.

        Args:
            name: Name of the toolset
            workdir: Working directory for evaluation workspaces
            timeout: Default timeout for evaluations in seconds
            **kwargs: Additional arguments passed to ToolSet
        """
        super().__init__(name, **kwargs)
        self.workdir = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        self.timeout = timeout

    @tool
    async def evaluate_code(
        self,
        code: str,
        evaluator_code: str,
        filename: str = "main.py",
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate a piece of code using a custom evaluator function.

        Args:
            code: The code to evaluate
            evaluator_code: Python code defining an `evaluate(workspace_path)` function
                that returns a dict with metrics. Must include at least "combined_score".
                Example:
                ```python
                def evaluate(workspace_path):
                    # Run tests, benchmarks, etc.
                    return {
                        "combined_score": 0.85,
                        "tests_passed": 10,
                        "tests_total": 12,
                    }
                ```
            filename: Name of the file to save the code as (default: "main.py")
            timeout: Evaluation timeout in seconds (default: 120)

        Returns:
            dict: Evaluation results containing:
                - success: Whether evaluation completed
                - metrics: Dict of all metrics returned by evaluator
                - combined_score: The main fitness score (0-1)
                - error: Error message if evaluation failed
        """
        timeout = timeout or self.timeout

        try:
            # Create temporary workspace
            with tempfile.TemporaryDirectory(prefix="eval_") as workspace:
                workspace_path = Path(workspace)

                # Write code to file
                code_file = workspace_path / filename
                code_file.write_text(code)

                # Run evaluator
                result = await self._run_evaluator(
                    evaluator_code, str(workspace_path), timeout
                )

                return {
                    "success": True,
                    "metrics": result,
                    "combined_score": result.get("combined_score", 0),
                }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Evaluation timed out after {timeout} seconds",
                "metrics": {},
                "combined_score": 0,
            }
        except Exception as e:
            logger.error(f"Evaluation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "metrics": {},
                "combined_score": 0,
            }

    @tool
    async def evaluate_codebase(
        self,
        codebase_path: str,
        evaluator_code: str,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate an entire codebase using a custom evaluator function.

        Args:
            codebase_path: Path to the codebase directory
            evaluator_code: Python code defining an `evaluate(workspace_path)` function
            timeout: Evaluation timeout in seconds

        Returns:
            dict: Evaluation results with metrics and combined_score
        """
        timeout = timeout or self.timeout

        try:
            codebase_path = Path(codebase_path).expanduser().resolve()
            if not codebase_path.is_dir():
                return {
                    "success": False,
                    "error": f"Codebase path not found: {codebase_path}",
                    "metrics": {},
                    "combined_score": 0,
                }

            result = await self._run_evaluator(
                evaluator_code, str(codebase_path), timeout
            )

            return {
                "success": True,
                "metrics": result,
                "combined_score": result.get("combined_score", 0),
            }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Evaluation timed out after {timeout} seconds",
                "metrics": {},
                "combined_score": 0,
            }
        except Exception as e:
            logger.error(f"Codebase evaluation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "metrics": {},
                "combined_score": 0,
            }

    @tool
    async def compute_code_metrics(
        self,
        code: str,
    ) -> Dict[str, Any]:
        """
        Compute static code metrics for a piece of code.

        Metrics include complexity, line counts, and diversity measures
        used by the MAP-Elites algorithm.

        Args:
            code: The code to analyze

        Returns:
            dict: Code metrics including:
                - complexity: Cyclomatic complexity score (0-1)
                - diversity: Code diversity/uniqueness score (0-1)
                - total_lines: Total number of lines
                - code_lines: Non-empty, non-comment lines
                - num_functions: Number of function definitions
                - num_classes: Number of class definitions
                - avg_function_length: Average function length
        """
        try:
            from pantheon.evolution.utils.metrics import (
                compute_complexity,
                compute_diversity,
            )

            complexity = compute_complexity(code)
            diversity = compute_diversity(code)

            # Count lines
            lines = code.split("\n")
            total_lines = len(lines)
            code_lines = sum(
                1 for line in lines if line.strip() and not line.strip().startswith("#")
            )

            # Count definitions
            import ast

            try:
                tree = ast.parse(code)
                num_functions = sum(
                    1 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                )
                num_classes = sum(
                    1 for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                )

                # Average function length
                func_lengths = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        func_lengths.append(node.end_lineno - node.lineno + 1)
                avg_function_length = (
                    sum(func_lengths) / len(func_lengths) if func_lengths else 0
                )

            except SyntaxError:
                num_functions = 0
                num_classes = 0
                avg_function_length = 0

            return {
                "success": True,
                "complexity": complexity,
                "diversity": diversity,
                "total_lines": total_lines,
                "code_lines": code_lines,
                "num_functions": num_functions,
                "num_classes": num_classes,
                "avg_function_length": avg_function_length,
            }

        except Exception as e:
            logger.error(f"Failed to compute code metrics: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @tool
    async def get_llm_code_review(
        self,
        code: str,
        context: Optional[str] = None,
        model: str = "normal",
    ) -> Dict[str, Any]:
        """
        Get an LLM-based code review.

        Uses an AI model to analyze code quality, find issues,
        and suggest improvements.

        Args:
            code: The code to review
            context: Optional context about what the code does
            model: Model to use for the review

        Returns:
            dict: Review results containing:
                - success: Whether review completed
                - score: Quality score (0-100)
                - issues: List of issues found
                - suggestions: List of improvement suggestions
                - summary: Overall assessment
        """
        try:
            from pantheon.agent import Agent

            reviewer = Agent(
                name="code-reviewer",
                instructions="""You are an expert code reviewer. Analyze the given code and provide:
1. A quality score (0-100)
2. Specific issues found
3. Concrete improvement suggestions
4. A brief overall assessment

Output JSON format:
{
    "score": 85,
    "issues": ["Issue 1", "Issue 2"],
    "suggestions": ["Suggestion 1", "Suggestion 2"],
    "summary": "Brief overall assessment"
}""",
                model=model,
            )

            prompt = f"Review this code:\n\n```python\n{code}\n```"
            if context:
                prompt = f"Context: {context}\n\n{prompt}"

            response = await reviewer.run(prompt)

            # Parse JSON response
            import json
            import re

            content = response.content
            # Try to extract JSON from response
            json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
            if json_match:
                review_data = json.loads(json_match.group())
            else:
                # Fallback if no JSON found
                review_data = {
                    "score": 50,
                    "issues": [],
                    "suggestions": [],
                    "summary": content[:500],
                }

            return {
                "success": True,
                "score": review_data.get("score", 50),
                "issues": review_data.get("issues", []),
                "suggestions": review_data.get("suggestions", []),
                "summary": review_data.get("summary", ""),
            }

        except Exception as e:
            logger.error(f"LLM code review failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "score": 0,
            }

    async def _run_evaluator(
        self,
        evaluator_code: str,
        workspace_path: str,
        timeout: int,
    ) -> Dict[str, Any]:
        """Run the evaluator code and return results."""
        # Build evaluation code
        eval_script = f'''
import sys
import json

{evaluator_code}

if __name__ == "__main__":
    result = evaluate("{workspace_path}")
    print("__EVAL_RESULT__")
    print(json.dumps(result))
'''

        # Run in subprocess with timeout
        process = await asyncio.create_subprocess_exec(
            sys.executable if hasattr(sys, "executable") else "python",
            "-c",
            eval_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_path,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            raise

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise RuntimeError(f"Evaluator failed: {stderr_str}")

        # Extract result
        if "__EVAL_RESULT__" in stdout_str:
            result_json = stdout_str.split("__EVAL_RESULT__")[1].strip()
            import json

            return json.loads(result_json)
        else:
            raise RuntimeError(f"Evaluator did not return result. stdout: {stdout_str}")


# Import sys for _run_evaluator
import sys
