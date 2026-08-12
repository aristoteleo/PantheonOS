"""Python execution, on a Jupyter kernel.

This used to drive its own interpreter: a generator running inside a loky
worker, fed code over a pipe, with stdout and stderr redirected around each
`exec`. That worked, but it meant owning a small execution engine — process
lifecycle, crash recovery, output capture, figure capture — and none of it was
Pantheon's problem to solve. A Jupyter kernel is the same thing, specified,
and the repo already had a toolset that drives one properly.

What the change buys, beyond less code to own:

  Other interpreters, for free. A kernel is chosen by kernelspec name, and
  jupyter resolves that to whatever Python the spec points at — so code can run
  in a conda env on the Volume instead of the agent's runtime venv, and a
  second env is just a second spec. The previous design could only be pointed
  at another interpreter by moving `sys.executable` at the moment loky forked
  its worker, which is as fragile as it sounds.

  Figures, natively. matplotlib's inline backend already publishes PNGs as
  display_data. The old code monkeypatched `plt.show` to write a file, stashed
  the path in a global, then fetched that global with a second round trip and
  read the file back. All of that is now one field of a message the kernel
  already sends.

`result_var_name` is gone. Getting a value out was a second execution against
a magic variable name; an agent that wants a value can print it, or leave the
expression on the last line, which is what `execute_result` is for.
"""

import base64
import os
import uuid
from pathlib import Path

from pantheon.toolset import tool, ToolSet
from pantheon.toolsets.notebook.jupyter_kernel import JupyterKernelToolSet
from pantheon.internal.package_runtime.context import build_context_env
from pantheon.utils.log import logger


class PythonInterpreterError(Exception):
    pass


# Only what the kernel does not already do. The inline backend publishes
# figures on its own, so there is no plt.show to intercept; nest_asyncio is
# still wanted because package APIs call asyncio.run() inside a running loop.
DEFAULT_INIT_CODE = """
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

try:
    get_ipython().run_line_magic('matplotlib', 'inline')
except Exception:
    pass
"""

# Where a figure is written for callers that want a path rather than bytes.
# The kernel hands over the PNG itself; this is only kept because the previous
# implementation returned it and things downstream read it.
FIG_DIR = ".matplotlib_figs"


class PythonInterpreterToolSet(ToolSet):
    """Run Python in a Jupyter kernel, one session per chat.

    Args:
        name: The name of the toolset.
        workdir: Working directory for the kernel.
        engine: Unused. Kept so existing constructor calls do not break.
        init_code: Code run once when a session is created.
        kernel_spec: Kernelspec to run in. Defaults to the analysis env when
            one is registered, otherwise the runtime's own kernel.
    """

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        engine=None,
        init_code: str | None = DEFAULT_INIT_CODE,
        shared_executor=None,
        kernel_spec: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.workdir = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        self.init_code = init_code
        self._kernel_spec_override = kernel_spec
        self.kernels = JupyterKernelToolSet(f"{name}_kernel", str(self.workdir), **kwargs)
        # Session per chat, not per connection: `client_id` is stable across
        # chats, so keying by it gave two simultaneous conversations one
        # namespace. Kept under the old attribute name because callers reach
        # into it.
        self.clientid_to_interpreterid: dict[str, str] = {}
        self._bootstrapped: set[str] = set()

    # ------------------------------------------------------------------ setup

    def _resolve_kernel_spec(self) -> str:
        """Which kernel to start.

        The analysis env registers a spec named after itself
        (pantheon-analysis-env does this on every boot). Preferring it is what
        keeps user packages out of the runtime venv. If it is not registered —
        the env is still building, or was never built — the runtime's own
        kernel is correct, not an error.
        """
        if self._kernel_spec_override:
            return self._kernel_spec_override
        wanted = os.environ.get("PANTHEON_ANALYSIS_ENV")
        if wanted:
            try:
                from jupyter_client.kernelspec import KernelSpecManager

                if wanted in KernelSpecManager().get_all_specs():
                    return wanted
            except Exception:  # noqa: BLE001 - a missing spec is not a failure
                pass
        return "python3"

    def _effective_workdir(self) -> str:
        return self._get_effective_workdir() or str(self.workdir)

    async def _inject_runtime_context(self, session_id: str):
        """Push the workspace's environment into the kernel.

        Once per session. The old implementation did it before every single
        execution, which cost a full round trip per call to re-send values that
        had not changed.
        """
        if session_id in self._bootstrapped:
            return
        env = build_context_env(
            workdir=self._effective_workdir(),
            context_variables=dict(self.get_context() or {}),
            base_env=os.environ.copy(),
            optimize=True,
        )
        if env:
            import json

            await self.kernels.execute_request(
                f"import os; os.environ.update({json.dumps(env)})",
                session_id,
                silent=True,
                store_history=False,
            )
        if self.init_code:
            await self.kernels.execute_request(
                self.init_code, session_id, silent=True, store_history=False
            )
        self._bootstrapped.add(session_id)

    # ------------------------------------------------------------- conversion

    def _to_result(self, kernel_reply: dict) -> dict:
        """Turn a kernel's outputs into what this tool has always returned.

        nbformat output types map onto the old shape directly: `stream` is
        stdout/stderr, `error` is a traceback on stderr, `execute_result` is
        the value of a trailing expression, and image data becomes the same
        base64 field the UI already renders.
        """
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        result = None
        images: list[str] = []

        for out in kernel_reply.get("outputs") or []:
            kind = out.get("output_type")
            if kind == "stream":
                (stderr_parts if out.get("name") == "stderr" else stdout_parts).append(
                    out.get("text", "")
                )
            elif kind == "error":
                stderr_parts.append("\n".join(out.get("traceback") or [out.get("evalue", "")]))
            elif kind in ("execute_result", "display_data"):
                data = out.get("data") or {}
                png = data.get("image/png")
                if png:
                    images.append(png)
                elif kind == "execute_result":
                    result = data.get("text/plain")

        # A traceback in the outputs means the code failed, whatever the
        # transport thought: the kernel delivered the message successfully, but
        # the cell did not run. The previous implementation raised here; a
        # returned failure is better for a tool an agent calls, because the
        # agent sees the traceback instead of a tool error.
        failed = any(o.get("output_type") == "error" for o in kernel_reply.get("outputs") or [])

        res: dict = {
            "success": bool(kernel_reply.get("success", False)) and not failed,
            "result": result,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
        }
        if kernel_reply.get("error"):
            res["stderr"] = (res["stderr"] + "\n" + str(kernel_reply["error"])).strip()

        if images:
            # `hidden_to_model` because an image is for the person, not for the
            # model's context window — the same contract as before.
            res["base64_uri"] = [f"data:image/png;base64,{img}" for img in images]
            res["hidden_to_model"] = ["base64_uri"]
            path = self._store_figure(images[-1])
            if path:
                res["fig_storage_path"] = path
        return res

    def _store_figure(self, png_base64: str) -> str | None:
        """Write a figure next to the work, for callers that want a path."""
        try:
            rel_dir = Path(self._effective_workdir()) / FIG_DIR
            rel_dir.mkdir(parents=True, exist_ok=True)
            name = f"{uuid.uuid4()}.png"
            (rel_dir / name).write_bytes(base64.b64decode(png_base64))
            return os.path.join(FIG_DIR, name)
        except Exception as e:  # noqa: BLE001 - a figure on disk is a nicety
            logger.debug(f"could not store figure: {e}")
            return None

    # ------------------------------------------------------------------ tools

    async def _session_for_current_chat(self) -> str:
        context = dict(self.get_context() or {})
        key = context.get("chat_id") or context.get("client_id") or "default"
        session_id = self.clientid_to_interpreterid.get(key)
        if session_id and session_id in self.kernels.sessions:
            return session_id
        created = await self.new_interpreter()
        if not created.get("success"):
            raise PythonInterpreterError(created.get("error", "could not start a kernel"))
        session_id = created["interpreter_id"]
        self.clientid_to_interpreterid[key] = session_id
        return session_id

    @tool
    async def run_python_code(self, code: str, interpreter_id: str | None = None):
        """Run Python code and return the result.

        This tool automatically manages a Python session for you. Variables and
        state are preserved between calls in the same session.

        To get a value back, print it or leave it as the last expression.

        Args:
            code: The Python code to run.
            interpreter_id: Optional. A specific session to run in. Only set
                this if you are deliberately keeping several independent
                sessions; otherwise the session for the current chat is used.

        Returns:
            dict: {"success": bool, "result": str | None, "stdout": str, "stderr": str}
        """
        session_id = interpreter_id or await self._session_for_current_chat()
        return await self.run_code_in_interpreter(code, session_id)

    @tool(exclude=True)
    async def run_code_in_interpreter(self, code: str, interpreter_id: str) -> dict:
        """Run code in a specific session.

        Args:
            code: The code to run.
            interpreter_id: The session to run it in.
        """
        await self._inject_runtime_context(interpreter_id)
        reply = await self.kernels.execute_request(code, interpreter_id)

        # A dead kernel is reported rather than raised, and the mapping is
        # cleared so the next call starts a fresh one. The previous
        # implementation tried to restart in place and referred to a variable
        # that no longer existed, so the recovery path raised NameError and the
        # session stayed broken for good.
        if not reply.get("success") and "not found" in str(reply.get("error", "")).lower():
            self._forget(interpreter_id)

        return self._to_result(reply)

    def _forget(self, session_id: str):
        self._bootstrapped.discard(session_id)
        for key, value in list(self.clientid_to_interpreterid.items()):
            if value == session_id:
                del self.clientid_to_interpreterid[key]

    @tool(exclude=True)
    async def new_interpreter(self) -> dict:
        """Start a new Python session and return its id."""
        spec = self._resolve_kernel_spec()
        created = await self.kernels.create_session(
            kernel_spec=spec, cwd=self._effective_workdir()
        )
        if not created.get("success"):
            return {"success": False, "error": created.get("error")}
        session_id = created.get("session_id") or created.get("kernel_session_id")
        logger.info(f"Python session {session_id} started on kernelspec '{spec}'")
        return {"success": True, "interpreter_id": session_id, "kernel_spec": spec}

    @tool(exclude=True)
    async def delete_interpreter(self, interpreter_id: str) -> dict:
        """Delete a session.

        Args:
            interpreter_id: The id of the session to delete.
        """
        result = await self.kernels.shutdown_session(interpreter_id)
        self._forget(interpreter_id)
        if not result.get("success"):
            return {"success": False, "error": result.get("error")}
        return {"success": True, "interpreter_id": interpreter_id}

    @tool(exclude=True)
    async def list_interpreters(self) -> dict:
        """List all sessions."""
        listed = await self.kernels.list_sessions()
        sessions = listed.get("sessions") or []
        return {
            "success": True,
            "interpreters": [
                {
                    "id": s.get("session_id") or s.get("kernel_session_id"),
                    "status": s.get("status", "running"),
                }
                for s in sessions
            ],
        }

    @tool
    async def manage_interpreters(
        self,
        operation: str,
        interpreter_id: str | None = None,
    ) -> dict:
        """Manage Python sessions.

        Args:
            operation: One of "create", "list", "delete".
            interpreter_id: The session to delete. Required for "delete".
        """
        if operation == "create":
            return await self.new_interpreter()
        if operation == "list":
            return await self.list_interpreters()
        if operation == "delete":
            if interpreter_id is None:
                return {
                    "success": False,
                    "error": "interpreter_id is required for delete operation",
                }
            return await self.delete_interpreter(interpreter_id)
        return {"success": False, "error": f"Unknown operation: {operation}"}

    async def run_setup(self):
        """Setup the toolset before running it."""
        await self.kernels.run_setup()
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )
