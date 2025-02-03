import io
import traceback
from executor.engine import Engine, ProcessJob
from contextlib import redirect_stdout, redirect_stderr

from ...remote import tool, ToolSet
from ...utils.log import logger


class PythonInterpreterError(Exception):
    pass


class PythonInterpreterToolSet(ToolSet):
    def __init__(
            self,
            name: str,
            worker_params: dict | None = None,
            engine: Engine | None = None,
            ):
        super().__init__(name, worker_params)
        self.interpreters = {}
        self.jobs = {}
        self._engine = engine
        self.engine = None
        self.clientid_to_interpreterid = {}

    def _init_engine(self):
        if self.engine is None:
            if self._engine is None:
                self.engine = Engine()
            else:
                self.engine = self._engine

    @tool
    async def run_code(
        self,
        code: str,
        result_var_name: str | None = None,
        __client_id__: str | None = None,
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
        if __client_id__ is not None:
            p_id = self.clientid_to_interpreterid.get(__client_id__)
            if (p_id is None) or (p_id not in self.interpreters):
                p_id = await self.new_interpreter()
                self.clientid_to_interpreterid[__client_id__] = p_id
        else:
            p_id = await self.new_interpreter()
        res = await self.run_code_in_interpreter(code, p_id, result_var_name)
        if __client_id__ is None:
            await self.delete_interpreter(p_id)
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
                __stderr.seek(0)
                try:
                    with redirect_stdout(__stdout), redirect_stderr(__stderr):
                        exec(code, globals())
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
        if interpreter_id not in self.interpreters:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        g = self.interpreters[interpreter_id]
        result, stdout, stderr = await g.asend((code, result_var_name))
        if isinstance(result, PythonInterpreterError):
            raise result
        return {
            "result": result,
            "stdout": stdout,
            "stderr": stderr,
        }

    async def run_setup(self):
        """Setup the toolset before running it."""
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )

