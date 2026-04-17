"""
Memory compression utilities for ACE and Skill Learning.

Provides a unified function to compress memory.json files into
truncated trajectory text for efficient LLM analysis.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .log import logger
from .message_formatter import format_messages_to_text


@dataclass
class CompressedMemory:
    """Result of memory compression."""
    
    trajectory_path: str  # Path to compressed trajectory text file
    details_path: str  # Path to full details (= input memory_path)
    trajectory_text: str  # The compressed text content
    skill_ids_cited: List[str]  # Skill IDs found in messages


def compress_memory(
    memory_path: str,
    output_dir: Optional[str] = None,
    max_arg_length: Optional[int] = None,
    max_output_length: Optional[int] = None,
    use_smart_truncate: bool = False,  # Enable smart truncation for tool outputs
) -> CompressedMemory:
    """
    Compress a memory.json file into trajectory files.
    
    Reads the memory file, creates a truncated trajectory summary,
    and saves it to a separate file. The original memory_path
    serves as the details_path for full content access.
    
    Args:
        memory_path: Path to memory.json file
        output_dir: Directory for trajectory output (default: temp dir)
        max_arg_length: Max chars for tool arguments (default: from settings)
        max_output_length: Max chars for tool outputs (default: from settings)
        use_smart_truncate: If True, use smart truncation (based on raw_content) for tool outputs
                           to preserve JSON structure and avoid cumulative information loss
        
    Returns:
        CompressedMemory with paths and content
        
    Raises:
        ValueError: If memory file is invalid or has no messages
        FileNotFoundError: If memory file doesn't exist
    """
    # Get settings defaults if not provided
    if max_arg_length is None:
        max_arg_length = 200
    if max_output_length is None:
        max_output_length = 500

    memory_path = Path(memory_path)
    if not memory_path.exists():
        raise FileNotFoundError(f"Memory file not found: {memory_path}")
    
    # Read memory
    with open(memory_path, "r", encoding="utf-8") as f:
        memory = json.load(f)
    
    messages = memory.get("messages", [])
    if not messages:
        raise ValueError(f"No messages found in {memory_path}")
    
    # Format with truncation (details_path = memory_path, no new file)
    result = format_messages_to_text(
        messages,
        max_arg_length=max_arg_length,
        max_output_length=max_output_length,
        extract_files=True,
        extract_skills=True,
        save_details_to=None,  # Don't create new details file
        include_footer_note=True,
        use_smart_truncate=use_smart_truncate,  # Pass through smart truncation flag
    )
    
    # Create output directory
    if output_dir:
        out_path = Path(output_dir)
    else:
        out_path = Path(tempfile.mkdtemp(prefix="trajectory_"))
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Save trajectory to file
    trajectory_id = uuid.uuid4().hex[:8]
    trajectory_path = out_path / f"trajectory_{trajectory_id}.txt"
    
    # Append note about details location and query instructions
    trajectory_text = result.text
    trajectory_text += f"\n\n[Full details: {memory_path}]"
    trajectory_text += "\n[Query truncated content by id:"
    trajectory_text += '\n  Output: jq \'.messages[] | select(.tool_call_id=="<id>") | .content\' <details>'
    trajectory_text += '\n  Input:  jq \'.messages[].tool_calls[]? | select(.id=="<id>") | .function.arguments\' <details>]'
    
    with open(trajectory_path, "w", encoding="utf-8") as f:
        f.write(trajectory_text)
    
    logger.debug(f"Compressed {memory_path} -> {trajectory_path}")
    
    return CompressedMemory(
        trajectory_path=str(trajectory_path),
        details_path=str(memory_path),  # Original file is the details
        trajectory_text=trajectory_text,
        skill_ids_cited=result.skill_ids,
    )


def save_messages_to_memory(
    messages: List[dict],
    output_path: str,
    metadata: Optional[dict] = None,
) -> str:
    """
    Save messages to a memory.json file.
    
    Helper for ACE to pre-save messages before calling compress_memory.
    
    Args:
        messages: List of message dicts
        output_path: Path to save the memory file
        metadata: Optional additional metadata to include
        
    Returns:
        The output path
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    data = {"messages": messages}
    if metadata:
        data.update(metadata)
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return str(path)
