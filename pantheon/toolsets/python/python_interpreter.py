import os
import io
import ast
import base64
import json
import traceback
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from executor.engine import Engine, ProcessJob

from pantheon.toolset import tool, ToolSet
from pantheon.internal.package_runtime.context import build_context_env
from pantheon.utils.log import logger


class PythonInterpreterError(Exception):
    pass


def exec_with_echo(code, env=None):
    """Execute code with echo."""
    if env is None:
        env = {}
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Expr):
            expr_code = compile(ast.Expression(node.value), "<string>", "eval")
            result = eval(expr_code, env)
            if result is not None:
                print(result)
        else:
            exec(compile(ast.Module([node], []), "<string>", "exec"), env)


DEFAULT_INIT_CODE = """
# Enable nested asyncio for packages API async methods (e.g., asyncio.run() in event loop)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio not available

try:
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import os
    import uuid

    GLOBAL_FIG_PATH = None
    GLOBAL_FIG_DIR = ".matplotlib_figs"

    original_show = plt.show

    def __custom_plt_show(*args, **kwargs):
        global GLOBAL_FIG_PATH
        fig = plt.gcf()
        if not fig.get_axes():
            print("No active figure to save.")
            plt.close(fig)
            return

        fig_uuid = str(uuid.uuid4())
        os.makedirs(GLOBAL_FIG_DIR, exist_ok=True)
        GLOBAL_FIG_PATH = os.path.join(GLOBAL_FIG_DIR, fig_uuid + ".png")
        fig.savefig(GLOBAL_FIG_PATH, format='png')
        plt.close(fig)

    __plt_show = plt.show
    plt.show = __custom_plt_show
except Exception as e:
    print(f"Error in matplotlib initialization: {e}")
"""


class PythonInterpreterToolSet(ToolSet):
    """Python interpreter toolset with automatic code validation.
    For running Python code in an interpreter with built-in validation.

    Args:
        name: The name of the toolset.
        workdir: The working directory for the interpreter.
        engine: The engine to use for the interpreter.
        init_code: The code to run before the interpreter starts.
        **kwargs: Additional keyword arguments.
    """

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        engine: Engine | None = None,
        init_code: str | None = DEFAULT_INIT_CODE,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.interpreters = {}
        self.jobs = {}
        self._engine = engine
        self.engine = None
        self.clientid_to_interpreterid = {}
        self.workdir = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        self.init_code = init_code
        self._bootstrapped: set[str] = set()

    def _init_engine(self):
        if self.engine is None:
            if self._engine is None:
                self.engine = Engine()
            else:
                self.engine = self._engine

    def _current_context_dict(self) -> dict:
        return dict(self.get_context() or {})

    async def _inject_runtime_context(self, interpreter_id: str):
        # build_context_env() now automatically optimizes environment size
        effective_workdir = self._get_effective_workdir() or str(self.workdir)
        env = build_context_env(
            workdir=effective_workdir,
            context_variables=self._current_context_dict(),
            base_env=os.environ.copy(),  # Inherit all env vars including R_HOME, LD_LIBRARY_PATH, etc.
            optimize=True,  # Enable automatic optimization
        )
        if not env:
            return
        env_literal = json.dumps(env)
        await self.__run_code_in_interpreter(
            f"import os; os.environ.update({env_literal})",
            interpreter_id,
        )

    @tool
    async def run_python_code(
        self,
        code: str,
        interpreter_id: str | None = None,
        result_var_name: str | None = None,
    ):
        """Run Python code and return the result.

        This tool automatically manages a Python interpreter session for you.
        Variables and state are preserved between calls in the same session.

        Args:
            code: The Python code to run.
            result_var_name: Optional. The name of the variable to return the value of.
                If not provided, the tool returns the captured stdout/stderr.
            interpreter_id: Optional. The specific interpreter ID to run the code in.
                If not provided, the tool uses the default interpreter for the current context.
                Only configure this parameter if you need to manage multiple independent interpreter sessions.

        Returns:
            dict: The execution result with the following structure:
            {
                "success": bool,        # True if execution was successful
                "result": Any | None,   # The value of `result_var_name` if specified
                "stdout": str,          # Captured standard output
                "stderr": str,          # Captured standard error
            }
        """
        if interpreter_id:
             p_id = interpreter_id
        else:
            context_dict = dict(self.get_context() or {})
            client_id = context_dict.get("client_id")
            if client_id is None:
                client_id = "default"
                # Debug level - not a real problem, just informational
                logger.debug("No client id provided, using default client id.")
            p_id = self.clientid_to_interpreterid.get(client_id)
            if (p_id is None) or (p_id not in self.interpreters):
                create_resp = await self.new_interpreter()
                p_id = create_resp["interpreter_id"]
                self.clientid_to_interpreterid[client_id] = p_id

        await self._inject_runtime_context(p_id)

        # Execute code with automatic recovery on process failure
        try:
            res = await self.run_code_in_interpreter(code, p_id, result_var_name)
        except Exception as e:
            # Handle BrokenProcessPool and other process failures
            error_str = str(e)
            error_type = type(e).__name__

            # Check for various process failure indicators
            is_process_failure = (
                "BrokenProcessPool" in error_str
                or "ProcessPool" in error_str
                or "loky.process_executor.BrokenProcessPool" in error_str
                or "TerminatedWorkerError" in error_type
                or "TerminatedWorkerError" in error_str
                or "SIGSEGV" in error_str
                or "segmentation fault" in error_str.lower()
                or "worker" in error_str.lower()
                and "terminated" in error_str.lower()
                or "un-serialize" in error_str
                or "picklable" in error_str
                or "KeyboardInterrupt" in error_str
                or error_type == "BrokenProcessPool"
                or p_id not in self.interpreters
            )

            if is_process_failure:
                logger.warning(
                    f"Python interpreter crashed (client_id: {client_id}), restarting..."
                )
                logger.debug(f"Crash details: {error_type}: {error_str[:200]}")

                # Clean up broken interpreter more thoroughly
                if p_id in self.interpreters:
                    try:
                        await self.delete_interpreter(p_id)
                    except:
                        # Force cleanup if normal deletion fails
                        try:
                            if p_id in self.interpreters:
                                del self.interpreters[p_id]
                            if p_id in self.jobs:
                                del self.jobs[p_id]
                            # Clear the client mapping
                            if client_id in self.clientid_to_interpreterid:
                                del self.clientid_to_interpreterid[client_id]
                        except:
                            pass  # Ignore cleanup errors

                # Create new interpreter and retry
                create_resp = await self.new_interpreter()
                p_id = create_resp["interpreter_id"]
                self.clientid_to_interpreterid[client_id] = p_id
                logger.info(f"Python interpreter restarted (client_id: {client_id})")

                try:
                    res = await self.run_code_in_interpreter(
                        code, p_id, result_var_name
                    )
                    logger.info("Code execution successful after interpreter restart")
                    # Add a note to the result that interpreter was restarted
                    res["interpreter_restarted"] = True
                    res["restart_reason"] = f"{error_type}: {error_str[:100]}..."
                except Exception as retry_error:
                    logger.error(
                        f"Code execution failed even after interpreter restart: {retry_error}"
                    )
                    # Return a more user-friendly error message
                    return {
                        "success": False,
                        "result": None,
                        "stdout": "",
                        "stderr": f"Python interpreter crashed and restart failed.\nOriginal error: {error_type}\nRetry error: {str(retry_error)[:200]}...\n\nTry using /restart command to fully reset the Python environment.",
                        "interpreter_crashed": True,
                    }
            else:
                # Re-raise non-process-related exceptions
                raise e

        res.setdefault("success", True)
        return res

    async def __run_code_in_interpreter(
        self,
        code: str,
        interpreter_id: str,
        result_var_name: str | None = None,
    ) -> dict:
        if interpreter_id not in self.interpreters:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        # logger.info(f"[DEBUG] Running code in interpreter {interpreter_id}")
        g = self.interpreters[interpreter_id]
        result, stdout, stderr = await g.asend((code, result_var_name))
        if isinstance(result, PythonInterpreterError):
            raise result
        return {
            "result": result,
            "stdout": stdout,
            "stderr": stderr,
        }

    @tool(exclude=True)
    async def run_code_in_interpreter(
        self,
        code: str,
        interpreter_id: str,
        result_var_name: str | None = None,
    ) -> dict:
        """Run code in an interpreter.

        Args:
            code: The code to run.
            interpreter_id: The id of the interpreter to run the code in.
            result_var_name: The name of the variable you want to get the result from.
                If not needed, set to None. Default is None.

        Returns:
            A dictionary with the result, stdout, and stderr.
        """
        await self._inject_runtime_context(interpreter_id)

        code = "GLOBAL_FIG_PATH = None\n" + code
        res = await self.__run_code_in_interpreter(
            code, interpreter_id, result_var_name
        )
        res2 = await self.__run_code_in_interpreter(
            "None", interpreter_id, "GLOBAL_FIG_PATH"
        )
        fig_path = res2["result"]
        if fig_path is not None:
            res["fig_storage_path"] = fig_path
            open_path = fig_path
            effective_wd = self._get_effective_workdir() or (str(self.workdir) if self.workdir else None)
            if effective_wd:
                open_path = os.path.join(effective_wd, fig_path)
            with open(open_path, "rb") as f:
                base64_img = base64.b64encode(f.read()).decode("utf-8")
            base64_uri = f"data:image/png;base64,{base64_img}"
            res["base64_uri"] = [base64_uri]
            res["hidden_to_model"] = ["base64_uri"]

        res["success"] = True
        return res

    @tool(exclude=True)
    async def new_interpreter(self) -> dict:
        """Create a new Python interpreter and return its id.
        You can use `run_code_in_interpreter` to run code in the interpreter,
        by providing the interpreter id."""
        self._init_engine()

        async def interpreter():
            __res = None
            __stdout = io.StringIO()
            __stderr = io.StringIO()
            while True:
                code, var_name = yield __res, __stdout.getvalue(), __stderr.getvalue()
                __stdout.seek(0)
                __stdout.truncate(0)
                __stderr.seek(0)
                __stderr.truncate(0)
                try:
                    with redirect_stdout(__stdout), redirect_stderr(__stderr):
                        exec_with_echo(code, globals())
                except Exception:
                    traceback_str = traceback.format_exc()
                    __res = PythonInterpreterError(traceback_str)
                    continue
                if var_name is None:
                    __res = None
                else:
                    __res = globals().get(var_name)

        job = ProcessJob(interpreter)
        await self.engine.submit_async(job)
        await job.wait_until_status("running")
        self.jobs[job.id] = job
        g = job.result()
        await g.asend(None)  # Initialize the generator
        self.interpreters[job.id] = g
        effective_workdir = self._get_effective_workdir() or str(self.workdir) if self.workdir else None
        if effective_workdir is not None:
            # Use repr() to properly escape backslashes on Windows
            await self.run_code_in_interpreter(
                f"import os; os.chdir({repr(effective_workdir)})", job.id
            )
        if self.init_code is not None:
            await self.run_code_in_interpreter(self.init_code, job.id)
        return {"success": True, "interpreter_id": job.id}

    @tool(exclude=True)
    async def delete_interpreter(self, interpreter_id: str) -> dict:
        """Delete an interpreter.

        Args:
            interpreter_id: The id of the interpreter to delete.
        """
        if interpreter_id not in self.interpreters:
            return {
                "success": False,
                "error": f"Interpreter {interpreter_id} not found",
            }
        job = self.jobs[interpreter_id]
        await job.cancel()
        del self.interpreters[interpreter_id]
        del self.jobs[interpreter_id]
        self.engine.jobs.remove(job)
        self._bootstrapped.discard(interpreter_id)
        return {"success": True, "interpreter_id": interpreter_id}

    @tool(exclude=True)
    async def list_interpreters(self) -> dict:
        """List all interpreters."""
        interpreters = [
            {
                "id": interpreter_id,
                "status": job.status,
            }
            for interpreter_id, job in self.jobs.items()
        ]
        return {"success": True, "interpreters": interpreters}
    @tool
    async def manage_interpreters(
        self,
        operation: str,
        interpreter_id: str | None = None,
    ) -> dict:
        """Manage Python interpreters.

        Args:
            operation: The operation to perform. choose from "create", "list", "delete".
            interpreter_id: The id of the interpreter to delete. Required for "delete" operation.
        """
        if operation == "create":
            return await self.new_interpreter()
        elif operation == "list":
            return await self.list_interpreters()
        elif operation == "delete":
            if interpreter_id is None:
                return {
                    "success": False,
                    "error": "interpreter_id is required for delete operation",
                }
            return await self.delete_interpreter(interpreter_id)
        else:
            return {
                "success": False,
                "error": f"Unknown operation: {operation}",
            }


    async def run_setup(self):
        """Setup the toolset before running it."""
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )

