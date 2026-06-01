"""
TaskSystemPlugin — PantheonTeam adapter for the task toolset.

Injects TaskToolSet into the primary agent (first agent in team) via
get_toolsets(). Per-LLM-call behaviour (EU message generation and tool
tracking) is registered as closures directly on the primary agent, so the
agent holds only plain callables and has no knowledge of this plugin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam

# ── Prompt constants ──────────────────────────────────────────────────────────

TASK_BRAIN_DIR_BLOCK = """

<task_brain_dir>
Artifact directory: {brain_dir}/{{chat_id}}
  - Base path: {brain_dir}
  - {{chat_id}} is the current conversation's ID (provided per-request in
    context). It keeps each conversation's task.md isolated from other chats.
  - Example: {brain_dir}/default/task.md
</task_brain_dir>"""


OUTPUT_REGISTRATION_BLOCK = """

<output_registration>
When you produce a user-facing DELIVERABLE — a report, a figure, a data
file/table, or an output folder — register it with the `register_output` tool
so the user can find and browse it in the Output panel.

  - This is the ONLY way deliverables show up for the user. In particular,
    files produced by RUNNING CODE (e.g. a plot a script saved, a generated
    CSV) are tracked NOWHERE else — you MUST register them.
  - Prefer organizing related deliverables into a folder and registering the
    FOLDER (its contents are shown as a live browsable tree) — you needn't
    register every file individually. Register single important files too,
    with a title + description.
  - Register the moment a deliverable is ready; when wrapping up a task
    (REVIEW), make sure every deliverable has been registered.
  - Do NOT register scratch/intermediate files or the task.md/plan.md
    artifacts — only things the user actually wants.
</output_registration>"""


class TaskSystemPlugin(TeamPlugin):
    """Injects TaskToolSet into the primary agent via closures."""

    async def get_toolsets(self, team: "PantheonTeam") -> list[tuple[Any, list[str] | None]]:
        """Create TaskToolSet, register closure hooks on primary agent, return toolset spec."""
        from pantheon.toolsets.task import TaskToolSet

        if not team.team_agents:
            return []

        primary = team.team_agents[0]
        task_toolset = TaskToolSet()

        # Closure captures task_toolset directly — no registry or cache needed.

        async def _ephemeral_hook(history: list[dict], context_variables: dict) -> list[dict]:
            return [task_toolset.get_ephemeral_prompt(context_variables)]

        async def _tool_tracking_hook(
            tool_calls: list[dict],
            tool_messages: list[dict],
            context_variables: dict,
        ) -> None:
            task_toolset.process_tool_messages(
                tool_calls=tool_calls,
                tool_messages=tool_messages,
                context_variables=context_variables,
            )

        # Register only on the primary agent — sub-agents don't need task hooks.
        primary._ephemeral_hooks.append(_ephemeral_hook)
        primary._tool_tracking_hooks.append(_tool_tracking_hook)

        logger.debug(f"TaskSystemPlugin: injecting TaskToolSet into '{primary.name}'")
        return [(task_toolset, [primary.name])]

    async def on_team_created(self, team: "PantheonTeam") -> None:
        """Inject <task_brain_dir> into the primary agent's instructions.

        Replaces the {task_brain_dir} placeholder in agentic_general.md with
        the real path. Only the primary (leader) agent writes artifacts, so
        only its instructions need the concrete path.
        """
        from pantheon.settings import get_settings

        if not team.team_agents:
            return

        settings = get_settings()
        brain_dir = str(settings.brain_dir)
        tag = TASK_BRAIN_DIR_BLOCK.format(brain_dir=brain_dir)

        primary = team.team_agents[0]
        if hasattr(primary, "instructions") and primary.instructions:
            primary.instructions += tag + OUTPUT_REGISTRATION_BLOCK
            logger.debug(f"TaskSystemPlugin: injected task_brain_dir + output_registration into '{primary.name}'")


def _create_task_plugin(config: dict, settings: Any) -> TaskSystemPlugin:
    """Factory function for plugin registry."""
    return TaskSystemPlugin()


# Register with plugin registry
from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(PluginDef(
    name="task_system",
    config_key="task_system",
    enabled_key="enabled",
    factory=_create_task_plugin,
    priority=10,  # Before memory (50) and learning (100)
))
