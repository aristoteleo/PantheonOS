import uuid
import os
import base64

from ._rinter import AsyncRInterpreter
from ...toolset import ToolSet, tool
from ...utils.log import logger


class RInterpreterToolSet(ToolSet):
    """R interpreter toolset.
    For running R code in an interpreter.

    Args:
        r_executable: The path to the R executable.
        r_args: The arguments to pass to the R executable.
        init_code: The code to run to initialize the R environment.
        workdir: The working directory to use for the R interpreter.
    """
    def __init__(
            self,
            name: str,
            worker_params: dict | None = None,
            r_executable: str = "R",
            r_args: list[str] | None = None,
            init_code: str | None = None,
            workdir: str | None = None,
            **kwargs,
            ):
        super().__init__(name, worker_params, **kwargs)
        self.interpreters = {}
        self.clientid_to_interpreterid = {}
        self.r_executable = r_executable
        self.r_args = r_args
        self.init_code = init_code
        self.workdir = workdir

    @tool
    async def run_r_code(
            self,
            code: str,
            timeout: int | None = None,
            context_variables: dict | None = None,
            ):
        """Run R code in a new interpreter and return the output with enhanced functionality.
        Automatically handles figures, provides sample data functions, and includes Seurat support.
        If you use this function, don't need to use `new_interpreter` and `delete_interpreter`.

        Args:
            code: The R code to run.
            timeout: The timeout for the code to run. Use None for no timeout (long-running commands).
        
        Returns:
            A dictionary with result, stdout, stderr, and optionally figure information.
        """
        # Reset figure path
        reset_code = "GLOBAL_FIG_PATH <- NULL"
        
        # Show R execution status
        logger.info("Starting R execution...")
        
        initial_output = ""

        if context_variables is None:
            context_variables = {}
        client_id = context_variables.get("client_id")
        if client_id is None:
            client_id = "default"
            logger.warning("No client_id provided, using default")
        p_id = self.clientid_to_interpreterid.get(client_id)
        if (p_id is None) or (p_id not in self.interpreters):
            res = await self.new_interpreter()
            p_id = res["interpreter_id"]
            initial_output = res["initial_output"]
            self.clientid_to_interpreterid[client_id] = p_id

        # Reset figure path and run the actual code
        try:
            await self.run_code_in_interpreter(reset_code, p_id, timeout=timeout)
            output = await self.run_code_in_interpreter(code, p_id, timeout=timeout)
        except Exception as e:
            # If there's an error due to process termination, the autorestart should handle it
            # But if it still fails, we should log it and re-raise
            logger.error(f"Error executing R code: {e}")
            raise
        
        # Check for generated figures
        fig_check_output = await self.run_code_in_interpreter("GLOBAL_FIG_PATH", p_id)
        
        # Format result similar to Python toolset
        full_output = output
        if initial_output:
            full_output = initial_output + "\n" + output
            
        result = {
            "result": None,  # R doesn't return specific variables like Python
            "stdout": full_output,
            "stderr": "",  # R stderr is usually mixed with stdout
            "code_executed": code  # Add the executed code for display
        }
            
        # Handle figure output
        fig_path_line = None
        for line in fig_check_output.split('\n'):
            if line.strip().startswith('[1]') and '.png' in line:
                fig_path_line = line.strip()[4:].strip('"').strip()
                break
                
        if fig_path_line and fig_path_line != "NULL" and os.path.exists(fig_path_line):
            result["fig_storage_path"] = fig_path_line
            logger.info(f"Figure saved: {fig_path_line}")
            try:
                with open(fig_path_line, "rb") as f:
                    base64_img = base64.b64encode(f.read()).decode("utf-8")
                base64_uri = f"data:image/png;base64,{base64_img}"
                result["base64_uri"] = [base64_uri]
                result["hidden_to_model"] = ["base64_uri"]
            except Exception as e:
                logger.warning(f"⚠️ Warning: Failed to read figure file: {e}")
                logger.warning(f"Failed to read figure file {fig_path_line}: {e}")
        
        # Show execution completion
        if "Error" in full_output or "error" in full_output.lower():
            logger.error("❌ R execution completed with errors")
        else:
            logger.info("R execution completed successfully")
        
        return result

    @tool
    async def new_interpreter(self) -> dict:
        """Create a new R interpreter and return its id and the initial output.
        You can use `run_code_in_interpreter` to run code in the interpreter,
        by providing the interpreter id. """
        # Show R interpreter creation status
        logger.info("Creating new R interpreter...")
        
        interpreter = AsyncRInterpreter(
            self.r_executable,
            self.r_args,
        )
        interpreter.id = str(uuid.uuid4())
        self.interpreters[interpreter.id] = interpreter
        initial_output = await interpreter.start()
        
        # Set working directory if specified
        if self.workdir is not None:
            await self.run_code_in_interpreter(f'setwd("{self.workdir}")', interpreter.id)
            
        # Run initialization code
        if self.init_code is not None:
            logger.info("Setting up R environment...")
            init_output, _ = await interpreter.run_command(self.init_code)
            initial_output += "\n" + init_output
            logger.info("✅ R environment ready")

        return {
            "interpreter_id": interpreter.id,
            "initial_output": initial_output,
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
            timeout: int | None = None,
            ) -> str:
        """Run R code in an interpreter and return the output.

        Args:
            code: The R code to run.
            interpreter_id: The id of the interpreter to run the code in.
            timeout: The timeout for the code to run. Use None for no timeout.
        """
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is None:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        
        # Check if process has terminated before running command
        was_terminated = interpreter.process and interpreter.process.returncode is not None
        
        # Run the command (restart will happen automatically if needed)
        output, finished = await interpreter.run_command(code, timeout=timeout)
        
        # Check if the interpreter was restarted and needs reinitialization
        if (was_terminated or getattr(interpreter, '_was_restarted', False)) and (self.workdir is not None or self.init_code is not None):
            logger.info("Detected interpreter restart, reinitializing...")
            interpreter._was_restarted = False  # Reset the flag
            await self._reinitialize_interpreter(interpreter_id)
            # Re-run the original command after reinitialization
            output, finished = await interpreter.run_command(code, timeout=timeout)
        
        if timeout is not None and not finished:
            output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_interpreter_output to get the remaining output of the interpreter."
        return output

    async def _reinitialize_interpreter(self, interpreter_id: str):
        """Reinitialize an interpreter after it has been restarted."""
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is None:
            return
            
        logger.info("Reinitializing R interpreter after restart...")
        
        # Set working directory if specified
        if self.workdir is not None:
            output, _ = await interpreter.run_command(f'setwd("{self.workdir}")')
            logger.info(f"Set workdir output: {output}")
            
        # Run initialization code
        if self.init_code is not None:
            output, _ = await interpreter.run_command(self.init_code)
            logger.info(f"Initialization code output: {output}")
            
        logger.info("R interpreter reinitialized")

    @tool
    async def get_interpreter_output(self, interpreter_id: str, timeout: int | None = None) -> str:
        """Get the output of an R interpreter. Don't use this function unless you need to get the remaining output of an interrupted command.

        Args:
            interpreter_id: The id of the interpreter to get the output from.
            timeout: The timeout for the output to be returned. Use None for no timeout.
        """
        interpreter = self.interpreters.get(interpreter_id)
        if interpreter is None:
            raise ValueError(f"Interpreter {interpreter_id} not found")
        output, finished = await interpreter.read_until_marker(timeout=timeout)
        if timeout is not None and not finished:
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
