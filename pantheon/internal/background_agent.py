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
    *, model_scope=None, tool_bindings=None,
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
    if model_scope is not None and tool_bindings is None:
        raise RuntimeError("Scoped background work requires an explicit Files dependency")

    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        use_memory=False,
        **({"model_scope": model_scope} if model_scope is not None else {}),
    )
    if tool_bindings is not None:
        await tool_bindings.attach(agent, ["file_manager"], [])
    else:
        from pantheon.apps.builtin.file.file_manager import FileManagerToolSet
        fm = FileManagerToolSet("file_manager", str(workspace_path))
        await agent.toolset(fm)
    return agent


async def run_background_agent(prompt, *, model_scope=None, tool_bindings=None, **kwargs):
    """Join adopted tool work before releasing the plugin's operation lifetime.

    The App owns explicit dependency clients. They are borrowed here; closing one
    background Agent must not close clients used by another accepted task.
    """
    import asyncio
    from pantheon.dependency_provider import _drain_call
    agent = await create_background_agent(**kwargs, **(
        {"model_scope": model_scope, "tool_bindings": tool_bindings}
        if model_scope is not None or tool_bindings is not None else {}))
    try:
        return await agent.run(prompt, use_memory=False)
    finally:
        # Legacy test/SDK agents may have no Pantheon task manager.
        from pantheon.background import BackgroundTaskManager
        manager = getattr(agent, '_bg_manager', None)
        if isinstance(manager, BackgroundTaskManager):
            async def drain():
                while pending := [t.asyncio_task for t in manager.list_tasks()
                                  if t.asyncio_task is not None and not t.asyncio_task.done()]:
                    await asyncio.gather(*pending, return_exceptions=True)
            await _drain_call(asyncio.create_task(drain()))
