"""Integrated Notebook ToolSet - Unified notebook experience connecting file operations and code execution"""

import asyncio
import uuid
from typing import Dict, Optional

from ..remote.backend.base import RemoteBackend
from ..remote.factory import RemoteBackendFactory
from ..toolset import ToolSet, tool
from ..utils.log import logger
from .jupyter_kernel import (
    IOPubEventBus,
    JupyterClientKernelToolSet,
    RemoteIOPubEventBus,
)
from .notebook_contents import NotebookContentsToolSet
from .jedi_integration import EnhancedCompletionService


class IntegratedNotebookToolSet(ToolSet):
    """Integrated Notebook ToolSet - Unified management of file operations and code execution"""

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        worker_params: dict | None = None,
        remote_backend: Optional[RemoteBackend] = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workdir = workdir
        self.remote_backend = remote_backend
        self.event_bus: Optional[IOPubEventBus] = (
            None  # Will be initialized in run_setup
        )

        # Initialize child toolsets (event_bus will be set in run_setup)
        self.kernel_toolset = JupyterClientKernelToolSet(
            f"{name}_kernel", workdir, worker_params, **kwargs
        )
        self.notebook_contents = NotebookContentsToolSet(
            f"{name}_contents", workdir, worker_params, **kwargs
        )

        # Session to notebook file mapping
        self.session_notebooks: Dict[str, str] = {}  # session_id -> notebook_path
        self.notebook_sessions: Dict[str, str] = {}  # notebook_path -> session_id

        # Enhanced completion service with Jedi integration
        self.completion_service = EnhancedCompletionService()

    async def run_setup(self):
        """Setup toolset"""
        await super().run_setup()

        # Initialize remote backend if not provided
        if self.remote_backend is None:
            try:
                self.remote_backend = RemoteBackendFactory.create_backend()
                logger.info(
                    "Auto-created remote backend from environment configuration"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to create remote backend: {e}, proceeding without streaming support"
                )

        # Initialize event bus using the remote backend
        if self.remote_backend:
            self.event_bus = RemoteIOPubEventBus(self.remote_backend)
            # Update kernel toolset with the initialized event bus
            self.kernel_toolset.event_bus = self.event_bus
            logger.info("Initialized IOPub event bus with remote backend")
        else:
            logger.info(
                "No remote backend available, running without streaming support"
            )

        await self.kernel_toolset.run_setup()
        await self.notebook_contents.run_setup()
        logger.info("IntegratedNotebookToolSet setup complete")

    @tool
    async def create_notebook_session(
        self,
        notebook_path: str,
        notebook_title: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Create notebook file and kernel session"""
        try:
            # 1. Auto-detect file existence and handle accordingly
            read_result = await self.notebook_contents.read_notebook(notebook_path)

            if not read_result["success"]:
                # File doesn't exist, create it
                logger.info(
                    f"Notebook file not found, creating new notebook: {notebook_path}"
                )
                create_result = await self.notebook_contents.create_notebook(
                    notebook_path, notebook_title or "New Notebook"
                )
                if not create_result["success"]:
                    return {
                        "success": False,
                        "error": f"Failed to create notebook: {create_result['error']}",
                    }
                logger.info(f"Successfully created notebook: {notebook_path}")
            else:
                # File exists, use it directly
                logger.info(f"Using existing notebook: {notebook_path}")

            # 2. Create kernel session (pass session_id for restart scenarios)
            session_result = await self.kernel_toolset.create_session(
                "python3", session_id
            )
            if not session_result["success"]:
                return {
                    "success": False,
                    "error": f"Failed to create kernel session: {session_result['error']}",
                }

            actual_session_id = session_result["session_id"]

            # 3. Establish association mapping
            self.session_notebooks[actual_session_id] = notebook_path
            self.notebook_sessions[notebook_path] = actual_session_id

            logger.info(
                f"Created integrated notebook session: {actual_session_id} -> {notebook_path}"
            )

            return {
                "success": True,
                "session_id": actual_session_id,
                "notebook_path": notebook_path,
                "kernel_info": session_result,
                "streaming_enabled": self.event_bus is not None,
            }

        except Exception as e:
            logger.error(f"Failed to create notebook session: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def execute_cell(
        self,
        session_id: str,
        code: str = "",
        cell_index: Optional[int] = None,
        cell_type: str = "code",
        operated_by: Optional[str] = None,
        sync_return: bool = True,
    ) -> dict:
        """Execute code cell. If cell_index=None, creates new cell. Otherwise executes existing cell at index."""
        logger.info(
            f"🔍 execute_cell REQUEST: session_id={session_id}, cell_index={cell_index}, "
            f"code={repr(code)}, cell_type={cell_type}, operated_by={operated_by}, sync_return={sync_return}"
        )

        # Validate session
        if session_id not in self.session_notebooks:
            return self._error_response(f"Session not found: {session_id}")

        notebook_path = self.session_notebooks[session_id]

        try:
            if cell_index is None:
                # Create new cell and execute
                return await self._add_and_execute_new_cell(
                    session_id, notebook_path, code, cell_type, operated_by, sync_return
                )
            else:
                # Execute existing cell
                return await self._execute_existing_cell_by_index(
                    session_id,
                    notebook_path,
                    cell_index,
                    code,
                    operated_by,
                    sync_return,
                )

        except Exception as e:
            logger.error(f"Failed to execute cell: {e}")
            return self._error_response(str(e))

    def _error_response(self, error_message: str) -> dict:
        """Create standardized error response"""
        error_result = {"success": False, "error": error_message}
        logger.error(f"❌ execute_cell RESPONSE: {error_result}")
        return error_result

    async def _add_and_execute_new_cell(
        self,
        session_id: str,
        notebook_path: str,
        code: str,
        cell_type: str,
        operated_by: Optional[str],
        sync_return: bool,
    ) -> dict:
        """Add new cell and execute if it's a code cell"""
        logger.info(f"📝 Adding new {cell_type} cell and executing")

        # Add cell to notebook
        add_result = await self.notebook_contents.add_cell(
            notebook_path, cell_type, code
        )
        if not add_result["success"]:
            return self._error_response(f"Failed to add cell: {add_result['error']}")

        cell_index = add_result["cell_index"]
        logger.info(f"✅ Added cell at index {cell_index}")

        if cell_type == "code":
            # Execute the code cell
            result = await self._execute_cell_with_code(
                session_id, notebook_path, cell_index, code, operated_by, sync_return
            )
            result["cell_added"] = True
            return result
        else:
            # Markdown cell - just return success
            return {
                "success": True,
                "cell_added": True,
                "cell_index": cell_index,
                "cell_type": cell_type,
            }

    async def _execute_existing_cell_by_index(
        self,
        session_id: str,
        notebook_path: str,
        cell_index: int,
        code: str,
        operated_by: Optional[str],
        sync_return: bool,
    ) -> dict:
        """Execute existing cell at specified index"""
        logger.info(f"🔄 Executing existing cell at index {cell_index}")

        # Update cell content if code provided, otherwise use existing code
        if code:
            update_result = await self.notebook_contents.update_cell(
                notebook_path, cell_index, code
            )
            if not update_result["success"]:
                return self._error_response(
                    f"Failed to update cell: {update_result['error']}"
                )
            logger.info(f"✅ Updated cell {cell_index} content")
        else:
            # Read existing code from notebook
            try:
                read_result = await self.notebook_contents.read_notebook(notebook_path)
                if not read_result["success"]:
                    return self._error_response(
                        f"Failed to read notebook: {read_result['error']}"
                    )

                cells = read_result["notebook"]["cells"]
                if cell_index >= len(cells):
                    return self._error_response(f"Cell index {cell_index} out of range")

                cell = cells[cell_index]
                if cell.cell_type != "code":
                    return self._error_response(f"Cell {cell_index} is not a code cell")

                code = self.notebook_contents._format_source(cell.source)
            except Exception as e:
                return self._error_response(f"Failed to read cell {cell_index}: {e}")

        # Execute the cell
        result = await self._execute_cell_with_code(
            session_id, notebook_path, cell_index, code, operated_by, sync_return
        )
        result["cell_added"] = False
        return result

    async def _execute_cell_with_code(
        self,
        session_id: str,
        notebook_path: str,
        cell_index: int,
        code: str,
        operated_by: Optional[str],
        sync_return: bool,
    ) -> dict:
        """Execute cell with given code (core execution logic)"""
        if sync_return:
            # Synchronous mode: block until full results (suitable for agent calls)
            exec_result = await self._execute_code_and_update_notebook(
                code,
                session_id,
                notebook_path,
                cell_index,
                True,  # Always update notebook (simplified)
                operated_by,
            )

            # Add notebook-specific fields to the execution result
            exec_result["notebook_path"] = notebook_path
            exec_result["cell_index"] = cell_index
            exec_result["updated_notebook"] = True

            logger.info(
                f"✅ execute_cell RESPONSE (sync): success={exec_result['success']}, execution_count={exec_result.get('execution_count')}, outputs={len(exec_result.get('outputs', []))}"
            )
            return exec_result
        else:
            # Asynchronous mode: return immediately, results sent via IOPub streaming (suitable for UI calls)
            # Generate unique execution ID
            execution_id = str(uuid.uuid4())

            # Start async execution (don't wait for results)
            async def _background_execute():
                try:
                    await self._execute_code_and_update_notebook(
                        code,
                        session_id,
                        notebook_path,
                        cell_index,
                        True,  # Always update notebook (simplified)
                        operated_by,
                    )
                    logger.info(f"✅ Background execution completed for {execution_id}")
                except Exception as e:
                    logger.error(
                        f"❌ Background execution failed for {execution_id}: {e}"
                    )

            asyncio.create_task(_background_execute())

            # Return execution ID and basic info immediately
            result = {
                "success": True,
                "execution_id": execution_id,
                "notebook_path": notebook_path,
                "cell_index": cell_index,
                "code": code,
                "status": "started",
                "message": "Execution started, results will be sent via IOPub",
            }
            logger.info(f"✅ execute_cell RESPONSE (async): {result}")
            return result

    @tool
    async def get_notebook_status(self, session_id: str) -> dict:
        """Get notebook and kernel status"""
        if session_id not in self.session_notebooks:
            return {"success": False, "error": f"Session not found: {session_id}"}

        notebook_path = self.session_notebooks[session_id]

        try:
            # 1. Get notebook file information
            read_result = await self.notebook_contents.read_notebook(notebook_path)

            # 2. Get kernel session information
            sessions_result = await self.kernel_toolset.list_sessions()

            kernel_info = None
            if sessions_result["success"]:
                for session in sessions_result["sessions"]:
                    if session["session_id"] == session_id:
                        kernel_info = session
                        break

            return {
                "success": True,
                "session_id": session_id,
                "notebook_path": notebook_path,
                "notebook_info": read_result if read_result["success"] else None,
                "kernel_info": kernel_info,
                "cell_count": len(read_result.get("notebook", {}).get("cells", []))
                if read_result["success"]
                else 0,
            }

        except Exception as e:
            logger.error(f"Failed to get notebook status: {e}")
            return {"success": False, "error": str(e)}

    async def subscribe_notebook_events(
        self, session_id: str, client_id: str, callback=None
    ) -> dict:
        """Subscribe to notebook real-time events"""
        if session_id not in self.session_notebooks:
            return {"success": False, "error": f"Session not found: {session_id}"}

        # Directly proxy to kernel subscription
        return await self.kernel_toolset.subscribe_iopub(
            session_id, client_id, callback
        )

    @tool
    async def manage_notebook_session(
        self, session_id: str, action: str = "restart"
    ) -> dict:
        """Manage notebook session - restart or shutdown

        Args:
            session_id: Session identifier
            action: "restart" (default) or "shutdown"
        """
        if session_id not in self.session_notebooks:
            return {"success": False, "error": f"Session not found: {session_id}"}

        if action not in ["restart", "shutdown"]:
            return {
                "success": False,
                "error": f"Invalid action: {action}. Must be 'restart' or 'shutdown'",
            }

        notebook_path = self.session_notebooks[session_id]

        try:
            # 1. Clear Jedi context for both actions
            if self.completion_service:
                self.completion_service.clear_session_context(session_id)
                logger.info(f"Cleared Jedi context for session: {session_id}")

            if action == "restart":
                # 2. Restart kernel session
                kernel_result = await self.kernel_toolset.restart_session(session_id)
                logger.info(f"Restarted integrated notebook session: {session_id}")

                return {
                    "success": kernel_result["success"],
                    "action": "restart",
                    "session_id": session_id,
                    "notebook_path": notebook_path,
                    "kernel_result": kernel_result,
                    "context_cleared": True,
                }

            else:  # action == "shutdown"
                # 2. Shutdown kernel session
                kernel_result = await self.kernel_toolset.shutdown_session(session_id)

                # 3. Clean up mapping relationships for shutdown
                del self.session_notebooks[session_id]
                if notebook_path in self.notebook_sessions:
                    del self.notebook_sessions[notebook_path]

                logger.info(f"Shutdown integrated notebook session: {session_id}")

                return {
                    "success": True,
                    "action": "shutdown",
                    "session_id": session_id,
                    "notebook_path": notebook_path,
                    "kernel_result": kernel_result,
                    "context_cleared": True,
                }

        except Exception as e:
            logger.error(f"Failed to {action} notebook session: {e}")
            return {"success": False, "error": str(e)}

    async def _update_cell_output(
        self, notebook_path: str, cell_index: int, exec_result: dict
    ):
        """Update cell output in notebook file using NotebookContentsToolSet"""
        try:
            # Use structured outputs directly from execute_request (already processed)
            outputs = exec_result.get(
                "outputs", []
            )  # Already structured by execute_request
            execution_count = exec_result.get("execution_count")

            logger.info(
                f"Updating cell {cell_index} with {len(outputs)} structured outputs"
            )

            # Convert outputs to notebook format (remove 'id' field which is for frontend only)
            notebook_outputs = []
            for output in outputs:
                # Remove frontend-specific 'id' field for notebook storage
                notebook_output = {k: v for k, v in output.items() if k != "id"}
                notebook_outputs.append(notebook_output)

            logger.info(
                f"Generated {len(notebook_outputs)} outputs for cell {cell_index}"
            )

            # Extract execution timing from exec_result metadata (standard nbformat structure)
            metadata = exec_result.get("metadata", {})
            execution_timing = metadata.get("execution", {})

            # Use notebook_contents to update outputs directly
            await self.notebook_contents.update_cell_outputs(
                notebook_path,
                cell_index,
                notebook_outputs,
                execution_count,
                execution_timing,
            )

            logger.info(f"✅ Cell {cell_index} output updated successfully")

        except Exception as e:
            logger.error(f"Failed to update cell output for cell {cell_index}: {e}")
            # Add context information for better debugging
            logger.error(f"  Notebook path: {notebook_path}")
            logger.error(f"  Execution count: {execution_count}")
            logger.error(f"  Output count: {len(exec_result.get('outputs', []))}")
            logger.error(f"  Exec result success: {exec_result.get('success')}")
            raise  # Re-raise to let caller handle

    async def _execute_code_and_update_notebook(
        self,
        code: str,
        session_id: str,
        notebook_path: str,
        cell_index: int,
        update_notebook: bool,
        operated_by: Optional[str],
    ) -> dict:
        """Execute code and update notebook file (SSOT principle)"""
        try:
            # Execute code with metadata
            execution_metadata = {
                "cell_index": cell_index,
                "notebook_path": notebook_path,
                "operated_by": operated_by,
                "update_notebook": update_notebook,
            }
            exec_result = await self.kernel_toolset.execute_request(
                code, session_id, silent=False, execution_metadata=execution_metadata
            )

            # SSOT: Always update backend notebook file after execution (even on failure)
            if update_notebook:
                await self._update_cell_output(notebook_path, cell_index, exec_result)

            # Update Jedi context with executed code for better completions
            if exec_result.get("success") and code.strip():
                self.completion_service.update_session_context(session_id, code)

            return exec_result

        except Exception as e:
            logger.error(f"❌ Execution failed: {e}")

            # SSOT: Update backend with error information
            if update_notebook:
                error_result = {
                    "success": False,
                    "error": str(e),
                    "outputs": [
                        {
                            "output_type": "error",
                            "ename": type(e).__name__,
                            "evalue": str(e),
                            "traceback": [],
                        }
                    ],
                    "execution_count": None,
                    "metadata": {"execution": {}},  # Empty timing for error cases
                }
                try:
                    await self._update_cell_output(
                        notebook_path, cell_index, error_result
                    )
                except Exception as update_error:
                    logger.error(
                        f"Failed to update notebook with error info: {update_error}"
                    )

            # Re-raise the original exception
            raise

    @tool
    async def read_notebook(self, path: str) -> dict:
        """Read notebook file content"""
        return await self.notebook_contents.read_notebook(path)

    @tool
    async def update_cell(
        self,
        path: str,
        cell_index: int,
        source: str,
        cell_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Update cell source code and metadata (not outputs)"""
        # SSOT principle: Frontend update_cell should only modify source/metadata
        # Outputs are only updated by backend after execution
        return await self.notebook_contents.update_cell(
            path, cell_index, source, cell_type, metadata
        )

    @tool
    async def add_cell(
        self,
        path: str,
        cell_type: str,
        source: str = "",
        position: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Add new cell to notebook"""
        return await self.notebook_contents.add_cell(
            path, cell_type, source, position, metadata
        )

    @tool
    async def delete_cell(self, path: str, cell_index: int) -> dict:
        """Delete cell from notebook"""
        return await self.notebook_contents.delete_cell(path, cell_index)

    @tool
    async def move_cell(self, path: str, from_index: int, to_index: int) -> dict:
        """Move cell to different position"""
        return await self.notebook_contents.move_cell(path, from_index, to_index)

    @tool
    async def get_variables(self, session_id: str) -> dict:
        """Get variables from kernel session"""
        if session_id not in self.session_notebooks:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            # Proxy to kernel toolset
            return await self.kernel_toolset.get_variables(session_id)

        except Exception as e:
            logger.error(f"Failed to get variables for session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def list_notebook_sessions(self) -> dict:
        """List active notebook sessions"""
        try:
            sessions = []
            for session_id, notebook_path in self.session_notebooks.items():
                # Get kernel status
                kernel_sessions = await self.kernel_toolset.list_sessions()
                kernel_info = None

                if kernel_sessions["success"]:
                    for session in kernel_sessions["sessions"]:
                        if session["session_id"] == session_id:
                            kernel_info = session
                            break

                # Extract notebook title from path (remove .ipynb extension)
                notebook_filename = (
                    notebook_path.split("/")[-1]
                    if notebook_path
                    else f"session_{session_id[:8]}"
                )
                notebook_title = (
                    notebook_filename.replace(".ipynb", "")
                    if notebook_filename.endswith(".ipynb")
                    else notebook_filename
                )

                sessions.append(
                    {
                        "session_id": session_id,
                        "notebook_path": notebook_path,
                        "notebook_title": notebook_title,
                        "created_by": "user",  # Currently all sessions are user-created, can be extended later
                        "kernel_status": kernel_info["status"]
                        if kernel_info
                        else "unknown",
                        "execution_count": kernel_info["execution_count"]
                        if kernel_info
                        else 0,
                    }
                )

            return {"success": True, "sessions": sessions, "count": len(sessions)}

        except Exception as e:
            logger.error(f"Failed to list notebook sessions: {e}")
            return {"success": False, "error": str(e)}

    async def cleanup(self):
        """Cleanup all resources"""
        try:
            # Cleanup event bus if it exists and has cleanup method
            if self.event_bus:
                if hasattr(self.event_bus, "cleanup") and callable(
                    getattr(self.event_bus, "cleanup")
                ):
                    cleanup_method = getattr(self.event_bus, "cleanup")
                    await cleanup_method()
                else:
                    logger.debug("Event bus doesn't have cleanup method, skipping")

            # Cleanup child toolsets
            if self.kernel_toolset:
                await self.kernel_toolset.cleanup()

            if self.notebook_contents and hasattr(self.notebook_contents, "cleanup"):
                await self.notebook_contents.cleanup()

            # Clear Jedi contexts for all sessions
            if self.completion_service:
                for session_id in list(self.session_notebooks.keys()):
                    self.completion_service.clear_session_context(session_id)

            # Clear session mappings
            self.session_notebooks.clear()
            self.notebook_sessions.clear()

            logger.info("IntegratedNotebookToolSet cleanup complete")

        except Exception as e:
            logger.error(f"Error during IntegratedNotebookToolSet cleanup: {e}")

    @tool
    async def complete_request(
        self,
        code: str,
        cursor_pos: int,
        session_id: Optional[str] = None,
    ) -> dict:
        """Get code completion suggestions with optional session context"""
        try:
            # If session_id provided, validate it exists
            if session_id is not None and session_id not in self.session_notebooks:
                return {
                    "success": False,
                    "error": f"Session not found: {session_id}",
                }

            # Use completion service with or without session context
            effective_session_id = session_id or "default"
            return await self.completion_service.get_completions(
                code=code,
                cursor_pos=cursor_pos,
                session_id=effective_session_id,
                context_code="",  # Empty - Jedi service uses its internal session context
            )

        except Exception as e:
            logger.error(f"Integrated completion failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def inspect_request(
        self,
        code: str,
        cursor_pos: int,
        session_id: Optional[str] = None,
    ) -> dict:
        """Inspect object or variable with optional session context"""
        try:
            # If session_id provided, validate it exists
            if session_id is not None and session_id not in self.session_notebooks:
                return {
                    "success": False,
                    "error": f"Session not found: {session_id}",
                }

            # Use completion service with or without session context
            effective_session_id = session_id or "default"
            return await self.completion_service.get_inspection(
                code=code,
                cursor_pos=cursor_pos,
                session_id=effective_session_id,
                context_code="",  # Empty - Jedi service uses its internal session context
            )

        except Exception as e:
            logger.error(f"Integrated inspection failed: {e}")
            return {"success": False, "error": str(e)}


# Export
__all__ = ["IntegratedNotebookToolSet"]
