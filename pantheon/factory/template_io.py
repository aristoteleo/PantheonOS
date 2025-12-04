"""
Unified Template I/O for Pantheon

Combines markdown parsing and file-based template management:
- UnifiedMarkdownParser: Parse/generate markdown with YAML frontmatter
- FileBasedTemplateManager: File CRUD operations for agents and teams

Template Storage:
- User templates: pwd/agents/, pwd/teams/
- System templates: pantheon/factory/templates/
- Priority: User templates > System templates
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

try:
    import frontmatter
except ImportError:
    raise ImportError(
        "python-frontmatter is required. Install with: pip install python-frontmatter"
    )

from ..utils.log import logger
from .models import AgentConfig, TeamConfig


def _is_path_reference(entry: str) -> bool:
    """
    Check if an agent entry is a path reference.

    Path references contain '/' or end with '.md'.
    Examples:
        - './custom/agent.md' -> True
        - '../shared/agent.md' -> True
        - '/absolute/path/agent.md' -> True
        - 'python_dev' -> False (ID reference)
    """
    return '/' in entry or entry.endswith('.md')


class UnifiedMarkdownParser:
    """Unified Markdown parser for agents and teams"""

    def parse_file(self, path: Path) -> Union[AgentConfig, TeamConfig]:
        """Parse a markdown file, auto-detecting whether it's an agent or team."""
        if not path.exists():
            raise IOError(f"File not found: {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise IOError(f"Failed to read file {path}: {exc}") from exc

        try:
            post = frontmatter.loads(content)
        except Exception as exc:
            raise ValueError(
                f"Failed to parse markdown frontmatter: {exc}"
            ) from exc

        entry_type = str(post.metadata.get("type", "")).lower()
        if entry_type in ("chatroom", "team"):
            return self.parse_team(post)
        return self.parse_agent(post)

    def parse_agent(self, content: Union[str, Any]) -> AgentConfig:
        """Parse a single agent markdown string or already-loaded post."""
        post = self._ensure_post(content)
        metadata = dict(post.metadata)

        agent_id = str(metadata.get("id", "")).strip()
        if not agent_id:
            raise ValueError("Agent must have 'id' in frontmatter")

        return AgentConfig(
            id=agent_id,
            name=str(metadata.get("name", "")),
            model=str(metadata.get("model", "")),
            icon=str(metadata.get("icon", "🤖")),
            instructions=(post.content or "").strip(),
            toolsets=list(metadata.get("toolsets", []) or []),
            mcp_servers=list(metadata.get("mcp_servers", []) or []),
            tags=list(metadata.get("tags", []) or []),
        )

    def parse_team(self, content: Union[str, Any]) -> TeamConfig:
        """
        Parse a team markdown string or already-loaded post.

        Supports three types of agent entries in the 'agents' list:
        1. Inline definition: agent_id with corresponding metadata block in frontmatter
        2. ID reference: agent_id without metadata (to be resolved from agents library)
        3. Path reference: path containing '/' or ending with '.md' (to be resolved from file)

        For ID and path references, a placeholder AgentConfig is created with only
        the 'id' field populated. The 'model' field will be empty, indicating the
        agent needs to be resolved later by FileBasedTemplateManager.
        """
        post = self._ensure_post(content)
        metadata = dict(post.metadata)

        team_id = str(metadata.get("id", "")).strip()
        if not team_id:
            raise ValueError("Team must have 'id' in frontmatter")

        raw_agent_entries = metadata.get("agents", [])
        if raw_agent_entries in (None, ""):
            agent_entries: List[str] = []
        elif isinstance(raw_agent_entries, list):
            agent_entries = [str(e) for e in raw_agent_entries]
        else:
            raise ValueError("'agents' must be a list of agent IDs or paths")

        # Classify each agent entry
        inline_entries: List[tuple[str, Dict[str, Any]]] = []  # (agent_id, metadata)
        reference_entries: List[str] = []  # agent_id or path (to be resolved later)

        for entry in agent_entries:
            agent_meta = metadata.get(entry)
            if isinstance(agent_meta, dict):
                # Inline definition: has metadata block
                inline_entries.append((entry, dict(agent_meta)))
                metadata.pop(entry, None)
            else:
                # Reference (ID or path): no metadata block
                reference_entries.append(entry)

        # Parse body text for description and inline agent instructions
        body_text = post.content or ""
        description_text = str(metadata.get("description", "")).strip()
        instruction_sections: List[str] = []

        inline_count = len(inline_entries)

        if inline_count > 0:
            instruction_sections = self._split_instruction_sections(body_text)

            if instruction_sections:
                # If sections = inline_count + 1, first section is description
                if len(instruction_sections) == inline_count + 1:
                    description_text = instruction_sections[0]
                    instruction_sections = instruction_sections[1:]
                elif len(instruction_sections) != inline_count:
                    raise ValueError(
                        f"Agent instructions count ({len(instruction_sections)}) "
                        f"does not match inline agents count ({inline_count})"
                    )

            if not instruction_sections:
                instruction_sections = ["" for _ in range(inline_count)]
        elif not inline_count and body_text.strip():
            # No inline agents, body is description
            description_text = body_text.strip()

        # Build agents list preserving original order
        agents: List[AgentConfig] = []
        inline_idx = 0

        for entry in agent_entries:
            # Check if this entry is inline
            inline_meta = None
            for ie_id, ie_meta in inline_entries:
                if ie_id == entry:
                    inline_meta = ie_meta
                    break

            if inline_meta is not None:
                # Inline definition
                agent_metadata = dict(inline_meta)
                agent_metadata.setdefault("id", entry)

                instructions = ""
                if inline_idx < len(instruction_sections):
                    instructions = instruction_sections[inline_idx].strip()
                inline_idx += 1

                agents.append(
                    AgentConfig(
                        id=str(agent_metadata.get("id", entry)),
                        name=str(agent_metadata.get("name", "")),
                        model=str(agent_metadata.get("model", "")),
                        icon=str(agent_metadata.get("icon", "🤖")),
                        instructions=instructions,
                        toolsets=list(agent_metadata.get("toolsets", []) or []),
                        mcp_servers=list(agent_metadata.get("mcp_servers", []) or []),
                        tags=list(agent_metadata.get("tags", []) or []),
                    )
                )
            else:
                # Reference (ID or path): create placeholder
                # Store the original entry string in 'id' field
                # Empty 'model' indicates this needs resolution
                agents.append(
                    AgentConfig(
                        id=entry,  # Could be agent_id or path
                        name="",
                        model="",  # Empty model = unresolved reference
                    )
                )

        return TeamConfig(
            id=team_id,
            name=str(metadata.get("name", "")),
            description=description_text,
            icon=str(metadata.get("icon", "💬")),
            category=str(metadata.get("category", "general")),
            version=str(metadata.get("version", "1.0.0")),
            agents=agents,
            tags=list(metadata.get("tags", []) or []),
        )

    def _split_instruction_sections(self, text: str) -> List[str]:
        """
        Split agent instructions using lines that contain only `---`.
        Returns the sections in order without dropping empty entries so we can
        validate alignment with the `agents` list.
        """
        if not text or not text.strip():
            return []

        normalized = text.strip()
        sections = re.split(r"\n\s*---\s*\n", normalized)
        return [section.strip() for section in sections]

    def generate_agent(self, agent: AgentConfig) -> str:
        """Generate agent markdown from AgentConfig."""
        import yaml

        metadata: Dict[str, Any] = {
            "id": agent.id,
            "name": agent.name,
            "model": agent.model,
            "icon": agent.icon,
        }

        if agent.toolsets:
            metadata["toolsets"] = agent.toolsets
        if agent.mcp_servers:
            metadata["mcp_servers"] = agent.mcp_servers
        if agent.tags:
            metadata["tags"] = agent.tags

        fm_text = yaml.dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

        body = (agent.instructions or "").strip()
        if body:
            return f"---\n{fm_text}---\n\n{body}"
        return f"---\n{fm_text}---\n"

    def generate_team(self, team: TeamConfig) -> str:
        """
        Generate team markdown from TeamConfig.

        All metadata (team + agents) is emitted within the first
        frontmatter block. Inline agent instructions are rendered in the body
        in the same order as the `agents` list, separated by `---` lines.
        """
        import yaml

        metadata: Dict[str, Any] = {
            "id": team.id,
            "name": team.name,
            "type": "team",
            "description": team.description,
            "icon": team.icon,
            "category": team.category,
            "version": team.version,
        }

        if team.tags:
            metadata["tags"] = team.tags

        if team.agents:
            metadata["agents"] = [agent.id for agent in team.agents]
            for agent in team.agents:
                agent_meta: Dict[str, Any] = {
                    "id": agent.id,
                    "name": agent.name,
                    "model": agent.model,
                    "icon": agent.icon,
                }
                if agent.toolsets:
                    agent_meta["toolsets"] = agent.toolsets
                if agent.mcp_servers:
                    agent_meta["mcp_servers"] = agent.mcp_servers
                if agent.tags:
                    agent_meta["tags"] = agent.tags
                metadata[agent.id] = agent_meta

        fm_text = yaml.dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

        body_sections: List[str] = []

        if not team.agents and team.description.strip():
            body_sections.append(team.description.strip())

        for agent in team.agents:
            if agent.instructions.strip():
                body_sections.append(agent.instructions.strip())

        body_text = "\n\n---\n\n".join(
            section for section in body_sections if section
        )

        if body_text:
            return f"---\n{fm_text}---\n\n{body_text}\n"
        return f"---\n{fm_text}---\n"

    def _ensure_post(self, content: Union[str, Any]):
        """Return a frontmatter.Post regardless of input type."""
        if hasattr(content, "metadata") and hasattr(content, "content"):
            return content
        try:
            return frontmatter.loads(content)
        except Exception as exc:
            raise ValueError(f"Failed to parse markdown: {exc}") from exc


# ===== FILE-BASED TEMPLATE MANAGER =====


class FileBasedTemplateManager:
    """Manager for file-based templates"""

    def __init__(self, work_dir: Optional[Path] = None):
        """
        Initialize template manager.

        Args:
            work_dir: Working directory for user templates. Defaults to cwd.
        """
        self.work_dir = work_dir or Path.cwd()
        self.agents_dir = self.work_dir / ".pantheon" / "agents"
        self.teams_dir = self.work_dir / ".pantheon" / "teams"

        # System templates location (in package)
        self.system_templates_dir = Path(__file__).parent / "templates"

        # Parser instance
        self.parser = UnifiedMarkdownParser()

        # Ensure directories exist
        self._ensure_directories()

    def _ensure_directories(self):
        """Ensure user template directories exist"""
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.teams_dir.mkdir(parents=True, exist_ok=True)

    # ===== Agent Operations =====

    def create_agent(self, agent: AgentConfig) -> Path:
        """
        Create a new agent file.

        Args:
            agent: AgentConfig to create

        Returns:
            Path to created file

        Raises:
            FileExistsError: If agent already exists
        """
        path = self.agents_dir / f"{agent.id}.md"

        if path.exists():
            raise FileExistsError(f"Agent {agent.id} already exists")

        self._write_agent_file(agent, path, overwrite=False)
        logger.info(f"Created agent: {agent.id}")
        return path

    def read_agent(self, agent_id: str) -> AgentConfig:
        """
        Read an agent file.

        Searches in user templates first, then system templates.

        Args:
            agent_id: Agent ID

        Returns:
            AgentConfig

        Raises:
            FileNotFoundError: If agent not found
            ValueError: If parsing fails
        """
        path = self._resolve_template_path("agents", agent_id)
        if path:
            return self._read_agent_from_path(path)

        raise FileNotFoundError(f"Agent {agent_id} not found")

    def update_agent(self, agent_id: str, agent: AgentConfig) -> Path:
        """
        Update an existing agent file.

        Args:
            agent_id: Existing agent ID
            agent: Updated AgentConfig

        Returns:
            Path to updated file

        Raises:
            FileNotFoundError: If agent not found
        """
        path = self.agents_dir / f"{agent_id}.md"

        if not path.exists():
            raise FileNotFoundError(f"Agent {agent_id} not found in user directory")

        self._write_agent_file(agent, path, overwrite=True)
        logger.info(f"Updated agent: {agent_id}")
        return path

    def delete_agent(self, agent_id: str):
        """
        Delete an agent file.

        Args:
            agent_id: Agent ID to delete

        Raises:
            FileNotFoundError: If agent not found
            ValueError: If agent is referenced by teams
        """
        path = self.agents_dir / f"{agent_id}.md"

        if not path.exists():
            raise FileNotFoundError(f"Agent {agent_id} not found")

        # Check if referenced by any teams
        if self._is_agent_referenced(agent_id):
            raise ValueError(f"Agent {agent_id} is referenced by teams")

        path.unlink()
        logger.info(f"Deleted agent: {agent_id}")

    def list_agents(self) -> List[AgentConfig]:
        """
        List all agents (user + system).

        Returns:
            List of AgentConfig
        """
        return self._list_templates("agents")

    def _read_agent_from_path(self, path: Path) -> AgentConfig:
        """Parse an agent markdown file."""
        parsed = self.parser.parse_file(path)

        if not isinstance(parsed, AgentConfig):
            raise ValueError(f"File {path} is not an agent")

        return parsed

    # ===== Team Operations =====

    def create_team(self, template: TeamConfig) -> Path:
        """
        Create a new team file.

        Args:
            template: TeamConfig to create

        Returns:
            Path to created file

        Raises:
            FileExistsError: If team already exists
        """
        path = self.teams_dir / f"{template.id}.md"

        if path.exists():
            raise FileExistsError(f"Team {template.id} already exists")

        self._write_team_file(template, path, overwrite=False)
        logger.info(f"Created team: {template.id}")
        return path

    def read_team(self, team_id: str, resolve_refs: bool = True) -> TeamConfig:
        """
        Read a team file.

        Searches in user templates first, then system templates.

        Args:
            team_id: Team ID
            resolve_refs: If True, resolve agent references (ID and path).
                         If False, return TeamConfig with unresolved placeholders.

        Returns:
            TeamConfig object

        Raises:
            FileNotFoundError: If team not found
            ValueError: If parsing fails
        """
        path = self._resolve_template_path("teams", team_id)
        if not path:
            raise FileNotFoundError(f"Team {team_id} not found")

        team = self._read_team_from_path(path)

        if resolve_refs:
            team = self._resolve_agent_references(team, base_path=path.parent)

        return team

    def update_team(self, team_id: str, template: TeamConfig):
        """
        Update an existing team file.

        Args:
            team_id: Existing team ID
            template: Updated TeamConfig

        Raises:
            FileNotFoundError: If team not found
        """
        path = self.teams_dir / f"{team_id}.md"

        if not path.exists():
            raise FileNotFoundError(
                f"Team {team_id} not found in user directory"
            )

        self._write_team_file(template, path, overwrite=True)
        logger.info(f"Updated team: {team_id}")

    def delete_team(self, team_id: str):
        """
        Delete a team file.

        Args:
            team_id: Team ID to delete

        Raises:
            FileNotFoundError: If team not found
        """
        path = self.teams_dir / f"{team_id}.md"

        if not path.exists():
            raise FileNotFoundError(f"Team {team_id} not found")

        path.unlink()
        logger.info(f"Deleted team: {team_id}")

    def list_teams(self, resolve_refs: bool = True) -> List[TeamConfig]:
        """
        List all teams (user + system).

        Args:
            resolve_refs: If True, resolve agent references in each team.

        Returns:
            List of TeamConfig
        """
        return self._list_templates("teams", resolve_refs=resolve_refs)

    def _read_team_from_path(self, path: Path) -> TeamConfig:
        """Parse a team markdown file."""
        parsed = self.parser.parse_file(path)

        if not isinstance(parsed, TeamConfig):
            raise ValueError(f"File {path} is not a team")

        return parsed

    def _resolve_agent_references(
        self, team: TeamConfig, base_path: Path
    ) -> TeamConfig:
        """
        Resolve agent references in a TeamConfig.

        Unresolved agents have empty 'model' field. The 'id' field contains
        either an agent ID (for library lookup) or a path (for file lookup).

        Args:
            team: TeamConfig with potentially unresolved agent references
            base_path: Base path for resolving relative paths (team file's directory)

        Returns:
            TeamConfig with all agent references resolved
        """
        resolved_agents: List[AgentConfig] = []

        for agent in team.agents:
            if agent.model:
                # Already resolved (inline definition)
                resolved_agents.append(agent)
            elif _is_path_reference(agent.id):
                # Path reference: load from file
                resolved = self._load_agent_from_path(agent.id, base_path)
                resolved_agents.append(resolved)
            else:
                # ID reference: load from agents library
                resolved = self.read_agent(agent.id)
                resolved_agents.append(resolved)

        # Create new TeamConfig with resolved agents
        return TeamConfig(
            id=team.id,
            name=team.name,
            description=team.description,
            icon=team.icon,
            category=team.category,
            version=team.version,
            agents=resolved_agents,
            tags=team.tags,
        )

    def _load_agent_from_path(self, ref_path: str, base_path: Path) -> AgentConfig:
        """
        Load an agent from a file path.

        Args:
            ref_path: Path to agent file (absolute or relative)
            base_path: Base path for resolving relative paths

        Returns:
            AgentConfig loaded from the file

        Raises:
            FileNotFoundError: If agent file not found
        """
        if ref_path.startswith('/'):
            # Absolute path
            full_path = Path(ref_path)
        else:
            # Relative path (relative to team file's directory)
            full_path = (base_path / ref_path).resolve()

        if not full_path.exists():
            raise FileNotFoundError(f"Agent file not found: {full_path}")

        return self._read_agent_from_path(full_path)

    # ===== Helper Methods =====

    def _is_agent_referenced(self, agent_id: str) -> bool:
        """Check if agent is referenced by any team"""
        for team in self.list_teams():
            if agent_id in team.all_agents:
                return True

        return False

    def _atomic_write(self, path: Path, content: str):
        """Atomic write to file (temp file → rename)"""
        try:
            temp_path = path.with_suffix(".md.tmp")
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
        except Exception as e:
            logger.error(f"Failed to write file {path}: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def _resolve_template_path(self, kind: str, template_id: str) -> Optional[Path]:
        """Resolve template path for user override (user > system)."""
        if kind == "agents":
            user_path = self.agents_dir / f"{template_id}.md"
            system_dir = self.system_templates_dir / "agents"
        elif kind == "teams":
            user_path = self.teams_dir / f"{template_id}.md"
            system_dir = self.system_templates_dir / "teams"
        else:
            raise ValueError(f"Unknown template kind: {kind}")

        if user_path.exists():
            return user_path

        system_path = system_dir / f"{template_id}.md"
        if system_path.exists():
            return system_path

        return None

    def _list_templates(
        self, kind: str, resolve_refs: bool = True
    ) -> List[Union[AgentConfig, TeamConfig]]:
        """List templates for a given kind with user override handling."""
        if kind == "agents":
            user_dir = self.agents_dir
            system_dir = self.system_templates_dir / "agents"
        elif kind == "teams":
            user_dir = self.teams_dir
            system_dir = self.system_templates_dir / "teams"
        else:
            raise ValueError(f"Unknown template kind: {kind}")

        items = []
        user_ids = set()

        for path in user_dir.glob("*.md"):
            try:
                if kind == "agents":
                    item = self._read_agent_from_path(path)
                else:
                    item = self._read_team_from_path(path)
                    if resolve_refs:
                        item = self._resolve_agent_references(item, path.parent)
            except Exception as exc:
                logger.error(f"Failed to parse {kind[:-1]} {path}: {exc}")
                continue
            items.append(item)
            user_ids.add(item.id)

        if system_dir.exists():
            for path in system_dir.glob("*.md"):
                try:
                    if kind == "agents":
                        item = self._read_agent_from_path(path)
                    else:
                        item = self._read_team_from_path(path)
                        if resolve_refs:
                            item = self._resolve_agent_references(item, path.parent)
                except Exception as exc:
                    logger.error(f"Failed to parse system {kind[:-1]} {path}: {exc}")
                    continue
                if item.id in user_ids:
                    continue
                items.append(item)

        return items

    def _write_agent_file(self, agent: AgentConfig, path: Path, *, overwrite: bool):
        """Serialize an AgentConfig to disk."""
        if path.exists() and not overwrite:
            raise FileExistsError(f"Agent {agent.id} already exists")

        content = self.parser.generate_agent(agent)
        self._atomic_write(path, content)

    def _write_team_file(
        self, template: TeamConfig, path: Path, *, overwrite: bool
    ):
        """Serialize a TeamConfig to disk."""
        if path.exists() and not overwrite:
            raise FileExistsError(f"Team {template.id} already exists")

        content = self.parser.generate_team(template)
        self._atomic_write(path, content)
