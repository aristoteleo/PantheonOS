import uuid

from ._rinter import AsyncRInterpreter
from ...remote import ToolSet, tool


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
    async def run_code(self, code: str, __client_id__: str | None = None):
        """Run R code in a new interpreter and return the output.
        If you use this function, don't need to use `new_interpreter` and `delete_interpreter`.

        Args:
            code: The R code to run.
            __client_id__: The client id of the client that is running the code.
        """
        if __client_id__ is not None:
            p_id = self.clientid_to_interpreterid.get(__client_id__)
            if p_id is None:
                p_id = await self.new_interpreter()
                self.clientid_to_interpreterid[__client_id__] = p_id
        else:
            p_id = await self.new_interpreter()
        output = await self.run_code_in_interpreter(code, p_id)
        if __client_id__ is None:
            await self.delete_interpreter(p_id)
        return output

    @tool
    async def new_interpreter(self) -> str:
        """Create a new R interpreter and return its id.
        You can use `run_code_in_interpreter` to run code in the interpreter,
        by providing the interpreter id. """
        interpreter = await AsyncRInterpreter.create(
            self.r_executable,
            self.r_args,
        )

        interpreter.id = str(uuid.uuid4())
        self.interpreters[interpreter.id] = interpreter
        return interpreter.id

    @tool
    async def delete_interpreter(self, interpreter_id: str):
        """Delete an R interpreter.
        You can't use this function if you are using `run_code` function.

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
            timeout: int = 20,
            ) -> dict:
        """Run R code in an interpreter and return the output.

        Args:
            code: The R code to run.
            interpreter_id: The id of the interpreter to run the code in.
            timeout: The timeout for the code to run.
        """
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is None:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        output = await interpreter.send_command(code, timeout=timeout)
        return output
