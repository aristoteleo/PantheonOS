"""Truncation utilities for tool output management.

Strategy: Unified JSON Format
- All tool outputs are formatted as JSON (compact for LLM, formatted for files)
- Special handling for tools with 'truncated' field (read_file, shell, etc.)
- Field-level recursive truncation preserves data structure
- Oversized outputs saved to files with intelligent previews

Key Features:
1. Token Optimization:
   - Returns compact JSON to LLM (no indent)
   - Saves formatted JSON to files (indent=2 for line-by-line reading)

2. Special Handling for 'truncated' Field:
   - Tools: read_file, shell, python_interpreter
   - Skip base64 filtering (already processed by tool)
   - Skip length limits (trust tool's internal truncation)
   - Return JSON as-is

3. Standard Tools (MCP, notebook, etc.):
   - Apply base64 filtering
   - Apply length limits
   - Field-level truncation if needed
   - Save to file + preview if too large

4. Guarantees:
   - Always returns a string (multiple fallback layers)
   - No data loss (full content saved to files)
   - Consistent JSON format
   - Handles edge cases (circular refs, non-serializable objects)

Configuration:
  - max_tool_content_length: 50K characters (and MAX_OUTPUT_LINES lines of text)
  - max_file_read_chars: 50K-100K (read_file internal limit)
  - Recursion depth: 2 layers (covers 99% cases)
"""

import json
from pathlib import Path
from typing import Any


def _format_file_size(num_bytes: int) -> str:
    """Format byte count as human-readable size string."""
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{num_bytes}B"


# Markers around a capped tool output (docs/agent/agent-core.md).
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
PREVIEW_SIZE_BYTES = 2000
#: A tool output longer than this many lines is capped like an oversized one.
MAX_OUTPUT_LINES = 2000


def _output_file(temp_dir: str, suffix: str) -> Path:
    import uuid

    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    return Path(temp_dir) / f"tool_output_{uuid.uuid4().hex[:12]}.{suffix}"


def _head(content: str, max_chars: int, max_lines: int = MAX_OUTPUT_LINES) -> str:
    """The start of content within max_chars and max_lines."""
    lines = content.split("\n")
    head = "\n".join(lines[:max_lines])
    return head[:max_chars]


def truncate_string(content: str, max_length: int) -> str:
    """Truncate string preserving head and tail with info.
    
    Args:
        content: String to truncate
        max_length: Maximum allowed length
        
    Returns:
        Truncated string with head...truncated...tail format
    """
    if len(content) <= max_length:
        return content
    
    truncated_chars = len(content) - max_length
    suffix = f"\n[truncated {len(content) - max_length:,}/{len(content):,} chars]"
    
    # Calculate available space for content
    available = max_length - len(suffix) - 20  # 20 for "...truncated..."
    
    # Protection: if available space is too small, use simple truncation
    if available < 100:
        simple_max = max(0, max_length - len(suffix))
        return content[:simple_max] + suffix
    
    half = available // 2
    
    head = content[:half]
    tail = content[-half:]
    
    return f"{head}\n\n...truncated...\n\n{tail}{suffix}"


def _format_truncated_message(
    preview: str,
    total_size: int,
    filepath: Path,
    preview_size: int | None = None,
    field: str | None = None,
) -> str:
    """The capped output the model reads: where the full output is, and its start."""
    if preview_size is None:
        preview_size = len(preview)
    what = f"Full text of `{field}`" if field else "Full output"

    return (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"Output too large ({_format_file_size(total_size)}). "
        f"{what} saved to: {filepath} (read it with read_file and start_line/end_line).\n\n"
        f"Preview (first {_format_file_size(preview_size)}):\n"
        f"{preview}\n"
        f"{PERSISTED_OUTPUT_CLOSING_TAG}"
    )





def _truncate_fields_recursive(
    data: Any, 
    budget: int, 
    depth: int = 0
) -> Any:
    """Recursively truncate large field values.
    
    Args:
        data: Data to truncate
        budget: Character budget
        depth: Current depth
        
    Returns:
        Truncated copy
    """
    # Simplified depth limit: 2 layers cover 99% of cases
    if depth > 2:
        return data
    
    if isinstance(data, dict):
        if not data:
            return data
        
        result = {}
        field_budget = budget // max(len(data), 1)
        
        for key, value in data.items():
            if isinstance(value, str) and len(value) > field_budget:
                n = len(value) - field_budget
                result[key] = value[:field_budget] + f"[truncated {n}/{len(value)} chars]"
            elif isinstance(value, (dict, list)):
                result[key] = _truncate_fields_recursive(value, field_budget, depth + 1)
            else:
                result[key] = value
        return result
    
    elif isinstance(data, list):
        if not data:
            return data
        
        item_budget = budget // max(len(data), 1)
        result = []
        for item in data:
            if isinstance(item, str) and len(item) > item_budget:
                # Truncate string directly
                n = len(item) - item_budget
                result.append(item[:item_budget] + f"[truncated {n}/{len(item)} chars]")
            elif isinstance(item, (dict, list)):
                # Recurse for nested structures
                result.append(_truncate_fields_recursive(item, item_budget, depth + 1))
            else:
                # Keep other types as-is
                result.append(item)
        return result
    
    elif isinstance(data, str) and len(data) > budget:
        n = len(data) - budget
        return data[:budget] + f"[truncated {n}/{len(data)} chars]"
    
    return data




def _truncate_non_dict(
    content: str,
    max_length: int,
    save_threshold_multiplier: float,
    temp_dir: str,
) -> str:
    """Cap a text result at max_length characters and MAX_OUTPUT_LINES lines;
    the full text goes to a file the model can read."""
    if len(content) <= max_length and content.count("\n") < MAX_OUTPUT_LINES:
        return content
    try:
        filepath = _output_file(temp_dir, "txt")
        filepath.write_text(content, encoding="utf-8")
        return _format_truncated_message(
            preview=_head(content, max(max_length // 2, PREVIEW_SIZE_BYTES)),
            total_size=len(content),
            filepath=filepath,
        )
    except Exception as e:
        from pantheonos.utils.log import logger
        logger.warning(f"Failed to save output: {e}")
    return truncate_string(content, max_length)


def _truncate_json_path(
    result: dict,
    max_length: int,
    filter_base64: bool,
    temp_dir: str,
) -> str:
    """Handle JSON tools truncation.

    Special handling for tools with 'truncated' field:
    - Skip base64 filtering (already processed by tool)
    - Length limits are ALWAYS applied (per-tool thresholds are the
      primary control; Layer 1's truncated flag only means the tool
      did its own pre-processing, not that no further limits apply).
    """
    # Check if tool already handled truncation
    has_truncated_field = 'truncated' in result

    # Step 1: Base64 filter (skip for tools with truncated field)
    if filter_base64 and not has_truncated_field:
        try:
            from pantheonos.utils.llm import filter_base64_in_tool_result
            result = filter_base64_in_tool_result(result)
        except RecursionError:
            from pantheonos.utils.log import logger
            logger.warning("Skipping base64 filter due to circular reference")
        except Exception as e:
            from pantheonos.utils.log import logger
            logger.warning(f"Skipping base64 filter due to error: {e}")

    # Step 2: Format to JSON
    try:
        formatted = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        from pantheonos.utils.log import logger
        logger.warning(f"JSON serialization failed: {e}, using repr")
        formatted = repr(result)

    # Step 3: Length check — always applied regardless of truncated field. A
    # text field longer than MAX_OUTPUT_LINES lines (command output) counts as
    # oversized too.
    long_text = any(isinstance(v, str) and v.count("\n") >= MAX_OUTPUT_LINES for v in result.values())
    if len(formatted) <= max_length and not long_text:
        return formatted

    # Step 4: Save and generate preview
    return _save_and_preview_json(result, formatted, max_length, temp_dir)


def _save_and_preview_json(
    result: dict,
    formatted: str,
    max_length: int,
    temp_dir: str,
) -> str:
    """Save oversized JSON and generate preview."""
    # A result whose bulk is one text field (command output, file content):
    # save that text, so the model can read the file by lines.
    texts = [(k, v) for k, v in result.items() if isinstance(v, str)]
    if texts:
        field, text = max(texts, key=lambda kv: len(kv[1]))
        if len(text) * 2 >= len(formatted):
            try:
                filepath = _output_file(temp_dir, "txt")
                filepath.write_text(text, encoding="utf-8")
                lines = text.count("\n") + 1
                head = _head(text, max(max_length // 2, PREVIEW_SIZE_BYTES))
                preview = json.dumps({**result, field: head + f"\n[... {lines} lines in all; the full text is in {filepath}]"},
                                     ensure_ascii=False)
                if len(preview) > max_length:
                    preview = truncate_string(preview, max_length)
                return _format_truncated_message(preview=preview, total_size=len(formatted), filepath=filepath,
                                                 field=field)
            except Exception as e:
                from pantheonos.utils.log import logger
                logger.warning(f"Failed to save output: {e}")
    filepath = _output_file(temp_dir, "json")
    
    # Save full content (formatted for line-by-line reading)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            # Re-serialize with indent for file readability
            json.dump(result, f, indent=2, ensure_ascii=False)
    except Exception as e:
        from pantheonos.utils.log import logger
        logger.warning(f"Failed to save output: {e}")
        return truncate_string(formatted, max_length)
    
    # Generate field-truncated preview
    preview_budget = max(int(max_length * 0.5), max_length // 3)
    truncated_result = _truncate_fields_recursive(result, preview_budget)
    
    try:
        preview = json.dumps(truncated_result, ensure_ascii=False)
    except:
        preview = repr(truncated_result)
    
    # Ensure preview doesn't exceed max_length
    if len(preview) > max_length:
        preview = truncate_string(preview, max_length)
    
    return _format_truncated_message(
        preview=preview,
        total_size=len(formatted),
        filepath=filepath,
        preview_size=None,
    )


def smart_truncate_result(
    result: Any,
    max_length: int,
    filter_base64: bool = True,
    save_threshold_multiplier: float = 1.0,
    temp_dir: str | None = None,
) -> str:
    """Smart truncation with unified JSON strategy.
    
    Guarantees:
    1. Always returns a string (multiple fallback layers)
    2. Non-truncated content returns as-is (json.dumps, fallback to repr)
    3. Tools with 'truncated' field skip base64 filtering and length limits
    4. All outputs are JSON formatted for consistency
    
    Special handling:
    - Tools with 'truncated' field (read_file, shell, python_interpreter):
      * Skip base64 filtering (already processed)
      * Skip length limits (trust tool's internal truncation)
      * Return JSON as-is
    
    - Other tools (MCP, notebook, etc.):
      * Apply base64 filtering
      * Apply length limits
      * Field-level truncation if needed
      * Save to file if too large
    
    Args:
        result: Tool result to truncate
        max_length: Maximum allowed length
        filter_base64: Whether to filter base64 content
        save_threshold_multiplier: Multiplier for save threshold
        temp_dir: Optional temp directory
        
    Returns:
        Formatted string (always succeeds with fallbacks)
    """
    # Initialize temp_dir
    if temp_dir is None:
        from pantheonos.settings import get_settings
        temp_dir = str(get_settings().tmp_dir)
    
    # Handle non-dict types
    if not isinstance(result, dict):
        content = str(result) if not isinstance(result, str) else result
        return _truncate_non_dict(content, max_length, save_threshold_multiplier, temp_dir)
    
    # Unified JSON path (with special handling for truncated field)
    return _truncate_json_path(result, max_length, filter_base64, temp_dir)
