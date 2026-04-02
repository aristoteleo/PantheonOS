import asyncio
import os
import re
from pathlib import Path
from typing import Literal
import tempfile
import shutil
import base64
import io
from datetime import datetime



from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
from .apply_patch import execute_patch_operations
from .grep_glob import grep_search, glob_search


def _replace_in_content(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    start_line: int | None = None,
    end_line: int | None = None,
) -> tuple[str | None, int, str | None]:
    """Perform string replacement in content with optional line range.

    Args:
        content: The full file content.
        old_string: The exact string to find.
        new_string: The replacement string.
        replace_all: If True, replace all occurrences.
        start_line: Optional start line (1-indexed, inclusive).
        end_line: Optional end line (1-indexed, inclusive).

    Returns:
        Tuple of (new_content, replacement_count, error_message).
        On success: (new_content, count, None)
        On failure: (None, 0, error_message)
    """

    def do_replace(text: str, old: str, new: str, count: int) -> tuple[str, int]:
        """Perform replacement, returns (new_text, actual_count)."""
        if count == 0:  # replace all
            n = text.count(old)
            return text.replace(old, new), n
        else:
            return text.replace(old, new, count), min(count, text.count(old))

    # Handle line range restriction
    if start_line is not None or end_line is not None:
        lines = content.splitlines(keepends=True)
        start_idx = (start_line - 1) if start_line else 0
        end_idx = end_line if end_line else len(lines)

        # Validate bounds
        if start_idx < 0 or start_idx >= len(lines):
            return (
                None,
                0,
                f"start_line {start_line} is out of range (file has {len(lines)} lines)",
            )
        if end_idx > len(lines):
            return (
                None,
                0,
                f"end_line {end_line} is out of range (file has {len(lines)} lines)",
            )
        if start_idx >= end_idx:
            return None, 0, "start_line must be less than end_line"

        before = "".join(lines[:start_idx])
        section = "".join(lines[start_idx:end_idx])
        after = "".join(lines[end_idx:])

        match_count = section.count(old_string)

        if match_count == 0:
            return None, 0, f"old_string not found in lines {start_line}-{end_line}"

        if match_count > 1 and not replace_all:
            return (
                None,
                0,
                f"old_string found {match_count} times in lines {start_line}-{end_line}. Set replace_all=True or narrow the line range.",
            )

        new_section, replaced = do_replace(
            section, old_string, new_string, 0 if replace_all else 1
        )
        return before + new_section + after, replaced, None
    else:
        # Full content replacement
        match_count = content.count(old_string)

        if match_count == 0:
            return None, 0, "old_string not found in file"

        if match_count > 1 and not replace_all:
            return (
                None,
                0,
                f"old_string found {match_count} times. Set replace_all=True or use start_line/end_line to target specific occurrence.",
            )

        new_content, replaced = do_replace(
            content, old_string, new_string, 0 if replace_all else 1
        )
        return new_content, replaced, None


class FileManagerToolSetBase(ToolSet):
    """Base class for file manager toolsets.

    Provides unified path management and file operations.
    """

    @tool(exclude=True)
    async def manage_path(
        self,
        operation: str,
        path: str,
        new_path: str | None = None,
        recursive: bool = False,
    ) -> dict:
        """Unified tool for managing files and directories.

        This tool consolidates common file system operations into a single interface.
        Use this instead of create_directory, delete_path, or move_file.

        Args:
            operation: The operation to perform. One of:
                      - "create_dir": Create a directory (and parents if needed)
                      - "delete": Delete a file or directory
                      - "move": Move or rename a file/directory

            path: The target path for the operation (relative to workspace root).
                  For "create_dir" and "delete", this is the path to create/delete.
                  For "move", this is the source path.

            new_path: Required for "move" operation. The destination path.
                     Ignored for other operations.

            recursive: For "delete" operation only. When True, directories are
                      deleted recursively. When False, only empty directories
                      can be deleted. Default: False.

        Returns:
            dict: {"success": bool, ...} with operation-specific details.
                 On error: {"success": False, "error": str}

        Examples:
            # Create a directory (parents created automatically)
            await manage_path("create_dir", "src/components")

            # Delete a file
            await manage_path("delete", "old_file.py")

            # Delete a directory recursively
            await manage_path("delete", "old_folder", recursive=True)

            # Move/rename a file
            await manage_path("move", "old_name.py", new_path="new_name.py")

            # Move to different directory
            await manage_path("move", "file.py", new_path="backup/file.py")
        """
        # Validate operation
        valid_operations = ["create_dir", "delete", "move"]
        if operation not in valid_operations:
            return {
                "success": False,
                "error": f"Invalid operation '{operation}'. Must be one of: {', '.join(valid_operations)}",
            }

        try:
            if operation == "create_dir":
                return await self.create_directory(path)

            elif operation == "delete":
                return await self.delete_path(path, recursive=recursive)

            elif operation == "move":
                if new_path is None:
                    return {
                        "success": False,
                        "error": "new_path is required for 'move' operation",
                    }
                return await self.move_file(path, new_path)

        except Exception as e:
            logger.warning(f"manage_path failed for operation {operation}: {e}")
            return {"success": False, "error": str(e)}

    def __init__(
        self,
        name: str,
        path: str | Path | None = None,
        black_list: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        if path is None:
            path = Path.cwd()
        self.path = Path(path)
        self.black_list = black_list or []

    def _get_root(self) -> Path:
        """Get the effective workspace root: workdir from context or default self.path."""
        workdir = self._get_effective_workdir()
        return Path(workdir) if workdir else self.path

    def _resolve_path(self, file_path: str) -> Path:
        """Resolve a file path: absolute paths pass through, relative paths
        resolve against the effective workspace root (workdir or self.path)."""
        if os.path.isabs(file_path):
            return Path(file_path)
        return self._get_root() / file_path

    @tool(exclude=True)
    async def get_cwd(self) -> dict:
        """Get current working directory."""
        return {"success": True, "cwd": str(self._get_root())}

    @tool(exclude=True)
    async def list_files(
        self,
        sub_dir: str | None = None,
        recursive: bool = False,
        max_depth: int = 5,
    ) -> dict:
        """DEPRECATED: Use glob() or grep() instead.

        This tool returns too much data and is inefficient.

        Recommended alternatives:
        - Use glob() to find files by pattern (e.g., glob("**/*.py"))
        - Use grep() to search file contents (e.g., grep("TODO", file_pattern="**/*.py"))

        Original functionality:
        List files and directories in the workspace.

        Args:
            sub_dir: Subdirectory to list (relative to workspace root).
                     If not provided, lists the workspace root.
            recursive: If True, list all files recursively as a tree structure.
            max_depth: Maximum depth to recurse (only used when recursive=True).
                       0 means only the target directory, 1 includes immediate children, etc.
                       Default is 5.

        Returns:
            dict: {success: bool, files: list} with name, type, size, last_modified.
        """
        # Determine target directory - support absolute paths
        if sub_dir:
            target_path = self._resolve_path(sub_dir)
        else:
            target_path = self._get_root()

        if not target_path.exists():
            return {"success": False, "error": "Directory does not exist"}

        if not recursive:
            files = list(target_path.glob("*"))
            return {
                "success": True,
                "files": [
                    {
                        "name": file.name,
                        "size": file.stat().st_size if file.is_file() else 0,
                        "type": "file" if file.is_file() else "directory",
                        "last_modified": datetime.fromtimestamp(
                            file.stat().st_mtime
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    for file in files
                    if file.name not in self.black_list
                ],
            }
        else:

            def _list_tree(path: Path, current_depth: int = 0) -> dict:
                """Helper function to recursively build the tree structure."""
                result = {
                    "name": path.name,
                    "type": "directory" if path.is_dir() else "file",
                    "size": path.stat().st_size if path.is_file() else 0,
                }
                if path.is_dir():
                    # Check depth limit before recursing
                    if max_depth is not None and current_depth >= max_depth:
                        result["children"] = []  # Empty children at max depth
                    else:
                        result["children"] = []
                        for item in sorted(path.iterdir()):
                            result["children"].append(
                                _list_tree(item, current_depth + 1)
                            )
                return result

            if not target_path.exists():
                return {"success": False, "error": "Target directory does not exist"}

            return {"success": True, "tree": _list_tree(target_path, 0)}

    @tool(exclude=True)
    async def create_directory(self, sub_dir: str | list[str]) -> dict:
        """Create one or more directories.

        Args:
            sub_dir: Directory path or list of directory paths to create.

        Returns:
            dict: Success status. For batch operations, includes results for each path.
        """
        if isinstance(sub_dir, str):
            new_dir = self._resolve_path(sub_dir)
            new_dir.mkdir(parents=True, exist_ok=True)
            return {"success": True}

        # Batch operation
        results = []
        for path in sub_dir:
            try:
                new_dir = self._resolve_path(path)
                new_dir.mkdir(parents=True, exist_ok=True)
                results.append({"path": path, "success": True})
            except Exception as exc:
                results.append({"path": path, "success": False, "error": str(exc)})

        all_success = all(r["success"] for r in results)
        return {"success": all_success, "results": results}

    @tool(exclude=True)
    async def delete_path(
        self,
        path: str | list[str],
        recursive: bool = False,
    ) -> dict:
        """Delete files or directories with optional recursion.

        Args:
            path: Single path or list of paths relative to the workspace root.
            recursive: When True, directories are deleted recursively using rmtree.
        """

        def _delete_single_path(relative_path: str) -> dict:
            target_path = self._resolve_path(relative_path)
            if not target_path.exists():
                return {
                    "path": relative_path,
                    "success": False,
                    "error": "Path does not exist",
                }
            try:
                if target_path.is_dir():
                    if recursive:
                        shutil.rmtree(target_path)
                    else:
                        target_path.rmdir()
                else:
                    target_path.unlink()
                return {"path": relative_path, "success": True}
            except Exception as exc:
                return {
                    "path": relative_path,
                    "success": False,
                    "error": str(exc),
                }

        if isinstance(path, str):
            return _delete_single_path(path)

        results = [_delete_single_path(p) for p in path]
        all_success = all(r["success"] for r in results)
        return {"success": all_success, "results": results}

    @tool(exclude=True)
    async def move_file(self, old_path: str, new_path: str):
        """Move or rename a file.

        Args:
            old_path: Current path of the file (relative to workspace root).
            new_path: New path for the file (relative to workspace root).

        Returns:
            dict: {success: bool} or {success: False, error: str}
        """
        old_path = self._resolve_path(old_path)
        if not old_path.exists():
            return {"success": False, "error": "Old path does not exist"}
        new_path = self._resolve_path(new_path)
        # Ensure parent directory exists before moving
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(old_path, new_path)
        return {"success": True}


def path_to_image_url(path: str) -> str:
    """Convert an image file to a base64 PNG data URL.
    
    Reads file bytes into memory first to avoid PIL lazy loading issues
    (e.g., 'PngImageFile' object has no attribute '_im').
    All images are converted to PNG format for consistency.
    """
    from PIL import Image
    
    # Read file bytes into memory first to avoid lazy loading issues
    with open(path, "rb") as f:
        file_bytes = f.read()
    
    # Open from memory buffer - this forces complete loading
    with Image.open(io.BytesIO(file_bytes)) as img:
        with io.BytesIO() as buffer:
            img.save(buffer, format="PNG")
            buffer.seek(0)
            return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


def is_image_blank(image_path: str | Path) -> bool:
    """Check if an image is likely blank (pure white, black, or transparent).

    Checks:
    1. Full transparency
    2. Zero standard deviation (solid color)
    3. Min == Max (solid color fallback)
    """
    try:
        from PIL import Image, ImageStat
        with Image.open(image_path) as img:
            # Check transparency first
            if img.mode in ('RGBA', 'LA') and img.getextrema()[-1][1] == 0:
                return True
                
            # Convert to grayscale for content analysis
            gray_img = img.convert("L")
            
            # Method 1: Standard Deviation
            try:
                stat = ImageStat.Stat(gray_img)
                if sum(stat.stddev) == 0:
                    return True
            except Exception:
                # Fallback if stat calculation fails (e.g., math domain error on some systems)
                pass

            # Method 2: Extrema (Min == Max)
            # This is a robust fallback for solid colors
            extrema = gray_img.getextrema()
            if extrema[0] == extrema[1]:
                return True
                
            return False
    except Exception:
        # If we can't open/analyze it, assume it's not strictly "blank" in the trivial sense
        return False


class FileManagerToolSet(FileManagerToolSetBase):
    """Extended file manager toolset.
    Builds on the base class with higher-level helpers:
        - `read_file` / `write_file` / `update_file`: text read/write/structured replace operations.
        - `observe_images` / `observe_pdf_screenshots`: LLM-assisted visual inspection.
        - `read_pdf`: PDF-to-text extraction for downstream consumption.
        - `fetch_image_base64`: encode images for frontend display pipelines.
        - `fetch_resources_batch`: batch load resources for HTML preview (frontend-only).

    Args:
        name: The name of the toolset.
        path: The path to the directory to manage.
        black_list: The list of files to ignore.
        **kwargs: Additional keyword arguments.
    """

    @tool(exclude=True)
    async def fetch_resources_batch(
        self,
        resource_paths: list[str],
        base_path: str | None = None,
    ) -> dict:
        """Batch fetch multiple resources for HTML preview (frontend-only).
        
        This tool is designed for frontend HTML preview to efficiently load
        multiple resources (images, CSS, JS) referenced in HTML files.
        
        Args:
            resource_paths: List of resource paths. Can be:
                           - Absolute paths (starting with /)
                           - Relative paths (if base_path is provided)
            base_path: Optional base directory for resolving relative paths.
                      Should be the directory containing the HTML file.
        
        Returns:
            dict: {
                "success": bool,
                "resources": [
                    {
                        "path": str,           # Original path from input
                        "resolved_path": str,  # Resolved absolute path
                        "success": bool,
                        "content": str,        # base64 data URI for images, text for css/js
                        "mime_type": str,
                        "error": str           # Only present if success=False
                    }
                ],
                "total": int,
                "loaded": int,
                "failed": int
            }
        
        Example:
            # Load resources with relative paths
            fetch_resources_batch(
                resource_paths=["./images/logo.png", "../styles/main.css", "js/app.js"],
                base_path="/workspace/project/pages"
            )
            
            # Load resources with absolute paths
            fetch_resources_batch(
                resource_paths=["/workspace/project/images/logo.png"]
            )
        """
        import mimetypes
        from pathlib import Path
        
        results = []
        loaded_count = 0
        failed_count = 0
        
        for resource_path in resource_paths:
            result = {
                "path": resource_path,
                "success": False
            }
            
            try:
                # Resolve path
                if os.path.isabs(resource_path):
                    # Absolute path
                    target_path = Path(resource_path)
                elif base_path:
                    # Relative path with base_path
                    base = Path(base_path) if os.path.isabs(base_path) else self._get_root() / base_path
                    target_path = (base / resource_path).resolve()
                else:
                    # Relative path without base_path (relative to workspace)
                    target_path = self._get_root() / resource_path
                
                result["resolved_path"] = str(target_path)
                
                # Security check: Ensure resolved path is within workspace
                # This prevents path traversal attacks (e.g., ../../etc/passwd)
                try:
                    target_path.relative_to(self.path)
                except ValueError:
                    result["error"] = "Resource path escapes workspace boundary"
                    failed_count += 1
                    results.append(result)
                    continue
                
                # Validate path exists
                if not target_path.exists():
                    result["error"] = "Resource file does not exist"
                    failed_count += 1
                    results.append(result)
                    continue
                
                if not target_path.is_file():
                    result["error"] = "Path is not a file"
                    failed_count += 1
                    results.append(result)
                    continue
                
                # Determine MIME type
                mime_type, _ = mimetypes.guess_type(str(target_path))
                if mime_type is None:
                    mime_type = "application/octet-stream"
                
                result["mime_type"] = mime_type
                
                # Load resource based on type
                if mime_type.startswith("image/"):
                    # Return base64 data URI for images
                    with open(target_path, "rb") as f:
                        file_bytes = f.read()
                    content = base64.b64encode(file_bytes).decode()
                    result["content"] = f"data:{mime_type};base64,{content}"
                else:
                    # Return text content for CSS/JS/HTML
                    try:
                        with open(target_path, "r", encoding="utf-8") as f:
                            result["content"] = f.read()
                    except UnicodeDecodeError:
                        result["error"] = "File is not a valid text file"
                        failed_count += 1
                        results.append(result)
                        continue
                
                result["success"] = True
                loaded_count += 1
                
            except Exception as e:
                result["error"] = str(e)
                failed_count += 1
            
            results.append(result)
        
        return {
            "success": True,
            "resources": results,
            "total": len(resource_paths),
            "loaded": loaded_count,
            "failed": failed_count
        }


    @tool
    async def read_file(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars: int | None = None,
        symbol: str | None = None,
    ) -> dict:
        """Read the contents of a text file.

        Usage:
        - Lines are 1-indexed (first line is line 1).
        - start_line and end_line are inclusive.
        - To read the entire file, do not pass start_line or end_line.
        - To read a specific range, pass both start_line and end_line.
        - max_chars: Optional character limit (default: 50000 from settings).
        - symbol: Optional. Extract a specific class/function/method by name
          (e.g., "MyClass", "MyClass.my_method", "helper_func").

        Args:
            file_path: Path to the file to read (relative to workspace root).
            start_line: Optional. First line to read (1-indexed, inclusive).
            end_line: Optional. Last line to read (1-indexed, inclusive).
            max_chars: Optional. Maximum characters to return (for quick preview, use lower values like 5000).
            symbol: Optional. Qualified name of a code symbol to extract (dot notation).
                   Examples: "MyClass", "MyClass.my_method", "helper_function"

        Returns:
            dict: {success, content, total_lines, format, [truncated, truncation_info, suggestions]}
        
        Note:
            Large files are limited to max_file_read_lines and max_file_read_chars.
            Use start_line/end_line to paginate or max_chars to control output size.
        """
        # Symbol extraction mode: use tree-sitter to extract specific code item
        if symbol:
            try:
                from pantheon.toolsets.code.tree_sitter_parser import get_code_item
                target_path = self._resolve_path(file_path)
                if not target_path.exists():
                    return {"success": False, "error": "File does not exist"}
                return get_code_item(target_path, symbol)
            except ImportError:
                return {"success": False, "error": "Code navigation requires 'pantheon-agents[toolsets]'"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # Support both absolute and relative paths
        target_path = self._resolve_path(file_path)
        if not target_path.exists():
            return {"success": False, "error": "File does not exist"}
        if not target_path.is_file():
            return {"success": False, "error": "Path is not a file"}

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)

            # Empty file - return early
            if total_lines == 0:
                return {
                    "success": True,
                    "content": "",
                    "total_lines": 0,
                    "format": "1-indexed",
                }

            # Get line limit from settings
            from pantheon.settings import get_settings
            max_lines = get_settings().max_file_read_lines

            # Handle line range
            if start_line is not None or end_line is not None:
                # Convert to 0-indexed
                start_idx = (start_line - 1) if start_line else 0
                end_idx = end_line if end_line else total_lines

                # Validate bounds
                if start_idx < 0:
                    return {"success": False, "error": "start_line must be >= 1"}
                if start_idx >= total_lines:
                    return {
                        "success": False,
                        "error": f"start_line {start_line} is out of range (file has {total_lines} lines)",
                    }
                if end_idx > total_lines:
                    end_idx = total_lines  # Clamp to file end
                if start_idx >= end_idx:
                    return {
                        "success": False,
                        "error": "start_line must be less than or equal to end_line",
                    }

                content = "".join(lines[start_idx:end_idx])
            else:
                # No range specified - apply line limit if file is large
                if total_lines > max_lines:
                    content = "".join(lines[:max_lines])
                    return {
                        "success": True,
                        "content": content,
                        "total_lines": total_lines,
                        "format": target_path.suffix.lower(),
                        "truncated": True,
                        "hint": f"Showing first {max_lines} of {total_lines} lines. Use start_line/end_line to read more."
                    }
                else:
                    content = "".join(lines)

            # NEW: Apply character limit (after line selection)
            from pantheon.settings import get_settings
            char_limit = max_chars if max_chars is not None else get_settings().max_file_read_chars
            
            if len(content) > char_limit:
                return {
                    "success": True,
                    "content": content[:char_limit],
                    "total_lines": total_lines,
                    "format": target_path.suffix.lower(),
                    "truncated": True,
                    "hint": (
                        f"⚠️ Content truncated: {len(content):,} chars → {char_limit:,} chars "
                        f"({char_limit / len(content) * 100:.1f}% shown). "
                        f"Use other tools to read/process the full file."
                    ),
                }

            return {
                "success": True,
                "content": content,
                "total_lines": total_lines,
                "format": target_path.suffix.lower(),
                "truncated": False,  # Explicitly mark as not truncated
            }
        except UnicodeDecodeError:
            return {
                "success": False,
                "error": "File is not a valid text file (binary or encoding issue)",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def view_file_outline(self, file_path: str) -> dict:
        """Get a structured outline of classes and functions in a file.

        Returns the "skeleton" of a source file: all top-level classes and
        functions with their line ranges, signatures, and nested members.
        Useful for understanding large files without reading all the code.

        Args:
            file_path: Path to the source file (relative to workspace or absolute).
                      Supports: .py, .js, .ts, .jsx, .tsx

        Returns:
            dict: {
                "success": bool,
                "file": str,
                "language": str,
                "total_lines": int,
                "symbols": [
                    {
                        "name": str,
                        "kind": str,    # "class", "function", "method"
                        "start_line": int,
                        "end_line": int,
                        "signature": str,
                        "docstring": str,
                        "children": [...]
                    }
                ]
            }

        Examples:
            # View outline of a Python file
            outline = await view_file_outline("src/utils.py")

            # View outline of a JavaScript file
            outline = await view_file_outline("lib/index.js")
        """
        try:
            from pantheon.toolsets.code.tree_sitter_parser import get_file_outline
            target_path = self._resolve_path(file_path)
            if not target_path.exists():
                return {"success": False, "error": "File does not exist"}
            return get_file_outline(target_path)
        except ImportError:
            return {"success": False, "error": "Code navigation requires 'pantheon-agents[toolsets]'"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def write_file(
        self,
        file_path: str,
        content: str = "",
        overwrite: bool = True,
    ) -> dict:
        """Use this tool to CREATE NEW file.

        This tool writes content to a file, automatically creating parent
        directories if they do not exist.

        IMPORTANT: For EDITING existing file, use `update_file` instead.
        DO NOT rewrite entire file when only small changes are needed, its is wasteful and error-prone.

        Use this tool when:
        - Creating a brand new file
        - Completely rewriting a file from scratch (rare)

        DO NOT use this tool when:
        - Making partial modifications to an existing file
        - Changing a few lines in a large file
        - For these cases, use `update_file` instead

        Args:
            file_path: The path to the file to write.
            content: The content to write to the file.
            overwrite: When False, abort if the target file already exists.
                       Default is True, but consider using update_file for edits.

        Returns:
            dict: Success status or error message.
        """

        target_path = self._resolve_path(file_path)
        if not overwrite and target_path.exists():
            return {
                "success": False,
                "error": "File already exists",
                "reason": "overwrite_disabled",
            }

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "overwritten": overwrite}
        except Exception as exc:
            logger.error(f"write_file failed for {file_path}: {exc}")
            return {"success": False, "error": str(exc)}

    @tool
    async def update_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict:
        """Use this tool to edit an existing file. Follow these rules:

        1. Use this tool ONLY when making a SINGLE edit to a file. If you need to make
           MULTIPLE different edits to one of multiple files, use `apply_patch` instead.
        2. The old_string must EXACTLY MATCH the text in the file, including whitespace
           and indentation. Copy the exact text you want to replace.
        3. When old_string appears multiple times in the file, use start_line and end_line
           to limit the search scope, OR set replace_all=True to replace all occurrences.
        4. DO NOT use `write_file` to rewrite entire files when you only need small changes.
           This tool is more efficient and safer.

        Args:
            file_path: Path to the file to update (relative to workspace root).
            old_string: The exact string to find and replace.
            new_string: The string to replace old_string with.
            replace_all: If True, replace all occurrences. Default False (safer).
            start_line: Optional. Limit search from this line (1-indexed, inclusive).
            end_line: Optional. Limit search to this line (1-indexed, inclusive).

        Returns:
            dict: {success: bool, replacements: int} or {success: False, error: str}
        """
        target_path = self._resolve_path(file_path)
        if not target_path.exists():
            return {"success": False, "error": "File does not exist"}
        if not target_path.is_file():
            return {"success": False, "error": "Path is not a file"}

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()

            new_content, replacements, error = _replace_in_content(
                content,
                old_string,
                new_string,
                replace_all=replace_all,
                start_line=start_line,
                end_line=end_line,
            )

            if error:
                return {"success": False, "error": error}

            with open(target_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return {"success": True, "replacements": replacements}

        except UnicodeDecodeError:
            return {"success": False, "error": "File is not a valid text file"}
        except Exception as e:
            logger.error(f"update_file failed for {file_path}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def observe_images(self, question: str, image_paths: list[str]) -> dict:
        """Observe images and answer a question about them.

        Args:
            question: The question to answer.
            image_paths: The paths to the images to view."""
        context = self.get_context()
        if context is None:
            return {"success": False, "error": "ExecutionContext not available"}

        # Build messages with question
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }
        ]

        # Add images to the message
        for img_path in image_paths:
            ipath = self._resolve_path(img_path)

            # Validate image path
            if not ipath.exists():
                return {
                    "success": False,
                    "error": f"Image file does not exist: {img_path}",
                }
            if not ipath.is_file():
                return {"success": False, "error": f"Path is not a file: {img_path}"}

            # Convert image to base64 URI and add to message
            base64_uri = path_to_image_url(str(ipath))
            messages[0]["content"].append(
                {
                    "type": "image_url",
                    "image_url": {"url": base64_uri},
                }
            )

        # Call LLM to analyze images
        try:
            # Check for blank images FIRST
            blank_warnings = []
            for img_path in image_paths:
                if is_image_blank(self._resolve_path(img_path)):
                    blank_warnings.append(f"WARNING: Image '{img_path}' appears to be BLANK (solid color or transparent). The image contains no visual information. Please check how this image was generated.")

            response = await context.call_agent(messages=messages, use_memory=True)
            
            # Build result with cost passthrough
            # call_agent always returns {"success": True, "response": ..., "_metadata": {...}}
            content = response.get("response", "")
            
            # Prepend blank warnings to the content so the Agent sees them immediately
            if blank_warnings:
                warning_msg = "\n".join(blank_warnings)
                content = f"SYSTEM DETECTED ISSUES:\n{warning_msg}\n\nLLM Observation:\n{content}"

            result = {
                "success": True, 
                "content": content,
            }
            # Merge _metadata from nested agent call (contains current_cost)
            if "_metadata" in response:
                result.setdefault("_metadata", {}).update(response["_metadata"])
            
            return result
        except Exception as e:
            logger.error(
                f"Error calling agent for image observation: {e}", exc_info=True
            )
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def observe_pdf_screenshots(
        self,
        question: str,
        pdf_path: str,
        page_numbers: list[int] | None = None,
        dpi: int = 300,
    ) -> dict:
        """Observe the screenshots of the PDF file and answer a question about them.

        Args:
            question: The question to answer.
            pdf_path: The path to the PDF file to observe.
            page_numbers: The numbers of the pages to observe. If not provided, all pages will be observed.
            dpi: The DPI of the screenshots. If not provided, the default value is 300.
        """
        file_path = self._resolve_path(pdf_path)
        if not file_path.exists():
            return {"success": False, "error": "PDF file does not exist"}
        if not file_path.is_file():
            return {"success": False, "error": "Path is not a file"}
        if file_path.suffix.lower() != ".pdf":
            return {"success": False, "error": "File is not a PDF (wrong extension)"}
        try:
            import pymupdf

            with tempfile.TemporaryDirectory() as tmp_dir:
                image_paths = []
                with pymupdf.open(str(file_path)) as doc:
                    page_count = len(doc)
                    if page_numbers is not None:
                        page_numbers = [p for p in page_numbers if p <= page_count]
                    else:
                        page_numbers = list(range(page_count))
                    for page_number in page_numbers:
                        page = doc.load_page(page_number)
                        image = page.get_pixmap(dpi=dpi)
                        path = os.path.join(tmp_dir, f"page_{page_number}.png")
                        image.save(path)
                        image_paths.append(path)
                    resp = await self.observe_images(question=question, image_paths=image_paths)
                    return resp
        except ImportError:
            return {
                "success": False,
                "error": "pymupdf library not installed. Install with: pip install pymupdf",
            }
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    @tool
    async def read_pdf(
        self,
        pdf_path: str,
        question: str | None = None,
        page_numbers: list[int] | None = None,
        dpi: int = 300,
    ) -> dict:
        """Read a PDF file. Extracts text by default, or analyzes page screenshots when a question is provided.

        Args:
            pdf_path: The path to the PDF file to read.
            question: Optional. If provided, renders pages as images and answers the question
                     using multimodal analysis (useful for PDFs with charts, tables, or images).
            page_numbers: Optional. Specific page numbers to read (0-indexed).
                         If not provided, reads all pages.
            dpi: Resolution for screenshot mode (default: 300). Only used when question is provided.

        Returns:
            dict: Success status, content, and metadata about the PDF.
        """
        # If question provided, use screenshot-based multimodal analysis
        if question:
            return await self.observe_pdf_screenshots(
                question=question,
                pdf_path=pdf_path,
                page_numbers=page_numbers,
                dpi=dpi,
            )

        file_path = self._resolve_path(pdf_path)

        # Check if file exists
        if not file_path.exists():
            return {"success": False, "error": "PDF file does not exist"}

        # Check if it's actually a file
        if not file_path.is_file():
            return {"success": False, "error": "Path is not a file"}

        # Check if it has a PDF extension
        if file_path.suffix.lower() != ".pdf":
            return {"success": False, "error": "File is not a PDF (wrong extension)"}

        try:
            # Try to import pymupdf
            import pymupdf
        except ImportError:
            return {
                "success": False,
                "error": "pymupdf library not installed. Install with: pip install pymupdf",
            }

        try:
            # Open and read the PDF
            texts = []
            page_count = 0

            with pymupdf.open(str(file_path)) as doc:
                page_count = len(doc)

                # Check if PDF is password protected
                if doc.needs_pass:
                    return {
                        "success": False,
                        "error": "PDF is password protected and cannot be read",
                    }

                # Extract text from each page
                for page_num, page in enumerate(doc):
                    try:
                        page_text = page.get_text()
                        if page_text.strip():  # Only add non-empty pages
                            texts.append(f"--- Page {page_num + 1} ---\n{page_text}")
                    except Exception as e:
                        texts.append(
                            f"--- Page {page_num + 1} (Error reading) ---\nError: {str(e)}"
                        )

            # Combine all text
            full_text = "\n\n".join(texts)

            return {
                "success": True,
                "content": full_text,
                "format": ".pdf",
                "metadata": {
                    "total_pages": page_count,
                    "file_size": file_path.stat().st_size,
                    "pages_with_text": len(
                        [
                            t
                            for t in texts
                            if not t.startswith("--- Page") or "Error" not in t
                        ]
                    ),
                },
            }

        except pymupdf.FileDataError:
            return {"success": False, "error": "Invalid or corrupted PDF file"}
        except pymupdf.FitzError as e:
            return {"success": False, "error": f"PDF processing error: {str(e)}"}
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error reading PDF: {str(e)}",
            }

    @tool(exclude=True)
    async def fetch_image_base64(self, image_path: str) -> dict:
        """Fetch an image and return the base64 encoded image. for frontend display

        Args:
            image_path: Path to the image file (relative to workspace)

        Returns:
            Dict with success status and either data_uri or error message

        Raises:
            Returns error dict for invalid paths, unsupported formats, or file issues
        """
        # Security: Maximum image size (10MB)
        MAX_IMAGE_SIZE = 10 * 1024 * 1024

        try:
            # Support both relative (to workspace) and absolute paths
            candidate = Path(image_path)
            if candidate.is_absolute():
                i_path = candidate
            else:
                i_path = self._resolve_path(image_path)

            # Security: Check if path is within allowed directories
            try:
                resolved_path = i_path.resolve()
                allowed_path = self.path.resolve()
                if not str(resolved_path).startswith(str(allowed_path)):
                    return {"success": False, "error": "Path outside allowed workspace"}
            except Exception:
                return {"success": False, "error": "Invalid path"}

            # Security: Reject symbolic links
            if resolved_path.is_symlink():
                return {"success": False, "error": "Symbolic links are not allowed"}

            # Check file existence
            if not resolved_path.exists():
                return {"success": False, "error": "Image does not exist"}

            # Check if it's a file (not directory)
            if not resolved_path.is_file():
                return {"success": False, "error": "Path is not a file"}

            # Validate format
            format = resolved_path.suffix.lower()
            if format not in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"]:
                return {
                    "success": False,
                    "error": "Image format must be jpg, jpeg, png, gif, webp, bmp, or svg",
                }

            # Check file size
            try:
                file_size = resolved_path.stat().st_size
                if file_size == 0:
                    return {"success": False, "error": "Image file is empty"}
                if file_size > MAX_IMAGE_SIZE:
                    return {
                        "success": False,
                        "error": f"Image size ({file_size / 1024 / 1024:.1f}MB) exceeds maximum ({MAX_IMAGE_SIZE / 1024 / 1024:.0f}MB)",
                    }
            except OSError as e:
                return {"success": False, "error": f"Cannot access file: {str(e)}"}

            # Encode image to base64
            try:
                with open(resolved_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
            except PermissionError:
                return {"success": False, "error": "Permission denied reading image"}
            except IOError as e:
                return {"success": False, "error": f"IO error reading image: {str(e)}"}

            # Map format to MIME type
            mime_format = format.lstrip(".")
            if mime_format == "jpg":
                mime_format = "jpeg"

            data_uri = f"data:image/{mime_format};base64,{b64}"
            return {
                "success": True,
                "image_path": image_path,
                "data_uri": data_uri,
            }

        except Exception as e:
            logger.error(f"Error fetching image base64 for {image_path}: {str(e)}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    # =========================================================================
    # Patch Application Tools
    # =========================================================================

    @tool
    async def apply_patch(
        self,
        patch: str,
        file_path: str | None = None,
        fuzzy_threshold: float = 0.5,
    ) -> dict:
        """Apply patches to files with fuzzy matching support.

        Automatically detects patch format (Unified Diff or V4A) and extracts file paths from headers.
        Supports single-file and multi-file patches with create/update/delete operations.
        Uses fuzzy matching to handle whitespace and minor differences.

        Format recommendations: Use Unified Diff for most cases (industry standard). Use V4A for 
        complex multi-file operations with explicit create/delete markers. Set fuzzy_threshold=0.8 
        for AI-generated patches.

        Args:
            patch: Patch content as string, format auto-detected.
                   - Unified Diff (Git style): Industry standard, multi-file support, human-readable
                   - V4A/Codex format: Explicit operation markers, good for complex multi-file changes

            file_path: Optional explicit file path (default: extracted from patch headers).
                       Can be relative to workspace or absolute.

            fuzzy_threshold: Optional, matching tolerance 0.0-1.0 (default: 0.5).
                            0.0 = exact match, 0.5 = balanced, 0.8 = tolerant for AI patches.

        Returns:
            dict: {
                "success": bool,
                "message": str,
                "summary": {
                    "total_files": int,
                    "modified": int,
                    "created": int,
                    "deleted": int,
                    "failed": int
                },
                "files": [
                    {
                        "file": str,
                        "action": str,  # "update"|"create"|"delete"
                        "success": bool,
                        "hunks_applied": int,  # Number of changes applied (updates only)
                        "hunks_total": int,  # Total changes in patch (updates only)
                        "exact_match": bool,  # True if no fuzzy matching needed (updates only)
                        "lines_added": int,  # Number of lines (creates only)
                        "error": str  # Error message (failures only)
                    }
                ],
                "failed_files": [str]
            }

        Examples:
            # Unified Diff format - single file update
            ```
            --- a/hello.py
            +++ b/hello.py
            @@ -1,2 +1,2 @@
             def hello():
            -    return "Hello"
            +    return "Hello, World!"
            ```

            # V4A format - multi-file with create/update/delete
            ```
            *** Begin Patch
            *** Update File: config.py
            - DEBUG = True
            + DEBUG = False

            *** Create File: new_module.py
            + def new_feature():
            +     pass

            *** Delete File: legacy.py
            *** End Patch
            ```

            # Using fuzzy_threshold parameter
            fuzzy_threshold=0.8

        Note:
            Common issues: "No valid operations" = check format; "No hunks applied" = try higher fuzzy_threshold; "File does not exist" = file must exist for updates.
        """
        return execute_patch_operations(
            patch=patch,
            workspace_root=self._get_root(),
            file_path=file_path,
            fuzzy_threshold=fuzzy_threshold,
        )

    # =========================================================================
    # File Search Tools
    # =========================================================================

    @tool
    async def glob(
        self,
        pattern: str,
        path: str | None = None,
        respect_git_ignore: bool = True,
        type_filter: Literal["file", "directory", "any"] | None = None,
        excludes: list[str] | None = None,
        max_depth: int | None = None,
    ) -> dict:
        """Search for files and subdirectories within a specified directory using glob patterns.
        
        Search uses smart case and will ignore gitignored files by default.
        To avoid overwhelming output, the results are capped at max_glob_results. 
        Use the various arguments to filter the search scope as needed.
        Results will include the type, size, modification time, and relative path.

        Args:
            pattern: Glob pattern to search for, supports glob format:
                     - "*" matches any characters except /
                     - "**" matches any characters including /
                     - "?" matches single character
                     - "[abc]" matches any character in brackets
                     - "{py,js,ts}" matches any of the extensions (brace expansion)
                     Examples: "*.py", "**/*.{js,ts}", "src/**/*.py"

            path: Optional directory to search from (default: workspace root).
                  Can be relative (to workspace) or absolute path.

            respect_git_ignore: Optional, whether to respect .gitignore patterns (default: True).
                               Set to False to search ignored files.

            type_filter: Optional, type filter, enum=file,directory,any.
                        Default None means "file" for backward compatibility.

            excludes: Optional, exclude files/directories that match the given glob patterns.

            max_depth: Optional, maximum depth to search.

        Returns:
            dict: {
                "success": bool,
                "files": [
                    {
                        "path": str,        # Relative path from workspace root
                        "name": str,        # File basename
                        "size": int,        # Size in bytes
                        "modified": str,    # ISO format timestamp
                        "type": str         # "file" or "directory"
                    }
                ],
                "total": int,
                "pattern": str,
                "message": str,
                "capped": bool,
                "filters_applied": {
                    "type": str | None,
                    "excludes": list[str] | None,
                    "max_depth": int | None,
                }
            }

        Examples:
            # Find Python files, excluding virtual environments
            pattern="**/*.py", excludes=[".venv/*", "**/__pycache__/*"]

            # Find configuration directories at top level
            pattern="*config*", type_filter="directory", max_depth=1

            # Complex search with multiple filters
            pattern="**/*.{py,js}", excludes=["node_modules/*", ".venv/*"], type_filter="file", max_depth=3
        """
        # Run in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(
            glob_search,
            pattern=pattern,
            workspace_root=self._get_root(),
            path=path,
            respect_git_ignore=respect_git_ignore,
            type_filter=type_filter,
            excludes=excludes,
            max_depth=max_depth,
        )

        # Apply result limit from settings
        if result.get("success") and result.get("files"):
            from pantheon.settings import get_settings

            max_results = get_settings().max_glob_results

            files = result["files"]
            total = len(files)

            if total > max_results:
                result["files"] = files[:max_results]
                result["total"] = total
                result["capped"] = True
                result["message"] = f"Results capped at {max_results}. Total matches: {total}. Refine pattern to narrow results."
            else:
                result["capped"] = False

            # Add filters summary
            result["filters_applied"] = {
                "type": type_filter,
                "excludes": excludes,
                "max_depth": max_depth,
            }

        return result

    @tool
    async def grep(
        self,
        pattern: str,
        path: str | None = None,
        file_pattern: str | None = None,
        context_lines: int = 0,
        case_sensitive: bool = False,
        respect_git_ignore: bool = True,
    ) -> dict:
        """Search for text patterns within file contents.
        
        Search uses case-insensitive matching by default and will ignore gitignored files.
        Searches recursively by default. Refine your pattern or use file_pattern to narrow scope.
        Results are capped at max_glob_results to avoid overwhelming output.
        Results will include file path, line number, line content, and optional context lines.

        Args:
            pattern: Text or regex pattern to search for, supports Rust regex syntax.
                     Examples: "TODO", "def\\s+\\w+", "class\\s+\\w+"

            path: Optional directory or file to search (default: workspace root).
                  Can be relative or absolute.

            file_pattern: Optional glob pattern to filter files.
                         Examples: "*.py", "*.{js,ts}", "src/**/*.py"

            context_lines: Optional, number of context lines before/after each match (default: 0).

            case_sensitive: Optional, whether search is case-sensitive (default: False).

            respect_git_ignore: Optional, whether to respect .gitignore patterns (default: True).

        Returns:
            dict: {
                "success": bool,
                "matches": [
                    {
                        "file": str,
                        "line_number": int,
                        "line_content": str,
                        "context_before": [str],
                        "context_after": [str],
                        "column": int
                    }
                ],
                "total_matches": int,
                "files_matched": int,
                "pattern": str,
                "message": str
            }

        Examples:
            # Find TODOs in Python files with context
            pattern="TODO", file_pattern="*.py", context_lines=2

            # Find function definitions using regex
            pattern="def\\s+\\w+", file_pattern="**/*.py"

            # Search in gitignored directory
            pattern="version.*1.2.3", path="node_modules", respect_git_ignore=False
        """
        # Get max results from settings to pass down for early termination
        from pantheon.settings import get_settings
        max_results = get_settings().max_glob_results
        
        # Run in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(
            grep_search,
            pattern=pattern,
            workspace_root=self._get_root(),
            path=path,
            file_pattern=file_pattern,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
            respect_git_ignore=respect_git_ignore,
            max_results=max_results,
        )
        
        # Apply result limit from settings (same as glob)
        # This is a backup in case grep_search returns more than expected
        if result.get("success") and result.get("matches"):
            matches = result["matches"]
            total = len(matches)
            
            if total > max_results:
                result["matches"] = matches[:max_results]
                result["total_matches"] = total
                result["capped"] = True
                result["message"] = (
                    f"Results capped at {max_results}. Total matches: {total}. "
                    f"Refine pattern or use file_pattern to narrow results."
                )
            elif not result.get("capped"):
                result["capped"] = False
        
        return result

    @tool
    async def generate_image(
        self,
        prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict:
        """Generate an image from a text description.

        Use this tool to create images based on your description. You can also
        provide reference images for style transfer or image editing.

        Args:
            prompt: Detailed description of the image to generate.
                Be specific about colors, composition, style, and subjects.
                When using reference_images, refer to them by order in prompt:
                "first image", "second image", etc.
                Example: "Combine the style of the first image with the subject of the second image"
            reference_images: File paths of existing images to use as reference.
                Images are passed to the model in array order.
                Example: ["style.png", "subject.png"]

        Returns:
            Dictionary with:
            - success: Whether generation succeeded
            - images: List of file paths to generated images
            - error: Error message if failed
        """
        from pantheon.toolsets.image import ImageGenerationToolSet

        # Lazy initialization of image generation toolset
        if not hasattr(self, "_image_gen"):
            self._image_gen = ImageGenerationToolSet()

        # Resolve relative paths to absolute paths
        abs_refs = None
        if reference_images:
            abs_refs = []
            for ref in reference_images:
                if not ref.startswith(("/", "file://", "http")):
                    abs_refs.append(str(self._resolve_path(ref)))
                else:
                    abs_refs.append(ref)

        return await self._image_gen.generate_image(prompt, abs_refs)

    # =========================================================================
    # LaTeX Compilation
    # =========================================================================

    @property
    def _latex_output_dir(self) -> Path:
        from pantheon.settings import get_settings
        return get_settings().pantheon_dir / "latex"

    @property
    def _latex_semaphore(self) -> asyncio.Semaphore:
        if not hasattr(self, "_latex_sem"):
            self._latex_sem = asyncio.Semaphore(3)
        return self._latex_sem

    @tool(exclude=True)
    async def compile_latex(
        self,
        file_path: str,
        compiler: str = "pdflatex",
    ) -> dict:
        """Compile a LaTeX (.tex) file into a PDF.

        Reads the .tex file from the given path, compiles it, and saves
        the resulting PDF to the output directory.

        Args:
            file_path: Path to the .tex file to compile (absolute or relative to workspace).
            compiler: LaTeX compiler to use. Options: "pdflatex" (default),
                     "tectonic", "xelatex", "lualatex".

        Returns:
            dict: {
                "success": bool,
                "pdf_path": str (absolute path to generated PDF, if successful),
                "error": str (compilation error message, if failed),
                "log": str (compiler output, if failed)
            }
        """
        # Resolve file path
        source_path = Path(file_path)
        if not source_path.is_absolute():
            source_path = self._get_root() / source_path

        if not source_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        if source_path.suffix.lower() not in ('.tex', '.latex'):
            return {"success": False, "error": f"Not a LaTeX file: {file_path}"}

        async with self._latex_semaphore:
            # Compile in the source file's directory so relative paths
            # (including ../ references) resolve correctly.
            source_dir = str(source_path.parent)
            tex_name = source_path.name

            # Build compilation command
            if compiler == "tectonic":
                cmd = ["tectonic", tex_name]
            else:
                # pdflatex / xelatex / lualatex
                cmd = [
                    compiler,
                    "-interaction=nonstopmode",
                    tex_name,
                ]

            # Run compiler in the source directory
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=source_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=60
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "success": False,
                    "error": "Compilation timed out (60s limit).",
                }

            # Check for output PDF
            pdf_name = tex_name.rsplit(".", 1)[0] + ".pdf"
            pdf_src = source_path.parent / pdf_name

            if pdf_src.exists():
                # Move PDF to output directory
                self._latex_output_dir.mkdir(parents=True, exist_ok=True)
                output_path = self._latex_output_dir / pdf_name
                shutil.copy2(str(pdf_src), str(output_path))
                # Clean up build artifacts in source directory
                for ext in ('.aux', '.log', '.out', '.toc', '.fls', '.fdb_latexmk', '.synctex.gz'):
                    artifact = source_path.with_suffix(ext)
                    if artifact.exists():
                        try:
                            artifact.unlink()
                        except Exception:
                            pass
                pdf_src.unlink(missing_ok=True)
                return {
                    "success": True,
                    "pdf_path": str(output_path),
                }
            else:
                # Compilation failed - return error info
                output = (stdout or b"").decode("utf-8", errors="replace")
                err = (stderr or b"").decode("utf-8", errors="replace")
                combined = f"{output}\n{err}".strip()
                if len(combined) > 3000:
                    combined = combined[-3000:]
                return {
                    "success": False,
                    "error": "Compilation failed. See log for details.",
                    "log": combined,
                }


__all__ = ["FileManagerToolSet"]
