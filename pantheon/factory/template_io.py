"""
Unified Template I/O for Pantheon

Combines markdown parsing and file-based template management:
- UnifiedMarkdownParser: Parse/generate markdown with YAML frontmatter
- FileBasedTemplateManager: File CRUD operations for agents and chatrooms

Template Storage:
- User templates: pwd/agents/, pwd/chatrooms/
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
from .models import AgentConfig, ChatroomConfig, normalize_skills_value


class UnifiedMarkdownParser:
    """Unified Markdown parser for agents and chatrooms"""

    def parse_file(self, path: Path) -> Union[AgentConfig, ChatroomConfig]:
        """Parse a markdown file, auto-detecting whether it's an agent or chatroom."""
        if not path.exists():
            raise IOError(f"File not found: {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise IOError(f"Failed to read file {path}: {exc}") from exc

        try:
            post = frontmatter.loads(content)
        except Exception as exc:
            raise ValueError(f"Failed to parse markdown frontmatter: {exc}") from exc

        entry_type = str(post.metadata.get("type", "")).lower()
        if entry_type == "chatroom":
            return self.parse_chatroom(post)
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

    def parse_chatroom(self, content: Union[str, Any]) -> ChatroomConfig:
        """Parse a chatroom markdown string or already-loaded post."""
        post = self._ensure_post(content)
        metadata = dict(post.metadata)

        chatroom_id = str(metadata.get("id", "")).strip()
        if not chatroom_id:
            raise ValueError("Chatroom must have 'id' in frontmatter")

        raw_agent_ids = metadata.get("agents", [])
        if raw_agent_ids in (None, ""):
            agent_ids: List[str] = []
        elif isinstance(raw_agent_ids, list):
            agent_ids = raw_agent_ids
        else:
            raise ValueError("'agents' must be a list of agent IDs")

        agent_entries: List[tuple[str, Dict[str, Any]]] = []
        for agent_id in agent_ids:
            agent_meta = metadata.get(agent_id)
            if not isinstance(agent_meta, dict):
                raise ValueError(f"Agent '{agent_id}' metadata must be a mapping")
            agent_entries.append((agent_id, dict(agent_meta)))
            metadata.pop(agent_id, None)

        body_text = post.content or ""
        description_text = str(metadata.get("description", "")).strip()
        instruction_sections: List[str] = []

        if agent_ids:
            instruction_sections = self._split_instruction_sections(body_text)

            if instruction_sections:
                if len(instruction_sections) == len(agent_ids) + 1:
                    description_text = instruction_sections[0]
                    instruction_sections = instruction_sections[1:]
                elif len(instruction_sections) != len(agent_ids):
                    raise ValueError(
                        "Agent instructions count "
                        f"({len(instruction_sections)}) does not match declared agents "
                        f"({len(agent_ids)})"
                    )

            if not instruction_sections:
                instruction_sections = ["" for _ in agent_ids]
        else:
            stripped = body_text.strip()
            if stripped:
                description_text = stripped

        agents: List[AgentConfig] = []
        for idx, (agent_id, agent_meta) in enumerate(agent_entries):
            agent_metadata = dict(agent_meta)
            agent_metadata.setdefault("id", agent_id)

            instructions = ""
            if idx < len(instruction_sections):
                instructions = instruction_sections[idx].strip()

            agents.append(
                AgentConfig(
                    id=str(agent_metadata.get("id", agent_id)),
                    name=str(agent_metadata.get("name", "")),
                    model=str(agent_metadata.get("model", "")),
                    icon=str(agent_metadata.get("icon", "🤖")),
                    instructions=instructions,
                    toolsets=list(agent_metadata.get("toolsets", []) or []),
                    mcp_servers=list(agent_metadata.get("mcp_servers", []) or []),
                    tags=list(agent_metadata.get("tags", []) or []),
                )
            )

        return ChatroomConfig(
            id=chatroom_id,
            name=str(metadata.get("name", "")),
            description=description_text,
            icon=str(metadata.get("icon", "💬")),
            category=str(metadata.get("category", "general")),
            version=str(metadata.get("version", "1.0.0")),
            agents=agents,
            sub_agents=list(metadata.get("sub_agents", []) or []),
            tags=list(metadata.get("tags", []) or []),
            skills=normalize_skills_value(metadata.get("skills", "none")),
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

    def generate_chatroom(self, chatroom: ChatroomConfig) -> str:
        """
        Generate chatroom markdown from ChatroomConfig.

        All metadata (chatroom + agents) is emitted within the first
        frontmatter block. Inline agent instructions are rendered in the body
        in the same order as the `agents` list, separated by `---` lines.
        """
        import yaml

        metadata: Dict[str, Any] = {
            "id": chatroom.id,
            "name": chatroom.name,
            "type": "chatroom",
            "description": chatroom.description,
            "icon": chatroom.icon,
            "category": chatroom.category,
            "version": chatroom.version,
        }

        normalized_skills = normalize_skills_value(chatroom.skills)
        if normalized_skills != "none":
            metadata["skills"] = normalized_skills

        if chatroom.sub_agents:
            metadata["sub_agents"] = chatroom.sub_agents
        if chatroom.tags:
            metadata["tags"] = chatroom.tags

        if chatroom.agents:
            metadata["agents"] = [agent.id for agent in chatroom.agents]
            for agent in chatroom.agents:
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

        if not chatroom.agents and chatroom.description.strip():
            body_sections.append(chatroom.description.strip())

        for agent in chatroom.agents:
            if agent.instructions.strip():
                body_sections.append(agent.instructions.strip())

        body_text = "\n\n---\n\n".join(section for section in body_sections if section)

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
        self.chatrooms_dir = self.work_dir / ".pantheon" / "chatrooms"
        self.skills_dir = self.work_dir / ".pantheon" / "skills"

        # System templates location (in package)
        self.system_templates_dir = Path(__file__).parent / "templates"

        # Parser instance
        self.parser = UnifiedMarkdownParser()

        # Ensure directories exist
        self._ensure_directories()

    def _ensure_directories(self):
        """Ensure user template directories exist"""
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.chatrooms_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

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
            ValueError: If agent is referenced by chatrooms
        """
        path = self.agents_dir / f"{agent_id}.md"

        if not path.exists():
            raise FileNotFoundError(f"Agent {agent_id} not found")

        # Check if referenced by any chatrooms
        if self._is_agent_referenced(agent_id):
            raise ValueError(f"Agent {agent_id} is referenced by chatrooms")

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

    # ===== Chatroom Operations =====

    def create_chatroom(self, template: ChatroomConfig) -> Path:
        """
        Create a new chatroom file.

        Args:
            template: ChatroomConfig to create

        Returns:
            Path to created file

        Raises:
            FileExistsError: If chatroom already exists
        """
        path = self.chatrooms_dir / f"{template.id}.md"

        if path.exists():
            raise FileExistsError(f"Chatroom {template.id} already exists")

        self._write_chatroom_file(template, path, overwrite=False)
        logger.info(f"Created chatroom: {template.id}")
        return path

    def read_chatroom(self, chatroom_id: str) -> ChatroomConfig:
        """
        Read a chatroom file.

        Searches in user templates first, then system templates.

        Args:
            chatroom_id: Chatroom ID

        Returns:
            ChatroomConfig object

        Raises:
            FileNotFoundError: If chatroom not found
            ValueError: If parsing fails
        """
        path = self._resolve_template_path("chatrooms", chatroom_id)
        if path:
            return self._read_chatroom_from_path(path)

        raise FileNotFoundError(f"Chatroom {chatroom_id} not found")

    def update_chatroom(self, chatroom_id: str, template: ChatroomConfig):
        """
        Update an existing chatroom file.

        Args:
            chatroom_id: Existing chatroom ID
            template: Updated ChatroomConfig

        Raises:
            FileNotFoundError: If chatroom not found
        """
        path = self.chatrooms_dir / f"{chatroom_id}.md"

        if not path.exists():
            raise FileNotFoundError(
                f"Chatroom {chatroom_id} not found in user directory"
            )

        self._write_chatroom_file(template, path, overwrite=True)
        logger.info(f"Updated chatroom: {chatroom_id}")

    def delete_chatroom(self, chatroom_id: str):
        """
        Delete a chatroom file.

        Args:
            chatroom_id: Chatroom ID to delete

        Raises:
            FileNotFoundError: If chatroom not found
        """
        path = self.chatrooms_dir / f"{chatroom_id}.md"

        if not path.exists():
            raise FileNotFoundError(f"Chatroom {chatroom_id} not found")

        path.unlink()
        logger.info(f"Deleted chatroom: {chatroom_id}")

    def list_chatrooms(self) -> List[ChatroomConfig]:
        """
        List all chatrooms (user + system).

        Returns:
            List of ChatroomConfig
        """
        return self._list_templates("chatrooms")

    def _read_chatroom_from_path(self, path: Path) -> ChatroomConfig:
        """Parse a chatroom markdown file."""
        parsed = self.parser.parse_file(path)

        if not isinstance(parsed, ChatroomConfig):
            raise ValueError(f"File {path} is not a chatroom")

        return parsed

    # ===== Helper Methods =====

    def _is_agent_referenced(self, agent_id: str) -> bool:
        """Check if agent is referenced by any chatroom"""
        for chatroom in self.list_chatrooms():
            if agent_id in (chatroom.sub_agents or []):
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
        elif kind == "chatrooms":
            user_path = self.chatrooms_dir / f"{template_id}.md"
            system_dir = self.system_templates_dir / "chatrooms"
        else:
            raise ValueError(f"Unknown template kind: {kind}")

        if user_path.exists():
            return user_path

        system_path = system_dir / f"{template_id}.md"
        if system_path.exists():
            return system_path

        return None

    def _list_templates(self, kind: str):
        """List templates for a given kind with user override handling."""
        if kind == "agents":
            user_dir = self.agents_dir
            system_dir = self.system_templates_dir / "agents"
            reader = self._read_agent_from_path
        elif kind == "chatrooms":
            user_dir = self.chatrooms_dir
            system_dir = self.system_templates_dir / "chatrooms"
            reader = self._read_chatroom_from_path
        else:
            raise ValueError(f"Unknown template kind: {kind}")

        items = []
        user_ids = set()

        for path in user_dir.glob("*.md"):
            try:
                item = reader(path)
            except Exception as exc:
                logger.error(f"Failed to parse {kind[:-1]} {path}: {exc}")
                continue
            items.append(item)
            user_ids.add(item.id)

        if system_dir.exists():
            for path in system_dir.glob("*.md"):
                try:
                    item = reader(path)
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

    def _write_chatroom_file(
        self, template: ChatroomConfig, path: Path, *, overwrite: bool
    ):
        """Serialize a ChatroomConfig to disk."""
        if path.exists() and not overwrite:
            raise FileExistsError(f"Chatroom {template.id} already exists")

        content = self.parser.generate_chatroom(template)
        self._atomic_write(path, content)
