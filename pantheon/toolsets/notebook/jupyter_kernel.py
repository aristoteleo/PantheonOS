"""Jupyter Client Kernel ToolSet - Standard Jupyter kernel implementation using jupyter_client"""

import asyncio
import base64
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import textwrap

import nbformat.v4

from jupyter_client import AsyncKernelManager
from jupyter_client.asynchronous import AsyncKernelClient

from pantheon.remote.backend.base import RemoteBackend, StreamMessage, StreamType
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger


# Terminal control character processing (nbclient-style)
# Reference: https://github.com/jupyter/nbclient/blob/main/nbclient/client.py

# Regex patterns for terminal control characters
_RGX_CARRIAGERETURN = re.compile(r".*\r(?=[^\n])")
_RGX_BACKSPACE = re.compile(r"[^\n]\b")


class KernelStatus(Enum):
    """Kernel execution status"""

    IDLE = "idle"
    BUSY = "busy"
    STARTING = "starting"
    DEAD = "dead"


@dataclass
class JupyterMessage:
    """Standard Jupyter message structure"""

    msg_type: str
    content: dict
    header: dict
    metadata: Optional[dict] = None
    parent_header: Optional[dict] = None
    buffers: Optional[List[bytes]] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.parent_header is None:
            self.parent_header = {}
        if self.buffers is None:
            self.buffers = []

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionInfo:
    """Kernel session information"""

    session_id: str
    kernel_spec: str
    status: KernelStatus
    created_at: str
    execution_count: int = 0



# KernelListener class removed - handlers are now called directly in execute_interactive's output_hook


def make_json_serializable(obj):
    """
    Convert datetime and other non-serializable objects to JSON-serializable format.
    Optimized for Jupyter message processing - handles datetime objects in headers.
    """
    # Fast path for already serializable basic types
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Handle container types recursively
    elif isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]

    # Handle datetime objects (main use case in Jupyter messages)
    elif isinstance(obj, datetime):
        return obj.isoformat()

    # Handle other common non-serializable types
    elif hasattr(obj, "isoformat"):  # date, time objects
        return obj.isoformat()
    elif hasattr(obj, "tolist"):  # numpy arrays
        return obj.tolist()
    elif hasattr(obj, "__dict__") and hasattr(obj, "__class__"):
        # Only convert custom objects, not built-in types
        if obj.__class__.__module__ not in ("builtins", "__builtin__"):
            return make_json_serializable(obj.__dict__)

    # Fallback: convert to string for any remaining non-serializable objects
    return str(obj)


class JupyterKernelToolSet(ToolSet):
    """Standard Jupyter kernel implementation using jupyter_client"""

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        execution_timeout: int | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.workdir = workdir or os.getcwd()

        # Store user override - will be resolved dynamically when needed
        self._execution_timeout_override = execution_timeout

        # Kernel management
        self.kernel_managers: Dict[str, AsyncKernelManager] = {}
        self.clients: Dict[str, AsyncKernelClient] = {}
        self.sessions: Dict[str, SessionInfo] = {}

        # Execution locks per session to prevent concurrent execution
        self._execution_locks: Dict[str, asyncio.Lock] = {}

        # IOPub message handlers registry: handler_id -> async callable
        self._iopub_handlers: Dict[str, Callable] = {}

    @property
    def execution_timeout(self) -> int:
        """Get execution timeout with priority: user override > settings > default."""
        if self._execution_timeout_override is not None:
            return self._execution_timeout_override
        try:
            from pantheon.settings import get_settings
            return get_settings().tool_timeout
        except Exception:
            return 3600

    def _current_context_dict(self) -> dict:
        ctx = self.get_context()
        return dict(ctx) if ctx else {}

    def _build_kernel_env(self) -> dict:
        import sys

        env = os.environ.copy()
        env.pop("LS_COLORS", None)

        # Get paths from current sys.path and existing PYTHONPATH
        # Prepend sys.path to give it priority for the kernel subprocess
        current_paths = [p for p in sys.path if p]
        existing_pythonpath = env.get("PYTHONPATH", "")
        existing_paths = [p for p in existing_pythonpath.split(os.pathsep) if p]

        # Combine and deduplicate while preserving order
        all_paths = current_paths + existing_paths
        unique_paths = list(dict.fromkeys(all_paths))

        env["PYTHONPATH"] = os.pathsep.join(unique_paths)

        # Do NOT include PANTHEON_CONTEXT in the spawn environment.
        # Large context causes E2BIG on execve(). Context is injected after
        # kernel start via _context_prefix_code().
        env.pop("PANTHEON_CONTEXT", None)

        return env

    def _context_prefix_code(self) -> str:
        from pantheon.internal.package_runtime.context import build_context_payload, export_context
        payload = build_context_payload(
            workdir=self._get_effective_workdir() or self.workdir,
            context_variables=self._current_context_dict(),
        )
        tmp: dict = {}
        export_context(payload, env=tmp)
        serialized = tmp.get("PANTHEON_CONTEXT")
        if not serialized:
            return ""
        encoded = base64.b64encode(serialized.encode("utf-8")).decode("ascii")
        return textwrap.dedent(
            f"""
            import base64, os
            os.environ['PANTHEON_CONTEXT'] = base64.b64decode('{encoded}').decode('utf-8')
            """
        ).strip()

    def _get_execution_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create execution lock for a kernel session (thread-safe)"""
        return self._execution_locks.setdefault(session_id, asyncio.Lock())



    def _extract_timing(self, iopub_messages: list, shell_reply: dict) -> dict:
        """Extract execution timing from IOPub messages and shell reply."""
        timing = {}
        for msg in iopub_messages:
            if not isinstance(msg, dict):
                continue
            header = msg.get("header", {})
            content = msg.get("content", {})
            if not isinstance(header, dict):
                continue
            msg_type = header.get("msg_type")
            ts = header.get("date")
            if msg_type == "status" and isinstance(content, dict):
                state = content.get("execution_state")
                if state == "busy" and ts:
                    timing["iopub.status.busy"] = ts
                elif state == "idle" and ts:
                    timing["iopub.status.idle"] = ts
            elif msg_type == "execute_input" and ts:
                timing["iopub.execute_input"] = ts
        # Shell reply timing
        if isinstance(shell_reply, dict):
            header = shell_reply.get("header", {})
            if isinstance(header, dict) and header.get("date"):
                timing["shell.execute_reply"] = header["date"]
        return timing

    async def subscribe(self, handler_id: str, handler: Callable) -> str:
        """
        Register an IOPub message handler.
        
        If this is the first handler being registered and kernels already exist,
        automatically sets up IOPub monitoring for those kernels.
        
        Args:
            handler_id: Unique identifier for this handler
            handler: Async callable with signature (session_id, message, metadata) -> None
        
        Returns:
            The handler_id for later unsubscription
        """
        is_first_handler = len(self._iopub_handlers) == 0
        self._iopub_handlers[handler_id] = handler
        logger.info(f"Subscribed IOPub handler: {handler_id}")
        
        # If this is the first handler and kernels already exist, set up monitoring
        if is_first_handler and self.sessions:
            logger.info(f"First handler registered, setting up IOPub monitoring for {len(self.sessions)} existing kernel(s)")
            for session_id in list(self.sessions.keys()):
                km = self.kernel_managers.get(session_id)
                kc = self.clients.get(session_id)
                if km and kc:
                    await self._setup_iopub_monitoring(session_id, km, kc)
        
        return handler_id
    
    def unsubscribe(self, handler_id: str) -> bool:
        """
        Unregister an IOPub message handler.
        
        Args:
            handler_id: Handler identifier to remove
        
        Returns:
            True if handler was found and removed, False otherwise
        """
        removed = self._iopub_handlers.pop(handler_id, None) is not None
        if removed:
            logger.info(f"Unsubscribed IOPub handler: {handler_id}")
        return removed
    async def _safe_exec_handler(self, handler_id: str, handler: Callable, *args):
        """Execute IOPub handler safely catching exceptions"""
        try:
            await handler(*args)
        except Exception as e:
            logger.warning(f"IOPub handler '{handler_id}' failed: {e}")

    # _handle_iopub_message, _cleanup_metadata_delayed, and _notify_kernel_death removed
    # These were part of the old unified_listener architecture
    # Now handlers are called directly in execute_request's output_hook



    def _kernel_env_context(self) -> dict:
        """Return a diagnostic snapshot of the kernel environment.
        Included in every create_session error so the agent can self-heal."""
        import sys, shutil

        workdir = self._get_effective_workdir() or self.workdir

        # Enumerate all registered kernelspecs with their Python path
        kernelspecs: dict = {}
        try:
            from jupyter_client.kernelspec import KernelSpecManager
            for name, info in KernelSpecManager().get_all_specs().items():
                argv = info.get("spec", {}).get("argv", [])
                python = argv[0] if argv else "unknown"
                if python and "{sys.executable}" in python:
                    python = python.replace("{sys.executable}", sys.executable)
                kernelspecs[name] = python
        except Exception:
            pass

        needs_fix = not os.path.isabs(kernelspecs.get("python3", ""))
        return {
            "workdir": workdir,
            "available_kernelspecs": kernelspecs,
            "uv_available": shutil.which("uv") is not None,
            "sys_python": sys.executable,
            "is_frozen_app": getattr(sys, "frozen", False),
            "kernelspec_needs_fix": needs_fix,
            # Self-healing guide for agents:
            # 1. Use `uv` (preferred, uv_available tells you if it exists) or pip to install
            #    ipykernel into the Python for the project workdir.
            # 2. Register with an ABSOLUTE path: `<abs_python> -m ipykernel install --user`
            #    (kernelspec_needs_fix=True means the current one uses a relative 'python',
            #     which breaks when the app is launched from Finder — launchd PATH differs
            #     from the user's shell PATH).
            # 3. Call create_session again — no restart needed.
            "hint": (
                "Install ipykernel into the project Python, then re-register the kernelspec "
                "using the ABSOLUTE Python path (`<abs_python> -m ipykernel install --user`). "
                "kernelspec_needs_fix=True means the current kernelspec uses a relative path "
                "which is unreliable when the app is GUI-launched. "
                "After fixing, call create_session again (no restart needed)."
            ),
        }

    def _kernel_python(self, kernel_spec_name: str = "python3") -> str:
        """Return the Python interpreter path used by the given kernelspec."""
        ctx = self._kernel_env_context()
        python = ctx["available_kernelspecs"].get(kernel_spec_name, "")
        if python and os.path.isfile(python):
            return python
        workdir = ctx["workdir"]
        for c in [
            os.path.join(workdir, ".venv", "bin", "python"),
            os.path.join(workdir, ".venv", "bin", "python3"),
            os.path.join(workdir, ".venv", "Scripts", "python.exe"),
        ]:
            if os.path.isfile(c):
                return c
        return ctx["sys_python"]

    @tool
    async def create_session(
        self, kernel_spec: str = "python3", kernel_session_id: str = None
    ) -> dict:
        """Create new kernel session"""
        try:
            if kernel_session_id is None:
                kernel_session_id = str(uuid.uuid4())

            # Try to create kernel manager with the specified kernel, fallback to default
            try:
                km = AsyncKernelManager(kernel_name=kernel_spec)
            except Exception as e:
                logger.warning(
                    f"Kernel '{kernel_spec}' not available, using default: {e}"
                )
                # Use default kernel (no kernel_name specified)
                km = AsyncKernelManager()

            # Start kernel in specified working directory with Pantheon context
            env = self._build_kernel_env()

            # Diagnostic logging for environment size
            total_env_size = sum(len(str(k)) + len(str(v)) + 1 for k, v in env.items())
            logger.debug(f"jupyter_kernel:create_session - Total environment size: {total_env_size} bytes")

            # Note: PANTHEON_CONTEXT is not in spawn env (see _build_kernel_env).
            if total_env_size > 100000:  # Warn if > 100KB
                logger.warning(f"Environment size {total_env_size} bytes is large after optimization.")

                # Identify largest environment variables
                sorted_env = sorted(env.items(), key=lambda x: len(str(x[1])), reverse=True)
                for k, v in sorted_env[:3]:
                    logger.warning(f"Large Env Var: {k} (Size: {len(str(v))} bytes)")

            await km.start_kernel(cwd=self._get_effective_workdir() or self.workdir, env=env)

            # Wait for kernel to be ready
            kc = km.client()
            # start_channels() might not be async in some versions
            try:
                result = kc.start_channels()
                if result is not None:
                    await result
            except TypeError:
                # start_channels() is not async, call it directly
                kc.start_channels()

            # Wait for kernel to be ready
            try:
                await kc.wait_for_ready(timeout=30)
            except RuntimeError as e:
                await km.shutdown_kernel()
                import json
                ctx = self._kernel_env_context()
                return {"success": False, "error": f"Kernel failed to start: {e}\n\nKernel environment:\n{json.dumps(ctx, indent=2)}"}

            # Store references
            self.kernel_managers[kernel_session_id] = km
            self.clients[kernel_session_id] = kc

            # Create session info
            session_info = SessionInfo(
                session_id=kernel_session_id,
                kernel_spec=kernel_spec,
                status=KernelStatus.IDLE,
                created_at=datetime.now(timezone.utc).isoformat(),
                execution_count=0,
            )
            self.sessions[kernel_session_id] = session_info

            # IOPub monitoring removed - handlers are called directly in execute_request's output_hook

            # Execute context prefix code once during kernel initialization
            # This sets up PANTHEON_CONTEXT environment variable in the kernel
            # Moved here from execute_request to avoid conflicts with magic commands (%%R, etc.)
            if context_prefix := self._context_prefix_code():
                await self.execute_request(
                    context_prefix,
                    kernel_session_id,
                    silent=True,
                    store_history=False,
                    execution_metadata={"operated_by": "system"},
                )

            logger.info(f"Created Jupyter kernel session: {kernel_session_id}")

            return {
                "success": True,
                "session_id": kernel_session_id,
                "kernel_spec": kernel_spec,
                "status": session_info.status.value,
                "created_at": session_info.created_at,
                "streaming_enabled": bool(self._iopub_handlers),
            }

        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            import json
            ctx = self._kernel_env_context()
            return {"success": False, "error": f"{e}\n\nKernel environment:\n{json.dumps(ctx, indent=2)}"}

    @tool
    async def execute_request(
        self,
        code: str,
        session_id: str,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: Optional[dict] = None,
        allow_stdin: bool = True,
        stop_on_error: bool = True,
        execution_metadata: Optional[dict] = None,
    ) -> dict:
        """Execute code in kernel session using official execute_interactive API
        
        This method uses Jupyter Client's execute_interactive API which handles:
        - IOPub message collection with proper parent_msg_id filtering
        - Automatic idle detection and completion
        - Real-time output streaming via output_hook
        
        Handlers (NATS, File Log, etc.) are called directly in output_hook for real-time streaming.
        """
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        # Check execution lock - prevent concurrent execution on same kernel
        lock = self._get_execution_lock(session_id)
        if lock.locked():
            return {
                "success": False,
                "error": "Kernel is busy executing another cell. Wait for completion or use manage_kernel(action='interrupt')."
            }

        async with lock:
            client = self.clients[session_id]
            session_info = self.sessions[session_id]

            # Collect IOPub messages via output_hook
            iopub_messages = []
            
            def output_hook(msg: dict):
                """Collect IOPub messages and call handlers in real-time
                
                This hook is called for every IOPub message during execution.
                Handlers are invoked directly for real-time streaming.
                """
                # 1. Collect message for generating outputs
                iopub_messages.append(msg)
                
                # 2. Call all registered handlers directly (NATS, File Log, etc.)
                if self._iopub_handlers:
                    # Convert to JupyterMessage format
                    jupyter_msg = JupyterMessage(
                        msg_type=msg.get("header", {}).get("msg_type", "unknown"),
                        content=msg.get("content", {}),
                        header=msg.get("header", {}),
                        parent_header=msg.get("parent_header", {}),
                        metadata=msg.get("metadata", {}),
                        buffers=msg.get("buffers", []),
                    )
                    
                    # Dispatch to all handlers (Fire-and-Forget)
                    for handler_id, handler in self._iopub_handlers.items():
                        asyncio.create_task(
                            self._safe_exec_handler(
                                handler_id, handler, session_id, jupyter_msg, execution_metadata or {}
                            )
                        )

            try:
                # Update kernel status
                session_info.status = KernelStatus.BUSY

                # Use official execute_interactive API
                reply = await client.execute_interactive(
                    code,
                    silent=silent,
                    store_history=store_history,
                    user_expressions=user_expressions or {},
                    allow_stdin=allow_stdin,
                    stop_on_error=stop_on_error,
                    timeout=self.execution_timeout,
                    output_hook=output_hook,
                )

                # Update execution count
                if store_history and reply["content"].get("status") == "ok":
                    execution_count = reply["content"].get("execution_count")
                    if execution_count:
                        session_info.execution_count = execution_count

                # Update kernel status
                session_info.status = KernelStatus.IDLE

                # Clean the reply to make it JSON serializable
                clean_reply = make_json_serializable(reply)
                
                # Clean IOPub messages to handle datetime objects in headers
                clean_iopub_messages = make_json_serializable(iopub_messages)
                
                # Extract timing information
                execution_timing = self._extract_timing(clean_iopub_messages, clean_reply)

                # Generate frontend-compatible outputs from IOPub messages
                execution_count = None
                if isinstance(clean_reply, dict):
                    content = clean_reply.get("content", {})
                    if isinstance(content, dict):
                        exec_count = content.get("execution_count")
                        if isinstance(exec_count, int):
                            execution_count = exec_count

                outputs = self._generate_outputs_from_iopub(clean_iopub_messages, execution_count)

                # Return standard nbformat-compatible data
                return {
                    "success": True,
                    "outputs": outputs,
                    "execution_count": execution_count,
                    "metadata": {
                        "execution": execution_timing
                    },
                    "error": None,
                }

            except TimeoutError as e:
                session_info.status = KernelStatus.IDLE
                error_msg = f"""Kernel execution timeout after {self.execution_timeout}s.

## Immediate Actions:
1. `manage_kernel(action="interrupt")` - stop current cell
2. `manage_kernel(action="restart")` - reset kernel (clears memory)
3. `manage_kernel(action="status")` - check kernel health

## Prevention Tips:
1. Split long operations into multiple cells (load → preprocess → compute)
2. Use parallel computing
3. Subsample for testing
4. Save checkpoints
5. Tune algorithm parameters
"""
                logger.warning(f"Execute request timeout for session {session_id}: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "outputs": [],
                    "execution_count": None,
                }
            except Exception as e:
                session_info.status = KernelStatus.IDLE
                error_msg = str(e) or f"Execution failed: {type(e).__name__}"
                logger.warning(f"Execute request failed for session {session_id}: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "outputs": [],
                    "execution_count": None,
                }

    @tool
    async def list_sessions(self) -> dict:
        """List active kernel sessions"""
        try:
            sessions = []
            for session_id, session_info in self.sessions.items():
                sessions.append(
                    {
                        "session_id": session_id,
                        "kernel_spec": session_info.kernel_spec,
                        "status": session_info.status.value,
                        "created_at": session_info.created_at,
                        "execution_count": session_info.execution_count,
                    }
                )

            return {"success": True, "sessions": sessions}
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def shutdown_session(self, session_id: str) -> dict:
        """Shutdown kernel session"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            # IOPub monitoring cleanup removed - no longer needed

            # Shutdown kernel
            km = self.kernel_managers[session_id]
            kc = self.clients[session_id]

            kc.stop_channels()
            await km.shutdown_kernel()

            # Clean up
            del self.kernel_managers[session_id]
            del self.clients[session_id]
            del self.sessions[session_id]

            logger.info(f"Shutdown kernel session: {session_id}")

            return {"success": True, "session_id": session_id}

        except Exception as e:
            logger.error(f"Failed to shutdown session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def restart_session(self, session_id: str) -> dict:
        """Restart kernel session or create new one if not found"""

        # First, try to restart existing session
        if session_id in self.sessions:
            try:
                session_info = self.sessions[session_id]
                session_info.status = KernelStatus.STARTING

                # IOPub listener cleanup removed - no longer needed

                # Step 2: Restart kernel
                km = self.kernel_managers[session_id]
                kc = self.clients[session_id]

                await km.restart_kernel()
                logger.debug(f"Kernel {session_id} restarted successfully")

                # Step 3: Wait for restarted kernel to be ready
                try:
                    await kc.wait_for_ready(timeout=30)
                    logger.debug(f"Kernel {session_id} is ready after restart")
                except RuntimeError as e:
                    logger.error(
                        f"Kernel {session_id} failed to ready after restart: {e}"
                    )
                    session_info.status = KernelStatus.DEAD
                    return {
                        "success": False,
                        "error": f"Kernel failed to be ready after restart: {e}",
                    }

                # IOPub monitoring re-setup removed - no longer needed

                # Step 5: Reset execution state
                session_info.execution_count = 0
                session_info.status = KernelStatus.IDLE

                logger.info(f"Restarted existing kernel session: {session_id}")
                return {"success": True, "session_id": session_id}

            except Exception as e:
                logger.error(f"Failed to restart existing session {session_id}: {e}")
                # Update session status if it exists
                if session_id in self.sessions:
                    self.sessions[session_id].status = KernelStatus.IDLE
                return {"success": False, "error": str(e)}

        # If session doesn't exist (e.g., after redeployment), create a new one with the same ID
        else:
            logger.info(
                f"Session {session_id} not found, creating new session with same ID"
            )
            result = await self.create_session(kernel_session_id=session_id)

            if result["success"]:
                logger.info(
                    f"Successfully created new kernel session with preserved ID: {session_id}"
                )

            return result

    @tool
    async def interrupt_session(self, session_id: str) -> dict:
        """Interrupt kernel session"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            km = self.kernel_managers[session_id]
            km.interrupt_kernel()

            session_info = self.sessions[session_id]
            session_info.status = KernelStatus.IDLE

            logger.info(f"Interrupted kernel session: {session_id}")

            return {"success": True, "session_id": session_id}

        except Exception as e:
            logger.error(f"Failed to interrupt session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def get_variables(self, session_id: str) -> dict:
        """Get variables from kernel session using %whos magic command"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session {session_id} not found"}

        # Use standard IPython %whos magic command - the only standard way
        # This matches VS Code notebook and JupyterLab behavior exactly
        # Note: %whos excludes private variables by design (standard behavior)
        variables_code = "%whos"

        try:
            # Execute using standard magic command with system metadata
            result = await self.execute_request(
                variables_code,
                session_id,
                silent=False,
                execution_metadata={"operated_by": "system"},
            )

            # Parse %whos output from the result
            variables = {}
            for output in result.get("outputs", []):
                if (
                    output.get("output_type") == "stream"
                    and output.get("name") == "stdout"
                ):
                    whos_output = output.get("text", "")

                    # Parse standard %whos output format
                    lines = whos_output.strip().split("\n")

                    # Skip header lines and find data
                    for line in lines:
                        line = line.strip()
                        if (
                            line
                            and not line.startswith("Variable")
                            and not line.startswith("---")
                            and not line.startswith("Interactive")
                        ):
                            parts = line.split(None, 3)  # Split into max 4 parts
                            if len(parts) >= 3:
                                name = parts[0]
                                var_type = parts[1]
                                size = parts[2] if len(parts) > 2 else "-"
                                value = parts[3] if len(parts) > 3 else ""

                                variables[name] = {
                                    "type": var_type,
                                    "size": size,
                                    "value": value,
                                }

            return {
                "success": True,
                "variables": variables,
                "session_id": session_id,
                "method": "ipython_whos",
            }

        except Exception as e:
            logger.error(f"Failed to get variables for session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def inspect_variable(
        self, session_id: str, variable_name: str, detail_level: int = 0
    ) -> dict:
        """Inspect specific variable. detail_level: 0=basic info, 1=detailed info"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            # Use standard inspect_request to get variable information
            # This is the proper Jupyter protocol way to inspect objects
            result = await self.inspect_request(
                code=variable_name,
                cursor_pos=len(variable_name),  # Position at end of variable name
                session_id=session_id,
                detail_level=detail_level,
            )

            if result["success"]:
                inspect_content = result["content"]

                # Check if object was found
                if inspect_content.get("found", False):
                    return {
                        "success": True,
                        "session_id": session_id,
                        "variable_name": variable_name,
                        "found": True,
                        "data": inspect_content.get("data", {}),
                        "metadata": inspect_content.get("metadata", {}),
                        "method": "inspect_request",
                    }
                else:
                    return {
                        "success": True,
                        "session_id": session_id,
                        "variable_name": variable_name,
                        "found": False,
                        "error": f"Variable '{variable_name}' not found",
                        "method": "inspect_request",
                    }
            else:
                return {
                    "success": False,
                    "error": f"Inspect request failed: {result.get('error', 'Unknown error')}",
                }

        except Exception as e:
            logger.error(
                f"Failed to inspect variable '{variable_name}' in session {session_id}: {e}"
            )
            return {"success": False, "error": str(e)}

    @tool
    async def get_variable_names(
        self, session_id: str, pattern: Optional[str] = None
    ) -> dict:
        """Get variable names using %who magic command. Optional pattern to filter by type."""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        # Use standard IPython %who magic command - the only standard way
        # This matches VS Code notebook and JupyterLab behavior exactly
        if pattern:
            who_code = f"%who {pattern}"
        else:
            who_code = "%who"

        try:
            # Execute using standard magic command with system metadata
            result = await self.execute_request(
                who_code,
                session_id,
                silent=False,
                execution_metadata={"operated_by": "system"},
            )

            # Parse %who output from the result
            names = []
            for output in result.get("outputs", []):
                if (
                    output.get("output_type") == "stream"
                    and output.get("name") == "stdout"
                ):
                    who_output = output.get("text", "")

                    # %who command returns variable names separated by spaces/newlines
                    if who_output.strip():
                        # Split by whitespace and filter out empty strings
                        names = [
                            name.strip() for name in who_output.split() if name.strip()
                        ]

            return {
                "success": True,
                "names": names,
                "session_id": session_id,
                "pattern": pattern,
                "method": "ipython_who",
            }

        except Exception as e:
            logger.error(f"Failed to get variable names for session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    # _setup_iopub_monitoring removed - no longer needed


    def _generate_outputs_from_iopub(
        self, iopub_messages: List[dict], execution_count: Optional[int] = None
    ) -> List[dict]:
        """Generate standard notebook outputs from IOPub messages with stream coalescing

        Implements nbclient-style stream merging and terminal control character processing:
        1. Merges consecutive stream outputs with the same name (stdout/stderr)
        2. Processes carriage return (\r) and backspace (\b) characters

        This ensures progress bars and terminal animations display correctly.
        """
        _ = execution_count  # Mark as intentionally unused (for potential future use)
        outputs = []
        streams = {}  # stream_name -> output_dict (for merging)

        # Phase 1: Convert IOPub messages to outputs and merge streams
        for msg in iopub_messages:
            msg_type = msg.get("header", {}).get("msg_type", "")

            # Only process output message types, skip status messages
            if msg_type in ["stream", "execute_result", "display_data", "error"]:
                try:
                    # Use official nbformat API to create output from IOPub message
                    output_node = nbformat.v4.output_from_msg(msg)

                    # Convert NotebookNode to dict
                    output_dict = dict(output_node)

                    if output_dict["output_type"] == "stream":
                        # Merge streams with the same name (nbclient-style)
                        stream_name = output_dict["name"]
                        if stream_name in streams:
                            # Append new text to existing stream
                            streams[stream_name]["text"] += output_dict["text"]
                        else:
                            # New stream: add to outputs and track for merging
                            output_dict["id"] = f"{msg_type}_{len(outputs)}"
                            outputs.append(output_dict)
                            streams[stream_name] = output_dict
                    else:
                        # Non-stream outputs: add directly
                        output_dict["id"] = f"{msg_type}_{len(outputs)}"
                        outputs.append(output_dict)

                except ValueError as e:
                    # If nbformat.output_from_msg fails, log the error and skip
                    logger.warning(
                        f"Failed to create output from IOPub message {msg_type}: {e}"
                    )
                    continue
                except Exception as e:
                    # Fallback for any other errors
                    logger.error(
                        f"Unexpected error processing IOPub message {msg_type}: {e}"
                    )
                    continue

        # Phase 2: Process terminal control characters in merged streams
        # Apply nbclient's exact approach: iterate until no more changes
        for stream in streams.values():
            original_length = len(stream["text"])

            # Process \r and \b characters (exact nbclient algorithm)
            old = stream["text"]
            while len(stream["text"]) < len(old):
                old = stream["text"]
                # Cancel out anything-but-newline followed by backspace
                stream["text"] = _RGX_BACKSPACE.sub("", stream["text"])
            # Replace all carriage returns not followed by newline (OUTSIDE loop - applied once)
            stream["text"] = _RGX_CARRIAGERETURN.sub("", stream["text"])

            if original_length != len(stream["text"]):
                logger.info(
                    f"Processed stream '{stream['name']}': {original_length} → {len(stream['text'])} chars "
                    f"(removed {original_length - len(stream['text'])} chars from \\r/\\b)"
                )

        logger.debug(
            f"Generated {len(outputs)} outputs ({len(streams)} merged streams) from {len(iopub_messages)} IOPub messages"
        )

        return outputs

    async def cleanup(self):
        """Cleanup all resources"""
        logger.info("JupyterKernelToolSet cleaning up")
        
        # Shutdown all sessions
        for session_id in list(self.sessions.keys()):
            # Clear Jedi contexts if completion service exists
            if hasattr(self, "completion_service"):
                self.completion_service.clear_session_context(session_id)
            await self.shutdown_session(session_id)



# Export
__all__ = ["JupyterKernelToolSet"]
