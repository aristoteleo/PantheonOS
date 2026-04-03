"""
Integrated Notebook ToolSet

Tools for working with Jupyter notebooks:
- create_notebook: Create or open a notebook file
- execute_cell: Run code in a cell
- add_cell, update_cell, delete_cell, move_cell: Edit notebook structure
- read_cell: Get complete cell data (deprecated, use read_cells instead)
- read_cells: Get all cells with optional content details
- list_notebooks: List available notebooks
- manage_kernel: Manage kernel state (restart, interrupt, check status, etc.)

Frontend-only tools (not for agents):
- complete_request: Code completion
- inspect_request: Documentation lookup
- read_notebook: Full notebook JSON
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
from .jupyter_kernel import JupyterKernelToolSet
from .notebook_contents import NotebookContentsToolSet
from .handlers import NatsStreamHandler, FileLogHandler


# rpy2 initialization code - executed once per kernel session when first %%R cell is detected
RPY2_INIT_CODE = '''
try:
    import rpy2
    get_ipython().run_line_magic('load_ext', 'rpy2.ipython')
    # Configure R options: CRAN mirror and parallel compilation
    get_ipython().run_cell_magic('R', '', """
options(
  repos = c(CRAN = "https://cloud.r-project.org"),
  Ncpus = max(1, parallel::detectCores() - 1)  # Enable parallel compilation
)
""")
except ImportError:
    print("⚠️ rpy2 not installed. Run: pip install rpy2")
except Exception as e:
    print(f"⚠️ rpy2 init failed: {e}")
'''


@dataclass
class NotebookContext:
    """Internal context for notebook operations"""

    notebook_path: str
    session_id: str
    kernel_session_id: str
    created_at: str
    notebook_title: str
    kernel_spec: str = "python3"
    notebook_is_new: bool = True
    rpy2_initialized: bool = False  # Runtime state: whether rpy2 extension is loaded


class IntegratedNotebookToolSet(ToolSet):
    """Notebook operations toolset for Jupyter notebooks."""

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        remote_backend: Optional[RemoteBackend] = None,
        streaming_mode: Literal["auto", "remote", "local"] = "auto",
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.workdir = workdir or Path.cwd().as_posix()
        self.remote_backend = remote_backend
        self.streaming_mode = streaming_mode
        self.streaming_enabled = False
        self.nats_handler: Optional["NatsStreamHandler"] = None

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

        # Notebook file locks to prevent concurrent edit operations
        import asyncio
        self._notebook_locks: Dict[str, asyncio.Lock] = {}

    async def run_setup(self):
        """Setup toolset"""
        await super().run_setup()

        # Load settings
        from pantheon.settings import get_settings
        settings = get_settings()

        # Decide whether streaming should be active for this toolset
        logger.debug(f"IntegratedNotebook: streaming_mode={self.streaming_mode}, remote_backend={self.remote_backend}")
        if self.streaming_mode == "local":
            allow_streaming = False
            logger.debug("IntegratedNotebook: streaming disabled (mode=local)")
        else:  # auto/remote both allow streaming unless explicitly disabled
            allow_streaming = True
            logger.debug(f"IntegratedNotebook: streaming allowed (mode={self.streaming_mode})")

        # Initialize remote backend only when streaming is allowed
        if allow_streaming and self.remote_backend is None:
            try:
                self.remote_backend = RemoteBackendFactory.create_backend()
                logger.info("Auto-created remote backend from environment")
            except Exception as e:
                logger.warning(f"No remote backend available: {e}")

        # Setup child toolsets
        await self.kernel_toolset.run_setup()
        await self.notebook_contents.run_setup()

        # Register NATS stream handler (if remote backend exists)
        logger.debug(f"IntegratedNotebook: allow_streaming={allow_streaming}, remote_backend={self.remote_backend}")
        if allow_streaming and self.remote_backend:
            self.nats_handler = NatsStreamHandler(self.remote_backend)
            await self.kernel_toolset.subscribe("nats_stream", self.nats_handler)
            self.streaming_enabled = True
            logger.info("Registered NatsStreamHandler")
        else:
            logger.info(f"NATS streaming NOT enabled (allow_streaming={allow_streaming}, has_backend={self.remote_backend is not None})")

        # Register file log handler (if enabled via settings)
        if settings.enable_notebook_execution_logging:
            log_dir = settings.logs_dir / "notebook"
            log_handler = FileLogHandler(log_dir)
            await self.kernel_toolset.subscribe("file_log", log_handler)
            logger.info(f"Registered FileLogHandler: {log_dir}")

        # Unified listener removed - handlers are called directly in execute_request's output_hook

        # Load persisted contexts
        await self._load_contexts()

        logger.info("IntegratedNotebookToolSet setup complete")

    async def _load_contexts(self):
        """Load notebook contexts from persistence"""
        try:
            if self.persistence_file.exists():
                with open(self.persistence_file, "r", encoding="utf-8") as f:
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
                    "notebook_is_new": context.notebook_is_new,
                }

            data = {
                "contexts": contexts_data,
                "last_updated": datetime.now().isoformat(),
            }

            with open(self.persistence_file, "w", encoding="utf-8") as f:
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
            notebook_file_is_new = False
            if not read_result["success"]:
                create_result = await self.notebook_contents.create_notebook(
                    notebook_path, "New Notebook"
                )
                if not create_result["success"]:
                    raise Exception(
                        f"Failed to create notebook: {create_result['error']}"
                    )
                notebook_file_is_new = True

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
                notebook_is_new=notebook_file_is_new,
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

                # Reset rpy2 initialization state (new kernel doesn't have extension loaded)
                context.rpy2_initialized = False

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

    async def _resolve_cell(
        self,
        notebook_path: str,
        cell_id: str,
        source_hint: str | None = None,
    ) -> tuple[Optional[int], Optional[dict], Optional[str]]:
        """Resolve a cell reference, with automatic ID assignment and source fallback.

        Resolution order:
        1. Read notebook and ensure all cells have stable IDs (auto-assign if missing)
        2. Exact cell_id match
        3. source_hint fallback (unique match required)
        4. Return structured error with available cells

        Returns:
            (cell_index, cell_data, error_message)
            On success: (index, cell, None)
            On failure: (None, None, "error string with available cells")
        """
        read_result = await self.notebook_contents.read_notebook(notebook_path)
        if not read_result["success"]:
            return None, None, read_result.get("error", "Failed to read notebook")

        notebook = read_result["notebook"]
        resolved_path = read_result.get("file_path")

        # Auto-assign stable IDs to cells that lack them, then persist once
        if resolved_path and any(
            not cell.get("id") for cell in notebook.get("cells", [])
        ):
            changed = await self.notebook_contents._ensure_cell_ids_and_upgrade(
                Path(resolved_path), notebook
            )
            if changed:
                await self.notebook_contents._save_notebook(Path(resolved_path), notebook)

        # Delegate to low-level _find_cell (supports source_hint fallback)
        idx, cell = self.notebook_contents._find_cell(notebook, cell_id, source_hint)
        if idx is not None:
            return idx, cell, None

        # Build informative error with available cells
        cells = notebook.get("cells", [])
        cell_summaries = []
        for i, c in enumerate(cells):
            cid = c.get("id", "?")
            src = self.notebook_contents._format_source(c.get("source", ""))
            preview = src[:60].replace("\n", " ")
            if len(src) > 60:
                preview += "..."
            cell_summaries.append(f"  [{i}] {cid}: {preview}")

        available = "\n".join(cell_summaries[:15])
        hint = (
            " You MUST call read_cells first to get current cell IDs."
            if not source_hint
            else " The source_hint also did not match any cell uniquely."
        )
        error = (
            f"Cell '{cell_id}' not found.{hint}\n"
            f"Available cells ({len(cells)}):\n{available}"
        )
        return None, None, error

    def _validate_cell_id(self, cell_id: str) -> tuple[bool, str]:
        """
        Validate cell_id format for Jupyter notebook compatibility

        Jupyter notebook cell_id requirements:
        - Must be non-empty string
        - Length: 1-256 characters
        - Allowed characters: alphanumeric, dash (-), underscore (_)

        Returns:
            tuple[bool, str]: (is_valid, error_message)
        """
        if not cell_id or not isinstance(cell_id, str):
            return False, "cell_id must be non-empty string"

        if len(cell_id) > 256:
            return (
                False,
                f"cell_id too long ({len(cell_id)} > 256 characters)",
            )

        # Check for invalid characters
        invalid_chars = set(c for c in cell_id if not (c.isalnum() or c in "-_"))
        if invalid_chars:
            return (
                False,
                f"cell_id contains invalid characters: {sorted(invalid_chars)}. "
                f"Only alphanumeric, dash (-), and underscore (_) allowed",
            )

        return True, ""

    def _get_notebook_lock(self, notebook_path: str):
        """Get or create lock for notebook file operations (thread-safe)"""
        import asyncio
        return self._notebook_locks.setdefault(notebook_path, asyncio.Lock())

    # ═══════════════════════════════════════════════════════════
    # Internal Execute Logic (shared by execute_cell, add_cell, update_cell)
    # ═══════════════════════════════════════════════════════════

    async def _execute_cell_internal(
        self,
        notebook_path: str,
        cell_id: str,
        session_id: str,
    ) -> dict:
        """
        Internal execute logic, reusable by add_cell/update_cell/execute_cell.
        
        This method handles:
        - Getting or creating kernel context
        - Finding cell content
        - rpy2 auto-initialization for R magic
        - Executing code and updating notebook
        
        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier
            session_id: Agent session ID
            
        Returns:
            dict with success, output, kernel_session_id, etc.
        """
        try:
            # Get or create context (this triggers kernel if needed)
            context = await self._get_or_create_context(notebook_path, session_id)

            # Find existing cell
            cell_index, cell_data, resolve_error = await self._resolve_cell(
                notebook_path, cell_id
            )
            if cell_index is None:
                return {"success": False, "error": resolve_error}

            # Use canonical cell_id from the resolved cell
            cell_id = cell_data.get("id", cell_id)

            # Get existing code from cell
            if cell_data and "source" in cell_data:
                code = self.notebook_contents._format_source(cell_data["source"])
            else:
                code = ""

            # Detect R magic and auto-initialize rpy2 if needed
            code_stripped = code.strip()
            needs_rpy2 = (
                code_stripped.startswith("%%R") or
                code_stripped.startswith("%R ") or
                code_stripped.startswith("%R\n") or
                "\n%R " in code
            )

            if needs_rpy2 and not context.rpy2_initialized:
                logger.info(f"Detected R magic, initializing rpy2 for session {context.kernel_session_id[:8]}")
                init_result = await self.kernel_toolset.execute_request(
                    RPY2_INIT_CODE,
                    context.kernel_session_id,
                    silent=True,
                    store_history=False,
                    execution_metadata={"operated_by": "system"},
                )
                if init_result.get("success"):
                    context.rpy2_initialized = True
                    logger.info("rpy2 initialized successfully")
                else:
                    logger.warning(f"rpy2 initialization failed: {init_result.get('error')}")

            # Execute with cell_id (not cell_index for stability)
            exec_result = await self._execute_and_update(
                context.kernel_session_id, notebook_path, cell_id, code
            )
            # Return stable identifiers (cell_id + kernel_session_id + notebook_path)
            exec_result["cell_id"] = cell_id
            exec_result["kernel_session_id"] = context.kernel_session_id
            exec_result["notebook_path"] = notebook_path

            # Remove metadata to save tokens (it is already saved to file)
            exec_result.pop("metadata", None)

            return exec_result

        except Exception as e:
            logger.error(f"_execute_cell_internal failed: {e}")
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════
    # Core Tools
    # ═══════════════════════════════════════════════════════════

    @tool(exclude=True)
    async def create_notebook(self, notebook_path: str) -> dict:
        """
        Create or open a notebook file.

        Args:
            notebook_path: Path to notebook file

        Returns:
            dict with:
            - success: True if notebook was created or already exists
            - notebook_path: Path to the notebook
            - action: "created" if new file was created, "opened" if already exists
            - kernel_session_id: Present if notebook has an active kernel session
        """
        session_id = self.get_session_id()

        try:
            # Check if notebook exists
            read_result = await self.notebook_contents.read_notebook(notebook_path)

            if read_result["success"]:
                # Notebook already exists
                action = "opened"
                logger.debug(f"Notebook already exists: {notebook_path}")
            else:
                # Create new notebook
                create_result = await self.notebook_contents.create_notebook(
                    notebook_path, "New Notebook"
                )
                if not create_result["success"]:
                    return {
                        "success": False,
                        "error": f"Failed to create notebook: {create_result.get('error', 'Unknown error')}",
                    }
                action = "created"
                logger.info(f"Created new notebook: {notebook_path}")

            result = {
                "success": True,
                "notebook_path": notebook_path,
                "action": action,
            }

            # Add kernel_session_id if context exists
            if session_id:
                context = self._get_context(notebook_path, session_id)
                if context:
                    result["kernel_session_id"] = context.kernel_session_id

            return result

        except Exception as e:
            logger.error(f"create_notebook failed: {e}")
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def execute_cell(
        self,
        notebook_path: str,
        cell_id: str,
    ) -> dict:
        """
        Execute a cell's existing content.

        Supported syntax in code cells:
        - Python code (default)
        - R language: `%%R` (cell) or `%R` (line) magic
          - Pass data: `%%R -i py_var` (Python→R), `%%R -o r_var` (R→Python)
        - Shell commands: `%%bash` or `%%sh`
        - Other magics: `%%time`, `%%html`, `%matplotlib inline`, etc.

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier

        Returns:
            dict with:
            - success: True if execution succeeded
            - outputs: Execution results/outputs (list of nbformat output nodes)
            - kernel_session_id: Kernel session ID
        
        Tips:
            - Split long operations into multiple cells  (load → preprocess → compute)
            - Use parallel computing when available (scanpy: n_jobs=-1 etc...)
            - Use manage_kernel(action="interrupt") to stop long-running cells
            - IMPORTANT: To install R packages, use `%%R install.packages('pkg')`. Shell `Rscript` installs may not be visible.
        
        Note:
            Only one execution can run at a time per kernel session. If you call
            execute_cell, add_cell(execute=True), or update_cell(execute=True)
            concurrently for the same notebook, subsequent calls will return a
            "Kernel is busy" error. Wait for the current execution to complete
            or use manage_kernel(action='interrupt') to stop it.
        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        return await self._execute_cell_internal(notebook_path, cell_id, session_id)

    @tool(exclude=True)
    async def add_cell(
        self,
        notebook_path: str,
        cell_type: str = "code",
        content: str = "",
        cell_id: Optional[str] = None,
        position: Optional[str] = None,
        execute: bool = False,
    ) -> dict:
        """
        Add a new cell to the notebook.

        Args:
            notebook_path: Path to notebook file
            cell_type: Type of cell:
                - "code": Executable code (Python, R via %%R, shell via %%bash, etc.)
                - "markdown": Rich text with Markdown, LaTeX math ($...$)
                - "raw": Unformatted text
            content: Cell content/source code
            cell_id: Optional cell identifier (auto-generated if not provided)
            position: Insertion position.
                     - None: Append to end
                     - "0", "1", "-1": Insert at specific index (0-based)
                     - "cell_id": Insert AFTER the cell with this ID
            execute: Execute the cell after adding.
                    **RECOMMENDED: Use execute=True for code cells** to avoid
                    a separate execute_cell call. Skipped for markdown/raw cells.

        Returns:
            dict with:
            - success: True if cell was added
            - cell_id: The cell identifier
            - notebook_path: Path to the notebook
            - execution: (only when execute=True and cell_type="code") Complete execution result dict containing:
                - success: True if execution succeeded
                - outputs: Execution outputs (list of nbformat output nodes)
                - execution_count: Kernel execution count
                - kernel_session_id: Kernel session ID
                - cell_id: The cell identifier
                - memory_hint: (optional) Memory warning if usage > 75%
                - error: Error message if execution failed
        
        Note:
            Only one execution can run at a time per kernel session. Concurrent
            execution calls (execute_cell, add_cell(execute=True), update_cell(execute=True))
            for the same notebook will return "Kernel is busy" error.
        """
        session_id = self.get_session_id()
        added_cell_id = None

        # File lock only covers edit operation
        async with self._get_notebook_lock(notebook_path):
            try:
                # NOTE: When execute=False, we don't trigger kernel (lightweight CRUD)
                # Get context only if exists (don't create kernel for simple edit)
                context = self._get_context(notebook_path, session_id) if session_id else None

                # Call notebook_contents API
                result = await self.notebook_contents.add_cell(
                    path=notebook_path,
                    cell_type=cell_type,
                    source=content,
                    cell_id=cell_id,
                    position=position,
                )

                # Add context information only if context exists
                if result["success"]:
                    result.pop("file_path", None)
                    result["notebook_path"] = notebook_path
                    if context:
                        result["kernel_session_id"] = context.kernel_session_id
                    added_cell_id = result.get("cell_id")

            except Exception as e:
                logger.error(f"add_cell failed: {e}")
                return {"success": False, "error": str(e)}

        # Execute OUTSIDE file lock - uses separate execution lock
        if result["success"] and execute and cell_type == "code" and session_id and added_cell_id:
            exec_result = await self._execute_cell_internal(
                notebook_path, added_cell_id, session_id
            )
            exec_result.pop("notebook_path", None)
            result["execution"] = exec_result
            # Hoist image URIs to top level so step message callbacks can find them
            if "base64_uri" in exec_result:
                result["base64_uri"] = exec_result["base64_uri"]
                result["hidden_to_model"] = ["base64_uri"]

        return result

    @tool(exclude=True)
    async def update_cell(
        self,
        notebook_path: str,
        cell_id: str,
        content: str,
        old_content: Optional[str] = None,
        execute: bool = False,
    ) -> dict:
        """
        Update cell content (supports full replacement or partial replacement).

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier
            content: New cell content
            old_content: Optional. If provided, will replace old_content with content.
                        If None/empty, will replace entire cell content.
            execute: Execute the cell after updating.
                    **RECOMMENDED: Use execute=True for code cells** to run the
                    updated code immediately. Skipped for non-code cells.

        Returns:
            dict with:
            - success: True if cell was updated
            - cell_id: The cell identifier
            - replacements: Number of replacements made (only when old_content provided)
            - execution: (only when execute=True and cell is code type) Complete execution result dict containing:
                - success: True if execution succeeded
                - outputs: Execution outputs (list of nbformat output nodes)
                - execution_count: Kernel execution count
                - kernel_session_id: Kernel session ID
                - cell_id: The cell identifier
                - memory_hint: (optional) Memory warning if usage > 75%
                - error: Error message if execution failed

        Examples:
            # Update and execute in one call (recommended)
            update_cell(notebook_path, cell_id, content="x = 1", execute=True)

            # Partial replacement with execution
            update_cell(notebook_path, cell_id, 
                       content="n_neighbors=30",
                       old_content="n_neighbors=15",
                       execute=True)
            
            # Update only (no execution)
            update_cell(notebook_path, cell_id, content="x = 1")
        
        Note:
            Only one execution can run at a time per kernel session. Concurrent
            execution calls (execute_cell, add_cell(execute=True), update_cell(execute=True))
            for the same notebook will return "Kernel is busy" error.
        """
        session_id = self.get_session_id()
        should_execute = False
        cell_type = "code"

        # File lock only covers edit operation
        async with self._get_notebook_lock(notebook_path):
            try:
                # NOTE: When execute=False, we don't trigger kernel (lightweight CRUD)
                # Get context only if exists (don't create kernel for simple edit)
                context = self._get_context(notebook_path, session_id) if session_id else None

                # Read cell data for partial replacement and cell type check
                cell_index, cell_data, resolve_error = await self._resolve_cell(
                    notebook_path, cell_id, source_hint=old_content
                )
                if cell_index is None:
                    return {"success": False, "error": resolve_error}

                # Use canonical cell_id from the resolved cell
                cell_id = cell_data.get("id", cell_id)
                
                cell_type = cell_data.get("cell_type", "code") if cell_data else "code"
                replacement_count = None

                # Partial replacement mode: replace old_content with content
                if old_content:
                    # Get source
                    source = self.notebook_contents._format_source(cell_data.get("source", ""))

                    # Check if old_content exists
                    if old_content not in source:
                        return {
                            "success": False,
                            "error": f"old_content not found in cell {cell_id}"
                        }

                    # Count replacements
                    replacement_count = source.count(old_content)

                    # Perform replacement
                    new_source = source.replace(old_content, content)

                    # Update content variable for the actual update
                    content = new_source

                # Call notebook_contents API with final content
                result = await self.notebook_contents.update_cell(
                    path=notebook_path,
                    cell_id=cell_id,
                    source=content,
                )

                # Add context information
                if result["success"]:
                    result.pop("file_path", None)
                    result["notebook_path"] = notebook_path
                    if context:
                        result["kernel_session_id"] = context.kernel_session_id

                    if replacement_count is not None:
                        result["replacements"] = replacement_count
                    
                    should_execute = execute and cell_type == "code" and session_id

            except Exception as e:
                logger.error(f"update_cell failed: {e}")
                return {"success": False, "error": str(e)}

        # Execute OUTSIDE file lock - uses separate execution lock
        if result["success"] and should_execute:
            exec_result = await self._execute_cell_internal(
                notebook_path, cell_id, session_id
            )
            exec_result.pop("notebook_path", None)
            result["execution"] = exec_result
            # Hoist image URIs to top level so step message callbacks can find them
            if "base64_uri" in exec_result:
                result["base64_uri"] = exec_result["base64_uri"]
                result["hidden_to_model"] = ["base64_uri"]

        return result

    @tool(exclude=True)
    async def delete_cell(
        self,
        notebook_path: str,
        cell_id: str,
    ) -> dict:
        """
        Delete a cell from the notebook.

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier

        Returns:
            dict with:
            - success: True if cell was deleted
            - cell_id: The cell identifier
            - notebook_path: Path to the notebook
        """
        session_id = self.get_session_id()

        async with self._get_notebook_lock(notebook_path):
            try:
                # Get context if exists (don't create kernel for simple edit)
                context = self._get_context(notebook_path, session_id) if session_id else None

                # Resolve cell_id (handles stale IDs, auto-assigns missing IDs)
                _, cell_data, resolve_error = await self._resolve_cell(
                    notebook_path, cell_id
                )
                if resolve_error:
                    return {"success": False, "error": resolve_error}
                canonical_id = cell_data.get("id", cell_id)

                # Call notebook_contents API
                result = await self.notebook_contents.delete_cell(
                    path=notebook_path,
                    cell_id=canonical_id,
                )

                # Add context information only if context exists
                if result["success"]:
                    result.pop("file_path", None)
                    result["notebook_path"] = notebook_path
                    if context:
                        result["kernel_session_id"] = context.kernel_session_id

                return result

            except Exception as e:
                logger.error(f"delete_cell failed: {e}")
                return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def move_cell(
        self,
        notebook_path: str,
        cell_id: str,
        below_cell_id: Optional[str] = None,
    ) -> dict:
        """
        Move a cell to a different position in the notebook.

        Args:
            notebook_path: Path to notebook file
            cell_id: Cell identifier to move
            below_cell_id: Cell ID to move after (moves to top if not provided)

        Returns:
            dict with:
            - success: True if cell was moved
            - cell_id: The cell identifier
            - notebook_path: Path to the notebook
        """
        session_id = self.get_session_id()

        async with self._get_notebook_lock(notebook_path):
            try:
                # Get context if exists (don't create kernel for simple edit)
                context = self._get_context(notebook_path, session_id) if session_id else None

                # Resolve cell references (handles stale IDs, auto-assigns missing IDs)
                _, cell_data, resolve_error = await self._resolve_cell(
                    notebook_path, cell_id
                )
                if resolve_error:
                    return {"success": False, "error": resolve_error}
                canonical_id = cell_data.get("id", cell_id)

                canonical_below_id = below_cell_id
                if below_cell_id:
                    _, below_data, resolve_error = await self._resolve_cell(
                        notebook_path, below_cell_id
                    )
                    if resolve_error:
                        return {"success": False, "error": resolve_error}
                    canonical_below_id = below_data.get("id", below_cell_id)

                # Call notebook_contents API
                result = await self.notebook_contents.move_cell(
                    path=notebook_path,
                    cell_id=canonical_id,
                    below_cell_id=canonical_below_id,
                )

                # Add context information only if context exists
                if result["success"]:
                    result.pop("file_path", None)
                    result["notebook_path"] = notebook_path
                    if context:
                        result["kernel_session_id"] = context.kernel_session_id

                return result

            except Exception as e:
                logger.error(f"move_cell failed: {e}")
                return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def read_cells(
        self,
        notebook_path: str,
        include_details: bool = False,
        cell_ids: Optional[list[str]] = None,
    ) -> dict:
        """
        Read cells in a notebook with execution status and optional content.

        Args:
            notebook_path: Path to notebook file
            include_details: Include complete cell data (source, outputs).
                When False (default): Returns cell summary (execution status, mime types).
                When True: Returns full cell data (source, outputs).
            cell_ids: Optional list of cell IDs to read. If None or empty, reads all cells.
                Example: ["cell_1", "cell_3"] reads only those cells.

        Returns:
            dict with cell list and notebook info. Each cell includes:
            - Always: cell_id, cell_index, cell_type, execution_count, execution_status, output_mime_types
            - When include_details=False (default): source_preview (first 60 chars)
            - When include_details=True: source, outputs
            - When execution_status="error": error_summary (ename, evalue)

        Example:
            # Get all cells with execution status only
            result = await toolset.read_cells("notebook.ipynb")

            # Get all cells with full details
            result = await toolset.read_cells("notebook.ipynb", include_details=True)
            for cell in result["cells"]:
                print(f"{cell['cell_id']}: {cell['source']}")

            # Get only specific cells
            result = await toolset.read_cells(
                "notebook.ipynb",
                include_details=True,
                cell_ids=["cell_1", "cell_3"]
            )

        Note:
            For kernel variables, use manage_kernel(action="variables") instead.
            For advanced JSON queries on notebook files, use jq via shell:
              jq '.cells[] | select(.id=="abc123")' notebook.ipynb  # Get cell by id
              jq '.cells[] | select(.cell_type=="code") | .source' notebook.ipynb
        """
        session_id = self.get_session_id()

        read_result = await self.notebook_contents.read_notebook(notebook_path)
        if not read_result["success"]:
            return read_result

        notebook = read_result["notebook"]
        resolved_path = read_result.get("file_path")

        # Auto-assign stable IDs to cells that lack them
        if resolved_path and any(
            not cell.get("id") for cell in notebook.get("cells", [])
        ):
            changed = await self.notebook_contents._ensure_cell_ids_and_upgrade(
                Path(resolved_path), notebook
            )
            if changed:
                await self.notebook_contents._save_notebook(Path(resolved_path), notebook)

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

        # Helper: Get source preview (first N characters)
        def get_source_preview(cell: dict, max_length: int = 60) -> str:
            """Extract source preview (first N characters)"""
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            source = source.strip()
            if len(source) > max_length:
                return source[:max_length] + "..."
            return source

        # Helper: Get error summary from cell outputs
        def get_error_summary(cell: dict) -> dict | None:
            """Extract error name and value from cell outputs"""
            outputs = cell.get("outputs", [])
            for output in outputs:
                if output.get("output_type") == "error":
                    return {
                        "ename": output.get("ename", "Unknown"),
                        "evalue": output.get("evalue", "")[:200],  # Limit error message length
                    }
            return None

        # Build cell info list with enhanced fields
        cells_info = []
        for idx, cell in enumerate(notebook.get("cells", [])):
            execution_status = get_execution_status(cell)

            # Basic cell info (always included)
            cell_info = {
                "cell_id": cell.get("id"),
                "cell_index": idx,
                "cell_type": cell.get("cell_type"),
                "execution_count": cell.get("execution_count"),
                "has_output": len(cell.get("outputs", [])) > 0,
                "execution_status": execution_status,
                "output_mime_types": get_output_mime_types(cell),
            }

            # Add error summary for failed cells
            if execution_status == "error":
                error_summary = get_error_summary(cell)
                if error_summary:
                    cell_info["error_summary"] = error_summary

            # Add language for code cells
            if cell.get("cell_type") == "code":
                # Get language from notebook metadata or default to python
                metadata = notebook.get("metadata", {})
                language_info = metadata.get("language_info", {})
                cell_info["language"] = language_info.get("name", "python")

            # Include either preview or full content
            if include_details:
                # Full source and outputs
                source = cell.get("source", "")
                if isinstance(source, list):
                    source = "".join(source)
                cell_info["source"] = source
                cell_info["outputs"] = cell.get("outputs", [])
            else:
                # Only preview (no full content)
                cell_info["source_preview"] = get_source_preview(cell)


            cells_info.append(cell_info)

        # Filter cells by cell_ids if provided
        if cell_ids:
            cell_ids_set = set(cell_ids)
            cells_info = [c for c in cells_info if c["cell_id"] in cell_ids_set]

        # Safely get kernel status (session may have been shutdown/deleted)
        kernel_status = None
        if context and context.kernel_session_id in self.kernel_toolset.sessions:
            kernel_status = self.kernel_toolset.sessions[context.kernel_session_id].status.value

        # Base response
        result = {
            "success": True,
            "notebook_path": notebook_path,
            "has_context": context is not None,
            "cell_count": len(cells_info),
            "cells": cells_info,
            "kernel_status": kernel_status,
            "kernel_session_id": context.kernel_session_id if context else None,
        }

        return result

    @tool(exclude=True)
    async def read_notebook(self, notebook_path: str, validate: bool = False) -> dict:
        """
        Read full notebook JSON content (Frontend only)

        This tool is for frontend/API use only. Not exposed to LLM agents.

        For LLM agents: Use the standard file Read tool to read .ipynb files.
        This matches VSCode's design where agents use built-in file operations
        for full content reads.

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

    @tool(exclude=True)
    async def list_notebooks(self) -> dict:
        """
        List running notebooks for current session
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

    @tool(exclude=True)
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
                              NOTE: Auto-creates kernel if it doesn't exist
                - "interrupt": Interrupt running execution
                - "status": Get kernel status information
                - "variables": Get current kernel variables
                - "shutdown": Shutdown kernel (context preserved, can restart later)
                          **Memory best practice**: Shutdown kernels for completed notebooks
                          to free memory. Running notebooks with loaded data (e.g., AnnData)
                          consume significant memory. Too many open notebooks can exhaust memory.
                - "delete": Delete context completely (shutdown kernel + remove from memory)

        Returns:
            dict with success status and action-specific data

        """
        session_id = self.get_session_id()
        if not session_id:
            return {"success": False, "error": "No session_id provided"}

        # For restart, auto-create context if needed
        # For other actions, require context to already exist
        if action == "restart":
            context = await self._get_or_create_context(notebook_path, session_id)
        else:
            context = self._get_context(notebook_path, session_id)
            if not context:
                return {"success": False, "error": "Notebook not opened"}

        try:
            if action == "restart":
                # Restart existing kernel
                result = await self.kernel_toolset.restart_session(
                    context.kernel_session_id
                )

                # Reset rpy2 initialization state (extension needs to be reloaded after restart)
                context.rpy2_initialized = False

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

    async def _check_memory_usage(self) -> float | None:
        """
        Check system memory usage from backend process (non-intrusive).
        
        This method checks memory from the backend process using psutil directly,
        avoiding kernel execution that would interfere with user code and produce
        unwanted IOPub messages.
        
        Note: GC is intentionally NOT performed. Users should explicitly run
        `import gc; gc.collect()` in their notebook if needed.
        
        Returns:
            Memory usage percentage (0-100), or None if check fails
        """
        try:
            import psutil
            return psutil.virtual_memory().percent
        except Exception as e:
            logger.debug(f"Memory check error: {e}")
            return None

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

            # Post-execution: check system memory (non-intrusive)
            logger.info(f"Checking system memory usage")
            mem_pct = await self._check_memory_usage()
            logger.info(f"Memory check result: {mem_pct}")
            if mem_pct is not None and mem_pct > 75:
                exec_result["memory_hint"] = (
                    f"⚠️ System memory at {mem_pct:.0f}%. Consider: "
                    "`import gc; gc.collect()` or `manage_kernel(action='restart')`"
                )

            # Add notebook-specific fields
            exec_result["notebook_path"] = notebook_path

            # Extract base64 images from outputs for downstream consumers (e.g. Claw channels)
            image_uris = []
            for output in outputs:
                if output.get("output_type") in ("display_data", "execute_result"):
                    data = output.get("data", {})
                    for mime in ("image/png", "image/jpeg", "image/gif", "image/svg+xml"):
                        img_b64 = data.get(mime)
                        if img_b64 and isinstance(img_b64, str):
                            image_uris.append(f"data:{mime};base64,{img_b64}")
            if image_uris:
                exec_result["base64_uri"] = image_uris
                exec_result["hidden_to_model"] = ["base64_uri"]

            return exec_result

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return {"success": False, "error": str(e), "notebook_path": notebook_path}

    # ═══════════════════════════════════════════════════════════
    # Unified Tools (LLM-facing, consolidated interface)
    # ═══════════════════════════════════════════════════════════

    @tool
    async def notebook_edit(
        self,
        notebook_path: str,
        action: str,
        cell_id: Optional[str] = None,
        cell_type: str = "code",
        content: str = "",
        old_content: Optional[str] = None,
        position: Optional[str] = None,
        execute: bool = False,
    ) -> dict:
        """
        Unified tool for notebook structure operations.

        Args:
            notebook_path: Path to notebook file
            action: Operation to perform:
                - "create": Create or open a notebook
                - "add_cell": Add a new cell
                - "update_cell": Update cell content
                - "delete_cell": Delete a cell
                - "move_cell": Move a cell to a different position
            cell_id: Cell identifier (required for update/delete/move, optional for add)
            cell_type: Cell type for add_cell: "code", "markdown", "raw" (default: "code")
            content: Cell content for add_cell or update_cell
            old_content: For update_cell partial replacement mode
            position: Target position:
                - For add_cell: None=append to end, "0"/"1"/"-1"=index, or a cell_id=insert after that cell
                - For move_cell: None=move to top, or a cell_id=move after that cell
            execute: For add_cell/update_cell: execute the cell after modification (recommended for code cells)

        Returns:
            dict with action-specific results

        Examples:
            # Create notebook
            notebook_edit("analysis.ipynb", action="create")

            # Add and execute a code cell
            notebook_edit("analysis.ipynb", action="add_cell",
                         content="import pandas as pd", execute=True)

            # Update cell content
            notebook_edit("analysis.ipynb", action="update_cell",
                         cell_id="abc123", content="x = 2", execute=True)

            # Partial replacement
            notebook_edit("analysis.ipynb", action="update_cell",
                         cell_id="abc123", content="n=30", old_content="n=15", execute=True)

            # Delete a cell
            notebook_edit("analysis.ipynb", action="delete_cell", cell_id="abc123")

            # Move a cell after another
            notebook_edit("analysis.ipynb", action="move_cell",
                         cell_id="abc123", position="def456")
        """
        if action == "create":
            return await self.create_notebook(notebook_path)

        elif action == "add_cell":
            return await self.add_cell(
                notebook_path=notebook_path,
                cell_type=cell_type,
                content=content,
                cell_id=cell_id,
                position=position,
                execute=execute,
            )

        elif action == "update_cell":
            if not cell_id:
                return {"success": False, "error": "cell_id is required for update_cell"}
            return await self.update_cell(
                notebook_path=notebook_path,
                cell_id=cell_id,
                content=content,
                old_content=old_content,
                execute=execute,
            )

        elif action == "delete_cell":
            if not cell_id:
                return {"success": False, "error": "cell_id is required for delete_cell"}
            return await self.delete_cell(
                notebook_path=notebook_path,
                cell_id=cell_id,
            )

        elif action == "move_cell":
            if not cell_id:
                return {"success": False, "error": "cell_id is required for move_cell"}
            return await self.move_cell(
                notebook_path=notebook_path,
                cell_id=cell_id,
                below_cell_id=position,
            )

        else:
            return {
                "success": False,
                "error": f"Unknown action '{action}'. Must be one of: create, add_cell, update_cell, delete_cell, move_cell",
            }

    @tool
    async def notebook_execute(
        self,
        notebook_path: str,
        action: str = "execute",
        cell_id: Optional[str] = None,
    ) -> dict:
        """
        Execute cells and manage kernel lifecycle.

        Args:
            notebook_path: Path to notebook file
            action: Operation to perform:
                - "execute": Execute a cell (requires cell_id)
                - "restart": Restart kernel (clears all state)
                - "interrupt": Interrupt running execution
                - "shutdown": Shutdown kernel session
            cell_id: Cell identifier (required for "execute" action)

        Returns:
            dict with execution results or kernel status

        Examples:
            # Execute a cell
            notebook_execute("analysis.ipynb", action="execute", cell_id="abc123")

            # Interrupt a long-running execution
            notebook_execute("analysis.ipynb", action="interrupt")

            # Restart kernel (clears all variables)
            notebook_execute("analysis.ipynb", action="restart")
        """
        if action == "execute":
            if not cell_id:
                return {"success": False, "error": "cell_id is required for execute action"}
            return await self.execute_cell(
                notebook_path=notebook_path,
                cell_id=cell_id,
            )

        elif action in ("restart", "interrupt", "shutdown"):
            return await self.manage_kernel(
                notebook_path=notebook_path,
                action=action,
            )

        else:
            return {
                "success": False,
                "error": f"Unknown action '{action}'. Must be one of: execute, restart, interrupt, shutdown",
            }

    @tool
    async def notebook_read(
        self,
        notebook_path: Optional[str] = None,
        action: str = "read_cells",
        include_details: bool = False,
        cell_ids: Optional[list[str]] = None,
    ) -> dict:
        """
        Read notebook content, list notebooks, or query kernel state.

        Args:
            notebook_path: Path to notebook file (required for read_cells and kernel queries)
            action: Operation to perform:
                - "read_cells": Read cells with execution status (default)
                - "list": List running notebooks for current session
                - "kernel_status": Get kernel status
                - "kernel_variables": List kernel variables
            include_details: For read_cells: include full source and outputs (default: False)
            cell_ids: For read_cells: optional list of specific cell IDs to read

        Returns:
            dict with notebook data

        Examples:
            # Read all cells (summary mode)
            notebook_read("analysis.ipynb")

            # Read specific cells with full details
            notebook_read("analysis.ipynb", include_details=True, cell_ids=["cell_1"])

            # List all running notebooks
            notebook_read(action="list")

            # Check kernel status
            notebook_read("analysis.ipynb", action="kernel_status")

            # Get kernel variables
            notebook_read("analysis.ipynb", action="kernel_variables")
        """
        if action == "read_cells":
            if not notebook_path:
                return {"success": False, "error": "notebook_path is required for read_cells"}
            return await self.read_cells(
                notebook_path=notebook_path,
                include_details=include_details,
                cell_ids=cell_ids,
            )

        elif action == "list":
            return await self.list_notebooks()

        elif action == "kernel_status":
            if not notebook_path:
                return {"success": False, "error": "notebook_path is required for kernel_status"}
            return await self.manage_kernel(
                notebook_path=notebook_path,
                action="status",
            )

        elif action == "kernel_variables":
            if not notebook_path:
                return {"success": False, "error": "notebook_path is required for kernel_variables"}
            return await self.manage_kernel(
                notebook_path=notebook_path,
                action="variables",
            )

        else:
            return {
                "success": False,
                "error": f"Unknown action '{action}'. Must be one of: read_cells, list, kernel_status, kernel_variables",
            }

    async def cleanup(self):
        """Cleanup all resources"""
        try:
            await self._save_contexts()

            if self.nats_handler:
                await self.nats_handler.cleanup()
                logger.info("Cleaned up NatsStreamHandler")

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
