"""
Lightweight background Agent for multi-turn reasoning tasks.

Used by Dream, MemoryExtractor, and SkillExtractor to enable
tool-calling loops (read files → analyze → decide) using the
same file_manager toolset that main agents use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pantheon.agent import Agent


async def create_background_agent(
    name: str,
    instructions: str,
    model: str | list[str],
    workspace_path: str | Path,
) -> Agent:
    """Create a lightweight Agent with file_manager for background reasoning.

    The agent has file_manager (read/write/edit files) but no memory,
    no streaming, no delegation — just tools + LLM in a loop.

    Args:
        name: Agent name (for logging)
        instructions: System prompt
        model: LLM model name or fallback chain
        workspace_path: Root directory for file_manager operations
    """
    from pantheon.toolsets.file.file_manager import FileManagerToolSet

    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        use_memory=False,
    )
    fm = FileManagerToolSet("file_manager", str(workspace_path))
    await agent.toolset(fm)
    return agent
