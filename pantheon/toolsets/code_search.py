"""Code Search Toolset - Claude Code style file and content search capabilities"""

import re
from pathlib import Path
from ..toolset import ToolSet, tool
from ..utils.log import logger


class CodeSearchToolSet(ToolSet):
    """Code Search Toolset with Claude Code-like search capabilities.
    
    This toolset provides file and content search functions similar to Claude Code:
    - Glob: File pattern matching and search
    - Grep: Content search within files (ripgrep-style)
    - LS: Enhanced directory listing

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
        Initialize the Code Search Toolset.
        
        Args:
            name: Name of the toolset
            workspace_path: Base directory for search operations (default: current directory)
            worker_params: Parameters for the worker
            **kwargs: Additional keyword arguments
        """
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
                
    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to workspace."""
        path_obj = Path(path)
        if path_obj.is_absolute():
            return path_obj
        return self.workspace_path / path_obj
    
    def _validate_path(self, path: str) -> tuple[bool, str, Path | None]:
        """
        Validate path for security and existence.
        
        Returns:
            tuple: (is_valid, error_message, resolved_path)
        """
        if '..' in path:
            return False, "Path cannot contain '..' for security reasons", None
            
        resolved_path = self._resolve_path(path)
        
        # Check if path is within workspace (allow some flexibility for searches)
        try:
            resolved_path.relative_to(self.workspace_path.parent)
        except ValueError:
            return False, f"Path must be within accessible area: {self.workspace_path.parent}", None
            
        return True, "", resolved_path
    
    def _print_single_item(self, item: Path, base_path: Path, connector: str, show_details: bool):
        """Print a single file or directory item."""
        try:
            if item.is_dir():
                display_name = self._truncate_name(item.name, 60)
                logger.info(f"{connector}📁 [bold cyan]{display_name}[/bold cyan]")
            else:
                display_name = item.name
                
                # Calculate size info
                size_info = ""
                if show_details:
                    size = item.stat().st_size
                    if size < 1024:
                        size_info = f" [dim]({size}B)[/dim]"
                    elif size < 1024 * 1024:
                        size_info = f" [dim]({size//1024}KB)[/dim]"
                    else:
                        size_info = f" [dim]({size//(1024*1024)}MB)[/dim]"
                
                # Truncate file names
                size_display_length = len(size_info.replace('[dim]', '').replace('[/dim]', '').replace('(', '').replace(')', ''))
                max_name_length = 60 - size_display_length
                display_name = self._truncate_name(display_name, max_name_length)
                
                logger.info(f"{connector}📄 [white]{display_name}[/white]{size_info}")
        except ValueError:
            pass
    
    def _truncate_name(self, name: str, max_length: int) -> str:
        """Truncate long names with middle ellipsis."""
        if len(name) <= max_length:
            return name
        start_len = (max_length - 3) // 2
        end_len = max_length - 3 - start_len
        return f"{name[:start_len]}...{name[-end_len:]}"
    
    def _print_tree_level(self, items: list, base_path: Path, prefix: str, is_root: bool, show_details: bool):
        """Print items at a single tree level with proper indentation."""
        directories = [item for item in items if item.is_dir()]
        files = [item for item in items if item.is_file()]
        
        all_items = directories + files
        
        for i, item in enumerate(all_items):
            try:
                is_last = (i == len(all_items) - 1)
                
                if item.is_dir():
                    display_name = item.name
                    # Truncate long directory names
                    display_name = self._truncate_name(display_name, 60)
                    
                    if is_root:
                        connector = "  ⎿  " if is_last else "  ├─ "
                    else:
                        connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                    
                    logger.info(f"{connector}📁 [bold cyan]{display_name}[/bold cyan]")
                else:
                    display_name = item.name
                    
                    # Calculate size info
                    size_info = ""
                    if show_details:
                        size = item.stat().st_size
                        if size < 1024:
                            size_info = f" [dim]({size}B)[/dim]"
                        elif size < 1024 * 1024:
                            size_info = f" [dim]({size//1024}KB)[/dim]"
                        else:
                            size_info = f" [dim]({size//(1024*1024)}MB)[/dim]"
                    
                    # Truncate file names accounting for size info
                    size_display_length = len(size_info.replace('[dim]', '').replace('[/dim]', '').replace('(', '').replace(')', ''))
                    max_name_length = 60 - size_display_length
                    display_name = self._truncate_name(display_name, max_name_length)
                    
                    if is_root:
                        connector = "  ⎿  " if is_last else "  ├─ "
                    else:
                        connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                    
                    logger.info(f"{connector}📄 [white]{display_name}[/white]{size_info}")
                    
            except ValueError:
                continue
    
    def _print_recursive_tree(self, root_path: Path, show_hidden: bool, show_details: bool, max_items_per_dir: int = 20):
        """Print recursive directory tree with proper multi-level indentation and line truncation.""" 
        def _walk_directory(current_path: Path, prefix: str, is_root: bool = False):
            try:
                items = list(current_path.glob("*"))
                
                # Filter hidden files
                if not show_hidden:
                    items = [item for item in items if not item.name.startswith('.')]
                
                # Sort: directories first, then files
                directories = [item for item in items if item.is_dir()]
                files = [item for item in items if item.is_file()]
                directories.sort(key=lambda x: x.name.lower())
                files.sort(key=lambda x: x.name.lower())
                
                all_items = directories + files
                
                # Apply truncation if there are too many items
                if len(all_items) > max_items_per_dir:
                    show_first = max_items_per_dir // 2
                    show_last = max_items_per_dir - show_first
                    displayed_items = all_items[:show_first] + all_items[-show_last:]
                    has_truncation = True
                    truncation_index = show_first
                else:
                    displayed_items = all_items
                    has_truncation = False
                    truncation_index = 0
                
                for i, item in enumerate(displayed_items):
                    # Check if we need to show truncation indicator
                    if has_truncation and i == truncation_index:
                        if is_root:
                            logger.info(f"  ├─ [dim]... ({len(all_items) - max_items_per_dir} more items)[/dim]")
                        else:
                            logger.info(f"{prefix}├─ [dim]... ({len(all_items) - max_items_per_dir} more items)[/dim]")
                    
                    # Determine if this is the last item to display
                    is_last = (i == len(displayed_items) - 1)
                    
                    if item.is_dir():
                        display_name = self._truncate_name(item.name, 60)
                        
                        if is_root:
                            connector = "  ⎿  " if is_last else "  ├─ "
                            new_prefix = "     " if is_last else "  │  "
                        else:
                            connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                            new_prefix = f"{prefix}   " if is_last else f"{prefix}│  "
                        
                        logger.info(f"{connector}📁 [bold cyan]{display_name}[/bold cyan]")
                        
                        # Recursively print subdirectory contents
                        _walk_directory(item, new_prefix)
                        
                    else:
                        display_name = item.name
                        
                        # Calculate size info
                        size_info = ""
                        if show_details:
                            size = item.stat().st_size
                            if size < 1024:
                                size_info = f" [dim]({size}B)[/dim]"
                            elif size < 1024 * 1024:
                                size_info = f" [dim]({size//1024}KB)[/dim]"
                            else:
                                size_info = f" [dim]({size//(1024*1024)}MB)[/dim]"
                        
                        # Truncate file names
                        size_display_length = len(size_info.replace('[dim]', '').replace('[/div]', '').replace('(', '').replace(')', ''))
                        max_name_length = 60 - size_display_length
                        display_name = self._truncate_name(display_name, max_name_length)
                        
                        if is_root:
                            connector = "  ⎿  " if is_last else "  ├─ "
                        else:
                            connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                        
                        logger.info(f"{connector}📄 [white]{display_name}[/white]{size_info}")
                        
            except PermissionError:
                if is_root:
                    logger.info("  ⎿  [red]Permission denied[/red]")
                else:
                    logger.info(f"{prefix}⎿  [red]Permission denied[/red]")
        
        _walk_directory(root_path, "", True)
    
    def _collect_single_item_line(self, item: Path, base_path: Path, connector: str, show_details: bool) -> str:
        """Collect a single file or directory item line."""
        try:
            if item.is_dir():
                display_name = self._truncate_name(item.name, 60)
                return f"{connector}📁 [bold cyan]{display_name}[/bold cyan]"
            else:
                display_name = item.name
                
                # Calculate size info
                size_info = ""
                if show_details:
                    size = item.stat().st_size
                    if size < 1024:
                        size_info = f" [dim]({size}B)[/dim]"
                    elif size < 1024 * 1024:
                        size_info = f" [dim]({size//1024}KB)[/dim]"
                    else:
                        size_info = f" [dim]({size//(1024*1024)}MB)[/dim]"
                
                # Truncate file names
                size_display_length = len(size_info.replace('[dim]', '').replace('[/dim]', '').replace('(', '').replace(')', ''))
                max_name_length = 60 - size_display_length
                display_name = self._truncate_name(display_name, max_name_length)
                
                return f"{connector}📄 [white]{display_name}[/white]{size_info}"
        except ValueError:
            return ""
    
    def _collect_tree_level_lines(self, items: list, base_path: Path, prefix: str, is_root: bool, show_details: bool, lines: list):
        """Collect items at a single tree level into lines list."""
        directories = [item for item in items if item.is_dir()]
        files = [item for item in items if item.is_file()]
        
        all_items = directories + files
        
        for i, item in enumerate(all_items):
            try:
                is_last = (i == len(all_items) - 1)
                
                if is_root:
                    connector = "  ⎿  " if is_last else "  ├─ "
                else:
                    connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                
                line = self._collect_single_item_line(item, base_path, connector, show_details)
                if line:
                    lines.append(line)
                    
            except ValueError:
                continue
    
    def _collect_recursive_tree_lines(self, root_path: Path, show_hidden: bool, show_details: bool, lines: list, max_depth: int | None = None):
        """Collect recursive directory tree lines into lines list with optional depth limit.""" 
        def _walk_directory(current_path: Path, prefix: str, is_root: bool = False, current_depth: int = 1):
            try:
                items = list(current_path.glob("*"))
                
                # Filter hidden files
                if not show_hidden:
                    items = [item for item in items if not item.name.startswith('.')]
                
                # Sort: directories first, then files
                directories = [item for item in items if item.is_dir()]
                files = [item for item in items if item.is_file()]
                directories.sort(key=lambda x: x.name.lower())
                files.sort(key=lambda x: x.name.lower())
                
                all_items = directories + files
                
                for i, item in enumerate(all_items):
                    is_last = (i == len(all_items) - 1)
                    
                    if item.is_dir():
                        display_name = self._truncate_name(item.name, 60)
                        
                        if is_root:
                            connector = "  ⎿  " if is_last else "  ├─ "
                            new_prefix = "     " if is_last else "  │  "
                        else:
                            connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                            new_prefix = f"{prefix}   " if is_last else f"{prefix}│  "
                        
                        lines.append(f"{connector}📁 [bold cyan]{display_name}[/bold cyan]")
                        
                        # Only recurse if within depth limit
                        if max_depth is None or current_depth < max_depth:
                            _walk_directory(item, new_prefix, False, current_depth + 1)
                        
                    else:
                        display_name = item.name
                        
                        # Calculate size info
                        size_info = ""
                        if show_details:
                            size = item.stat().st_size
                            if size < 1024:
                                size_info = f" [dim]({size}B)[/dim]"
                            elif size < 1024 * 1024:
                                size_info = f" [dim]({size//1024}KB)[/dim]"
                            else:
                                size_info = f" [dim]({size//(1024*1024)}MB)[/dim]"
                        
                        # Truncate file names
                        size_display_length = len(size_info.replace('[dim]', '').replace('[/div]', '').replace('(', '').replace(')', ''))
                        max_name_length = 60 - size_display_length
                        display_name = self._truncate_name(display_name, max_name_length)
                        
                        if is_root:
                            connector = "  ⎿  " if is_last else "  ├─ "
                        else:
                            connector = f"{prefix}⎿  " if is_last else f"{prefix}├─ "
                        
                        lines.append(f"{connector}📄 [white]{display_name}[/white]{size_info}")
                        
            except PermissionError:
                if is_root:
                    lines.append("  ⎿  [red]Permission denied[/red]")
                else:
                    lines.append(f"{prefix}⎿  [red]Permission denied[/red]")
        
        _walk_directory(root_path, "", True, 1)
    
    def _display_with_line_limit(self, lines: list, max_lines: int):
        """Display lines with strict total line limit, never exceeding max_lines."""
        if len(lines) <= max_lines:
            # Display all lines
            for line in lines:
                logger.info(line)
        else:
            # Strict truncation: always output exactly max_lines total
            # Reserve 1 line for the truncation indicator
            available_lines = max_lines - 1
            show_first = available_lines // 2
            show_last = available_lines - show_first
            
            # Display first portion
            for line in lines[:show_first]:
                logger.info(line)
            
            # Display truncation indicator
            omitted_count = len(lines) - available_lines
            logger.info(f"  ├─ [dim]... ({omitted_count} more lines)[/dim]")
            
            # Display last portion
            for line in lines[-show_last:]:
                logger.info(line)

    @tool
    async def glob(
        self,
        pattern: str,
        path: str = ".",
        include_hidden: bool = False,
        max_results: int = 100
    ) -> dict:
        """
        Search for files using glob patterns (similar to Claude Code Glob tool).
        
        Args:
            pattern: Glob pattern (e.g., "*.py", "**/*.js", "src/**/*.tsx")
            path: Directory to search in (default: current directory)
            include_hidden: Whether to include hidden files/directories
            max_results: Maximum number of results to return
            
        Returns:
            dict with matching file paths
        """
        is_valid, error_msg, resolved_path = self._validate_path(path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
            
        if not resolved_path.is_dir():
            return {"success": False, "error": f"Path is not a directory: {path}"}
            
        try:
            logger.info(f"[cyan]Searching for pattern: {pattern} in {path}[/cyan]")
            
            # Use pathlib glob for pattern matching
            if '**' in pattern:
                # Recursive glob
                matches = list(resolved_path.rglob(pattern))
            else:
                # Non-recursive glob
                matches = list(resolved_path.glob(pattern))
            
            # Filter out hidden files if not requested
            if not include_hidden:
                matches = [m for m in matches if not any(part.startswith('.') for part in m.parts)]
            
            # Sort by modification time (newest first)
            matches.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
            
            # Limit results
            matches = matches[:max_results]
            
            if matches:
                # Print results immediately to console
                logger.info("")
                logger.info(f"╭─ [bold green]Found {len(matches)} files matching '{pattern}'[/bold green] " + "─" * (50 - len(pattern)) + "╮")
                
                for match in matches:
                    try:
                        rel_path = match.relative_to(self.workspace_path)
                        file_type = "📁" if match.is_dir() else "📄"
                        logger.info(f"│ {file_type} [cyan]{rel_path}[/cyan]" + " " * (70 - len(str(rel_path))) + "│")
                    except ValueError:
                        # File outside workspace
                        logger.info(f"│ 📄 [cyan]{match}[/cyan]" + " " * (70 - len(str(match))) + "│")
                
                logger.info("╰" + "─" * 74 + "╯")
                logger.info(f"[green]✅ Found {len(matches)} matching files[/green]")
            else:
                logger.info("")
                logger.info(f"╭─ [bold yellow]No files found matching '{pattern}'[/bold yellow] " + "─" * (45 - len(pattern)) + "╮")
                logger.info("│ No matching files found in the specified directory" + " " * 23 + "│")
                logger.info("╰" + "─" * 74 + "╯")
                logger.info("[yellow]No matching files found[/yellow]")
            
            # Convert to string paths for return
            file_paths = []
            for match in matches:
                try:
                    rel_path = match.relative_to(self.workspace_path)
                    file_paths.append(str(rel_path))
                except ValueError:
                    file_paths.append(str(match))
            
            return {
                "success": True,
                "pattern": pattern,
                "search_path": str(resolved_path),
                "files": file_paths,
                "count": len(file_paths),
                "max_results": max_results
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error during glob search: {str(e)}"}

    @tool
    async def grep(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str = "*",
        recursive: bool = True,
        case_sensitive: bool = True,
        regex: bool = False,
        context_lines: int = 2,
        max_results: int = 50
    ) -> dict:
        """
        Search for text within files (similar to Claude Code Grep tool).
        
        Args:
            pattern: Text pattern to search for
            path: Directory to search in OR specific file path (default: current directory)  
            file_pattern: File pattern to limit search (e.g., "*.py") or specific filename
            recursive: Whether to search recursively
            case_sensitive: Whether search is case-sensitive
            regex: Whether pattern is a regular expression
            context_lines: Number of context lines around matches
            max_results: Maximum number of matches to return
            
        Returns:
            dict with search results
        """
        is_valid, error_msg, resolved_path = self._validate_path(path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Path not found: {path}"}
        
        # Check if path is a specific file
        is_single_file = resolved_path.is_file()
        if is_single_file:
            # For single file search, set file_pattern to match this file
            file_pattern = resolved_path.name
            resolved_path = resolved_path.parent
            recursive = False
            
        try:
            # Show search status
            logger.info(f"[cyan]Searching for '{pattern}' in files matching '{file_pattern}'[/cyan]")
            
            # Debug info
            logger.info(f"[dim]Search path: {resolved_path}, recursive: {recursive}[/dim]")
            
            # Find files to search
            if recursive:
                if file_pattern == "*":
                    files = list(resolved_path.rglob("*"))
                else:
                    files = list(resolved_path.rglob(file_pattern))
            else:
                if file_pattern == "*":
                    files = list(resolved_path.glob("*"))
                else:
                    files = list(resolved_path.glob(file_pattern))
            
            # Filter to only text files
            logger.info(f"[dim]Scanning {len(files)} files...[/dim]")
            
            text_files = []
            for f in files:
                if f.is_file():
                    try:
                        # Try to read a small portion to check if it's text
                        with open(f, 'r', encoding='utf-8', errors='ignore') as test_file:
                            test_file.read(100)
                        text_files.append(f)
                    except:
                        continue
            
            logger.info(f"[dim]Found {len(text_files)} text files to search[/dim]")
            
            matches = []
            total_matches = 0
            
            # Compile regex if needed
            if regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                try:
                    compiled_pattern = re.compile(pattern, flags)
                except re.error as e:
                    return {"success": False, "error": f"Invalid regex pattern: {str(e)}"}
            else:
                search_pattern = pattern if case_sensitive else pattern.lower()
            
            # Search through files
            for file_path in text_files:
                if total_matches >= max_results:
                    break
                    
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                    
                    file_matches = []
                    for line_no, line in enumerate(lines, 1):
                        if total_matches >= max_results:
                            break
                            
                        line_to_search = line if case_sensitive else line.lower()
                        
                        # Check for match
                        match_found = False
                        if regex:
                            match_found = bool(compiled_pattern.search(line))
                        else:
                            match_found = search_pattern in line_to_search
                        
                        if match_found:
                            # Get context lines
                            start = max(0, line_no - context_lines - 1)
                            end = min(len(lines), line_no + context_lines)
                            
                            context = []
                            for i in range(start, end):
                                prefix = ">>>" if i == line_no - 1 else "   "
                                context.append({
                                    "line_no": i + 1,
                                    "content": lines[i].rstrip(),
                                    "is_match": i == line_no - 1
                                })
                            
                            file_matches.append({
                                "line_number": line_no,
                                "line": line.rstrip(),
                                "context": context
                            })
                            total_matches += 1
                    
                    if file_matches:
                        try:
                            rel_path = file_path.relative_to(self.workspace_path)
                        except ValueError:
                            rel_path = file_path
                            
                        matches.append({
                            "file": str(rel_path),
                            "matches": file_matches
                        })
                        
                except Exception:
                    continue  # Skip files that can't be read
            
            # Print results
            if matches:
                logger.info("")
                logger.info(f"╭─ [bold green]Found {total_matches} matches for '{pattern}'[/bold green] " + "─" * (40 - len(pattern)) + "╮")
                
                for file_match in matches:
                    logger.info(f"│ 📄 [bold cyan]{file_match['file']}[/bold cyan]" + " " * (70 - len(file_match['file'])) + "│")
                    
                    for match in file_match['matches'][:3]:  # Show max 3 matches per file in display
                        highlighted_line = match['line'][:60] + ('...' if len(match['line']) > 60 else '')
                        logger.info(f"│   [dim]Line {match['line_number']}:[/dim] {highlighted_line}" + " " * (70 - min(60, len(match['line'])) - len(str(match['line_number'])) - 8) + "│")
                    
                    if len(file_match['matches']) > 3:
                        logger.info(f"│   [dim]... and {len(file_match['matches']) - 3} more matches[/dim]" + " " * 40 + "│")
                
                logger.info("╰" + "─" * 74 + "╯")
                logger.info(f"[green]✅ Found {total_matches} matches in {len(matches)} files[/green]")
            else:
                logger.info(f"╭─ [bold yellow]No matches found for '{pattern}'[/bold yellow] " + "─" * (45 - len(pattern)) + "╮")
                logger.info("│ No matching content found in the specified files" + " " * 25 + "│")
                logger.info("╰" + "─" * 74 + "╯")
                logger.info("[yellow]No matches found[/yellow]")
            
            return {
                "success": True,
                "pattern": pattern,
                "search_path": str(resolved_path),
                "file_pattern": file_pattern,
                "matches": matches,
                "total_matches": total_matches,
                "files_searched": len(text_files)
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error during grep search: {str(e)}"}

    @tool
    async def ls(
        self,
        path: str = ".",
        show_hidden: bool = False,
        show_details: bool = False,
        recursive: bool = False,
        max_lines: int = 10,
        max_depth: int | None = None
    ) -> dict:
        """
        Enhanced directory listing (similar to Claude Code LS tool).
        
        Args:
            path: Directory path to list (default: current directory)
            show_hidden: Whether to show hidden files/directories
            show_details: Whether to show file sizes and modification times
            recursive: Whether to list recursively
            max_lines: Maximum total lines to display (never exceeds this limit)
            max_depth: Maximum depth to recurse (1=only current dir, 2=one level deep, etc.)
            
        Returns:
            dict with directory contents
        """
        is_valid, error_msg, resolved_path = self._validate_path(path)
        if not is_valid:
            return {"success": False, "error": error_msg}
            
        if not resolved_path.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
            
        if not resolved_path.is_dir():
            return {"success": False, "error": f"Path is not a directory: {path}"}
            
        try:
            # Collect all output lines first
            display_path = resolved_path.name if resolved_path.name else str(resolved_path)
            all_lines = []
            all_lines.append("")  # Empty line before
            all_lines.append(f"Directory: {display_path}")
            
            if recursive:
                # For recursive, collect all lines with optional depth limit
                self._collect_recursive_tree_lines(resolved_path, show_hidden, show_details, all_lines, max_depth)
            else:
                # For non-recursive, collect all lines
                items = list(resolved_path.glob("*"))
                
                # Filter hidden files
                if not show_hidden:
                    items = [item for item in items if not item.name.startswith('.')]
                
                # Sort: directories first, then files
                items.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
                
                if not items:
                    all_lines.append("  ⎿  [yellow]Empty directory[/yellow]")
                else:
                    self._collect_tree_level_lines(items, resolved_path, "", True, show_details, all_lines)
            
            # Apply line truncation and display
            self._display_with_line_limit(all_lines, max_lines)
            
            # Prepare return data
            if recursive:
                items = list(resolved_path.rglob("*"))
            else:
                items = list(resolved_path.glob("*"))
            
            # Filter hidden files for return data
            if not show_hidden:
                if recursive:
                    items = [item for item in items if not any(part.startswith('.') for part in item.parts[len(resolved_path.parts):])]
                else:
                    items = [item for item in items if not item.name.startswith('.')]
            
            file_list = []
            for item in items:
                try:
                    rel_path = item.relative_to(resolved_path)
                    file_info = {
                        "path": str(rel_path),
                        "type": "directory" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else None
                    }
                    file_list.append(file_info)
                except:
                    continue
            
            return {
                "success": True,
                "directory": str(resolved_path),
                "files": file_list,
                "count": len(file_list),
                "show_hidden": show_hidden,
                "recursive": recursive
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error listing directory: {str(e)}"}


__all__ = ["CodeSearchToolSet"]
