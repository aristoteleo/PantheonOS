"""ThinkPlugin — leader-only think tool and prompt injection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pantheon.team.plugin import TeamPlugin

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam


THINK_PROMPT = """
## Think Tool Usage

You have access to a `think` tool. Use it as a scratchpad to pause and reason through complex situations before taking action. The think tool does not execute anything or retrieve information — it simply records your reasoning process.

**When to use think:**
- After receiving tool results, before deciding next steps
- When multiple rules or constraints apply to the current request
- When you need to verify that your planned action is correct before executing
- When analyzing results that may require backtracking or a different approach
- When brainstorming multiple solutions and assessing trade-offs

**Before taking action after receiving tool results, use think to:**

1. **List applicable rules** — Identify the specific constraints, policies, or requirements that apply to the current request
2. **Check information completeness** — Verify you have all required information to proceed; identify gaps
3. **Verify compliance** — Confirm your planned action satisfies all applicable constraints
4. **Iterate on correctness** — Review tool results for errors, edge cases, or unexpected outcomes
""".strip()


def think(thought: str) -> str:
    """Use this tool to think step-by-step before acting. It records your reasoning without taking any action or obtaining new information. Use it to analyze tool outputs, plan multi-step approaches, verify compliance with instructions, or reconsider your strategy."""
    return "Thought recorded."


class ThinkPlugin(TeamPlugin):
    """Inject think tool/prompt only into leader when leader declares think."""

    async def get_toolsets(self, team: "PantheonTeam") -> list[tuple[Any, list[str] | None]]:
        if not team.team_agents:
            return []

        primary = team.team_agents[0]
        declared = list(getattr(primary, "_declared_toolsets", []) or [])

        if "think" in declared:
            primary.tool(think, key="think")

        return []

    async def on_team_created(self, team: "PantheonTeam") -> None:
        if not team.team_agents:
            return

        primary = team.team_agents[0]
        declared = list(getattr(primary, "_declared_toolsets", []) or [])

        if "think" not in declared:
            return

        if not getattr(primary, "instructions", None):
            return

        if "## Think Tool Usage" in primary.instructions:
            return

        primary.instructions += "\n\n" + THINK_PROMPT


def _create_think_plugin(config: dict, settings: Any) -> ThinkPlugin:
    """Factory function for plugin registry."""
    return ThinkPlugin()


from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(
    PluginDef(
        name="think_system",
        config_key="think_system",
        enabled_key="enabled",
        factory=_create_think_plugin,
        priority=15,
    )
)
