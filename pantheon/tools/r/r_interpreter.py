import uuid

from ._rinter import AsyncRInterpreter
from ...remote.toolset import ToolSet, tool
from ...utils.log import logger


class RInterpreterToolSet(ToolSet):
    def __init__(
            self,
            name: str,
            worker_params: dict | None = None,
            r_executable: str = "R",
            r_args: list[str] | None = None,
            ):
        super().__init__(name, worker_params)
        self.interpreters = {}
        self.clientid_to_interpreterid = {}
        self.r_executable = r_executable
        self.r_args = r_args

    @tool
    async def run_code(self, code: str, timeout: int = 100, __client_id__: str | None = None):
        """Run R code in a new interpreter and return the output.
        If you use this function, don't need to use `new_interpreter` and `delete_interpreter`.

        Args:
            code: The R code to run.
            timeout: The timeout for the code to run.
        """

        initial_output = ""
        if __client_id__ is not None:
            p_id = self.clientid_to_interpreterid.get(__client_id__)
            if (p_id is None) or (p_id not in self.interpreters):
                res = await self.new_interpreter()
                p_id = res["interpreter_id"]
                initial_output = res["initial_output"]
                self.clientid_to_interpreterid[__client_id__] = p_id
        else:
            res = await self.new_interpreter()
            p_id = res["interpreter_id"]
            initial_output = res["initial_output"]
        output = await self.run_code_in_interpreter(code, p_id, timeout=timeout)
        if __client_id__ is None:
            await self.delete_interpreter(p_id)
        if initial_output:
            output = initial_output + "\n" + output
        return output

    @tool
    async def new_interpreter(self) -> dict:
        """Create a new R interpreter and return its id and the initial output.
        You can use `run_code_in_interpreter` to run code in the interpreter,
        by providing the interpreter id. """
        interpreter = AsyncRInterpreter(
            self.r_executable,
            self.r_args,
        )
        interpreter.id = str(uuid.uuid4())
        self.interpreters[interpreter.id] = interpreter
        return {
            "interpreter_id": interpreter.id,
            "initial_output": await interpreter.start(),
        }

    @tool
    async def delete_interpreter(self, interpreter_id: str):
        """Delete an R interpreter.

        Args:
            interpreter_id: The id of the interpreter to delete.
        """
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is not None:
            await interpreter.close()
            del self.interpreters[interpreter_id]

    @tool
    async def run_code_in_interpreter(
            self,
            code: str,
            interpreter_id: str,
            timeout: int = 100,
            ) -> str:
        """Run R code in an interpreter and return the output.

        Args:
            code: The R code to run.
            interpreter_id: The id of the interpreter to run the code in.
            timeout: The timeout for the code to run.
        """
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is None:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        output, finished = await interpreter.run_command(code, timeout=timeout)
        if not finished:
            output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_interpreter_output to get the remaining output of the interpreter."
        return output

    @tool
    async def get_interpreter_output(self, interpreter_id: str, timeout: int = 10) -> str:
        """Get the output of an R interpreter. Don't use this function unless you need to get the remaining output of an interrupted command.

        Args:
            interpreter_id: The id of the interpreter to get the output from.
            timeout: The timeout for the output to be returned.
        """
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is None:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        output, finished = await interpreter.read_until_marker(timeout=timeout)
        if not finished:
            output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_interpreter_output to get the remaining output of the interpreter."
        return output

    async def run_setup(self):
        """Setup the toolset before running it."""
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )
