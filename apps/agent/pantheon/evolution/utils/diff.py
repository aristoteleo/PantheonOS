"""
Git diff parsing and application utilities.

Supports both unified diff format and SEARCH/REPLACE block format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import unified_diff
from typing import Dict, List, Optional, Tuple


@dataclass
class DiffHunk:
    """Represents a single diff hunk (change block)."""

    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    old_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)


@dataclass
class FileDiff:
    """Represents all changes to a single file."""

    old_path: str
    new_path: str
    hunks: List[DiffHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False


# Patterns for unified diff parsing
DIFF_HEADER_PATTERN = re.compile(r"^diff --git a/(.+) b/(.+)$")
OLD_FILE_PATTERN = re.compile(r"^--- (?:a/)?(.+)$")
NEW_FILE_PATTERN = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
HUNK_HEADER_PATTERN = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Patterns for SEARCH/REPLACE block format (used by LLM)
SEARCH_REPLACE_PATTERN = re.compile(
    r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)

# Pattern for file markers in LLM output
FILE_MARKER_PATTERN = re.compile(r"^(?:File|PATH|file):\s*(.+)$", re.MULTILINE)


def parse_unified_diff(diff_text: str) -> List[FileDiff]:
    """
    Parse a unified diff string into structured FileDiff objects.

    Args:
        diff_text: The unified diff text

    Returns:
        List of FileDiff objects representing changes to each file
    """
    file_diffs: List[FileDiff] = []
    current_file: Optional[FileDiff] = None
    current_hunk: Optional[DiffHunk] = None
    old_line_num = 0
    new_line_num = 0

    lines = diff_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check for diff header
        header_match = DIFF_HEADER_PATTERN.match(line)
        if header_match:
            if current_file:
                file_diffs.append(current_file)
            current_file = FileDiff(
                old_path=header_match.group(1),
                new_path=header_match.group(2),
            )
            current_hunk = None
            i += 1
            continue

        # Check for old file path
        old_match = OLD_FILE_PATTERN.match(line)
        if old_match and current_file:
            path = old_match.group(1)
            if path == "/dev/null":
                current_file.is_new = True
            else:
                current_file.old_path = path
            i += 1
            continue

        # Check for new file path
        new_match = NEW_FILE_PATTERN.match(line)
        if new_match and current_file:
            path = new_match.group(1)
            if path == "/dev/null":
                current_file.is_deleted = True
            else:
                current_file.new_path = path
            i += 1
            continue

        # Check for hunk header
        hunk_match = HUNK_HEADER_PATTERN.match(line)
        if hunk_match and current_file:
            if current_hunk:
                current_file.hunks.append(current_hunk)

            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1

            current_hunk = DiffHunk(
                file_path=current_file.new_path,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            old_line_num = old_start
            new_line_num = new_start
            i += 1
            continue

        # Process diff content lines
        if current_hunk:
            if line.startswith("-"):
                current_hunk.old_lines.append(line[1:])
                old_line_num += 1
            elif line.startswith("+"):
                current_hunk.new_lines.append(line[1:])
                new_line_num += 1
            elif line.startswith(" "):
                # Context line - appears in both old and new
                current_hunk.old_lines.append(line[1:])
                current_hunk.new_lines.append(line[1:])
                old_line_num += 1
                new_line_num += 1

        i += 1

    # Add last hunk and file
    if current_hunk and current_file:
        current_file.hunks.append(current_hunk)
    if current_file:
        file_diffs.append(current_file)

    return file_diffs


def parse_search_replace_blocks(
    text: str, default_file: str = "main.py"
) -> List[Tuple[str, str, str]]:
    """
    Parse SEARCH/REPLACE blocks from LLM output.

    Args:
        text: The LLM output text containing SEARCH/REPLACE blocks
        default_file: Default file path if none specified

    Returns:
        List of (file_path, search_text, replace_text) tuples
    """
    results: List[Tuple[str, str, str]] = []

    # Find all file markers and their positions
    file_markers = [(m.group(1).strip(), m.start()) for m in FILE_MARKER_PATTERN.finditer(text)]

    # Find all SEARCH/REPLACE blocks
    for match in SEARCH_REPLACE_PATTERN.finditer(text):
        search_text = match.group(1)
        replace_text = match.group(2)
        block_pos = match.start()

        # Find the most recent file marker before this block
        file_path = default_file
        for marker_path, marker_pos in reversed(file_markers):
            if marker_pos < block_pos:
                file_path = marker_path
                break

        results.append((file_path, search_text, replace_text))

    return results


def parse_diff(diff_text: str, default_file: str = "main.py") -> Dict[str, List[Tuple[str, str]]]:
    """
    Parse diff text (unified or SEARCH/REPLACE format) into changes per file.

    Args:
        diff_text: The diff text to parse
        default_file: Default file path for SEARCH/REPLACE blocks

    Returns:
        Dict mapping file paths to list of (search, replace) tuples
    """
    changes: Dict[str, List[Tuple[str, str]]] = {}

    # Try SEARCH/REPLACE format first (common in LLM output)
    sr_blocks = parse_search_replace_blocks(diff_text, default_file)
    if sr_blocks:
        for file_path, search, replace in sr_blocks:
            if file_path not in changes:
                changes[file_path] = []
            changes[file_path].append((search, replace))
        return changes

    # Fall back to unified diff format
    file_diffs = parse_unified_diff(diff_text)
    for fd in file_diffs:
        if fd.is_deleted:
            changes[fd.old_path] = [("__DELETE_FILE__", "")]
        elif fd.is_new:
            # For new files, combine all new lines
            all_new_lines = []
            for hunk in fd.hunks:
                all_new_lines.extend(hunk.new_lines)
            changes[fd.new_path] = [("__NEW_FILE__", "\n".join(all_new_lines))]
        else:
            # For modifications, create search/replace pairs from hunks
            file_changes: List[Tuple[str, str]] = []
            for hunk in fd.hunks:
                old_text = "\n".join(hunk.old_lines)
                new_text = "\n".join(hunk.new_lines)
                file_changes.append((old_text, new_text))
            if file_changes:
                changes[fd.new_path] = file_changes

    return changes


def apply_diff(
    files: Dict[str, str],
    changes: Dict[str, List[Tuple[str, str]]],
) -> Dict[str, str]:
    """
    Apply parsed diff changes to file contents.

    Args:
        files: Dict mapping file paths to their content
        changes: Dict mapping file paths to list of (search, replace) tuples

    Returns:
        New dict with updated file contents
    """
    result = dict(files)

    for file_path, file_changes in changes.items():
        for search, replace in file_changes:
            if search == "__DELETE_FILE__":
                # Delete file
                result.pop(file_path, None)
            elif search == "__NEW_FILE__":
                # Create new file
                result[file_path] = replace
            else:
                # Apply search/replace
                if file_path in result:
                    content = result[file_path]
                    if search in content:
                        result[file_path] = content.replace(search, replace, 1)
                    else:
                        # Try with normalized whitespace
                        normalized_search = _normalize_whitespace(search)
                        normalized_content = _normalize_whitespace(content)
                        if normalized_search in normalized_content:
                            # Find the actual text to replace
                            result[file_path] = _fuzzy_replace(content, search, replace)

    return result


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy matching."""
    lines = text.split("\n")
    return "\n".join(line.strip() for line in lines)


def _fuzzy_replace(content: str, search: str, replace: str) -> str:
    """
    Fuzzy replace that handles whitespace differences.
    """
    search_lines = search.split("\n")
    content_lines = content.split("\n")

    # Try to find matching lines
    for i in range(len(content_lines) - len(search_lines) + 1):
        match = True
        for j, search_line in enumerate(search_lines):
            if content_lines[i + j].strip() != search_line.strip():
                match = False
                break

        if match:
            # Found match, replace
            new_lines = (
                content_lines[:i]
                + replace.split("\n")
                + content_lines[i + len(search_lines) :]
            )
            return "\n".join(new_lines)

    # No match found, return original
    return content


def generate_diff(
    old_files: Dict[str, str],
    new_files: Dict[str, str],
    context_lines: int = 3,
) -> str:
    """
    Generate unified diff between two file sets.

    Args:
        old_files: Dict mapping file paths to old content
        new_files: Dict mapping file paths to new content
        context_lines: Number of context lines in diff

    Returns:
        Unified diff string
    """
    diff_parts: List[str] = []
    all_paths = set(old_files.keys()) | set(new_files.keys())

    for path in sorted(all_paths):
        old_content = old_files.get(path, "")
        new_content = new_files.get(path, "")

        if old_content == new_content:
            continue

        old_lines = old_content.split("\n") if old_content else []
        new_lines = new_content.split("\n") if new_content else []

        diff = unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=context_lines,
        )

        diff_text = "\n".join(diff)
        if diff_text:
            diff_parts.append(f"diff --git a/{path} b/{path}")
            diff_parts.append(diff_text)

    return "\n".join(diff_parts)


def apply_search_replace_to_content(
    content: str,
    search: str,
    replace: str,
) -> Tuple[str, bool]:
    """
    Apply a single search/replace to content.

    Args:
        content: The file content
        search: Text to search for
        replace: Text to replace with

    Returns:
        Tuple of (new_content, success)
    """
    if search in content:
        return content.replace(search, replace, 1), True

    # Try fuzzy matching
    new_content = _fuzzy_replace(content, search, replace)
    return new_content, new_content != content
