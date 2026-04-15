"""Skill index injector for agent system prompts."""

from __future__ import annotations

from pantheon.utils.log import logger

from .store import SkillStore


class SkillInjector:
    """Builds and caches skill index for injection into agent instructions."""

    def __init__(self, store: SkillStore, disabled_skills: list[str] | None = None):
        self.store = store
        self.disabled_skills = set(disabled_skills or [])
        self._cache: str | None = None

    def build_skill_index(self, agent_name: str | None = None) -> str:
        """Build skill index text listing available skills.

        Args:
            agent_name: If provided, filter by agent_scope.

        Returns:
            Formatted skill index string (may be empty if no skills).
        """
        if self._cache is not None and agent_name is None:
            return self._cache

        headers = self.store.scan_headers()

        # Filter disabled
        if self.disabled_skills:
            headers = [h for h in headers if h.name not in self.disabled_skills]

        # Filter by agent scope
        if agent_name:
            headers = [
                h for h in headers
                if h.agent_scope is None or agent_name in h.agent_scope
            ]

        if not headers:
            result = ""
        else:
            # Group by category (first path segment before /)
            categorized: dict[str, list] = {}
            for h in headers:
                parts = h.path.split("/")
                category = parts[0] if len(parts) > 1 else ""
                categorized.setdefault(category, []).append(h)

            lines: list[str] = []
            # Uncategorized first (flat skills)
            if "" in categorized:
                for h in categorized[""]:
                    lines.append(f"- {h.path}: {h.description}")
            # Then grouped categories
            for cat in sorted(k for k in categorized if k):
                lines.append(f"\n[{cat}]")
                for h in categorized[cat]:
                    lines.append(f"- {h.path}: {h.description}")
            result = "\n".join(lines).strip()

        # Only cache unfiltered results
        if agent_name is None:
            self._cache = result

        return result

    def invalidate_cache(self) -> None:
        """Clear cached index (call after skill changes)."""
        self._cache = None
