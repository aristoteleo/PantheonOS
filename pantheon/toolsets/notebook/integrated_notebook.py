"""
Integrated Notebook ToolSet - VSCode-style Hybrid Approach

Key improvements over v1:
- Uses (notebook_path, session_id) as composite key for isolation
- Automatic session creation (no explicit initialization needed)
- Uses cell_id instead of cell_index for stability (like VSCode)
- session_id automatically injected via @tool decorator
- Unified kernel management (manage_kernel with 6 actions including shutdown/delete)
- Code intelligence tools (complete/inspect) marked exclude=True for frontend only
- read_notebook marked exclude=True (agents use file Read tool for full content)
- Subscribe/unsubscribe API for backend streaming (non-tool methods)

Design philosophy (Hybrid Approach):
- Separate creation from execution: create_notebook vs execute_cell
- Unified operations: edit_notebook (4 types), manage_kernel (6 actions)
- Efficient read tools: read_cell (complete cell data), get_notebook_info (summary)
- Full notebook reads: Use standard file Read tool (like VSCode design)
- Backend streaming: subscribe/unsubscribe API for real-time IOPub events
- LLM-friendly: Clear tool purposes, minimal cognitive load
- VSCode-compatible: Matches VSCode Copilot builtin tools design

API: 7 tools for agents + 3 frontend-only tools (exclude=True)
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Literal, Optional

from pantheon.remote.backend.base import RemoteBackend
from pantheon.remote.factory import RemoteBackendFactory
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

from .jedi_integration import EnhancedCompletionService
from .jupyter_kernel import (
    IOPubEventBus,
    JupyterKernelToolSet,
    RemoteIOPubEventBus,
)
from .notebook_contents import NotebookContentsToolSet


@dataclass
class NotebookContext:
    """Notebook context for a specific (notebook_path, session_id) combination"""

    notebook_path: str
    session_id: str
    kernel_session_id: str  # Internal kernel session (hidden from users)
    created_at: str
    notebook_title: str
    kernel_spec: str = "python3"


class IntegratedNotebookToolSet(ToolSet):
    """
    Integrated Notebook ToolSet - VSCode-style Hybrid Approach

    Design principles:
    1. Use notebook_path as primary identifier (not internal session_id)
    2. session_id from context for multi-chat isolation (automatic)
    3. Automatic context creation (no explicit initialization)
    4. Use cell_id instead of cell_index (stable identifiers)
    5. Separate creation and execution (VSCode-style)
    6. Unified editing with convenience tools (Hybrid approach)
    7. Let agents use file Read tool for full content (VSCode design)

    API Surface (7 tools exposed to LLM agents):

    Core Tools (VSCode-inspired):
    - create_notebook: Create new notebook (like VSCode newNotebookTool)
    - execute_cell: Execute existing cell (like VSCode runNotebookCellTool)
    - edit_notebook: Unified editing (insert/delete/move/update) (like VSCode editNotebookTool)

    Read Operations:
    - read_cell: Read complete cell data by cell_id (source + outputs + metadata)
    - get_notebook_info: Get notebook summary with execution status (like VSCode getNotebookSummary)
    - list_notebooks: List notebooks for current session

    Kernel Management (unified):
    - manage_kernel: Unified kernel operations (restart/interrupt/status/variables/shutdown/delete)

    Frontend-Only Tools (exclude=True, not exposed to agents):
    - complete_request: Code completion for editors
    - inspect_request: Hover documentation for editors
    - read_notebook: Read full notebook JSON (agents use file Read tool instead)

    Backend/UI Streaming API (not @tool, for internal use):
    - subscribe_notebook_events: Subscribe to IOPub events for real-time streaming
    - unsubscribe_notebook_events: Unsubscribe from IOPub events

    Note: For full notebook content, agents should use the standard file Read tool.
    This matches VSCode's design where agents use built-in file operations.
    """

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        remote_backend: Optional[RemoteBackend] = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.workdir = workdir or Path.cwd().as_posix()
        self.remote_backend = remote_backend
        self.event_bus: Optional[IOPubEventBus] = None

        # Initialize child toolsets
        self.kernel_toolset = JupyterKernelToolSet(f"{name}_kernel", workdir, **kwargs)
        self.notebook_contents = NotebookContentsToolSet(
            f"{name}_contents", workdir, **kwargs
        )

        # Notebook contexts: (notebook_path, session_id) -> NotebookContext
        self.notebook_contexts: Dict[tuple, NotebookContext] = {}

        # Persistence
        self.persistence_dir = Path(self.workdir)
        self.persistence_file = self.persistence_dir / ".notebook_contexts.json"

        # Completion service
        self.completion_service = EnhancedCompletionService()

    async def run_setup(self):
        """Setup toolset"""
        await super().run_setup()

        # Initialize remote backend
        if self.remote_backend is None:
            try:
                self.remote_backend = RemoteBackendFactory.create_backend()
                logger.info("Auto-created remote backend from environment")
            except Exception as e:
                logger.warning(f"No remote backend: {e}")

        # Initialize event bus
        if self.remote_backend:
            self.event_bus = RemoteIOPubEventBus(self.remote_backend)
            self.kernel_toolset.event_bus = self.event_bus
            logger.info("Initialized IOPub event bus")

        await self.kernel_toolset.run_setup()
        await self.notebook_contents.run_setup()

        # Start unified IOPub listener
        if (
            self.kernel_toolset.use_unified_listener
            and self.kernel_toolset.unified_listener
        ):
            await self.kernel_toolset.unified_listener.start_listening()
            logger.info("Started unified IOPub listener")

        # Load persisted contexts
        await self._load_contexts()

        logger.info("IntegratedNotebookToolSet setup complete")

    async def _load_contexts(self):
        """Load notebook contexts from persistence"""
        try:
            if self.persistence_file.exists():
                with open(self.persistence_file, "r") as f:
                    data = json.load(f)

                # Convert back to NotebookContext objects
                for key_str, context_data in data.get("contexts", {}).items():
                    # key_str is "notebook_path::session_id"
                    notebook_path, session_id = key_str.split("::", 1)
                    key = (notebook_path, session_id)
                    self.notebook_contexts[key] = NotebookContext(**context_data)

                logger.info(f"Loaded {len(self.notebook_contexts)} context(s)")
        except Exception as e:
            logger.error(f"Failed to load contexts: {e}")
            self.notebook_contexts = {}

    async def _save_contexts(self):
        """Save notebook contexts to persistence"""
        try:
            self.persistence_dir.mkdir(parents=True, exist_ok=True)

            # Convert to serializable format
            contexts_data = {}
            for (notebook_path, session_id), context in self.notebook_contexts.items():
                # Use :: as separator (won't conflict with paths or session_ids)
                key_str = f"{notebook_path}::{session_id}"
                contexts_data[key_str] = {
                    "notebook_path": context.notebook_path,
                    "session_id": context.session_id,
                    "kernel_session_id": context.kernel_session_id,
                    "created_at": context.created_at,
                    "notebook_title": context.notebook_title,
                    "kernel_spec": context.kernel_spec,
                }

            data = {
                "contexts": contexts_data,
                "last_updated": datetime.now().isoformat(),
            }

            with open(self.persistence_file, "w") as f:
                json.dump(data, f, indent=2)

            logger.debug(f"Saved {len(self.notebook_contexts)} context(s)")
        except Exception as e:
            logger.error(f"Failed to save contexts: {e}")

    async def _get_or_create_context(
        self, notebook_path: str, session_id: str
    ) -> NotebookContext:
        """
        Get or create notebook context for (notebook_path, session_id)

        This is the core isolation mechanism - each (notebook, session) pair
        gets its own kernel and state.

        Auto-recovery: If context exists but kernel session is lost (e.g., after
        backend restart), automatically recreates the kernel with the same ID.
        """
        key = (notebook_path, session_id)

        if key not in self.notebook_contexts:
            logger.info(f"Creating new context: {notebook_path} @ {session_id}")

            # 1. Ensure notebook file exists
            read_result = await self.notebook_contents.read_notebook(notebook_path)
            if not read_result["success"]:
                create_result = await self.notebook_contents.create_notebook(
                    notebook_path, "New Notebook"
                )
                if not create_result["success"]:
                    raise Exception(
                        f"Failed to create notebook: {create_result['error']}"
                    )

            # 2. Create kernel session (internal)
            kernel_result = await self.kernel_toolset.create_session("python3")
            if not kernel_result["success"]:
                raise Exception(f"Failed to create kernel: {kernel_result['error']}")

            # 3. Create context
            self.notebook_contexts[key] = NotebookContext(
                notebook_path=notebook_path,
                session_id=session_id,
                kernel_session_id=kernel_result["session_id"],
                created_at=datetime.now().isoformat(),
                notebook_title="New Notebook",
                kernel_spec="python3",
            )

            # 4. Persist
            await self._save_contexts()

            logger.info(f"Created context: {notebook_path} @ {session_id}")

        else:
            # Context exists - check if kernel session is still alive
            context = self.notebook_contexts[key]

            if context.kernel_session_id not in self.kernel_toolset.sessions:
                logger.warning(
                    f"Kernel session {context.kernel_session_id[:8]} not found for "
                    f"notebook '{notebook_path}' (possible backend restart). "
                    f"Auto-recovering kernel..."
                )

                # Recreate kernel with the SAME session ID to maintain consistency
                # Use keyword arguments with renamed parameter (kernel_session_id)
                kernel_result = await self.kernel_toolset.create_session(
                    kernel_spec=context.kernel_spec,
                    kernel_session_id=context.kernel_session_id,
                )

                if not kernel_result["success"]:
                    error_msg = (
                        f"Failed to restore kernel session {context.kernel_session_id[:8]}: "
                        f"{kernel_result.get('error', 'Unknown error')}"
                    )
                    logger.error(error_msg)
                    raise Exception(error_msg)

                logger.info(
                    f"✅ Successfully restored kernel session {context.kernel_session_id[:8]} "
                    f"for notebook '{notebook_path}'"
                )

        return self.notebook_contexts[key]

    def _get_context(
        self, notebook_path: str, session_id: str
    ) -> Optional[NotebookContext]:
        """Get existing context (without creating)"""
        return self.notebook_contexts.get((notebook_path, session_id))

    async def _get_cell_by_id(
        self, notebook_path: str, cell_id: str
    ) -> tuple[Optional[int], Optional[dict]]:
        """Get cell index and data by cell_id"""
        read_result = await self.notebook_contents.read_notebook(notebook_path)
        if not read_result["success"]:
            return None, None

        cells = read_result["notebook"]["cells"]
        for idx, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return idx, cell

        return None, None

    # ═══════════════════════════════════════════════════════════
    # Core Tools (VSCode-style Hybrid Approach)
    # ═══════════════════════════════════════════════════════════

    @tool
    async def create_notebook(
        self, notebook_path: str, title: str = "New Notebook"
    ) -> dict:
        """
        Create a new notebook (like VSCode newNotebookTool)

        Args:
            notebook_path: Path to notebook file
            title: Notebook title

        Returns:
            dict with success, notebook_path, session_id
        """
        session_id = self.get_session_id()
        logger.debug(f"Creating notebook {notebook_path} @ {session_id}")
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # Create or get context (automatically creates notebook and kernel)
            context = await self._get_or_create_context(notebook_path, session_id)

            return {
                "success": True,
                "notebook_path": notebook_path,
                "kernel_session_id": context.kernel_session_id,
                "created_at": context.created_at,
            }

        except Exception as e:
            logger.error(f"create_notebook failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def execute_cell(
        self, notebook_path: str, cell_id: str, code: str = ""
    ) -> dict:
        """
        Execute existing notebook cell (like VSCode runNotebookCellTool)

        Uses cell_id for stability. If code is provided, updates cell before executing.
        session_id automatically injected via @tool decorator.

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier (must exist)
            code: Optional code to update cell with before execution

        Returns:
            dict with execution results
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # Get or create context (automatic)
            context = await self._get_or_create_context(notebook_path, session_id)

            # Find existing cell
            cell_index, cell_data = await self._get_cell_by_id(notebook_path, cell_id)
            if cell_index is None:
                return {"success": False, "error": f"Cell {cell_id} not found"}

            # Update cell content if code provided
            if code:
                await self.notebook_contents.update_cell(notebook_path, cell_id, code)
            else:
                # Use existing code
                code = self.notebook_contents._format_source(cell_data["source"])

            # Execute with cell_id (not cell_index for stability)
            exec_result = await self._execute_and_update(
                context.kernel_session_id, notebook_path, cell_id, code
            )
            # V2: Return stable identifiers (cell_id + kernel_session_id + notebook_path)
            exec_result["cell_id"] = cell_id
            exec_result["kernel_session_id"] = context.kernel_session_id
            exec_result["notebook_path"] = (
                notebook_path  # Explicit, avoid frontend parsing
            )
            return exec_result

        except Exception as e:
            logger.error(f"execute_cell failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def add_cell(
        self,
        notebook_path: str,
        cell_type: str = "code",
        content: str = "",
        cell_id: Optional[str] = None,
        below_cell_id: Optional[str] = None,
    ) -> dict:
        """
        Add new cell to notebook

        Args:
            notebook_path: Path to notebook file
            cell_type: Type of cell ('code', 'markdown', or 'raw'), default 'code'
            content: Cell source code content
            cell_id: Optional cell identifier (auto-generated if not provided)
            below_cell_id: Cell id to insert after. If None, append to end.

        Returns:
            dict with success, cell_id, notebook_path, kernel_session_id
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # Get or create context
            context = await self._get_or_create_context(notebook_path, session_id)

            # Call notebook_contents API
            result = await self.notebook_contents.add_cell(
                path=notebook_path,
                cell_type=cell_type,
                source=content,
                cell_id=cell_id,
                below_cell_id=below_cell_id,
            )

            # Add context information
            if result["success"]:
                result["notebook_path"] = notebook_path
                result["kernel_session_id"] = context.kernel_session_id

            return result

        except Exception as e:
            logger.error(f"add_cell failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def update_cell(
        self,
        notebook_path: str,
        cell_id: str,
        content: str,
    ) -> dict:
        """
        Update cell content

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier
            content: New cell source code content

        Returns:
            dict with success, cell_id, notebook_path, kernel_session_id
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # Get or create context
            context = await self._get_or_create_context(notebook_path, session_id)

            # Call notebook_contents API
            result = await self.notebook_contents.update_cell(
                path=notebook_path,
                cell_id=cell_id,
                source=content,
            )

            # Add context information
            if result["success"]:
                result["notebook_path"] = notebook_path
                result["kernel_session_id"] = context.kernel_session_id

            return result

        except Exception as e:
            logger.error(f"update_cell failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def delete_cell(
        self,
        notebook_path: str,
        cell_id: str,
    ) -> dict:
        """
        Delete cell from notebook

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier

        Returns:
            dict with success, cell_id, notebook_path, kernel_session_id
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # Get or create context
            context = await self._get_or_create_context(notebook_path, session_id)

            # Call notebook_contents API
            result = await self.notebook_contents.delete_cell(
                path=notebook_path,
                cell_id=cell_id,
            )

            # Add context information
            if result["success"]:
                result["notebook_path"] = notebook_path
                result["kernel_session_id"] = context.kernel_session_id

            return result

        except Exception as e:
            logger.error(f"delete_cell failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def move_cell(
        self,
        notebook_path: str,
        cell_id: str,
        below_cell_id: Optional[str] = None,
    ) -> dict:
        """
        Move cell to different position

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier to move
            below_cell_id: Cell id to move after. If None, move to top.

        Returns:
            dict with success, cell_id, notebook_path, kernel_session_id
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            # Get or create context
            context = await self._get_or_create_context(notebook_path, session_id)

            # Call notebook_contents API
            result = await self.notebook_contents.move_cell(
                path=notebook_path,
                cell_id=cell_id,
                below_cell_id=below_cell_id,
            )

            # Add context information
            if result["success"]:
                result["notebook_path"] = notebook_path
                result["kernel_session_id"] = context.kernel_session_id

            return result

        except Exception as e:
            logger.error(f"move_cell failed: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def read_notebook(self, notebook_path: str, validate: bool = False) -> dict:
        """
        Read full notebook JSON content (Frontend only)

        This tool is for frontend/API use only. Not exposed to LLM agents.

        For LLM agents: Use the standard file Read tool to read .ipynb files.
        This matches VSCode's design where agents use built-in file operations
        for full content reads.

        Use get_notebook_info() for notebook summaries and read_cell()
        for complete cell data retrieval.

        Args:
            notebook_path: Path to notebook
            validate: Perform nbformat schema validation (default: False)
                     Set True for initial load, False for periodic updates (faster)

        Returns:
            dict with notebook JSON structure

        Performance note:
            validation=False is recommended for periodic polling to reduce latency.
            The frontend can validate on initial load with validation=True.
        """
        return await self.notebook_contents.read_notebook(
            notebook_path, validate=validate
        )

    @tool
    async def read_cell(self, notebook_path: str, cell_id: str) -> dict:
        """
        Read complete cell data (source, outputs, metadata)

        Efficient targeted read for a single cell. Returns full cell information
        including source code, outputs, execution state, and metadata.

        Use this for:
        - Debugging (see source + outputs together)
        - Cell inspection (full state in one call)
        - Error analysis (source code + error output)

        Use get_notebook_info() for summaries of all cells.
        Use file Read tool for complete notebook JSON.

        Args:
            notebook_path: Path to notebook
            cell_id: Cell identifier

        Returns:
            dict with complete cell data including:
            - source: Cell source code
            - outputs: Cell outputs (for code cells)
            - execution_count: Execution counter
            - cell_type: Type of cell (code/markdown/raw)
            - metadata: Cell metadata
            - notebook_path: Path to notebook
            - kernel_session_id: Kernel session ID (if context exists)
        """
        session_id = self.get_session_id()

        try:
            cell_index, cell_data = await self._get_cell_by_id(notebook_path, cell_id)
            if cell_index is None:
                return {"success": False, "error": f"Cell {cell_id} not found"}

            # Format source code (handle both string and list formats)
            source = cell_data.get("source", "")
            if isinstance(source, list):
                source = "".join(source)

            # Get context if exists
            context = (
                self._get_context(notebook_path, session_id) if session_id else None
            )

            result = {
                "success": True,
                "cell_id": cell_id,
                "notebook_path": notebook_path,
                # V2: cell_index removed (unstable identifier - use cell_id instead)
                "cell_type": cell_data.get("cell_type"),
                "source": source,
                "outputs": cell_data.get("outputs", []),
                "execution_count": cell_data.get("execution_count"),
                "metadata": cell_data.get("metadata", {}),
            }

            # Add kernel_session_id if context exists
            if context:
                result["kernel_session_id"] = context.kernel_session_id

            return result

        except Exception as e:
            logger.error(f"read_cell failed: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def list_notebooks(self) -> dict:
        """
        List notebooks for current session

        Automatically filters by session_id from context.
        """
        session_id = self.get_session_id()

        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        notebooks = []
        for (nb_path, sess_id), context in self.notebook_contexts.items():
            if sess_id == session_id:
                # Get kernel status
                kernel_status = "dead"
                if context.kernel_session_id in self.kernel_toolset.sessions:
                    kernel_status = self.kernel_toolset.sessions[
                        context.kernel_session_id
                    ].status.value

                notebooks.append(
                    {
                        "notebook_path": nb_path,
                        "notebook_title": context.notebook_title,
                        "created_at": context.created_at,
                        "kernel_status": kernel_status,
                        "kernel_session_id": context.kernel_session_id,
                    }
                )
        logger.debug(f"Listing notebooks for session_id: {session_id} {notebooks}")

        return {"success": True, "notebooks": notebooks, "count": len(notebooks)}

    @tool
    async def manage_kernel(
        self,
        notebook_path: str,
        action: Literal[
            "restart", "interrupt", "status", "variables", "shutdown", "delete"
        ],
    ) -> dict:
        """
        Manage kernel for a notebook (unified kernel operations)

        Args:
            notebook_path: Path to notebook
            action: Kernel operation
                - "restart": Restart kernel (clears all state)
                - "interrupt": Interrupt running execution
                - "status": Get kernel status information
                - "variables": Get current kernel variables
                - "shutdown": Shutdown kernel (context preserved, can restart later)
                - "delete": Delete context completely (shutdown kernel + remove from memory)

        Returns:
            dict with success status and action-specific data
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        context = self._get_context(notebook_path, session_id)
        if not context:
            return {"success": False, "error": "Notebook not opened"}

        try:
            if action == "restart":
                # Restart kernel
                result = await self.kernel_toolset.restart_session(
                    context.kernel_session_id
                )

                # Clear completion context
                self.completion_service.clear_session_context(context.kernel_session_id)

                return {
                    "success": result["success"],
                    "action": "restart",
                    "notebook_path": notebook_path,
                    "kernel_session_id": context.kernel_session_id,
                }

            elif action == "interrupt":
                # Interrupt execution
                result = await self.kernel_toolset.interrupt_session(
                    context.kernel_session_id
                )

                return {
                    "success": result["success"],
                    "action": "interrupt",
                    "notebook_path": notebook_path,
                    "kernel_session_id": context.kernel_session_id,
                }

            elif action == "status":
                # Get kernel status
                if context.kernel_session_id in self.kernel_toolset.sessions:
                    kernel_session = self.kernel_toolset.sessions[
                        context.kernel_session_id
                    ]
                    return {
                        "success": True,
                        "action": "status",
                        "notebook_path": notebook_path,
                        "kernel_session_id": context.kernel_session_id,
                        "kernel_status": kernel_session.status.value,
                        "execution_count": kernel_session.execution_count,
                        "kernel_spec": context.kernel_spec,
                    }
                else:
                    return {
                        "success": False,
                        "error": "Kernel session not found",
                        "action": "status",
                    }

            elif action == "variables":
                # Get variables
                result = await self.kernel_toolset.get_variables(
                    context.kernel_session_id
                )
                result["action"] = "variables"
                result["notebook_path"] = notebook_path
                result["kernel_session_id"] = context.kernel_session_id
                return result

            elif action == "shutdown":
                # Shutdown kernel but keep context
                result = await self.kernel_toolset.shutdown_session(
                    context.kernel_session_id
                )

                # Clear completion context
                self.completion_service.clear_session_context(context.kernel_session_id)

                return {
                    "success": result["success"],
                    "action": "shutdown",
                    "notebook_path": notebook_path,
                    "kernel_session_id": context.kernel_session_id,
                    "message": "Kernel shutdown, context preserved (can restart)",
                }

            elif action == "delete":
                # Delete context completely (shutdown + remove from memory)

                # 1. Shutdown kernel session
                result = await self.kernel_toolset.shutdown_session(
                    context.kernel_session_id
                )

                # 2. Clear completion context
                self.completion_service.clear_session_context(context.kernel_session_id)

                # 3. Remove context from memory
                key = (notebook_path, session_id)
                if key in self.notebook_contexts:
                    del self.notebook_contexts[key]

                # 4. Persist changes
                await self._save_contexts()

                logger.info(f"Deleted notebook context: {notebook_path} @ {session_id}")

                return {
                    "success": True,
                    "action": "delete",
                    "notebook_path": notebook_path,
                    "kernel_session_id": context.kernel_session_id,
                    "kernel_result": result,
                    "message": "Context deleted (kernel shutdown + removed from memory)",
                }

            else:
                return {"success": False, "error": f"Invalid action: {action}"}

        except Exception as e:
            logger.error(f"manage_kernel failed: {e}")
            return {"success": False, "error": str(e), "action": action}

    # ═══════════════════════════════════════════════════════════
    # Event Subscription API (Backend/UI Streaming - Not @tool)
    # ═══════════════════════════════════════════════════════════

    async def subscribe_notebook_events(
        self, notebook_path: str, client_id: str, callback=None
    ) -> dict:
        """
        Subscribe to notebook real-time IOPub events (Backend use only, not exposed to agents)

        This method allows backend/UI to receive real-time execution events:
        - stream (stdout/stderr)
        - display_data (plots, images)
        - execute_result (return values)
        - error (exceptions)
        - status (kernel busy/idle)

        Args:
            notebook_path: Path to notebook
            client_id: Unique client identifier for this subscription
            callback: Optional callback function for events

        Returns:
            dict with success status and subscription info

        Example:
            # Backend subscribes for UI streaming
            await toolset.subscribe_notebook_events(
                notebook_path="analysis.ipynb",
                client_id="ui-client-123",
                callback=send_to_websocket
            )

            # Execute cell (events streamed to callback)
            # UI can fire-and-forget (don't await)
            execute_cell("analysis.ipynb", cell_id, code)

        Note:
            - NOT a @tool (backend internal use only)
            - Requires event_bus to be initialized
            - Client must unsubscribe when done
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        # Get context
        context = self._get_context(notebook_path, session_id)
        if not context:
            return {"success": False, "error": f"Notebook not opened: {notebook_path}"}

        # Check event bus
        if not self.event_bus:
            return {
                "success": False,
                "error": "Event bus not initialized (no streaming support)",
            }

        try:
            # Subscribe to kernel's IOPub channel
            result = await self.kernel_toolset.subscribe_iopub(
                context.kernel_session_id, client_id, callback
            )

            if result["success"]:
                logger.info(
                    f"Subscribed to notebook events: {notebook_path} "
                    f"(client={client_id}, kernel={context.kernel_session_id})"
                )

            return {
                "success": result["success"],
                "notebook_path": notebook_path,
                "kernel_session_id": context.kernel_session_id,
                "client_id": client_id,
                "subscription_info": result,
            }

        except Exception as e:
            logger.error(f"Failed to subscribe to notebook events: {e}")
            return {"success": False, "error": str(e)}

    async def unsubscribe_notebook_events(
        self, notebook_path: str, client_id: str
    ) -> dict:
        """
        Unsubscribe from notebook IOPub events (Backend use only, not exposed to agents)

        Cleanup subscription created by subscribe_notebook_events().
        Should be called when client disconnects or no longer needs events.

        Args:
            notebook_path: Path to notebook
            client_id: Client identifier used in subscribe

        Returns:
            dict with success status

        Example:
            # Client disconnects, cleanup subscription
            await toolset.unsubscribe_notebook_events(
                notebook_path="analysis.ipynb",
                client_id="ui-client-123"
            )

        Note:
            - NOT a @tool (backend internal use only)
            - Safe to call even if subscription doesn't exist
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        # Get context
        context = self._get_context(notebook_path, session_id)
        if not context:
            # Context might have been deleted, try to handle gracefully
            logger.warning(
                f"Context not found for {notebook_path}, "
                f"cannot unsubscribe (may already be cleaned up)"
            )
            return {
                "success": False,
                "error": f"Notebook context not found: {notebook_path}",
            }

        # Check event bus
        if not self.event_bus:
            return {"success": False, "error": "Event bus not initialized"}

        try:
            # Unsubscribe from kernel's IOPub channel
            result = await self.kernel_toolset.unsubscribe_iopub(
                context.kernel_session_id, client_id
            )

            if result["success"]:
                logger.info(
                    f"Unsubscribed from notebook events: {notebook_path} "
                    f"(client={client_id}, kernel={context.kernel_session_id})"
                )

            return {
                "success": result["success"],
                "notebook_path": notebook_path,
                "kernel_session_id": context.kernel_session_id,
                "client_id": client_id,
                "unsubscription_info": result,
            }

        except Exception as e:
            logger.error(f"Failed to unsubscribe from notebook events: {e}")
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════
    # Code Intelligence Tools (Frontend Only - exclude=True)
    # ═══════════════════════════════════════════════════════════

    @tool(exclude=True)
    async def complete_request(
        self, notebook_path: str, code: str, cursor_pos: int
    ) -> dict:
        """
        Get code completion suggestions (Frontend only)

        This tool is for interactive code editor features only.
        Not exposed to LLM agents.

        Args:
            notebook_path: Path to notebook
            code: Code to complete
            cursor_pos: Cursor position in code

        Returns:
            dict with completion suggestions
        """
        session_id = self.get_session_id()

        try:
            # Get context if exists, use default otherwise
            context = (
                self._get_context(notebook_path, session_id) if session_id else None
            )
            effective_session_id = context.kernel_session_id if context else "default"

            return await self.completion_service.get_completions(
                code=code,
                cursor_pos=cursor_pos,
                session_id=effective_session_id,
                context_code="",  # Jedi service uses its internal session context
            )

        except Exception as e:
            logger.error(f"complete_request failed: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def inspect_request(
        self, notebook_path: str, code: str, cursor_pos: int
    ) -> dict:
        """
        Get hover documentation/inspection (Frontend only)

        This tool is for interactive code editor features only.
        Not exposed to LLM agents.

        Args:
            notebook_path: Path to notebook
            code: Code to inspect
            cursor_pos: Cursor position in code

        Returns:
            dict with inspection/documentation data
        """
        session_id = self.get_session_id()

        try:
            # Get context if exists, use default otherwise
            context = (
                self._get_context(notebook_path, session_id) if session_id else None
            )
            effective_session_id = context.kernel_session_id if context else "default"

            return await self.completion_service.get_inspection(
                code=code,
                cursor_pos=cursor_pos,
                session_id=effective_session_id,
                context_code="",  # Jedi service uses its internal session context
            )

        except Exception as e:
            logger.error(f"inspect_request failed: {e}")
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════
    # Utility Tools
    # ═══════════════════════════════════════════════════════════

    @tool
    async def get_notebook_info(
        self, notebook_path: str, include_variables: bool = False
    ) -> dict:
        """
        Get notebook information with execution status and output types (VSCode-enhanced)

        Provides comprehensive notebook summary similar to VSCode's getNotebookSummary:
        - Cell list with execution status
        - Output mime types for each cell
        - Language information for code cells
        - Optional kernel variables

        Args:
            notebook_path: Path to notebook file
            include_variables: Include kernel variables in response (default: False)

        Returns:
            dict with notebook summary, cell info, and optional variables
        """
        session_id = self.get_session_id()

        read_result = await self.notebook_contents.read_notebook(notebook_path)
        if not read_result["success"]:
            return read_result

        notebook = read_result["notebook"]

        # Get context if exists
        context = self._get_context(notebook_path, session_id) if session_id else None

        # Helper: Get execution status for a cell
        def get_execution_status(cell: dict) -> str:
            """Determine execution status: not_executed, success, or error"""
            if cell.get("cell_type") != "code":
                return "not_applicable"

            # Not executed if no execution_count
            if cell.get("execution_count") is None:
                return "not_executed"

            # Check outputs for errors
            outputs = cell.get("outputs", [])
            for output in outputs:
                if output.get("output_type") == "error":
                    return "error"

            # Executed successfully
            return "success"

        # Helper: Get output mime types
        def get_output_mime_types(cell: dict) -> list[str]:
            """Extract all mime types from cell outputs"""
            mime_types = set()
            outputs = cell.get("outputs", [])

            for output in outputs:
                output_type = output.get("output_type")

                if output_type == "stream":
                    mime_types.add("text/plain")
                elif output_type == "display_data" or output_type == "execute_result":
                    # Extract mime types from data dict
                    data = output.get("data", {})
                    mime_types.update(data.keys())
                elif output_type == "error":
                    mime_types.add("application/vnd.code.notebook.error")

            return sorted(list(mime_types))

        # Build cell info list with enhanced fields
        cells_info = []
        for idx, cell in enumerate(notebook.get("cells", [])):
            cell_info = {
                "cell_id": cell.get("id"),
                "cell_index": idx,
                "cell_type": cell.get("cell_type"),
                "execution_count": cell.get("execution_count"),
                "has_output": len(cell.get("outputs", [])) > 0,
                "execution_status": get_execution_status(cell),
                "output_mime_types": get_output_mime_types(cell),
            }

            # Add language for code cells
            if cell.get("cell_type") == "code":
                # Get language from notebook metadata or default to python
                metadata = notebook.get("metadata", {})
                language_info = metadata.get("language_info", {})
                cell_info["language"] = language_info.get("name", "python")

            cells_info.append(cell_info)

        # Base response
        result = {
            "success": True,
            "notebook_path": notebook_path,
            "has_context": context is not None,
            "cell_count": len(cells_info),
            "cells": cells_info,
            "kernel_status": self.kernel_toolset.sessions[
                context.kernel_session_id
            ].status.value
            if context
            else None,
            "kernel_session_id": context.kernel_session_id if context else None,
        }

        # Optionally include variables
        if include_variables and context:
            try:
                vars_result = await self.kernel_toolset.get_variables(
                    context.kernel_session_id
                )
                if vars_result.get("success"):
                    result["variables"] = vars_result.get("variables", [])
            except Exception as e:
                logger.warning(f"Failed to get variables: {e}")
                result["variables"] = []

        return result

    # ═══════════════════════════════════════════════════════════
    # Internal Methods
    # ═══════════════════════════════════════════════════════════

    def _get_operator_context(self) -> str:
        """
        Determine who is operating: 'user', 'agent', or 'system'

        This helps streaming distinguish between user-initiated executions,
        agent-initiated executions, and system operations.
        """
        # TODO: Implement logic to detect caller context
        # For now, default to 'user' (can be enhanced with context tracking)
        return "user"

    async def _execute_and_update(
        self, kernel_session_id: str, notebook_path: str, cell_id: str, code: str
    ) -> dict:
        """Execute code and update notebook using cell_id"""
        try:
            # Build execution_metadata for Jupyter Protocol
            # This metadata will be attached to IOPub messages for accurate cell matching
            execution_metadata = {
                "cell_id": cell_id,
                "notebook_path": notebook_path,
                "operated_by": self._get_operator_context(),
            }

            # Execute with metadata
            exec_result = await self.kernel_toolset.execute_request(
                code,
                kernel_session_id,
                silent=False,
                user_expressions={},
                execution_metadata=execution_metadata,
            )

            # Update notebook with results
            outputs = exec_result.get("outputs", [])
            execution_count = exec_result.get("execution_count")

            # Remove frontend-specific 'id' field
            notebook_outputs = [
                {k: v for k, v in output.items() if k != "id"} for output in outputs
            ]

            # Get timing metadata
            execution_timing = exec_result.get("metadata", {}).get("execution", {})

            # Update notebook file using cell_id
            await self.notebook_contents.update_cell_outputs(
                path=notebook_path,
                cell_id=cell_id,
                outputs=notebook_outputs,
                execution_count=execution_count,
                execution_timing=execution_timing,
            )

            # Update completion context
            if exec_result.get("success") and code.strip():
                self.completion_service.update_session_context(kernel_session_id, code)

            # Add notebook-specific fields
            exec_result["notebook_path"] = notebook_path

            return exec_result

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return {"success": False, "error": str(e), "notebook_path": notebook_path}

    async def cleanup(self):
        """Cleanup all resources"""
        try:
            await self._save_contexts()

            if self.event_bus and hasattr(self.event_bus, "cleanup"):
                await self.event_bus.cleanup()

            if self.kernel_toolset:
                await self.kernel_toolset.cleanup()

            if self.notebook_contents and hasattr(self.notebook_contents, "cleanup"):
                await self.notebook_contents.cleanup()

            if self.completion_service:
                for (_, _), context in self.notebook_contexts.items():
                    self.completion_service.clear_session_context(
                        context.kernel_session_id
                    )

            self.notebook_contexts.clear()

            logger.info("IntegratedNotebookToolSet cleanup complete")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")


# Export
__all__ = ["IntegratedNotebookToolSet"]
