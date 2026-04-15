"""LearningRuntime — shared runtime for the learning system."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pantheon.utils.log import logger

from .config import resolve_skills_dir, resolve_skills_runtime_dir
from .extractor import SkillExtractor
from .injector import SkillInjector
from .prompts import SKILLS_GUIDANCE
from .store import SkillStore


class LearningRuntime:
    """Shared learning runtime, used by both ChatRoom and PantheonTeam.

    Singleton pattern — factory creates one, adapters share it.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.store: SkillStore | None = None
        self.injector: SkillInjector | None = None
        self.extractor: SkillExtractor | None = None
        self._initialized = False
        self._current_session_id: str = "default"

    def initialize(self, pantheon_dir: Path) -> None:
        """Initialize all components."""
        skills_dir = resolve_skills_dir(pantheon_dir)
        runtime_dir = resolve_skills_runtime_dir(pantheon_dir)

        self.store = SkillStore(skills_dir, runtime_dir)
        self.injector = SkillInjector(
            self.store,
            disabled_skills=self.config.get("disabled_skills"),
        )

        # Auto-extraction is disabled by default (extract_enabled: false)
        # Skills are typically created via skill_manage tool
        # Can be enabled for automatic extraction or future Dream integration
        if self.config.get("extract_enabled", False):
            self.extractor = SkillExtractor(
                self.store,
                model=self.config.get("extract_model", "gpt-4o-mini"),
                nudge_interval=self.config.get("extract_nudge_interval", 5),
            )
            logger.info(f"LearningRuntime initialized with auto-extraction: skills_dir={skills_dir}")
        else:
            logger.info(f"LearningRuntime initialized: skills_dir={skills_dir}")

        self._initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def build_skill_guidance(self, agent_name: str | None = None) -> str:
        """Build skill guidance text for injection into agent.instructions.

        Returns empty string if no skills exist.
        """
        if not self.injector:
            return ""

        skill_index = self.injector.build_skill_index(agent_name)
        if not skill_index:
            return ""

        return SKILLS_GUIDANCE.format(skill_index=skill_index)

    async def maybe_extract_skills(
        self, session_id: str, messages: list[dict[str, Any]],
        session_note_path: str = "",
    ) -> list[str] | None:
        """Called on_run_end: increment counter and possibly extract skills.

        Only active when extract_enabled=true in settings.
        """
        if not self.extractor:
            return None

        self.extractor.increment_run(session_id)
        result = await self.extractor.maybe_extract(session_id, messages, session_note_path=session_note_path)

        if result:
            if self.injector:
                self.injector.invalidate_cache()
            logger.info(f"Auto-extracted {len(result)} skill(s): {', '.join(result)}")

        return result

    def on_skill_tool_used(self, session_id: str | None = None) -> None:
        """Called when agent manually uses a skill tool.

        Resets extraction counter (if extractor enabled) and invalidates cache.
        """
        if self.injector:
            self.injector.invalidate_cache()

        if self.extractor:
            sid = session_id or self._current_session_id
            self.extractor.reset_counter(sid)
