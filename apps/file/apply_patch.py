"""Apply patch utilities for file modifications.

Provides functions to parse and apply patches in various formats:
- Unified diff (git diff style)
- V4A/Codex format (OpenAI Codex style)
- Search/Replace format
- diff-match-patch native format
"""

import re
from pathlib import Path

from diff_match_patch import diff_match_patch as DiffMatchPatch


def generate_patch(
    file_path: str | Path,
    original_content: str,
    new_content: str,
    output_format: str = "unified",
) -> dict:
    """Generate a patch from original content to new content.
    
    Utility function for creating patches (not exposed as a tool to LLM).
    Useful for testing, debugging, or programmatic patch generation.
    
    Args:
        file_path: Path to the file (for context in patch headers).
        original_content: Original file content.
        new_content: Desired new content.
        output_format: "unified" for standard diff format, "dmp" for native format.
    
    Returns:
        dict: {
            success: bool,
            patch: str,  # Generated patch content
            stats: {
                additions: int,  # Number of additions
                deletions: int,  # Number of deletions
                hunks: int       # Number of hunks
            }
        }
    
    Example:
        result = generate_patch(
            "config.py",
            "DEBUG = True\n",
            "DEBUG = False\n"
        )
        # result["patch"] contains unified diff
    """
    try:
        dmp = DiffMatchPatch()
        diffs = dmp.diff_main(original_content, new_content)
        dmp.diff_cleanupSemantic(diffs)

        patches = dmp.patch_make(original_content, diffs)

        if output_format == "dmp":
            patch_text = dmp.patch_toText(patches)
        else:
            # Generate unified-style diff
            patch_text = dmp_to_unified(str(file_path), patches)

        # Calculate stats
        additions = sum(1 for d in diffs if d[0] == 1)  # DIFF_INSERT
        deletions = sum(1 for d in diffs if d[0] == -1)  # DIFF_DELETE

        return {
            "success": True,
            "patch": patch_text,
            "stats": {
                "additions": additions,
                "deletions": deletions,
                "hunks": len(patches),
            },
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def execute_patch_operations(
    patch: str,
    workspace_root: Path,
    file_path: str | None = None,
    fuzzy_threshold: float = 0.5,
) -> dict:
    """Execute patch operations on files.
    
    Main entry point for applying patches to files. Handles:
    - Format detection
    - Parsing into operations
    - Executing operations (create/update/delete)
    - Building result summary
    
    Args:
        patch: Patch content string.
        workspace_root: Workspace root directory (Path object).
        file_path: Optional explicit file path.
        fuzzy_threshold: Fuzzy matching tolerance (0.0-1.0).
    
    Returns:
        dict: {
            "success": bool,
            "message": str,
            "summary": {...},
            "files": [...],
            "failed_files": [...]
        }
    """
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        # Detect patch format and parse into file operations
        patch_format = detect_patch_format(patch)
        file_ops = parse_multi_file_patch(patch, patch_format, file_path)

        if not file_ops:
            return {
                "success": False,
                "error": "Failed to parse patch - no valid operations found. Check patch format and headers.",
            }

        files = []
        failed_files = []
        files_modified = 0
        files_created = 0
        files_deleted = 0

        for op in file_ops:
            op_type = op["type"]  # "update", "create", "delete"
            target_file = op["file"]
            op_patch = op.get("patch", "")

            # Resolve file path
            if os.path.isabs(target_file):
                target_path = Path(target_file)
            else:
                target_path = workspace_root / target_file

            if op_type == "delete":
                result = apply_delete_operation(target_path, target_file)
                if result["success"]:
                    files_deleted += 1
                else:
                    failed_files.append(target_file)
                files.append(result)

            elif op_type == "create":
                result = apply_create_operation(target_path, target_file, op_patch)
                if result["success"]:
                    files_created += 1
                else:
                    failed_files.append(target_file)
                files.append(result)

            else:  # update
                result = apply_update_operation(
                    target_path,
                    target_file,
                    op_patch,
                    patch_format,
                    fuzzy_threshold,
                )
                if result["success"]:
                    files_modified += 1
                else:
                    failed_files.append(target_file)
                files.append(result)

        # Build summary
        total_files = files_modified + files_created + files_deleted
        all_success = len(failed_files) == 0

        if all_success:
            message = f"✓ Successfully processed {total_files} file(s): {files_modified} modified, {files_created} created, {files_deleted} deleted"
        else:
            success_count = total_files - len(failed_files)
            message = f"⚠ Partially completed: {success_count}/{total_files} succeeded, {len(failed_files)} failed"

        return {
            "success": all_success,
            "message": message,
            "summary": {
                "total_files": total_files,
                "modified": files_modified,
                "created": files_created,
                "deleted": files_deleted,
                "failed": len(failed_files),
            },
            "files": files,
            "failed_files": failed_files,
        }

    except Exception as e:
        logger.error(f"execute_patch_operations failed: {e}")
        return {"success": False, "error": str(e)}


def _build_operation_result(
    file_name: str,
    action: str,
    success: bool,
    error: str | None = None,
    **kwargs
) -> dict:
    """Build standardized operation result dictionary.
    
    Helper to reduce boilerplate in operation functions.
    
    Args:
        file_name: Name of the file.
        action: Action type ("create", "update", "delete").
        success: Whether operation succeeded.
        error: Optional error message (included only if provided).
        **kwargs: Additional fields to include in result.
    
    Returns:
        Standardized result dict.
    """
    result = {
        "file": file_name,
        "action": action,
        "success": success,
    }
    if error is not None:
        result["error"] = error
    result.update(kwargs)
    return result


def apply_delete_operation(target_path: Path, file_name: str) -> dict:
    """Handle file deletion operation.
    
    Args:
        target_path: Path object to the file to delete.
        file_name: Name of the file (for result reporting).
    
    Returns:
        dict: Operation result with file, action, success, and optional error.
    """
    try:
        if not target_path.exists():
            return _build_operation_result(
                file_name, "delete", False, error="File does not exist"
            )
        target_path.unlink()
        return _build_operation_result(file_name, "delete", True)
    except Exception as e:
        return _build_operation_result(file_name, "delete", False, error=str(e))


def apply_create_operation(target_path: Path, file_name: str, content: str) -> dict:
    """Handle file creation operation.
    
    Args:
        target_path: Path object where the file should be created.
        file_name: Name of the file (for result reporting).
        content: Patch content containing the file content to create.
    
    Returns:
        dict: Operation result with file, action, success, lines_added, and optional error.
    """
    try:
        file_content = extract_create_content(content)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(file_content)
        
        return _build_operation_result(
            file_name, "create", True, lines_added=file_content.count("\n")
        )
    except Exception as e:
        return _build_operation_result(file_name, "create", False, error=str(e))


def apply_update_operation(
    target_path: Path,
    file_name: str,
    patch_content: str,
    patch_format: str,
    fuzzy_threshold: float,
) -> dict:
    """Handle file update operation with fuzzy matching.
    
    Args:
        target_path: Path object to the file to update.
        file_name: Name of the file (for result reporting).
        patch_content: The patch content to apply.
        patch_format: Format of the patch ("unified", "v4a", "search_replace", "dmp").
        fuzzy_threshold: Fuzzy matching threshold (0.0 to 1.0).
    
    Returns:
        dict: Operation result with file, action, success, hunks info, and optional error.
    """
    try:
        if not target_path.exists():
            return _build_operation_result(
                file_name, "update", False,
                error="File does not exist"
            )
        
        with open(target_path, "r", encoding="utf-8") as f:
            original_text = f.read()
        
        dmp_patches = convert_patch_to_dmp(patch_content, patch_format, original_text)
        
        if not dmp_patches:
            return _build_operation_result(
                file_name, "update", False,
                error="No valid patches parsed",
                hunks_applied=0,
                hunks_total=0
            )
        
        new_text, hunks_applied, hunks_total = apply_dmp_patches(
            original_text, dmp_patches, fuzzy_threshold
        )
        
        if hunks_applied == 0:
            return _build_operation_result(
                file_name, "update", False,
                error="No hunks applied - content mismatch",
                hunks_applied=0,
                hunks_total=hunks_total
            )
        
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        
        return _build_operation_result(
            file_name, "update", True,
            hunks_applied=hunks_applied,
            hunks_total=hunks_total,
            exact_match=(hunks_applied == hunks_total)
        )
        
    except UnicodeDecodeError:
        return _build_operation_result(
            file_name, "update", False,
            error="File is not a valid text file"
        )
    except Exception as e:
        return _build_operation_result(file_name, "update", False, error=str(e))






def detect_patch_format(patch: str) -> str:
    """Auto-detect the format of a patch string.
    
    Returns one of: "v4a", "unified"
    
    Supported formats:
    - unified: Standard Git diff format (industry standard)
    - v4a: OpenAI Codex format (*** Update File:, etc.)
    """
    patch_stripped = patch.strip()

    # Check for V4A/Codex format first (most specific)
    if "*** Begin Patch" in patch or "*** Update File:" in patch or "*** Create File:" in patch:
        return "v4a"

    # Default to unified diff (industry standard)
    return "unified"


def parse_multi_file_patch(
    patch: str, patch_format: str, file_path: str | None
) -> list[dict]:
    """Parse patch into list of file operations.
    
    Args:
        patch: The patch content string.
        patch_format: Format of the patch ("v4a" or "unified").
        file_path: Optional default file path.
    
    Returns:
        List of dicts: [{type: "update"|"create"|"delete", file: str, patch: str}]
    """
    if patch_format == "v4a":
        return parse_v4a_patch(patch)
    else:  # unified (default)
        return parse_unified_multi_file(patch, file_path)


# V4A format markers patterns
_V4A_MARKERS = {
    r"\*\*\* Update File:\s*(.+)": "update",
    r"\*\*\* (?:Create|Add) File:\s*(.+)": "create", 
    r"\*\*\* (?:Delete|Remove) File:\s*(.+)": "delete",
}


def parse_v4a_patch(patch: str) -> list[dict]:
    """Parse V4A/Codex format patch into file operations.
    
    V4A format example:
        *** Begin Patch
        *** Update File: path/to/file.py
        @@ context @@
        - old line
        + new line
        *** Create File: path/to/new.py
        + content
        *** Delete File: path/to/old.py
        *** End Patch
    """
    operations = []
    lines = patch.split("\n")
    
    current_op = None
    current_file = None
    current_content = []
    
    for line in lines:
        # Skip begin/end markers
        if line.strip() in ("*** Begin Patch", "*** End Patch"):
            continue
        
        # Check for file operation markers using regex
        matched = False
        for pattern, op_type in _V4A_MARKERS.items():
            match = re.match(pattern, line)
            if match:
                # Save previous operation
                if current_op and current_file:
                    operations.append({
                        "type": current_op,
                        "file": current_file,
                        "patch": "\n".join(current_content),
                    })
                
                # Handle delete immediately (no content to collect)
                if op_type == "delete":
                    operations.append({
                        "type": "delete",
                        "file": match.group(1),
                        "patch": "",
                    })
                    current_op = None
                    current_file = None
                    current_content = []
                else:
                    current_op = op_type
                    current_file = match.group(1)
                    current_content = []
                
                matched = True
                break
        
        # If not a marker, accumulate content
        if not matched and current_op:
            current_content.append(line)
    
    # Don't forget the last operation
    if current_op and current_file:
        operations.append({
            "type": current_op,
            "file": current_file,
            "patch": "\n".join(current_content),
        })
    
    return operations




def parse_unified_multi_file(patch: str, default_file: str | None) -> list[dict]:
    """Parse unified diff that may contain multiple files.
    
    Uses regex to split by file boundaries for cleaner implementation.
    """
    operations = []
    
    # Split by file boundaries (lines starting with "--- ")
    # Use positive lookahead to keep the delimiter
    file_blocks = re.split(r'(?=^--- )', patch, flags=re.MULTILINE)
    
    for block in file_blocks:
        block = block.strip()
        if not block:
            continue
        
        # Extract file path from +++ header
        plus_match = re.search(r'^\+\+\+ (?:b/)?(.+?)(?:\t|$)', block, re.MULTILINE)
        minus_match = re.search(r'^--- (?:a/)?(.+?)(?:\t|$)', block, re.MULTILINE)
        
        if plus_match:
            file_path = plus_match.group(1)
            
            # Check if this is a new file (--- /dev/null)
            is_new_file = minus_match and minus_match.group(1) == "/dev/null"
            
            operations.append({
                "type": "create" if is_new_file else "update",
                "file": file_path,
                "patch": block
            })
        elif minus_match:
            # Has --- but no +++, use the --- path
            file_path = minus_match.group(1)
            operations.append({
                "type": "update",
                "file": file_path,
                "patch": block
            })
    
    # If no files found but we have a default, use it
    if not operations and default_file:
        operations.append({
            "type": "update",
            "file": default_file,
            "patch": patch,
        })
    
    return operations


def extract_create_content(content: str) -> str:
    """Extract file content from a create patch."""
    lines = content.split("\n")
    file_lines = []
    
    for line in lines:
        # Skip hunk headers
        if line.startswith("@@"):
            continue
        # V4A format: lines start with + or are plain
        if line.startswith("+"):
            file_lines.append(line[1:])
        elif line.startswith("-"):
            continue  # Skip removal lines in create
        elif not line.startswith("\\"):
            # Plain content line
            if line.startswith(" "):
                file_lines.append(line[1:])
            elif line:
                file_lines.append(line)
    
    file_content = "\n".join(file_lines)
    if file_content and not file_content.endswith("\n"):
        file_content += "\n"
    
    return file_content



def _parse_diff_lines(lines: list[str], skip_headers: bool = False) -> tuple[str, str]:
    """Parse diff-format lines (+/-/ prefix) into old and new content.
    
    Shared parser for V4A and unified diff formats.
    
    Args:
        lines: List of diff lines.
        skip_headers: If True, skip --- and +++ headers.
    
    Returns:
        Tuple of (old_content, new_content) as strings.
    """
    old_content = []
    new_content = []
    
    for line in lines:
        # Skip headers if requested
        if skip_headers and (line.startswith("---") or line.startswith("+++")):
            continue
        
        # Skip hunk headers
        if line.startswith("@@"):
            continue
        
        # Parse diff markers
        if line.startswith("-"):
            old_content.append(line[1:] + "\n")
        elif line.startswith("+"):
            new_content.append(line[1:] + "\n")
        elif line.startswith(" "):
            # Context line (appears in both)
            old_content.append(line[1:] + "\n")
            new_content.append(line[1:] + "\n")
        elif line == "" and old_content and new_content:
            # Empty context line
            old_content.append("\n")
            new_content.append("\n")
    
    return "".join(old_content), "".join(new_content)


def v4a_content_to_dmp(content: str, original_text: str) -> list:
    """Convert V4A patch content to diff-match-patch patches."""
    dmp = DiffMatchPatch()
    lines = content.split("\n")
    
    old_text, new_text = _parse_diff_lines(lines)
    
    if not old_text and not new_text:
        return []
    
    diffs = dmp.diff_main(old_text, new_text)
    dmp.diff_cleanupSemantic(diffs)
    return dmp.patch_make(old_text, diffs)


def unified_to_dmp(unified_patch: str, original_text: str) -> list:
    """Convert unified diff to diff-match-patch patches."""
    dmp = DiffMatchPatch()
    lines = unified_patch.split("\n")

    # Collect hunks (separated by @@ markers)
    hunks = []
    current_hunk = []
    
    for line in lines:
        if line.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = []
        else:
            current_hunk.append(line)
    
    # Don't forget the last hunk
    if current_hunk:
        hunks.append(current_hunk)

    # Create DMP patches from each hunk
    all_patches = []
    for hunk_lines in hunks:
        old_text, new_text = _parse_diff_lines(hunk_lines, skip_headers=True)
        
        if old_text or new_text:
            diffs = dmp.diff_main(old_text, new_text)
            dmp.diff_cleanupSemantic(diffs)
            patches = dmp.patch_make(old_text, diffs)
            all_patches.extend(patches)

    return all_patches


def dmp_to_unified(file_path: str, patches: list) -> str:
    """Convert diff-match-patch patches to unified diff format."""
    if not patches:
        return ""

    lines = [
        f"--- a/{file_path}",
        f"+++ b/{file_path}",
    ]

    for patch in patches:
        # Generate hunk header
        lines.append(
            f"@@ -{patch.start1 + 1},{patch.length1} +{patch.start2 + 1},{patch.length2} @@"
        )

        for diff_op, text in patch.diffs:
            text_lines = text.split("\n")
            for i, line in enumerate(text_lines):
                # Skip empty string at end from split
                if i == len(text_lines) - 1 and line == "":
                    continue
                if diff_op == 0:  # DIFF_EQUAL
                    lines.append(f" {line}")
                elif diff_op == 1:  # DIFF_INSERT
                    lines.append(f"+{line}")
                elif diff_op == -1:  # DIFF_DELETE
                    lines.append(f"-{line}")

    return "\n".join(lines)


def apply_dmp_patches(
    original_text: str,
    dmp_patches: list,
    fuzzy_threshold: float = 0.5,
) -> tuple[str, int, int]:
    """Apply diff-match-patch patches with fuzzy matching.
    
    Args:
        original_text: The original file content.
        dmp_patches: List of DMP patch objects.
        fuzzy_threshold: Matching threshold (0.0=exact, 1.0=very fuzzy).
    
    Returns:
        Tuple of (new_text, hunks_applied, hunks_total)
    """
    dmp = DiffMatchPatch()
    dmp.Match_Threshold = fuzzy_threshold
    dmp.Match_Distance = 1000
    
    new_text, results = dmp.patch_apply(dmp_patches, original_text)
    
    hunks_applied = sum(results)
    hunks_total = len(results)
    
    return new_text, hunks_applied, hunks_total


def convert_patch_to_dmp(
    patch_content: str,
    patch_format: str,
    original_text: str,
) -> list:
    """Convert patch content to DMP patches based on format.
    
    Args:
        patch_content: The patch content string.
        patch_format: Format of the patch ("unified" or "v4a").
        original_text: The original file content.
    
    Returns:
        List of DMP patch objects.
    """
    if patch_format == "v4a":
        return v4a_content_to_dmp(patch_content, original_text)
    else:  # unified (default)
        return unified_to_dmp(patch_content, original_text)
