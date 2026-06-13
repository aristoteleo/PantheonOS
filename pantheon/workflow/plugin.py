"""WorkflowTeamPlugin — injects the WorkflowToolSet into the Leader (Task 10).

This is the PantheonTeam adapter for the dynamic-workflow module. It mirrors the
:class:`~pantheon.internal.task_system.plugin.TaskSystemPlugin` pattern: the
WorkflowToolSet is injected into the *primary* agent (the Leader / entry agent,
``team.team_agents[0]``) ONLY — sub-agents never see the workflow tools
(decision 9/13: only the Leader drives workflows).

Coupling contract (§6.1): this module must NOT import ``chatroom``. The room
constructs the :class:`WorkflowEngine` and passes it in; the plugin only stores
it and exposes it via a leader-scoped toolset. The only imports are the
``TeamPlugin`` interface type and the local ``WorkflowToolSet``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger

from .toolset import WorkflowToolSet

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam
    from .engine import WorkflowEngine


class WorkflowTeamPlugin(TeamPlugin):
    """Inject :class:`WorkflowToolSet` into the Leader, scoped to it alone."""

    def __init__(self, engine: "WorkflowEngine") -> None:
        # The room constructs the engine (a room-level singleton) and passes it
        # in; the plugin never constructs it (coupling contract).
        self._engine = engine

    async def get_toolsets(
        self, team: "PantheonTeam"
    ) -> list[tuple[Any, list[str] | None]]:
        """Return a single leader-scoped WorkflowToolSet spec.

        The Leader / entry agent is ``team.team_agents[0]`` — the same accessor
        TaskSystemPlugin uses for the primary agent. We target it by name so the
        toolset is injected into the Leader ONLY and never into sub-agents
        (decision 9/13). If no leader is resolvable we return ``[]`` so team
        setup is never crashed by this plugin.
        """
        if not getattr(team, "team_agents", None):
            return []

        leader = team.team_agents[0]
        toolset = WorkflowToolSet(self._engine)
        logger.debug(
            f"WorkflowTeamPlugin: injecting WorkflowToolSet into leader '{leader.name}'"
        )
        return [(toolset, [leader.name])]

    async def on_team_created(self, team: "PantheonTeam") -> None:
        """Required by the ABC. No team-level instruction injection in Phase 1."""
        return None
