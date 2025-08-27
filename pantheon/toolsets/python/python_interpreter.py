import os
import io
import ast
import base64
import traceback
from contextlib import redirect_stdout, redirect_stderr

from executor.engine import Engine, ProcessJob

from ...toolset import tool, ToolSet
from ...utils.log import logger


class PythonInterpreterError(Exception):
    pass


def exec_with_echo(code, env=None):
    """Execute code with echo."""
    if env is None:
        env = {}
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Expr):
            expr_code = compile(ast.Expression(node.value), '<string>', 'eval')
            result = eval(expr_code, env)
            print(result)
        else:
            exec(compile(ast.Module([node], []), '<string>', 'exec'), env)


DEFAULT_INIT_CODE = """
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

    Features:
        - Automatic code validation before execution
        - Detection of common coding errors (e.g., adata=adata patterns)
        - Function existence checking with intelligent suggestions
        - Syntax and import validation
        - Support for matplotlib figure auto-saving

    Args:
        name: The name of the toolset.
        workdir: The working directory for the interpreter.
        engine: The engine to use for the interpreter.
        init_code: The code to run before the interpreter starts.
        worker_params: The parameters for the worker.
        **kwargs: Additional keyword arguments.
    """

    def __init__(
            self,
            name: str,
            workdir: str | None = None,
            engine: Engine | None = None,
            init_code: str | None = DEFAULT_INIT_CODE,
            worker_params: dict | None = None,
            **kwargs,
            ):
        super().__init__(name, worker_params, **kwargs)
        self.interpreters = {}
        self.jobs = {}
        self._engine = engine
        self.engine = None
        self.clientid_to_interpreterid = {}
        self.workdir = workdir
        self.init_code = init_code

    def _init_engine(self):
        if self.engine is None:
            if self._engine is None:
                self.engine = Engine()
            else:
                self.engine = self._engine

    @tool
    async def run_python_code(
        self,
        code: str,
        result_var_name: str | None = None,
        context_variables: dict | None = None,
    ):
        """Run Python code in a new interpreter and return the result.
        If you use this function, don't need to use `new_interpreter` and `delete_interpreter`.

        Args:
            code: The Python code to run.
            result_var_name: The name of the variable you want to get the result from.
                If not needed, set to None. Default is None.

        Returns:
            A dictionary with the result, stdout, and stderr.
        """
        #context_variables: The context variables to use.
        #    "client_id" is used to identify the interpreter.
        #    If not provided will use the default client id.
        #    For each client id, a new interpreter will be created.
        #    NOTE: This context_variables is not visible to the agent.
        if context_variables is None:
            context_variables = {}
        client_id = context_variables.get("client_id")
        if client_id is None:
            client_id = "default"
            logger.warning("No client id provided, using default client id.")
        p_id = self.clientid_to_interpreterid.get(client_id)
        if (p_id is None) or (p_id not in self.interpreters):
            p_id = await self.new_interpreter()
            self.clientid_to_interpreterid[client_id] = p_id

        # Execute code with automatic recovery on process failure
        try:
            res = await self.run_code_in_interpreter(code, p_id, result_var_name)
        except Exception as e:
            # Handle BrokenProcessPool and other process failures
            error_str = str(e)
            error_type = type(e).__name__
            
            # Check for various process failure indicators
            is_process_failure = (
                "BrokenProcessPool" in error_str or 
                "ProcessPool" in error_str or
                "loky.process_executor.BrokenProcessPool" in error_str or
                "TerminatedWorkerError" in error_type or
                "TerminatedWorkerError" in error_str or
                "SIGSEGV" in error_str or
                "segmentation fault" in error_str.lower() or
                "worker" in error_str.lower() and "terminated" in error_str.lower() or
                "un-serialize" in error_str or
                "picklable" in error_str or
                "KeyboardInterrupt" in error_str or
                error_type == "BrokenProcessPool" or
                p_id not in self.interpreters
            )
            
            if is_process_failure:
                logger.warning(f"Python interpreter crashed (client_id: {client_id}), restarting...")
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
                p_id = await self.new_interpreter()
                self.clientid_to_interpreterid[client_id] = p_id
                logger.info(f"Python interpreter restarted (client_id: {client_id})")
                
                try:
                    res = await self.run_code_in_interpreter(code, p_id, result_var_name)
                    logger.info("Code execution successful after interpreter restart")
                    # Add a note to the result that interpreter was restarted
                    res["interpreter_restarted"] = True
                    res["restart_reason"] = f"{error_type}: {error_str[:100]}..."
                except Exception as retry_error:
                    logger.error(f"Code execution failed even after interpreter restart: {retry_error}")
                    # Return a more user-friendly error message
                    return {
                        "result": None,
                        "stdout": "",
                        "stderr": f"Python interpreter crashed and restart failed.\nOriginal error: {error_type}\nRetry error: {str(retry_error)[:200]}...\n\nTry using /restart command to fully reset the Python environment.",
                        "code_executed": code,
                        "interpreter_crashed": True
                    }
            else:
                # Re-raise non-process-related exceptions
                raise e

        # Add executed code for display
        res["code_executed"] = code
        return res

    async def __run_code_in_interpreter(
            self,
            code: str,
            interpreter_id: str,
            result_var_name: str | None = None,
            ) -> dict:
        if interpreter_id not in self.interpreters:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        #logger.info(f"[DEBUG] Running code in interpreter {interpreter_id}")
        g = self.interpreters[interpreter_id]
        result, stdout, stderr = await g.asend((code, result_var_name))
        if isinstance(result, PythonInterpreterError):
            raise result
        return {
            "result": result,
            "stdout": stdout,
            "stderr": stderr,
        }

    @tool
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
        code = "GLOBAL_FIG_PATH = None\n" + code
        res = await self.__run_code_in_interpreter(
            code, interpreter_id, result_var_name)
        res2 = await self.__run_code_in_interpreter(
            "None", interpreter_id, "GLOBAL_FIG_PATH")
        fig_path = res2["result"]
        if fig_path is not None:
            res["fig_storage_path"] = fig_path
            open_path = fig_path
            if self.workdir:
                open_path = os.path.join(self.workdir, fig_path)
            with open(open_path, "rb") as f:
                base64_img = base64.b64encode(f.read()).decode("utf-8")
            base64_uri = f"data:image/png;base64,{base64_img}"
            res["base64_uri"] = [base64_uri]
            res["hidden_to_model"] = ["base64_uri"]
        
        # Add executed code for display consistency 
        res["code_executed"] = code
        return res

    @tool
    async def new_interpreter(self) -> str:
        """Create a new Python interpreter and return its id.
        You can use `run_code_in_interpreter` to run code in the interpreter,
        by providing the interpreter id. """
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
        if self.workdir is not None:
            await self.run_code_in_interpreter(f"import os; os.chdir('{self.workdir}')", job.id)
        if self.init_code is not None:
            await self.run_code_in_interpreter(self.init_code, job.id)
        return job.id

    @tool
    async def delete_interpreter(self, interpreter_id: str):
        """Delete an interpreter.

        Args:
            interpreter_id: The id of the interpreter to delete.
        """
        if interpreter_id not in self.interpreters:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        job = self.jobs[interpreter_id]
        await job.cancel()
        del self.interpreters[interpreter_id]
        del self.jobs[interpreter_id]
        self.engine.jobs.remove(job)

    async def run_setup(self):
        """Setup the toolset before running it."""
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )
