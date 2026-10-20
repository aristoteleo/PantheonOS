"""
Unified message formatting utility for ACE and Compression.

This module provides a shared function for converting conversation messages
into formatted text with optional file extraction and skill ID detection.
"""

from dataclasses import dataclass, field
from typing import List
import json
import re

from .log import logger


@dataclass
class FormattedConversation:
    """Formatted conversation result."""
    
    text: str  # Formatted conversation text
    viewed_files: List[str] = field(default_factory=list)
    edited_files: List[str] = field(default_factory=list)
    skill_ids: List[str] = field(default_factory=list)
    question: str = ""  # First user question
    final_answer: str = ""  # Last assistant response
    details_path: str = ""  # Path to full message details (for reading complete content)


# File operation tool categories (match FileManagerToolSet real tool names)
VIEW_TOOLS = {
    # Pantheon FileManagerToolSet
    "read_file", "glob", "grep", "read_pdf", "observe_images", "observe_pdf_screenshots",
    # Antigravity-style tools (for compatibility)
    "view_file", "view_file_outline", "view_code_item", "grep_search", "find_by_name",
}
EDIT_TOOLS = {
    # Pantheon FileManagerToolSet
    "write_file", "update_file", "apply_patch", "manage_path",
    # Antigravity-style tools (for compatibility)
    "write_to_file", "replace_file_content", "multi_replace_file_content",
}


def format_messages_to_text(
    messages: List[dict],
    max_arg_length: int = 200,
    max_output_length: int = 500,
    extract_files: bool = True,
    extract_skills: bool = False,
    save_details_to: str | None = None,
    include_footer_note: bool = True,
    use_smart_truncate: bool = False,  # Enable smart truncation for tool outputs
) -> FormattedConversation:
    """Format conversation messages to text (shared by ACE and Compression).
    
    Uses bracket format: [USER], [ASSISTANT], [TOOL_CALL], [TOOL_RESULT]
    
    Args:
        messages: List of message dicts
        max_arg_length: Max length for individual argument values (per-value truncation)
        max_output_length: Max length for tool output
        extract_files: Whether to extract viewed/edited file paths
        extract_skills: Whether to extract skill IDs from content
        save_details_to: If provided, save original messages to this path
        include_footer_note: Whether to append the truncation note at the end
        use_smart_truncate: If True, use smart truncation (based on raw_content) for tool outputs
                           to preserve JSON structure. Falls back to content if raw_content unavailable.
        
    Returns:
        FormattedConversation with text, files, skills, and metadata
    """
    trajectory_parts = []
    viewed_files = set()
    edited_files = set()
    skill_ids = []
    question = ""
    final_answer = ""
    details_path = ""
    
    for msg in messages:
        role = msg.get("role", "")
        
        # Prefer _llm_content if present (ACE mode)
        content = msg.get("_llm_content") or msg.get("content") or ""
        
        # Handle multimodal content
        if isinstance(content, list):
            text_parts = [
                item.get("text", "") 
                for item in content 
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            content = " ".join(text_parts)
        
        if role == "user":
            if not question:
                question = content
            trajectory_parts.append(f"[USER]\n{content}")
        
        elif role == "assistant":
            # Extract skill IDs if requested
            if extract_skills and content:
                skill_ids.extend(re.findall(r"\[([a-z]+-\d{5})\]", content))
            
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                if content:
                    trajectory_parts.append(f"[ASSISTANT]\n{content}")
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    args_str = func.get("arguments", "")
                    tc_id = tc.get("id", "")
                    
                    # Parse args and truncate per-value (preserve short params like filenames)
                    try:
                        args = json.loads(args_str)
                        
                        # Extract file paths
                        if extract_files:
                            file_path = (
                                args.get("AbsolutePath") or 
                                args.get("TargetFile") or 
                                args.get("File")
                            )
                            if file_path:
                                if name in VIEW_TOOLS:
                                    viewed_files.add(file_path)
                                elif name in EDIT_TOOLS:
                                    edited_files.add(file_path)
                        
                        # Per-value truncation (keep short params like filename intact)
                        truncated_args = {}
                        for k, v in args.items():
                            if isinstance(v, str) and len(v) > max_arg_length:
                                truncated_args[k] = v[:max_arg_length] + f"... [{len(v)} chars]"
                            else:
                                truncated_args[k] = v
                        args_str = json.dumps(truncated_args, ensure_ascii=False)
                    except Exception:
                        # JSON parse failed, fallback to whole-string truncation
                        if len(args_str) > max_arg_length:
                            args_str = args_str[:max_arg_length] + "... (truncated)"
                    
                    trajectory_parts.append(f"[TOOL_CALL: {name}] id={tc_id}\n{args_str}")
            else:
                trajectory_parts.append(f"[ASSISTANT]\n{content}")
                final_answer = content
        
        elif role == "tool":
            tc_id = msg.get("tool_call_id", "")
            
            # Smart truncation: prefer raw_content to avoid cumulative information loss
            if use_smart_truncate and "raw_content" in msg:
                raw = msg.get("raw_content")
                
                # Only use smart truncation if raw_content is a dict
                if isinstance(raw, dict):
                    try:
                        from pantheonos.utils.truncate import smart_truncate_result
                        result = smart_truncate_result(
                            raw, 
                            max_output_length,
                            filter_base64=True  # Re-filter base64 from raw_content
                        )
                    except Exception as e:
                        # Fallback to content if smart truncation fails
                        logger.warning(f"Smart truncation failed: {e}, falling back to content")
                        result = content
                        if len(result) > max_output_length:
                            result = result[:max_output_length] + f"... [{len(result)} chars]"
                else:
                    # raw_content is not a dict, use content
                    result = content
                    if len(result) > max_output_length:
                        result = result[:max_output_length] + f"... [{len(result)} chars]"
            else:
                # Original logic: simple string truncation on content
                result = content
                if len(result) > max_output_length:
                    result = result[:max_output_length] + f"... [{len(result)} chars]"
            
            trajectory_parts.append(f"[TOOL_RESULT] id={tc_id}\n{result}")
        
        elif role == "compression":
            trajectory_parts.append(f"[PREVIOUS_SUMMARY]\n{content}")
    
    # Save original messages to file for later reference
    if save_details_to:
        try:
            from pathlib import Path
            path = Path(save_details_to)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump({"messages": messages}, f, ensure_ascii=False, indent=2)
            details_path = str(path)
        except Exception as e:
            logger.warning(f"Failed to save details: {e}")
    
    # Assemble final text
    final_text = "\n\n".join(trajectory_parts)
    
    # Add unified note about truncated content if details_path exists and requested
    if details_path and include_footer_note:
        final_text += f"\n\n[NOTE: Some content was truncated. Full details saved to: {details_path}]"
    
    return FormattedConversation(
        text=final_text,
        viewed_files=list(viewed_files),
        edited_files=list(edited_files),
        skill_ids=list(set(skill_ids)),
        question=question,
        final_answer=final_answer,
        details_path=details_path,
    )
