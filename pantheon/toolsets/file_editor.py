"""File Editor Toolset - Claude-like file editing capabilities"""

from pathlib import Path
import difflib
import re
from ..toolset import ToolSet, tool
from ..utils.log import logger


class FileEditorToolSet(ToolSet):
    """File Editor Toolset with Claude-like editing capabilities.
    
    This toolset provides advanced file editing functions similar to Claude Code:
    - Read files with line numbers
    - Edit files by searching and replacing text
    - View specific line ranges
    - Insert content at specific locations
    - Delete lines or text blocks

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
        Initialize the File Editor Toolset.
        
        Args:
            name: Name of the toolset
            workspace_path: Base directory for file operations (default: current directory)
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
    
    def _print_diff_display(self, original_content: str, new_content: str, file_path: str):
        """
        Print a visual diff display in Claude Code style with line numbers.
        """
        original_lines = original_content.splitlines()
        new_lines = new_content.splitlines()
        
        # Count actual changes for header
        additions = 0
        removals = 0
        
        # Use SequenceMatcher for better diff generation
        matcher = difflib.SequenceMatcher(None, original_lines, new_lines)
        
        # Calculate stats first
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'delete':
                removals += i2 - i1
            elif tag == 'insert':
                additions += j2 - j1
            elif tag == 'replace':
                removals += i2 - i1
                additions += j2 - j1
        
        if additions == 0 and removals == 0:
            logger.info("[dim]No changes detected.[/dim]")
            return
        
        # Print header in Claude Code style
        display_path = file_path
        logger.info(f"Update({display_path})")
        
        additions_text = f"{additions} addition{'s' if additions != 1 else ''}" if additions > 0 else ""
        removals_text = f"{removals} removal{'s' if removals != 1 else ''}" if removals > 0 else ""
        
        if additions_text and removals_text:
            changes_text = f"{additions_text} and {removals_text}"
        elif additions_text:
            changes_text = additions_text
        elif removals_text:
            changes_text = removals_text
        else:
            changes_text = "no changes"
            
        logger.info(f"  ⎿  Updated {display_path} with {changes_text}")
        
        # Generate line-by-line diff with line numbers
        old_line_num = 1
        new_line_num = 1
        context_lines = 3
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                # Show context lines around changes
                equal_lines = original_lines[i1:i2]
                for idx, line in enumerate(equal_lines):
                    line_num = old_line_num + idx
                    # Only show limited context
                    if idx < context_lines or len(equal_lines) - idx <= context_lines:
                        logger.info(f"       {line_num:3d}                {line}")
                    elif idx == context_lines:
                        logger.info("       ...")
                old_line_num += len(equal_lines)
                new_line_num += len(equal_lines)
                
            elif tag == 'delete':
                # Show deleted lines with red background
                for idx in range(i1, i2):
                    line_content = original_lines[idx] if idx < len(original_lines) else ""
                    logger.info(f"       [on red]{old_line_num:3d} -              {line_content}[/on red]")
                    old_line_num += 1
                    
            elif tag == 'insert':
                # Show inserted lines with green background
                for idx in range(j1, j2):
                    line_content = new_lines[idx] if idx < len(new_lines) else ""
                    logger.info(f"       [on green]{new_line_num:3d} +              {line_content}[/on green]")
                    new_line_num += 1
                    
            elif tag == 'replace':
                # Show replaced lines - deletions first with red background
                for idx in range(i1, i2):
                    line_content = original_lines[idx] if idx < len(original_lines) else ""
                    logger.info(f"       [on red]{old_line_num:3d} -              {line_content}[/on red]")
                    old_line_num += 1
                
                # Then insertions with green background
                for idx in range(j1, j2):
                    line_content = new_lines[idx] if idx < len(new_lines) else ""
                    logger.info(f"       [on green]{new_line_num:3d} +              {line_content}[/on green]")
                    new_line_num += 1
    
    @tool
    async def read_file(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        show_line_numbers: bool = True
    ) -> dict:
        """
        Read a file with optional line range and line numbers.
        
        Args:
            file_path: Path to the file to read
            start_line: Starting line number (1-indexed, inclusive)
            end_line: Ending line number (1-indexed, inclusive)
            show_line_numbers: Whether to show line numbers in output
            
        Returns:
            dict with success status and file content
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
            
        if not resolved_path.is_file():
            return {"success": False, "error": f"Path is not a file: {file_path}"}
            
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            total_lines = len(lines)
            
            # Adjust line ranges
            start = (start_line - 1) if start_line else 0
            end = end_line if end_line else total_lines
            start = max(0, min(start, total_lines - 1))
            end = max(1, min(end, total_lines))
            
            # Get the requested lines
            selected_lines = lines[start:end]
            
            if show_line_numbers:
                # Format with line numbers - print directly to console for better formatting
                display_path = file_path.split('/')[-1] if file_path else "file"
                logger.info("")
                logger.info(f"╭─ [bold]File content:[/bold] {display_path} " + "─" * (65 - 15 - len(display_path)) + "╮")
                
                for i, line in enumerate(selected_lines, start=start + 1):
                    # Format: " 5 | content" with proper alignment
                    line_no = str(i).rjust(3)
                    line_content = line.rstrip()
                    if len(line_content) > 65:
                        line_content = line_content[:62] + "..."
                    padding = max(0, 65 - len(line_content) - 8)
                    logger.info(f"   [dim]{line_no}[/dim] │ {line_content}" + " " * padding )
                
                logger.info("╰" + "─" * 75 + "╯")
                
                # Return actual content even when showing line numbers
                content = ''.join(selected_lines)
            else:
                content = ''.join(selected_lines)
                
            return {
                "success": True,
                "file_path": str(resolved_path),
                "content": content,
                "total_lines": total_lines,
                "displayed_lines": f"{start + 1}-{end}" if start_line or end_line else "all"
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error reading file: {str(e)}"}
    
    @tool
    async def write_file(self, file_path: str, content: str) -> dict:
        """
        Write content to a file (creates if doesn't exist).
        
        Args:
            file_path: Path to the file
            content: Content to write
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        try:
            # Create parent directories if they don't exist
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Check if file exists for the response message
            is_new = not resolved_path.exists()
            
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Show success message
            action_word = "created" if is_new else "updated"
            lines_count = content.count('\n') + 1
            logger.info(f"[green]File {action_word} successfully - {lines_count} lines written[/green]")
                
            return {
                "success": True,
                "action": "created" if is_new else "updated",
                "file_path": str(resolved_path),
                "size": len(content),
                "lines": content.count('\n') + 1
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error writing file: {str(e)}"}
    
    @tool
    async def edit_file(
        self,
        file_path: str,
        old_text: str,
        new_text: str,
        occurrence: int | None = None
    ) -> dict:
        """
        Edit a file by replacing text (similar to Claude's Edit tool).
        
        Args:
            file_path: Path to the file to edit
            old_text: Text to search for and replace
            new_text: Text to replace with
            occurrence: Which occurrence to replace (1-indexed). None means replace all.
            
        Returns:
            dict with success status and edit details
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
            
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
                
            # Count occurrences
            occurrences = original_content.count(old_text)
            
            if occurrences == 0:
                return {
                    "success": False,
                    "error": f"Text not found in file: '{old_text[:50]}...'" if len(old_text) > 50 else f"Text not found in file: '{old_text}'"
                }
                
            # Perform replacement
            if occurrence is None:
                # Replace all occurrences
                new_content = original_content.replace(old_text, new_text)
                replaced_count = occurrences
            else:
                # Replace specific occurrence
                if occurrence < 1 or occurrence > occurrences:
                    return {
                        "success": False,
                        "error": f"Invalid occurrence number. File has {occurrences} occurrences, requested {occurrence}"
                    }
                    
                # Replace the nth occurrence
                parts = original_content.split(old_text)
                new_content = old_text.join(parts[:occurrence]) + new_text + old_text.join(parts[occurrence:])
                replaced_count = 1
            
            # Print diff display immediately
            self._print_diff_display(original_content, new_content, file_path)
                
            # Write back to file
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Show success message
            logger.info(f"[green]File edited successfully - {replaced_count} replacement(s) made[/green]")
                
            return {
                "success": True,
                "file_path": str(resolved_path),
                "replaced_count": replaced_count,
                "total_occurrences": occurrences,
                "old_text_preview": old_text[:100] + "..." if len(old_text) > 100 else old_text,
                "new_text_preview": new_text[:100] + "..." if len(new_text) > 100 else new_text
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error editing file: {str(e)}"}
    
    @tool
    async def search_in_file(
        self,
        file_path: str,
        search_text: str,
        regex: bool = False,
        case_sensitive: bool = True,
        context_lines: int = 2
    ) -> dict:
        """
        Search for text in a file and show matches with context.
        
        Args:
            file_path: Path to the file to search
            search_text: Text or regex pattern to search for
            regex: Whether search_text is a regex pattern
            case_sensitive: Whether search is case-sensitive
            context_lines: Number of context lines to show around matches
            
        Returns:
            dict with search results
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
            
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            matches = []
            
            if regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(search_text, flags)
            else:
                if not case_sensitive:
                    search_text = search_text.lower()
                    
            for i, line in enumerate(lines, 1):
                line_to_search = line if case_sensitive else line.lower()
                
                if regex:
                    if pattern.search(line):
                        matches.append(i)
                else:
                    if search_text in line_to_search:
                        matches.append(i)
                        
            if not matches:
                return {
                    "success": True,
                    "file_path": str(resolved_path),
                    "matches": [],
                    "match_count": 0,
                    "message": "No matches found"
                }
                
            # Prepare match results with context
            match_results = []
            for line_no in matches:
                start = max(1, line_no - context_lines)
                end = min(len(lines), line_no + context_lines)
                
                context = []
                for j in range(start, end + 1):
                    prefix = ">>>" if j == line_no else "   "
                    line_str = str(j).rjust(6)
                    context.append(f"{prefix} {line_str} | {lines[j-1].rstrip()}")
                    
                match_results.append({
                    "line_number": line_no,
                    "line": lines[line_no - 1].rstrip(),
                    "context": "\n".join(context)
                })
                
            return {
                "success": True,
                "file_path": str(resolved_path),
                "matches": match_results,
                "match_count": len(matches),
                "search_pattern": search_text
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error searching file: {str(e)}"}
    
    @tool
    async def insert_at_line(
        self,
        file_path: str,
        line_number: int,
        content: str,
        position: str = "after"
    ) -> dict:
        """
        Insert content at a specific line number.
        
        Args:
            file_path: Path to the file
            line_number: Line number where to insert (1-indexed)
            content: Content to insert
            position: "before" or "after" the specified line
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
            
        if position not in ["before", "after"]:
            return {"success": False, "error": "Position must be 'before' or 'after'"}
            
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
                lines = original_content.splitlines(True)
                
            total_lines = len(lines)
            
            if line_number < 1 or line_number > total_lines:
                return {
                    "success": False,
                    "error": f"Line number {line_number} out of range (file has {total_lines} lines)"
                }
                
            # Ensure content ends with newline if it doesn't
            if content and not content.endswith('\n'):
                content += '\n'
                
            # Insert at the appropriate position
            if position == "before":
                lines.insert(line_number - 1, content)
            else:  # after
                lines.insert(line_number, content)
                
            # Generate new content and print diff
            new_content = ''.join(lines)
            self._print_diff_display(original_content, new_content, file_path)
                
            # Write back
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
                
            return {
                "success": True,
                "file_path": str(resolved_path),
                "inserted_at": f"line {line_number} ({position})",
                "new_total_lines": len(lines)
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error inserting content: {str(e)}"}
    
    @tool
    async def delete_lines(
        self,
        file_path: str,
        start_line: int,
        end_line: int | None = None
    ) -> dict:
        """
        Delete one or more lines from a file.
        
        Args:
            file_path: Path to the file
            start_line: Starting line to delete (1-indexed)
            end_line: Ending line to delete (inclusive). If None, only delete start_line.
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
            
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
                lines = original_content.splitlines(True)
                
            total_lines = len(lines)
            
            if end_line is None:
                end_line = start_line
                
            if start_line < 1 or start_line > total_lines:
                return {
                    "success": False,
                    "error": f"Start line {start_line} out of range (file has {total_lines} lines)"
                }
                
            if end_line < start_line or end_line > total_lines:
                return {
                    "success": False,
                    "error": f"End line {end_line} out of range or less than start line"
                }
                
            # Delete the lines
            deleted_content = lines[start_line - 1:end_line]
            del lines[start_line - 1:end_line]
            
            # Generate new content and print diff
            new_content = ''.join(lines)
            self._print_diff_display(original_content, new_content, file_path)
            
            # Write back
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
                
            return {
                "success": True,
                "file_path": str(resolved_path),
                "deleted_lines": f"{start_line}-{end_line}" if end_line != start_line else str(start_line),
                "deleted_count": end_line - start_line + 1,
                "new_total_lines": len(lines),
                "deleted_preview": ''.join(deleted_content[:5]) + "..." if len(deleted_content) > 5 else ''.join(deleted_content)
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error deleting lines: {str(e)}"}
    
    @tool
    async def list_files(
        self,
        directory: str = ".",
        pattern: str | None = None,
        recursive: bool = False
    ) -> dict:
        """
        List files in a directory with optional pattern matching.
        
        Args:
            directory: Directory to list (relative to workspace)
            pattern: Glob pattern to filter files (e.g., "*.py")
            recursive: Whether to list recursively
            
        Returns:
            dict with file list
        """
        is_valid, error_msg, resolved_path = self._validate_path(directory)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Directory not found: {directory}"}
            
        if not resolved_path.is_dir():
            return {"success": False, "error": f"Path is not a directory: {directory}"}
            
        try:
            if pattern:
                if recursive:
                    files = list(resolved_path.rglob(pattern))
                else:
                    files = list(resolved_path.glob(pattern))
            else:
                if recursive:
                    files = list(resolved_path.rglob("*"))
                else:
                    files = list(resolved_path.glob("*"))
                    
            # Sort and format results
            file_list = []
            for f in sorted(files):
                try:
                    rel_path = f.relative_to(self.workspace_path)
                    file_info = {
                        "path": str(rel_path),
                        "type": "dir" if f.is_dir() else "file",
                        "size": f.stat().st_size if f.is_file() else None
                    }
                    file_list.append(file_info)
                except:
                    continue
                    
            return {
                "success": True,
                "directory": str(resolved_path),
                "files": file_list,
                "count": len(file_list),
                "pattern": pattern or "*",
                "recursive": recursive
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error listing files: {str(e)}"}
    
    @tool
    async def create_file(
        self,
        file_path: str,
        content: str = "",
        overwrite: bool = False
    ) -> dict:
        """
        Create a new file with content.
        
        Args:
            file_path: Path for the new file
            content: Initial content for the file
            overwrite: Whether to overwrite if file exists
            
        Returns:
            dict with success status
        """
        is_valid, error_msg, resolved_path = self._validate_path(file_path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if resolved_path.exists() and not overwrite:
            return {"success": False, "error": f"File already exists: {file_path}. Use overwrite=True to replace."}
            
        try:
            # Create parent directories if needed
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
            return {
                "success": True,
                "file_path": str(resolved_path),
                "created": True,
                "size": len(content),
                "lines": content.count('\n') + 1 if content else 0
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error creating file: {str(e)}"}


__all__ = ["FileEditorToolSet"]
