"""Notebook Contents ToolSet - File-based notebook content management using nbformat standard library"""

import time
import uuid
from pathlib import Path
from typing import Optional

import nbformat

try:
    from nbformat import ValidationError
except ImportError:
    # Fallback for incomplete nbformat installation
    ValidationError = Exception

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger


class NotebookContentsToolSet(ToolSet):
    """Notebook file content management using nbformat standard library

    This toolset provides comprehensive notebook file operations:
    - Read and write complete notebook files using nbformat
    - Cell-level operations (add, update, delete, move) with standard validation
    - Output management for executed cells
    - Version tracking via file modification time
    - Atomic file operations with proper error handling
    - Full compliance with Jupyter notebook format standards
    """

    def __init__(self, name: str, workdir: Optional[str] = None, **kwargs):
        super().__init__(name, **kwargs)
        self.workdir = Path(workdir) if workdir else Path.cwd()
        logger.info(f"NotebookContentsToolSet initialized with workdir: {self.workdir}")

    def _resolve_path(self, file_path: str) -> Path:
        """Resolve file path relative to workspace"""
        path = Path(file_path)
        if path.is_absolute():
            return path
        return self.workdir / path

    def _validate_path(self, file_path: str) -> tuple[bool, str, Path | None]:
        """Validate file path for security and existence checks"""
        if ".." in file_path:
            return False, "Path cannot contain '..' for security reasons", None

        resolved_path = self._resolve_path(file_path)

        # Check if path is within workspace (for security)
        try:
            resolved_path.relative_to(self.workdir)
        except ValueError:
            return False, f"Path must be within workspace: {self.workdir}", None

        return True, "", resolved_path

    def _load_and_validate_notebook(
        self, path: str, must_exist: bool = True, validate: bool = True
    ) -> tuple[bool, str, Path | None, nbformat.NotebookNode | None]:
        """Combined path validation and notebook loading

        Args:
            path: Notebook path
            must_exist: Require file to exist
            validate: Perform nbformat schema validation
        """
        is_valid, error_msg, resolved_path = self._validate_path(path)
        if not is_valid:
            return False, error_msg, None, None

        # At this point, resolved_path should be valid
        assert resolved_path is not None
        if must_exist and not resolved_path.exists():
            return False, f"Notebook file not found: {path}", resolved_path, None

        if must_exist:
            success, error_msg, notebook = self._load_notebook(
                resolved_path, validate=validate
            )
            if not success:
                return False, error_msg, resolved_path, None
            return True, "", resolved_path, notebook

        return True, "", resolved_path, None

    def _validate_cell_index(self, cell_index: int, cells: list) -> tuple[bool, str]:
        """Validate cell index range"""
        if cell_index < 0 or cell_index >= len(cells):
            return False, f"Cell index {cell_index} out of range (0-{len(cells) - 1})"
        return True, ""

    def _format_source(self, source: str | list) -> str:
        """Convert source to standard nbformat string format"""
        if isinstance(source, str):
            return source
        elif isinstance(source, list):
            # Convert list format back to string (for compatibility)
            return "".join(source) if source else ""
        return ""

    def _collect_cell_ids(self, notebook: nbformat.NotebookNode) -> set[str]:
        """Collect existing cell ids (if any)"""
        ids: set[str] = set()
        for cell in notebook.get("cells", []):
            cid = cell.get("id")
            if isinstance(cid, str) and cid:
                ids.add(cid)
        return ids

    def _find_cell(
        self, notebook: nbformat.NotebookNode, cell_id: str
    ) -> tuple[int | None, nbformat.NotebookNode | None]:
        """Find cell by cell_id and return (index, cell_object)

        Args:
            notebook: Notebook object
            cell_id: Cell identifier to find

        Returns:
            tuple of (cell_index, cell_object) if found, (None, None) otherwise
        """
        cells = notebook.get("cells", [])
        for idx, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return idx, cell
        return None, None

    def _load_notebook(
        self, file_path: Path, validate: bool = True
    ) -> tuple[bool, str, nbformat.NotebookNode | None]:
        """Load and parse a Jupyter notebook file using nbformat

        Args:
            file_path: Path to notebook file
            validate: Perform nbformat schema validation (default: True)
        """
        try:
            # Use nbformat to read notebook
            notebook = nbformat.read(file_path, as_version=4)

            # Optionally validate using nbformat
            if validate:
                nbformat.validate(notebook)

            logger.debug(
                f"Successfully loaded notebook with {len(notebook.cells)} cells (validated={validate})"
            )
            return True, "", notebook

        except ValidationError as e:
            return False, f"Notebook validation failed: {str(e)}", None
        except Exception as e:
            return False, f"Error loading notebook: {str(e)}", None

    async def _ensure_cell_ids_and_upgrade(
        self,
        resolved_path: Path,
        notebook: nbformat.NotebookNode,
        upgrade_minor: bool = True,
    ) -> bool:
        """Internal: Ensure nbformat 4.5+ and stable cell ids.

        Note: This method only mutates the in-memory notebook and returns whether
        a change occurred. The caller (usually _save_notebook) is responsible for persistence.
        """
        changed = False

        # Upgrade minor version to support cell ids
        try:
            if (
                upgrade_minor
                and getattr(notebook, "nbformat", 4) == 4
                and getattr(notebook, "nbformat_minor", 4) < 5
            ):
                notebook.nbformat_minor = 5
                changed = True
        except Exception:
            # Defensive: ignore if attributes not present as expected
            pass

        existing_ids = self._collect_cell_ids(notebook)

        for cell in notebook.get("cells", []):
            if not isinstance(cell.get("id"), str) or not cell.get("id"):
                # Generate unique id
                new_id = uuid.uuid4().hex
                while new_id in existing_ids:
                    new_id = uuid.uuid4().hex
                cell["id"] = new_id
                existing_ids.add(new_id)
                changed = True

        return changed

    @tool
    async def read_notebook(self, path: str, validate: bool = True) -> dict:
        """Read complete notebook content with version tracking

        Args:
            path: Path to notebook file
            validate: Perform nbformat schema validation (default: True)
                     Set False for faster reads when validation is not needed
                     (e.g., periodic polling of trusted content)
        """
        logger.debug(f"Reading notebook: {path} (validate={validate})")

        # Load with optional validation
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(
            path, validate=validate
        )
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        if not resolved_path.suffix.lower() == ".ipynb":
            return {
                "success": False,
                "error": f"File is not a Jupyter notebook: {path}",
            }

        # Do not auto-upgrade on read to avoid implicit writes; save path will enforce

        # Add version info for change detection
        try:
            stat = resolved_path.stat()
            return {
                "success": True,
                "notebook": notebook,
                "file_path": str(resolved_path),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "version": int(stat.st_mtime * 1000),  # millisecond timestamp
                "cell_count": len(notebook.cells),
            }

        except Exception as e:
            logger.error(f"Failed to get file stats: {e}")
            return {
                "success": False,
                "error": f"Failed to get file information: {str(e)}",
            }

    @tool
    async def update_cell(
        self,
        path: str,
        cell_id: str,
        source: str,
        cell_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Update single cell content using cell_id - SSOT: only updates source, never outputs

        Args:
            path: Path to notebook file
            cell_id: Cell identifier
            source: New source code
            cell_type: Optional new cell type
            metadata: Optional metadata updates

        Returns:
            Minimal dict: {"success": bool, "cell_id": str, "file_path": str}
        """
        logger.info(f"Updating cell {cell_id} in: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(
            path
        )
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        # Find cell by cell_id
        cell_index, cell = self._find_cell(notebook, cell_id)
        if cell_index is None or cell is None:
            return {"success": False, "error": f"Cell with id '{cell_id}' not found"}

        try:
            # Update source using helper method
            cell.source = self._format_source(source)

            # Update cell type if provided
            if cell_type and cell_type in ["code", "markdown", "raw"]:
                old_type = cell.cell_type
                cell.cell_type = cell_type
                logger.info(f"Cell type changed from {old_type} to {cell_type}")

            # Update metadata if provided (but NEVER execution-related metadata)
            if metadata:
                if not hasattr(cell, "metadata"):
                    cell.metadata = {}
                # Filter out execution-related metadata that should only be updated by execution
                safe_metadata = {
                    k: v
                    for k, v in metadata.items()
                    if k not in ["execution", "collapsed", "scrolled"]
                }
                cell.metadata.update(safe_metadata)

                if len(safe_metadata) != len(metadata):
                    logger.warning(
                        f"Filtered out execution-related metadata keys: {set(metadata.keys()) - set(safe_metadata.keys())}"
                    )

            # SSOT: Do NOT modify execution result fields - only execution should modify:
            # - outputs
            # - execution_count
            # - metadata.execution
            # - metadata.collapsed/scrolled (output display state)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            # Return minimal response
            return {
                "success": True,
                "cell_id": cell_id,
                "file_path": str(resolved_path),
            }

        except Exception as e:
            logger.error(f"Failed to update cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def add_cell(
        self,
        path: str,
        cell_type: str,
        source: str = "",
        cell_id: Optional[str] = None,
        below_cell_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Add new cell to notebook using cell_id positioning

        Args:
            path: Path to notebook file
            cell_type: Type of cell ('code', 'markdown', or 'raw')
            source: Cell source code
            cell_id: Optional cell identifier (auto-generated if not provided)
            below_cell_id: Cell id to insert after. If None, append to end.
            metadata: Optional cell metadata

        Returns:
            Minimal dict: {"success": bool, "cell_id": str, "file_path": str}
        """
        logger.info(f"Adding {cell_type} cell to: {path}")

        if cell_type not in ["code", "markdown", "raw"]:
            return {
                "success": False,
                "error": "Invalid cell_type. Must be 'code', 'markdown', or 'raw'",
            }

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(
            path
        )
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        try:
            # Determine id for new cell (prefer provided, else generate)
            existing_ids = self._collect_cell_ids(notebook)
            if cell_id:
                if cell_id in existing_ids:
                    return {
                        "success": False,
                        "error": f"cell_id already exists: {cell_id}",
                    }
                new_id = cell_id
            else:
                new_id = uuid.uuid4().hex
                while new_id in existing_ids:
                    new_id = uuid.uuid4().hex

            # Create new cell using nbformat helpers
            if cell_type == "code":
                new_cell = nbformat.v4.new_code_cell(
                    source=self._format_source(source), metadata=metadata or {}
                )
            elif cell_type == "markdown":
                new_cell = nbformat.v4.new_markdown_cell(
                    source=self._format_source(source), metadata=metadata or {}
                )
            elif cell_type == "raw":
                new_cell = nbformat.v4.new_raw_cell(
                    source=self._format_source(source), metadata=metadata or {}
                )
            else:
                return {"success": False, "error": f"Invalid cell_type: {cell_type}"}

            # Assign stable id
            try:
                new_cell["id"] = new_id
            except Exception:
                # Fallback to attribute assignment if needed
                try:
                    setattr(new_cell, "id", new_id)
                except Exception:
                    return {"success": False, "error": "Failed to assign cell id"}

            # Determine insertion position
            if below_cell_id is None:
                # Append to end
                notebook.cells.append(new_cell)
            else:
                # Find target cell and insert after it
                target_index, _ = self._find_cell(notebook, below_cell_id)
                if target_index is None:
                    return {
                        "success": False,
                        "error": f"Target cell with id '{below_cell_id}' not found",
                    }
                # Insert after target cell
                notebook.cells.insert(target_index + 1, new_cell)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            # Return minimal response
            return {
                "success": True,
                "cell_id": new_id,
                "file_path": str(resolved_path),
            }

        except Exception as e:
            logger.error(f"Failed to add cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def delete_cell(self, path: str, cell_id: str) -> dict:
        """Delete cell from notebook using cell_id

        Args:
            path: Path to notebook file
            cell_id: Cell identifier

        Returns:
            Minimal dict: {"success": bool, "cell_id": str, "file_path": str}
        """
        logger.info(f"Deleting cell {cell_id} from: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(
            path
        )
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        # Find cell by cell_id
        cell_index, cell = self._find_cell(notebook, cell_id)
        if cell_index is None or cell is None:
            return {"success": False, "error": f"Cell with id '{cell_id}' not found"}

        try:
            # Remove cell
            cells = notebook.cells
            deleted_cell = cells.pop(cell_index)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            # Return minimal response
            return {
                "success": True,
                "cell_id": cell_id,
                "file_path": str(resolved_path),
            }

        except Exception as e:
            logger.error(f"Failed to delete cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def move_cell(
        self, path: str, cell_id: str, below_cell_id: Optional[str] = None
    ) -> dict:
        """Move cell to different position using cell_id

        Args:
            path: Path to notebook file
            cell_id: Cell identifier to move
            below_cell_id: Cell id to move after. If None, move to top.

        Returns:
            Minimal dict: {"success": bool, "cell_id": str, "file_path": str}
        """
        logger.info(f"Moving cell {cell_id} in: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(
            path
        )
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        # Find source cell
        from_index, cell = self._find_cell(notebook, cell_id)
        if from_index is None or cell is None:
            return {"success": False, "error": f"Cell with id '{cell_id}' not found"}

        try:
            cells = notebook.cells

            # Determine target position
            if below_cell_id is None:
                # Move to top
                to_index = 0
            else:
                # Find target cell
                target_index, _ = self._find_cell(notebook, below_cell_id)
                if target_index is None:
                    return {
                        "success": False,
                        "error": f"Target cell with id '{below_cell_id}' not found",
                    }
                # Move after target cell
                to_index = target_index + 1

            # Check if move is needed
            if from_index == to_index or from_index + 1 == to_index:
                return {
                    "success": True,
                    "cell_id": cell_id,
                    "file_path": str(resolved_path),
                    "message": "No movement needed",
                }

            # Move cell
            cell = cells.pop(from_index)
            # Adjust target index if moving down (after removal)
            adjusted_to = to_index - 1 if to_index > from_index else to_index
            cells.insert(adjusted_to, cell)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            # Return minimal response
            return {
                "success": True,
                "cell_id": cell_id,
                "file_path": str(resolved_path),
            }

        except Exception as e:
            logger.error(f"Failed to move cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def update_cell_outputs(
        self,
        path: str,
        cell_id: str,
        outputs: list,
        execution_count: Optional[int] = None,
        execution_timing: Optional[dict] = None,
    ) -> dict:
        """Update cell outputs after execution using cell_id - only called by backend execution

        Args:
            path: Path to notebook file
            cell_id: Cell identifier
            outputs: Cell outputs
            execution_count: Execution counter
            execution_timing: Execution timing metadata

        Returns:
            Minimal dict: {"success": bool, "cell_id": str, "file_path": str}
        """
        logger.info(f"Updating outputs for cell {cell_id} in: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(
            path
        )
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        # Find cell by cell_id
        cell_index, cell = self._find_cell(notebook, cell_id)
        if cell_index is None or cell is None:
            return {"success": False, "error": f"Cell with id '{cell_id}' not found"}

        if cell.cell_type != "code":
            return {"success": False, "error": "Can only update outputs for code cells"}

        try:
            # Convert outputs to NotebookNode objects if they're plain dicts
            if outputs:
                converted_outputs = []
                for output in outputs:
                    if isinstance(output, dict):
                        # Convert dict to NotebookNode using nbformat
                        converted_output = nbformat.NotebookNode(output)
                        converted_outputs.append(converted_output)
                    else:
                        converted_outputs.append(output)
                cell.outputs = converted_outputs
            else:
                cell.outputs = []

            if execution_count is not None:
                cell.execution_count = execution_count

            # Update cell metadata with execution timing (standard nbformat)
            if execution_timing and isinstance(execution_timing, dict):
                if not hasattr(cell, "metadata"):
                    cell.metadata = {}
                if "execution" not in cell.metadata:
                    cell.metadata["execution"] = {}

                # Update timing information following nbformat specification
                # Timestamps are already cleaned by jupyter_kernel.py's make_json_serializable
                cell.metadata["execution"].update(execution_timing)
                logger.debug(
                    f"Updated cell {cell_id} with execution timing: {list(execution_timing.keys())}"
                )

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            # Return minimal response
            return {
                "success": True,
                "cell_id": cell_id,
                "file_path": str(resolved_path),
            }

        except Exception as e:
            logger.error(f"Failed to update cell outputs: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def create_notebook(
        self, path: str, title: Optional[str] = None, kernel_spec: Optional[dict] = None
    ) -> dict:
        """Create new notebook file"""
        logger.info(f"Creating notebook: {path}")

        # Validate path (don't require existence)
        success, error_msg, resolved_path, _ = self._load_and_validate_notebook(
            path, must_exist=False
        )
        if not success or not resolved_path:
            return {"success": False, "error": error_msg}

        if resolved_path.exists():
            return {"success": False, "error": f"Notebook already exists: {path}"}

        try:
            # Ensure .ipynb extension
            if not resolved_path.suffix.lower() == ".ipynb":
                resolved_path = resolved_path.with_suffix(".ipynb")

            # Create notebook using nbformat standard library
            notebook = nbformat.v4.new_notebook()

            # Set metadata
            if kernel_spec:
                notebook.metadata.kernelspec = kernel_spec
            else:
                notebook.metadata.kernelspec = {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                }

            notebook.metadata.language_info = {
                "name": "python",
                "version": "3.8.0",
                "mimetype": "text/x-python",
                "file_extension": ".py",
            }

            # Note: We don't automatically add a title cell to follow nbformat standards
            # Users should add title cells manually if needed
            # Empty notebook should have empty cells array: cells: []

            # Set nbformat version to 4.5 to support stable cell ids
            notebook.nbformat = 4
            notebook.nbformat_minor = 5

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "title": title,
                "cell_count": len(notebook.cells),
            }

        except Exception as e:
            logger.error(f"Failed to create notebook: {e}")
            return {"success": False, "error": str(e)}

    async def _save_notebook(
        self, resolved_path: Path, notebook: nbformat.NotebookNode
    ) -> dict:
        """Save notebook to file with atomic operation using nbformat

        Args:
            resolved_path: Resolved file path
            notebook: Notebook data as NotebookNode

        Returns:
            dict with success status
        """
        try:
            # Enforce nbformat 4.5 and ensure cell ids before writing
            try:
                await self._ensure_cell_ids_and_upgrade(
                    resolved_path, notebook, upgrade_minor=True
                )
            except Exception as e:
                logger.warning(f"Failed to enforce ids/format before save: {e}")

            # Skip strict validation for compatibility - nbformat.write will handle basic validation
            # nbformat.validate(notebook) - commenting out to avoid version conflicts

            # Create parent directories
            resolved_path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write using temporary file
            temp_path = resolved_path.with_suffix(resolved_path.suffix + ".tmp")

            # Use nbformat to write the notebook
            nbformat.write(notebook, temp_path)

            # Atomic move
            temp_path.replace(resolved_path)

            logger.debug(f"Notebook saved successfully: {resolved_path}")
            # Collect fresh file stats for callers that need versioning info
            try:
                stat = resolved_path.stat()
                mtime = stat.st_mtime
                size = stat.st_size
                version = int(stat.st_mtime * 1000)
            except Exception:
                mtime = None
                size = None
                version = None

            return {
                "success": True,
                "file_path": str(resolved_path),
                "saved_at": time.time(),
                "notebook": notebook,
                "mtime": mtime,
                "size": size,
                "version": version,
                "cell_count": len(notebook.get("cells", []))
                if isinstance(notebook, dict)
                else len(getattr(notebook, "cells", [])),
            }

        except ValidationError as e:
            logger.error(f"Notebook validation failed during save: {e}")
            return {"success": False, "error": f"Notebook validation failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Failed to save notebook: {e}")
            # Clean up temp file if it exists
            temp_path_name = resolved_path.with_suffix(resolved_path.suffix + ".tmp")
            if temp_path_name.exists():
                try:
                    temp_path_name.unlink()
                except Exception:
                    pass
            return {"success": False, "error": f"Failed to save notebook: {str(e)}"}

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("NotebookContentsToolSet cleanup complete")


# Export
__all__ = ["NotebookContentsToolSet"]
