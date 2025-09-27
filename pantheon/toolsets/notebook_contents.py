"""Notebook Contents ToolSet - File-based notebook content management using nbformat standard library"""

import time
from pathlib import Path
from typing import Optional

import nbformat
from nbformat import ValidationError

from ..toolset import ToolSet, tool
from ..utils.log import logger


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

    def __init__(
        self,
        name: str,
        workdir: str = None,
        worker_params: dict = None,
        **kwargs
    ):
        super().__init__(name, worker_params, **kwargs)
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
        if '..' in file_path:
            return False, "Path cannot contain '..' for security reasons", None

        resolved_path = self._resolve_path(file_path)

        # Check if path is within workspace (for security)
        try:
            resolved_path.relative_to(self.workdir)
        except ValueError:
            return False, f"Path must be within workspace: {self.workdir}", None

        return True, "", resolved_path

    def _load_and_validate_notebook(self, path: str, must_exist: bool = True) -> tuple[bool, str, Path | None, nbformat.NotebookNode | None]:
        """Combined path validation and notebook loading"""
        is_valid, error_msg, resolved_path = self._validate_path(path)
        if not is_valid:
            return False, error_msg, None, None

        if must_exist and not resolved_path.exists():
            return False, f"Notebook file not found: {path}", resolved_path, None

        if must_exist:
            success, error_msg, notebook = self._load_notebook(resolved_path)
            if not success:
                return False, error_msg, resolved_path, None
            return True, "", resolved_path, notebook

        return True, "", resolved_path, None

    def _validate_cell_index(self, cell_index: int, cells: list) -> tuple[bool, str]:
        """Validate cell index range"""
        if cell_index < 0 or cell_index >= len(cells):
            return False, f"Cell index {cell_index} out of range (0-{len(cells)-1})"
        return True, ""

    def _format_source(self, source: str | list) -> str:
        """Convert source to standard nbformat string format"""
        if isinstance(source, str):
            return source
        elif isinstance(source, list):
            # Convert list format back to string (for compatibility)
            return ''.join(source) if source else ""
        return ""

    def _load_notebook(self, file_path: Path) -> tuple[bool, str, nbformat.NotebookNode | None]:
        """Load and parse a Jupyter notebook file using nbformat"""
        try:
            # Use nbformat to read and validate notebook
            notebook = nbformat.read(file_path, as_version=4)

            # Additional validation using nbformat
            nbformat.validate(notebook)

            logger.debug(f"Successfully loaded notebook with {len(notebook.cells)} cells")
            return True, "", notebook

        except ValidationError as e:
            return False, f"Notebook validation failed: {str(e)}", None
        except Exception as e:
            return False, f"Error loading notebook: {str(e)}", None

    @tool
    async def read_notebook(self, path: str) -> dict:
        """Read complete notebook content with version tracking"""
        logger.info(f"Reading notebook: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(path)
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        if not resolved_path.suffix.lower() == '.ipynb':
            return {"success": False, "error": f"File is not a Jupyter notebook: {path}"}

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
                "cell_count": len(notebook.cells)
            }

        except Exception as e:
            logger.error(f"Failed to get file stats: {e}")
            return {"success": False, "error": f"Failed to get file information: {str(e)}"}

    @tool
    async def update_cell(
        self,
        path: str,
        cell_index: int,
        source: str,
        cell_type: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> dict:
        """Update single cell content - SSOT: only updates source, never outputs"""
        logger.info(f"Updating cell {cell_index} in: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(path)
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        cells = notebook.cells

        # Validate cell index
        index_valid, index_error = self._validate_cell_index(cell_index, cells)
        if not index_valid:
            return {"success": False, "error": index_error}

        try:
            cell = cells[cell_index]

            # Update source using helper method
            cell.source = self._format_source(source)

            # Update cell type if provided
            if cell_type and cell_type in ["code", "markdown", "raw"]:
                old_type = cell.cell_type
                cell.cell_type = cell_type
                logger.info(f"Cell type changed from {old_type} to {cell_type}")

            # Update metadata if provided (but NEVER execution-related metadata)
            if metadata:
                if not hasattr(cell, 'metadata'):
                    cell.metadata = {}
                # Filter out execution-related metadata that should only be updated by execution
                safe_metadata = {k: v for k, v in metadata.items()
                               if k not in ["execution", "collapsed", "scrolled"]}
                cell.metadata.update(safe_metadata)

                if len(safe_metadata) != len(metadata):
                    logger.warning(f"Filtered out execution-related metadata keys: {set(metadata.keys()) - set(safe_metadata.keys())}")

            # SSOT: Do NOT modify execution result fields - only execution should modify:
            # - outputs
            # - execution_count
            # - metadata.execution
            # - metadata.collapsed/scrolled (output display state)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "cell_index": cell_index,
                "cell_type": cell.cell_type,
                "updated_at": time.time()
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
        position: Optional[int] = None,
        metadata: Optional[dict] = None
    ) -> dict:
        """Add new cell to notebook"""
        logger.info(f"Adding {cell_type} cell to: {path} at position {position}")

        if cell_type not in ["code", "markdown", "raw"]:
            return {"success": False, "error": "Invalid cell_type. Must be 'code', 'markdown', or 'raw'"}

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(path)
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        try:
            # Create new cell using nbformat helpers
            if cell_type == "code":
                new_cell = nbformat.v4.new_code_cell(
                    source=self._format_source(source),
                    metadata=metadata or {}
                )
            elif cell_type == "markdown":
                new_cell = nbformat.v4.new_markdown_cell(
                    source=self._format_source(source),
                    metadata=metadata or {}
                )
            elif cell_type == "raw":
                new_cell = nbformat.v4.new_raw_cell(
                    source=self._format_source(source),
                    metadata=metadata or {}
                )
            else:
                return {"success": False, "error": f"Invalid cell_type: {cell_type}"}

            # For compatibility with existing notebooks, match their format version
            # Check if existing notebook supports id fields
            supports_id = getattr(notebook, 'nbformat_minor', 4) >= 5

            # Remove 'id' field if target notebook doesn't support it
            if not supports_id:
                try:
                    if 'id' in new_cell:
                        del new_cell['id']
                except (KeyError, TypeError, AttributeError):
                    try:
                        if hasattr(new_cell, 'id'):
                            delattr(new_cell, 'id')
                    except (AttributeError, TypeError):
                        pass

            # Insert at position
            if position is None or position >= len(notebook.cells):
                notebook.cells.append(new_cell)
                cell_index = len(notebook.cells) - 1
            else:
                position = max(0, position)
                notebook.cells.insert(position, new_cell)
                cell_index = position

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "cell_index": cell_index,
                "cell_type": cell_type,
                "total_cells": len(notebook.cells)
            }

        except Exception as e:
            logger.error(f"Failed to add cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def delete_cell(self, path: str, cell_index: int) -> dict:
        """Delete cell from notebook"""
        logger.info(f"Deleting cell {cell_index} from: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(path)
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        cells = notebook.cells

        # Validate cell index
        index_valid, index_error = self._validate_cell_index(cell_index, cells)
        if not index_valid:
            return {"success": False, "error": index_error}

        try:
            # Remove cell
            deleted_cell = cells.pop(cell_index)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "deleted_cell_index": cell_index,
                "deleted_cell_type": deleted_cell.cell_type,
                "remaining_cells": len(cells)
            }

        except Exception as e:
            logger.error(f"Failed to delete cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def move_cell(self, path: str, from_index: int, to_index: int) -> dict:
        """Move cell to different position"""
        logger.info(f"Moving cell from {from_index} to {to_index} in: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(path)
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        cells = notebook.cells

        # Validate both indices
        from_valid, from_error = self._validate_cell_index(from_index, cells)
        if not from_valid:
            return {"success": False, "error": f"Source {from_error.lower()}"}

        to_valid, to_error = self._validate_cell_index(to_index, cells)
        if not to_valid:
            return {"success": False, "error": f"Target {to_error.lower()}"}

        if from_index == to_index:
            return {"success": True, "message": "No movement needed"}

        try:
            # Move cell
            cell = cells.pop(from_index)
            # Adjust target index if moving down (after removal)
            adjusted_to = to_index - 1 if to_index > from_index else to_index
            cells.insert(adjusted_to, cell)

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "from_index": from_index,
                "to_index": adjusted_to,
                "cell_type": cell.cell_type
            }

        except Exception as e:
            logger.error(f"Failed to move cell: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def update_cell_outputs(
        self,
        path: str,
        cell_index: int,
        outputs: list,
        execution_count: Optional[int] = None,
        execution_timing: Optional[dict] = None
    ) -> dict:
        """Update cell outputs after execution - only called by backend execution"""
        logger.info(f"Updating outputs for cell {cell_index} in: {path}")

        # Load and validate
        success, error_msg, resolved_path, notebook = self._load_and_validate_notebook(path)
        if not success or not resolved_path or not notebook:
            return {"success": False, "error": error_msg}

        cells = notebook.cells

        # Validate cell index
        index_valid, index_error = self._validate_cell_index(cell_index, cells)
        if not index_valid:
            return {"success": False, "error": index_error}

        cell = cells[cell_index]
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
                if not hasattr(cell, 'metadata'):
                    cell.metadata = {}
                if "execution" not in cell.metadata:
                    cell.metadata["execution"] = {}

                # Update timing information following nbformat specification
                # Timestamps are already cleaned by jupyter_kernel.py's make_json_serializable
                cell.metadata["execution"].update(execution_timing)
                logger.debug(f"Updated cell {cell_index} with execution timing: {list(execution_timing.keys())}")

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "cell_index": cell_index,
                "execution_count": execution_count,
                "output_count": len(outputs or []),
                "timing_updated": bool(execution_timing)
            }

        except Exception as e:
            logger.error(f"Failed to update cell outputs: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def create_notebook(
        self,
        path: str,
        title: Optional[str] = None,
        kernel_spec: Optional[dict] = None
    ) -> dict:
        """Create new notebook file"""
        logger.info(f"Creating notebook: {path}")

        # Validate path (don't require existence)
        success, error_msg, resolved_path, _ = self._load_and_validate_notebook(path, must_exist=False)
        if not success or not resolved_path:
            return {"success": False, "error": error_msg}

        if resolved_path.exists():
            return {"success": False, "error": f"Notebook already exists: {path}"}

        try:
            # Ensure .ipynb extension
            if not resolved_path.suffix.lower() == '.ipynb':
                resolved_path = resolved_path.with_suffix('.ipynb')

            # Create notebook using nbformat standard library
            notebook = nbformat.v4.new_notebook()

            # Set metadata
            if kernel_spec:
                notebook.metadata.kernelspec = kernel_spec
            else:
                notebook.metadata.kernelspec = {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3"
                }

            notebook.metadata.language_info = {
                "name": "python",
                "version": "3.8.0",
                "mimetype": "text/x-python",
                "file_extension": ".py"
            }

            # Note: We don't automatically add a title cell to follow nbformat standards
            # Users should add title cells manually if needed
            # Empty notebook should have empty cells array: cells: []

            # Set compatible nbformat version (4.4 for compatibility)
            notebook.nbformat = 4
            notebook.nbformat_minor = 4

            # Save notebook
            save_result = await self._save_notebook(resolved_path, notebook)
            if not save_result["success"]:
                return save_result

            return {
                "success": True,
                "file_path": str(resolved_path),
                "title": title,
                "cell_count": len(notebook.cells)
            }

        except Exception as e:
            logger.error(f"Failed to create notebook: {e}")
            return {"success": False, "error": str(e)}

    async def _save_notebook(self, resolved_path: Path, notebook: nbformat.NotebookNode) -> dict:
        """Save notebook to file with atomic operation using nbformat

        Args:
            resolved_path: Resolved file path
            notebook: Notebook data as NotebookNode

        Returns:
            dict with success status
        """
        try:
            # Skip strict validation for compatibility - nbformat.write will handle basic validation
            # nbformat.validate(notebook) - commenting out to avoid version conflicts

            # Create parent directories
            resolved_path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write using temporary file
            temp_path = resolved_path.with_suffix(resolved_path.suffix + '.tmp')

            # Use nbformat to write the notebook
            nbformat.write(notebook, temp_path)

            # Atomic move
            temp_path.replace(resolved_path)

            logger.debug(f"Notebook saved successfully: {resolved_path}")
            return {
                "success": True,
                "file_path": str(resolved_path),
                "saved_at": time.time()
            }

        except ValidationError as e:
            logger.error(f"Notebook validation failed during save: {e}")
            return {"success": False, "error": f"Notebook validation failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Failed to save notebook: {e}")
            # Clean up temp file if it exists
            temp_path_name = resolved_path.with_suffix(resolved_path.suffix + '.tmp')
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