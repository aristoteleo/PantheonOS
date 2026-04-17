"""LearningPlugin — TeamPlugin adapter for the learning system."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pantheon.settings import get_settings
from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger

from .runtime import LearningRuntime

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam


class LearningPlugin(TeamPlugin):
    """TeamPlugin adapter — zero business logic, delegates to LearningRuntime."""

    def __init__(self, runtime: LearningRuntime):
        self.runtime = runtime
        self._background_tasks: set[asyncio.Task] = set()

    async def get_toolsets(self, team: "PantheonTeam") -> list:
        """Inject SkillToolSet into all agents."""
        if not self.runtime.is_initialized:
            return []
        from .toolset import SkillToolSet
        return [(SkillToolSet(self.runtime), None)]

    async def on_team_created(self, team: "PantheonTeam") -> None:
        """Inject skill index into agent instructions."""
        if not self.runtime.is_initialized:
            return

        agents = getattr(team, "team_agents", None)
        if not isinstance(agents, list):
            agents = team.agents if isinstance(team.agents, list) else list(team.agents.values())
        pantheon_dir = str(get_settings().pantheon_dir)
        for agent in agents:
            guidance = self.runtime.build_skill_guidance(agent_name=agent.name)
            if guidance and hasattr(agent, "instructions") and agent.instructions:
                guidance = guidance.replace(".pantheon/", f"{pantheon_dir}/")
                agent.instructions += f"\n\n{guidance}"
                logger.debug(f"Injected skill guidance into agent '{agent.name}'")

    async def on_run_start(
        self, team: "PantheonTeam", user_input: Any, context: dict
    ) -> None:
        pass

    async def on_run_end(self, team: "PantheonTeam", result: dict) -> None:
        """Post-run: increment counter and maybe extract skills (non-blocking).

        Sub-agent runs (identified by "question" key in result) are skipped —
        their results are already captured in the main agent's conversation.
        """
        if not self.runtime.is_initialized:
            return

        # Sub-agent delegation results have a "question" key; skip them
        if result.get("question") is not None:
            return

        session_id = result.get("chat_id") or "default"
        messages = result.get("messages", [])
        if not messages:
            return

        memory = result.get("memory")
        all_messages = memory._messages if memory and hasattr(memory, "_messages") else messages

        # Non-blocking extraction with proper error handling
        # Pass session note path so background agent can read it via file_manager
        session_note_path = ""
        for plugin in getattr(team, "plugins", []):
            mem_runtime = getattr(plugin, "runtime", None)
            if mem_runtime and hasattr(mem_runtime, "session_note") and mem_runtime.session_note:
                note_path = mem_runtime.session_note.note_path(session_id)
                if note_path.exists():
                    session_note_path = str(note_path)
                break

        async def _safe_extract():
            try:
                extracted = await self.runtime.maybe_extract_skills(
                    session_id, all_messages, session_note_path=session_note_path
                )
                if extracted:
                    logger.info(f"Auto-extracted {len(extracted)} skill(s): {', '.join(extracted)}")
            except Exception as e:
                logger.warning(f"Skill extraction failed: {e}")

        task = asyncio.create_task(_safe_extract())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)



# ── Singleton runtime ──

_learning_runtime = None


def _create_learning_plugin(config: dict, settings) -> LearningPlugin | None:
    """Factory function for plugin registry."""
    global _learning_runtime
    if _learning_runtime is None:
        from pantheon.internal.memory_system.config import resolve_pantheon_dir
        from .config import get_learning_system_config

        _learning_runtime = LearningRuntime(get_learning_system_config(settings))
        _learning_runtime.initialize(resolve_pantheon_dir(settings))
    return LearningPlugin(_learning_runtime)


# Register with plugin registry
from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(PluginDef(
    name="learning_system",
    config_key="learning_system",
    enabled_key="enabled",
    factory=_create_learning_plugin,
    priority=60,
))
