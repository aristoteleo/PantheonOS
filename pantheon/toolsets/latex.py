import subprocess

from ..toolset import ToolSet, tool


class LatexToolSet(ToolSet):
    """Latex toolset. Allow agent to compile LaTeX files to pdf.

    Args:
        name: The name of the toolset.
        worker_params: The parameters for the worker.
        **kwargs: Additional keyword arguments.
    """

    @tool(job_type="thread")
    async def compile_latex(self, tex_file: str) -> str:
        """
        Compile a LaTeX file and return the output.
        """
        try:
            result = subprocess.run(
                ["pdflatex", tex_file],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            return f"Error compiling LaTeX file: {e}"


__all__ = ["LatexToolSet"]