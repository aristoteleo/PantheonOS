"""Package collector for publishing agents, teams, and skills to the Store."""

from pathlib import Path
from typing import Tuple, Dict, Optional

from loguru import logger
from pantheonos.settings import agent_package_templates


def _is_path_reference(entry: str) -> bool:
    """Check if an agent entry is a path reference (not an ID)."""
    return entry.startswith(("/", "./", "../")) or entry.endswith(".md")


class PackageCollector:
    """Collect agent/team/skill files for publishing to the Store.

    Resolves references and bundles all necessary files into
    (content, files) tuples suitable for the Store API.
    """

    def __init__(self, work_dir: Optional[Path] = None):
        from pantheonos.settings import get_settings
        self.settings = get_settings(work_dir)

    def collect(self, item_id: str, item_type: str) -> Tuple[str, Dict[str, str]]:
        """Collect a package by type.

        Args:
            item_id: The ID of the agent/team/skill.
            item_type: One of "agent", "team", "skill".

        Returns:
            (content, files) where content is the main .md content and
            files is a dict of relative_path -> file_content for bundled files.
        """
        if item_type == "agent":
            return self.collect_agent(item_id)
        elif item_type == "team":
            return self.collect_team(item_id)
        elif item_type == "skill":
            return self.collect_skill(item_id)
        else:
            raise ValueError(f"Unknown type: {item_type}. Must be agent, team, or skill.")

    def collect_agent(self, agent_id: str) -> Tuple[str, Dict[str, str]]:
        """Collect an agent file for publishing.

        Returns:
            (content, {}) — agents are self-contained, no extra files.
        """
        path = self._find_file("agents", agent_id)
        content = path.read_text(encoding="utf-8")
        logger.info(f"Collected agent: {agent_id} from {path}")
        return content, {}

    def collect_team(self, team_id: str) -> Tuple[str, Dict[str, str]]:
        """Collect a team file and its referenced agent files.

        Inline agents (defined within the team frontmatter) are self-contained.
        ID-referenced agents are bundled into the files dict.
        Path-referenced agents are resolved and bundled.
        Prompt references ({{name}}) are NOT bundled.

        Returns:
            (content, files) where files maps "agents/{id}.md" -> agent content.
        """
        import frontmatter

        path = self._find_file("teams", team_id)
        content = path.read_text(encoding="utf-8")

        # Parse to find agent references
        post = frontmatter.loads(content)
        agent_entries = post.metadata.get("agents", []) or []
        files: Dict[str, str] = {}

        for entry in agent_entries:
            entry = str(entry)
            # Check if this agent has an inline definition block in frontmatter
            agent_meta = post.metadata.get(entry)
            if isinstance(agent_meta, dict):
                # Inline agent — already in the team file, skip
                continue

            if _is_path_reference(entry):
                # Path reference — resolve and bundle
                agent_path = self._resolve_path_ref(entry, path.parent)
                if agent_path and agent_path.exists():
                    rel_key = f"agents/{agent_path.stem}.md"
                    files[rel_key] = agent_path.read_text(encoding="utf-8")
                    logger.info(f"Bundled path-referenced agent: {entry} -> {rel_key}")
                else:
                    logger.warning(f"Path-referenced agent not found: {entry}")
            else:
                # ID reference — find in agents library
                try:
                    agent_path = self._find_file("agents", entry)
                    rel_key = f"agents/{entry}.md"
                    files[rel_key] = agent_path.read_text(encoding="utf-8")
                    logger.info(f"Bundled ID-referenced agent: {entry}")
                except FileNotFoundError:
                    logger.warning(f"Referenced agent not found: {entry} (skipping)")

        logger.info(f"Collected team: {team_id} with {len(files)} bundled agent(s)")
        return content, files

    def collect_skill(self, skill_id: str) -> Tuple[str, Dict[str, str]]:
        """Collect a skill file for publishing.

        Returns:
            (content, {}) — skills are self-contained.
        """
        path = self._find_file("skills", skill_id)
        content = path.read_text(encoding="utf-8")
        logger.info(f"Collected skill: {skill_id} from {path}")
        return content, {}

    def _find_file(self, kind: str, item_id: str) -> Path:
        """Find a template file by kind and ID.

        Searches user directory first, then system templates.
        Supports namespaced IDs (e.g., "single_cell/leader").
        """
        if kind == "agents":
            user_dir = self.settings.agents_dir
        elif kind == "teams":
            user_dir = self.settings.teams_dir
        elif kind == "skills":
            user_dir = self.settings.skills_dir
        else:
            raise ValueError(f"Unknown kind: {kind}")

        system_dir = agent_package_templates() / kind

        # Try user directory first
        user_path = user_dir / f"{item_id}.md"
        if user_path.exists():
            return user_path

        # Try system templates
        system_path = system_dir / f"{item_id}.md"
        if system_path.exists():
            return system_path

        raise FileNotFoundError(
            f"{kind[:-1].capitalize()} '{item_id}' not found in "
            f"{user_dir} or {system_dir}"
        )

    def _resolve_path_ref(self, ref_path: str, base_path: Path) -> Optional[Path]:
        """Resolve a path reference relative to base_path."""
        if ref_path.startswith("/"):
            full_path = Path(ref_path)
        else:
            full_path = (base_path / ref_path).resolve()
        return full_path if full_path.exists() else None
