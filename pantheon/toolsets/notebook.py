"""Notebook Toolset - Jupyter notebook editing capabilities"""

import json
from pathlib import Path
from typing import List, Union
from ..toolset import ToolSet, tool
from ..utils.log import logger


class NotebookToolSet(ToolSet):
    """Notebook Toolset with Jupyter notebook capabilities.
    
    This toolset provides comprehensive Jupyter notebook editing functions:
    - Read and display notebook contents with beautiful formatting
    - Edit individual cells (code/markdown)
    - Add new cells at specific positions
    - Delete cells
    - Manage cell metadata and execution counts

    Args:
        name: The name of the toolset.
        workspace_path: The path to the workspace.
        worker_params: The parameters for the worker.
        **kwargs: Additional keyword arguments.
    """
    
    def __init__(
        self,
        name: str,
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        """
        Initialize the Notebook Toolset.
        
        Args:
            name: Name of the toolset
            workspace_path: Base directory for notebook operations (default: current directory)
            worker_params: Parameters for the worker
            **kwargs: Additional keyword arguments
        """
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        
    def _resolve_path(self, file_path: str) -> Path:
        """Resolve file path relative to workspace."""
        path = Path(file_path)
        if path.is_absolute():
            return path
        return self.workspace_path / path
    
    def _validate_path(self, file_path: str) -> tuple[bool, str, Path | None]:
        """
        Validate file path for security and existence.
        
        Returns:
            tuple: (is_valid, error_message, resolved_path)
        """
        if '..' in file_path:
            return False, "Path cannot contain '..' for security reasons", None
            
        resolved_path = self._resolve_path(file_path)
        
        # Check if path is within workspace
        try:
            resolved_path.relative_to(self.workspace_path)
        except ValueError:
            return False, f"Path must be within workspace: {self.workspace_path}", None
            
        return True, "", resolved_path
    
    def _load_notebook(self, file_path: Path) -> tuple[bool, str, dict | None]:
        """
        Load and parse a Jupyter notebook file.
        
        Returns:
            tuple: (success, error_message, notebook_data)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                notebook = json.load(f)
                
            # Validate notebook format
            if not isinstance(notebook, dict):
                return False, "Invalid notebook format: not a JSON object", None
                
            if 'cells' not in notebook:
                return False, "Invalid notebook format: missing 'cells' field", None
                
            if not isinstance(notebook['cells'], list):
                return False, "Invalid notebook format: 'cells' must be a list", None
                
            return True, "", notebook
            
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON format: {str(e)}", None
        except Exception as e:
            return False, f"Error loading notebook: {str(e)}", None
    
    def _save_notebook(self, file_path: Path, notebook: dict) -> tuple[bool, str]:
        """
        Save notebook to file.
        
        Returns:
            tuple: (success, error_message)
        """
        try:
            # Ensure proper notebook structure
            if 'metadata' not in notebook:
                notebook['metadata'] = {}
            if 'nbformat' not in notebook:
                notebook['nbformat'] = 4
            if 'nbformat_minor' not in notebook:
                notebook['nbformat_minor'] = 4
                
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(notebook, f, indent=1, ensure_ascii=False)
                
            return True, ""
            
        except Exception as e:
            return False, f"Error saving notebook: {str(e)}"
    
    def _format_cell_source(self, source: Union[str, List[str]]) -> str:
        """Format cell source for display."""
        if isinstance(source, list):
            return ''.join(source)
        return source or ""
    
    def _print_notebook_content(self, notebook: dict, file_path: str, start_cell: int = None, end_cell: int = None):
        """Print notebook content with beautiful formatting."""
        from rich.syntax import Syntax
        cells = notebook.get('cells', [])
        
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        # Determine cell range
        total_cells = len(cells)
        start = start_cell - 1 if start_cell else 0
        end = end_cell if end_cell else total_cells
        start = max(0, min(start, total_cells))
        end = max(start, min(end, total_cells))
        
        selected_cells = cells[start:end]
        
        logger.info(f"╭─ [bold cyan]Notebook:[/bold cyan] {display_path} " + "─" * (65 - len(display_path)) + "╮")
        logger.info(f"│ [dim]Total cells: {total_cells} | Showing: {start + 1}-{end}[/dim]" + " " * (65 - 35) + "│")
        
        if not selected_cells:
            logger.info("│ [yellow]No cells to display[/yellow]" + " " * 44 + "│")
        else:
            for i, cell in enumerate(selected_cells, start=start + 1):
                cell_type = cell.get('cell_type', 'unknown')
                source = self._format_cell_source(cell.get('source', ''))
                execution_count = cell.get('execution_count')
                
                # Cell header
                logger.info("├" + "─" * 74 + "┤")
                
                if cell_type == 'code':
                    exec_info = f" [{execution_count}]" if execution_count else " [ ]"
                    logger.info(f"│ [bold blue]Cell {i}[/bold blue] [dim]Code{exec_info}[/dim]" + " " * (65 - len(f"Cell {i} Code{exec_info}")) + "│")
                elif cell_type == 'markdown':
                    logger.info(f"│ [bold green]Cell {i}[/bold green] [dim]Markdown[/dim]" + " " * (65 - len(f"Cell {i} Markdown")) + "│")
                else:
                    logger.info(f"│ [bold]Cell {i}[/bold] [dim]{cell_type}[/dim]" + " " * (65 - len(f"Cell {i} {cell_type}")) + "│")
                
                # Cell content
                if source.strip():
                    lines = source.split('\n')
                    max_display_lines = 10
                    
                    if len(lines) <= max_display_lines:
                        display_lines = lines
                    else:
                        display_lines = lines[:max_display_lines] + [f"... ({len(lines) - max_display_lines} more lines)"]
                    
                    for line in display_lines:
                        # Truncate long lines
                        if len(line) > 70:
                            display_line = line[:67] + "..."
                        else:
                            display_line = line
                        
                        padding = max(0, 72 - len(display_line))
                        if cell_type == 'code':
                            logger.info(f"│ [cyan]{display_line}[/cyan]" + " " * padding + " │")
                        else:
                            logger.info(f"│ {display_line}" + " " * padding + " │")
                else:
                    logger.info("│ [dim](empty cell)[/dim]" + " " * 59 + "│")
                
                # Show outputs for code cells if they exist
                if cell_type == 'code' and 'outputs' in cell and cell['outputs']:
                    logger.info("│ [dim]Outputs:[/dim]" + " " * 54 + "│")
                    for output in cell['outputs'][:3]:  # Show first 3 outputs
                        if 'text' in output:
                            text_lines = output['text'][:2]  # Show first 2 lines
                            for text_line in text_lines:
                                display_line = text_line.rstrip()[:67] + ("..." if len(text_line.rstrip()) > 67 else "")
                                padding = max(0, 70 - len(display_line))
                                logger.info(f"│ [yellow]  {display_line}[/yellow]" + " " * padding + "│")
        
        logger.info("╰" + "─" * 74 + "╯")

    @tool
    async def read_notebook(
        self,
        file_path: str,
        start_cell: int = None,
        end_cell: int = None
    ) -> dict:
        """
        Read and display a Jupyter notebook with beautiful formatting.
        
        Args:
            file_path: Path to the .ipynb file
            start_cell: Starting cell number (1-indexed, optional)
            end_cell: Ending cell number (1-indexed, optional)
            
        Returns:
            dict with success status and notebook info
        """
        logger.info(f"[cyan]Reading notebook: {file_path}[/cyan]")
        
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            logger.info(f"[red]❌ Invalid path: {error_msg}[/red]")
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            logger.info(f"[red]❌ Notebook file not found[/red]")
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        if not resolved_path.suffix.lower() == '.ipynb':
            logger.info(f"[red]❌ Not a Jupyter notebook file[/red]")
            return {"success": False, "error": f"File is not a Jupyter notebook: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            logger.info(f"[red]❌ Failed to load notebook: {error_msg}[/red]")
            return {"success": False, "error": error_msg}
            
        # Print the notebook content
        self._print_notebook_content(notebook, file_path, start_cell, end_cell)
        
        logger.info(f"[green]✅ Notebook loaded successfully ({len(notebook['cells'])} cells)[/green]")
        
        return {
            "success": True,
            "file_path": str(resolved_path),
            "total_cells": len(notebook['cells']),
            "displayed_range": f"{start_cell or 1}-{end_cell or len(notebook['cells'])}",
            "nbformat": notebook.get('nbformat', 4)
        }

    @tool
    async def edit_notebook_cell(
        self,
        file_path: str,
        cell_number: int,
        new_content: str,
        cell_type: str = None
    ) -> dict:
        """
        Edit a specific cell in a Jupyter notebook.
        
        Args:
            file_path: Path to the .ipynb file
            cell_number: Cell number to edit (1-indexed)
            new_content: New content for the cell
            cell_type: Cell type ('code' or 'markdown'), optional
            
        Returns:
            dict with success status and edit details
        """
        logger.info(f"[cyan]Editing cell {cell_number} in: {file_path}[/cyan]")
        
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            logger.info(f"[red]❌ Invalid path: {error_msg}[/red]")
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            logger.info(f"[red]❌ Notebook file not found[/red]")
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            logger.info(f"[red]❌ Failed to load notebook: {error_msg}[/red]")
            return {"success": False, "error": error_msg}
            
        cells = notebook['cells']
        if cell_number < 1 or cell_number > len(cells):
            logger.info(f"[red]❌ Cell number {cell_number} out of range (1-{len(cells)})[/red]")
            return {
                "success": False, 
                "error": f"Cell number {cell_number} out of range (notebook has {len(cells)} cells)"
            }
            
        cell_index = cell_number - 1
        cell = cells[cell_index]
        
        # Store original content for diff display
        original_content = self._format_cell_source(cell.get('source', ''))
        
        # Update cell content
        cell['source'] = new_content.split('\n') if '\n' in new_content else [new_content]
        
        # Update cell type if specified
        if cell_type and cell_type in ['code', 'markdown']:
            cell['cell_type'] = cell_type
            
        # Clear outputs and execution count for code cells if content changed
        if cell.get('cell_type') == 'code':
            cell['outputs'] = []
            cell['execution_count'] = None
            
        # Print diff
        self._print_cell_diff(original_content, new_content, file_path, cell_number, cell.get('cell_type', 'code'))
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            logger.info(f"[red]❌ Failed to save notebook: {error_msg}[/red]")
            return {"success": False, "error": error_msg}
            
        logger.info(f"[green]✅ Cell {cell_number} edited successfully[/green]")
        
        return {
            "success": True,
            "file_path": str(resolved_path),
            "cell_number": cell_number,
            "cell_type": cell.get('cell_type', 'unknown'),
            "content_length": len(new_content)
        }
    
    def _print_cell_diff(self, original: str, new: str, file_path: str, cell_number: int, cell_type: str):
        """Print cell diff with colors."""
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        logger.info("")
        logger.info(f"╭─ [bold cyan]Cell {cell_number} changes:[/bold cyan] {display_path} ({cell_type}) " + "─" * (50 - len(display_path)) + "╮")
        
        original_lines = original.split('\n')
        new_lines = new.split('\n')
        
        import difflib
        diff = list(difflib.unified_diff(original_lines, new_lines, lineterm=""))
        
        if not diff:
            logger.info("│ [yellow]No changes detected[/yellow]" + " " * 42 + "│")
        else:
            showed_content = False
            for line in diff:
                if line.startswith('---') or line.startswith('+++') or line.startswith('@@'):
                    continue
                    
                # Truncate long lines
                display_line = line[:70] if len(line) <= 70 else line[:67] + "..."
                padding = max(0, 72 - len(display_line))
                
                if line.startswith('-'):
                    logger.info(f"│ [red]{display_line}[/red]" + " " * padding + "│")
                    showed_content = True
                elif line.startswith('+'):
                    logger.info(f"│ [green]{display_line}[/green]" + " " * padding + "│")
                    showed_content = True
                elif line.startswith(' '):
                    logger.info(f"│ [dim]{display_line}[/dim]" + " " * padding + "│")
                    showed_content = True
            
            if not showed_content:
                logger.info("│ [dim]Content replaced[/dim]" + " " * 47 + "│")
        
        logger.info("╰" + "─" * 74 + "╯")

    @tool
    async def add_notebook_cell(
        self,
        file_path: str,
        cell_type: str = "code",
        content: str = "",
        position: int = None
    ) -> dict:
        """
        Add a new cell to a Jupyter notebook.
        
        Args:
            file_path: Path to the .ipynb file
            cell_type: Type of cell ('code' or 'markdown')
            content: Initial content for the cell
            position: Position to insert (1-indexed, None for end)
            
        Returns:
            dict with success status
        """
        if cell_type not in ['code', 'markdown']:
            return {"success": False, "error": "Cell type must be 'code' or 'markdown'"}
            
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            return {"success": False, "error": error_msg}
            
        cells = notebook['cells']
        
        # Create new cell
        new_cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": content.split('\n') if '\n' in content else [content]
        }
        
        if cell_type == 'code':
            new_cell["execution_count"] = None
            new_cell["outputs"] = []
            
        # Determine insert position
        if position is None:
            insert_pos = len(cells)
            cells.append(new_cell)
        else:
            if position < 1:
                position = 1
            elif position > len(cells) + 1:
                position = len(cells) + 1
                
            insert_pos = position - 1
            cells.insert(insert_pos, new_cell)
        
        # Print notification
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        logger.info("")
        logger.info(f"╭─ [bold]Added {cell_type} cell:[/bold] {display_path} " + "─" * (55 - len(display_path)) + "╮")
        logger.info(f"│ [green]✓[/green] New cell {insert_pos + 1} added" + " " * 48 + "│")
        if content.strip():
            lines = content.split('\n')[:3]  # Show first 3 lines
            for line in lines:
                display_line = line[:67] + ("..." if len(line) > 67 else "")
                padding = max(0, 70 - len(display_line))
                logger.info(f"│   {display_line}" + " " * padding + " │")
        logger.info("╰" + "─" * 74 + "╯")
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            return {"success": False, "error": error_msg}
            
        return {
            "success": True,
            "file_path": str(resolved_path),
            "cell_number": insert_pos + 1,
            "cell_type": cell_type,
            "total_cells": len(cells)
        }

    @tool
    async def delete_notebook_cell(
        self,
        file_path: str,
        cell_number: int
    ) -> dict:
        """
        Delete a cell from a Jupyter notebook.
        
        Args:
            file_path: Path to the .ipynb file
            cell_number: Cell number to delete (1-indexed)
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            return {"success": False, "error": error_msg}
            
        cells = notebook['cells']
        if cell_number < 1 or cell_number > len(cells):
            return {
                "success": False, 
                "error": f"Cell number {cell_number} out of range (notebook has {len(cells)} cells)"
            }
            
        cell_index = cell_number - 1
        deleted_cell = cells.pop(cell_index)
        
        # Print notification
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        logger.info(f"╭─ [bold]Deleted cell {cell_number}:[/bold] {display_path} " + "─" * (50 - len(display_path)) + "╮")
        logger.info(f"│ [red]✗[/red] {deleted_cell.get('cell_type', 'unknown')} cell removed" + " " * 41 + "│")
        logger.info(f"│ [dim]Remaining cells: {len(cells)}[/dim]" + " " * 40 + "│")
        logger.info("╰" + "─" * 74 + "╯")
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            return {"success": False, "error": error_msg}
            
        return {
            "success": True,
            "file_path": str(resolved_path),
            "deleted_cell_number": cell_number,
            "deleted_cell_type": deleted_cell.get('cell_type', 'unknown'),
            "remaining_cells": len(cells)
        }

    @tool
    async def create_notebook(
        self,
        file_path: str,
        title: str = None
    ) -> dict:
        """
        Create a new Jupyter notebook.
        
        Args:
            file_path: Path for the new notebook (.ipynb)
            title: Optional title for the notebook
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if resolved_path.exists():
            return {"success": False, "error": f"Notebook already exists: {file_path}"}
            
        if not resolved_path.suffix.lower() == '.ipynb':
            resolved_path = resolved_path.with_suffix('.ipynb')
            
        # Create basic notebook structure
        notebook = {
            "cells": [],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3"
                },
                "language_info": {
                    "name": "python",
                    "version": "3.8.0"
                }
            },
            "nbformat": 4,
            "nbformat_minor": 4
        }
        
        if title:
            # Add title as first markdown cell
            title_cell = {
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"# {title}"]
            }
            notebook["cells"].append(title_cell)
            
        # Create parent directories if needed
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            return {"success": False, "error": error_msg}
            
        # Print notification
        display_path = resolved_path.name
        logger.info("")
        logger.info(f"╭─ [bold]Created notebook:[/bold] {display_path} " + "─" * (55 - len(display_path)) + "╮")
        logger.info(f"│ [green]✓[/green] New Jupyter notebook created" + " " * 37 + "│")
        if title:
            logger.info(f"│   Title: {title}" + " " * (65 - len(title)) + "│")
        logger.info(f"│   Cells: {len(notebook['cells'])}" + " " * 60 + "│")
        logger.info("╰" + "─" * 74 + "╯")
        
        return {
            "success": True,
            "file_path": str(resolved_path),
            "title": title,
            "cells_count": len(notebook["cells"])
        }

    @tool
    async def copy_notebook_cell(
        self,
        file_path: str,
        source_cell: int,
        target_position: int = None
    ) -> dict:
        """
        Copy a cell to another position in the notebook.
        
        Args:
            file_path: Path to the .ipynb file
            source_cell: Cell number to copy (1-indexed)
            target_position: Position to insert copy (1-indexed, None for end)
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            return {"success": False, "error": error_msg}
            
        cells = notebook['cells']
        if source_cell < 1 or source_cell > len(cells):
            return {
                "success": False, 
                "error": f"Source cell {source_cell} out of range (notebook has {len(cells)} cells)"
            }
            
        # Copy the cell
        source_index = source_cell - 1
        cell_copy = cells[source_index].copy()
        
        # Clear execution info for copied code cells
        if cell_copy.get('cell_type') == 'code':
            cell_copy['execution_count'] = None
            cell_copy['outputs'] = []
            
        # Insert the copy
        if target_position is None:
            insert_pos = len(cells)
            cells.append(cell_copy)
        else:
            if target_position < 1:
                target_position = 1
            elif target_position > len(cells) + 1:
                target_position = len(cells) + 1
                
            insert_pos = target_position - 1
            cells.insert(insert_pos, cell_copy)
        
        # Print notification
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        logger.info("")
        logger.info(f"╭─ [bold]Copied cell {source_cell}:[/bold] {display_path} " + "─" * (55 - len(display_path)) + "╮")
        logger.info(f"│ [blue]📋[/blue] Cell {source_cell} → Cell {insert_pos + 1}" + " " * 45 + "│")
        logger.info(f"│ [dim]Type: {cell_copy.get('cell_type', 'unknown')}[/dim]" + " " * 50 + "│")
        logger.info("╰" + "─" * 74 + "╯")
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            return {"success": False, "error": error_msg}
            
        return {
            "success": True,
            "file_path": str(resolved_path),
            "source_cell": source_cell,
            "new_cell_position": insert_pos + 1,
            "total_cells": len(cells)
        }

    @tool
    async def move_notebook_cell(
        self,
        file_path: str,
        source_cell: int,
        target_position: int
    ) -> dict:
        """
        Move a cell to a different position in the notebook.
        
        Args:
            file_path: Path to the .ipynb file
            source_cell: Cell number to move (1-indexed)
            target_position: New position (1-indexed)
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            return {"success": False, "error": error_msg}
            
        cells = notebook['cells']
        if source_cell < 1 or source_cell > len(cells):
            return {
                "success": False, 
                "error": f"Source cell {source_cell} out of range (notebook has {len(cells)} cells)"
            }
            
        if target_position < 1 or target_position > len(cells):
            return {
                "success": False, 
                "error": f"Target position {target_position} out of range (notebook has {len(cells)} cells)"
            }
            
        if source_cell == target_position:
            return {"success": False, "error": "Source and target positions are the same"}
            
        # Move the cell
        source_index = source_cell - 1
        target_index = target_position - 1
        
        cell = cells.pop(source_index)
        
        # Adjust target index if we're moving down (after removal)
        if target_index > source_index:
            target_index -= 1
            
        cells.insert(target_index, cell)
        
        # Print notification
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        logger.info("")
        logger.info(f"╭─ [bold]Moved cell:[/bold] {display_path} " + "─" * (60 - len(display_path)) + "╮")
        logger.info(f"│ [yellow]🔄[/yellow] Cell {source_cell} → Position {target_position}" + " " * 40 + "│")
        logger.info(f"│ [dim]Type: {cell.get('cell_type', 'unknown')}[/dim]" + " " * 50 + "│")
        logger.info("╰" + "─" * 74 + "╯")
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            return {"success": False, "error": error_msg}
            
        return {
            "success": True,
            "file_path": str(resolved_path),
            "source_cell": source_cell,
            "new_position": target_position,
            "total_cells": len(cells)
        }

    @tool
    async def add_notebook_template(
        self,
        file_path: str,
        template_type: str = "data_analysis"
    ) -> dict:
        """
        Add a template structure to an existing notebook.
        
        Args:
            file_path: Path to the .ipynb file
            template_type: Type of template ("data_analysis", "ml_workflow", "research")
            
        Returns:
            dict with success status
        """
        templates = {
            "data_analysis": [
                ("markdown", "# Data Analysis\n\nOverview of the analysis workflow."),
                ("markdown", "## 1. Import Libraries\n\nImport all necessary libraries."),
                ("code", "import pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt\nimport seaborn as sns\n\n# Set plotting style\nplt.style.use('default')\nsns.set_palette('husl')"),
                ("markdown", "## 2. Load Data\n\nLoad and explore the dataset."),
                ("code", "# Load your data here\n# df = pd.read_csv('your_data.csv')\n\n# Display basic information\n# print(df.info())\n# df.head()"),
                ("markdown", "## 3. Data Exploration\n\nExplore data structure and patterns."),
                ("code", "# Explore data distribution\n# df.describe()\n\n# Check for missing values\n# df.isnull().sum()"),
                ("markdown", "## 4. Data Visualization\n\nCreate visualizations to understand the data."),
                ("code", "# Create your plots here\n# plt.figure(figsize=(10, 6))\n# # Add your plotting code"),
                ("markdown", "## 5. Analysis Results\n\nSummarize findings and conclusions.")
            ],
            
            "ml_workflow": [
                ("markdown", "# Machine Learning Workflow\n\nComplete ML pipeline from data to model."),
                ("markdown", "## 1. Setup\n\nImport libraries and set random seeds."),
                ("code", "import pandas as pd\nimport numpy as np\nfrom sklearn.model_selection import train_test_split\nfrom sklearn.preprocessing import StandardScaler\nfrom sklearn.metrics import classification_report, confusion_matrix\nimport matplotlib.pyplot as plt\nimport seaborn as sns\n\n# Set random seed for reproducibility\nnp.random.seed(42)"),
                ("markdown", "## 2. Data Loading & Preprocessing"),
                ("code", "# Load and preprocess data\n# df = pd.read_csv('your_data.csv')\n# X = df.drop('target', axis=1)\n# y = df['target']"),
                ("markdown", "## 3. Train-Test Split"),
                ("code", "# Split the data\n# X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)"),
                ("markdown", "## 4. Model Training"),
                ("code", "# Train your model\n# model = YourModel()\n# model.fit(X_train, y_train)"),
                ("markdown", "## 5. Model Evaluation"),
                ("code", "# Evaluate model performance\n# y_pred = model.predict(X_test)\n# print(classification_report(y_test, y_pred))")
            ],
            
            "research": [
                ("markdown", "# Research Notebook\n\nDocumented research analysis with reproducible results."),
                ("markdown", "## Abstract\n\nBrief description of the research question and approach."),
                ("markdown", "## Introduction\n\nBackground and motivation for the research."),
                ("markdown", "## Methodology\n\nDescription of methods and approaches used."),
                ("code", "# Import necessary libraries\nimport pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt\nimport scipy.stats as stats"),
                ("markdown", "## Data Collection\n\nDescription of data sources and collection methods."),
                ("code", "# Load and examine data\n# data = pd.read_csv('research_data.csv')\n# print(f\"Data shape: {data.shape}\")"),
                ("markdown", "## Analysis\n\nMain analysis and statistical tests."),
                ("code", "# Perform statistical analysis\n# Add your analysis code here"),
                ("markdown", "## Results\n\nPresentation of findings with visualizations."),
                ("markdown", "## Discussion\n\nInterpretation of results and implications."),
                ("markdown", "## Conclusion\n\nSummary of findings and future directions.")
            ]
        }
        
        if template_type not in templates:
            return {
                "success": False, 
                "error": f"Unknown template type: {template_type}. Available: {list(templates.keys())}"
            }
            
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Notebook file not found: {file_path}"}
            
        success, error_msg, notebook = self._load_notebook(resolved_path)
        if not success:
            return {"success": False, "error": error_msg}
            
        # Add template cells
        template_cells = templates[template_type]
        cells = notebook['cells']
        added_count = 0
        
        for cell_type, content in template_cells:
            new_cell = {
                "cell_type": cell_type,
                "metadata": {},
                "source": content.split('\n') if '\n' in content else [content]
            }
            
            if cell_type == 'code':
                new_cell["execution_count"] = None
                new_cell["outputs"] = []
                
            cells.append(new_cell)
            added_count += 1
        
        # Print notification
        display_path = file_path.split('/')[-1] if file_path else "notebook"
        
        logger.info("")
        logger.info(f"╭─ [bold]Added {template_type} template:[/bold] {display_path} " + "─" * (50 - len(display_path)) + "╮")
        logger.info(f"│ [green]✓[/green] {added_count} template cells added" + " " * 42 + "│")
        logger.info(f"│ [dim]Total cells: {len(cells)}[/dim]" + " " * 48 + "│")
        logger.info("╰" + "─" * 74 + "╯")
        
        # Save notebook
        success, error_msg = self._save_notebook(resolved_path, notebook)
        if not success:
            return {"success": False, "error": error_msg}
            
        return {
            "success": True,
            "file_path": str(resolved_path),
            "template_type": template_type,
            "cells_added": added_count,
            "total_cells": len(cells)
        }


__all__ = ["NotebookToolSet"]
