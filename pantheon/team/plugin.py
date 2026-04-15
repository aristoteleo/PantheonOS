"""
Team plugin system for extending PantheonTeam functionality.

Plugins provide a clean way to add optional features (memory, learning,
compression, etc.) without coupling them directly to PantheonTeam.

Lifecycle hooks (in execution order):
    get_toolsets     — declare toolsets to auto-inject into agents (before on_team_created)
    on_team_created  — after team setup, before any runs
    on_run_start     — before each agent execution
    on_run_end       — after each agent execution
    pre_compression  — before context compression (flush important data)
    post_compression — after context compression
    on_tool_call     — after a tool executes
    on_shutdown      — process exit / cleanup
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam


class TeamPlugin(ABC):
    """Base class for PantheonTeam plugins.

    Subclasses must implement on_team_created(). All other hooks are optional.
    """

    async def get_toolsets(self, team: "PantheonTeam") -> list[tuple[Any, list[str] | None]]:
        """Declare toolsets this plugin wants to inject into agents.

        Called during async_setup, before on_team_created.

        Returns a list of (toolset_instance, agent_names) tuples:
        - toolset_instance: the ToolSet to inject
        - agent_names: list of agent names to inject into, or None for all agents

        Example:
            return [(SkillToolSet(self.runtime), None)]       # all agents
            return [(SkillToolSet(self.runtime), ["coder"])]  # specific agents only
        """
        return []

    @abstractmethod
    async def on_team_created(self, team: "PantheonTeam") -> None:
        """Called after toolsets are injected and team is fully set up."""
        pass

    async def on_run_start(
        self, team: "PantheonTeam", user_input: Any, context: dict
    ) -> None:
        """Called before each run starts."""
        pass

    async def on_run_end(self, team: "PantheonTeam", result: dict) -> None:
        """Called after each run completes."""
        pass

    async def pre_compression(
        self, team: "PantheonTeam", session_id: str, messages: list[dict]
    ) -> str | None:
        """Called before context compression. Return flushed content or None."""
        return None

    async def post_compression(
        self, team: "PantheonTeam", result: dict
    ) -> None:
        """Called after context compression completes."""
        pass

    async def on_tool_call(
        self, team: "PantheonTeam", tool_name: str, args: dict, result: Any
    ) -> None:
        """Called after a tool executes."""
        pass

    async def on_shutdown(self) -> None:
        """Called on process exit for cleanup."""
        pass


    async def on_run_start(
        self, team: "PantheonTeam", user_input: Any, context: dict
    ) -> None:
        """Called before each run starts."""
        pass

    async def on_run_end(self, team: "PantheonTeam", result: dict) -> None:
        """Called after each run completes."""
        pass

    async def pre_compression(
        self, team: "PantheonTeam", session_id: str, messages: list[dict]
    ) -> str | None:
        """Called before context compression. Return flushed content or None."""
        return None

    async def post_compression(
        self, team: "PantheonTeam", result: dict
    ) -> None:
        """Called after context compression completes."""
        pass

    async def on_tool_call(
        self, team: "PantheonTeam", tool_name: str, args: dict, result: Any
    ) -> None:
        """Called after a tool executes."""
        pass

    async def on_shutdown(self) -> None:
        """Called on process exit for cleanup."""
        pass
