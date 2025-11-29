"""
Template Manager for Pantheon

Provides interface for template discovery, loading, file operations, and bootstrap.
- Template discovery and loading
- File-based template operations (CRUD)
- Bootstrap initialization on startup
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dataclasses import dataclass

from ..utils.log import logger
from .template_io import FileBasedTemplateManager
from .models import AgentConfig, ChatroomConfig, normalize_skills_value


@dataclass
class SubAgentSelection:
    explicit_ids: list[str]
    include_all: bool


def _normalize_sub_agent_spec(sub_agents_value) -> SubAgentSelection:
    """Normalize the sub_agents specification and detect "all" wildcard usage."""
    if not sub_agents_value:
        return SubAgentSelection([], False)

    entries = (
        [sub_agents_value]
        if isinstance(sub_agents_value, str)
        else list(sub_agents_value)
    )

    explicit_ids: list[str] = []
    seen_ids: set[str] = set()
    include_all = False

    for entry in entries:
        if entry is None:
            continue

        normalized = str(entry).strip()
        if not normalized:
            continue

        if normalized.lower() == "all":
            include_all = True
            continue

        if normalized not in seen_ids:
            explicit_ids.append(normalized)
            seen_ids.add(normalized)

    return SubAgentSelection(explicit_ids, include_all)


class TemplateManager:
    """Template manager for discovery, loading, file operations, and bootstrap"""

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
        self.system_templates_dir = Path(__file__).parent / "templates"

        self.file_manager = FileBasedTemplateManager(work_dir)

        # Auto-bootstrap template system on initialization
        self.bootstrap()

    # ===== Bootstrap =====

    def bootstrap(self):
        """
        Bootstrap the template system.

        Creates necessary user directories and copies system templates on first run.
        """
        logger.info("Bootstrapping template system...")

        # Ensure user directories exist
        self._ensure_directories()

        # Ensure packaged templates exist locally (copy missing ones)
        self._ensure_default_agents()
        self._ensure_default_chatrooms()
        self._ensure_default_skills()

        logger.info("Template system bootstrap complete")

    def _ensure_directories(self):
        """Ensure user template directories exist"""
        try:
            self.agents_dir.mkdir(parents=True, exist_ok=True)
            self.chatrooms_dir.mkdir(parents=True, exist_ok=True)
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured template directories exist at {self.work_dir}")
        except Exception as e:
            logger.error(f"Failed to create template directories: {e}")
            raise

    def _copy_missing_templates(self, src_dir: Path, dest_dir: Path, label: str):
        if not src_dir.exists():
            return 0
        copied = 0
        for item in src_dir.glob("*.md"):
            dest = dest_dir / item.name
            if dest.exists():
                continue
            shutil.copy(item, dest)
            copied += 1
        if copied:
            logger.info(f"Copied {copied} {label} from system templates")
        return copied

    def _ensure_default_agents(self):
        try:
            self._copy_missing_templates(
                self.system_templates_dir / "agents", self.agents_dir, "agent(s)"
            )
        except Exception as e:
            logger.error(f"Failed to copy default agents: {e}")

    def _ensure_default_chatrooms(self):
        try:
            self._copy_missing_templates(
                self.system_templates_dir / "chatrooms",
                self.chatrooms_dir,
                "chatroom(s)",
            )
        except Exception as e:
            logger.error(f"Failed to copy default chatrooms: {e}")

    def _ensure_default_skills(self):
        """Copy packaged skills into the user directory when missing."""
        system_skills_dir = self.system_templates_dir / "skills"
        if not system_skills_dir.exists():
            return

        try:
            copied = self._copy_missing_templates(
                system_skills_dir, self.skills_dir, "default skill(s)"
            )
            if copied == 0:
                logger.debug("No default skills needed to be copied")
        except Exception as e:
            logger.error(f"Failed to copy default skills: {e}")

    # ===== Helper Methods =====

    def dict_to_chatroom_config(self, template_dict: dict) -> ChatroomConfig:
        """Convert frontend template dict to ChatroomConfig object."""
        agents = [
            AgentConfig.from_dict(agent_data)
            for agent_data in template_dict.get("agents", [])
        ]

        sub_agent_spec = _normalize_sub_agent_spec(
            template_dict.get("sub_agents", [])
        )
        sub_agents_list = list(sub_agent_spec.explicit_ids)
        sub_agent_ids = set(sub_agents_list)

        if sub_agent_spec.include_all:
            try:
                available_agents = self.file_manager.list_agents()
            except Exception as exc:
                logger.error(f"Failed to expand 'all' sub_agents: {exc}")
            else:
                added = 0
                for agent in available_agents:
                    agent_id = getattr(agent, "id", None)
                    if agent_id and agent_id not in sub_agent_ids:
                        sub_agents_list.append(agent_id)
                        sub_agent_ids.add(agent_id)
                        added += 1
                logger.debug("Expanded 'all' sub_agents to include %d agent(s)", added)

        return ChatroomConfig(
            id=template_dict.get("id", ""),
            name=template_dict.get("name", ""),
            description=template_dict.get("description", ""),
            icon=template_dict.get("icon", "💬"),
            category=template_dict.get("category", "general"),
            version=template_dict.get("version", "1.0.0"),
            agents=agents,
            sub_agents=sub_agents_list,
            tags=template_dict.get("tags", []),
            skills=normalize_skills_value(template_dict.get("skills", "none")),
        )

    def prepare_team(
        self, chatroom_config: ChatroomConfig
    ) -> Tuple[dict, dict, set[str], set[str], list[str]]:
        """Resolve agents/sub_agents and required services for a chatroom."""

        sub_agent_ids = list(chatroom_config.sub_agents or [])
        agent_payloads: dict[str, dict] = {}
        sub_agent_payloads: dict[str, dict] = {}
        required_toolsets: set[str] = set()
        required_mcp_servers: set[str] = set()
        missing_sub_agents: list[str] = []

        agent_index = {agent.id: agent for agent in chatroom_config.agents}

        skills_spec = normalize_skills_value(chatroom_config.skills)

        def _should_enable_skills(agent_id: str | None) -> bool:
            allowed_tokens = {entry.strip().lower() for entry in skills_spec if entry}
            if not allowed_tokens:
                return False
            if "all" in allowed_tokens:
                return True
            if "none" in allowed_tokens:
                return False
            if not agent_id:
                return False
            return agent_id.strip().lower() in allowed_tokens

        def collect_requirements(agent_cfg: AgentConfig | None):
            if not agent_cfg:
                return
            required_toolsets.update(agent_cfg.toolsets or [])
            required_mcp_servers.update(agent_cfg.mcp_servers or [])

        def payload_for(agent_cfg: AgentConfig | None) -> dict | None:
            if not agent_cfg:
                return None
            collect_requirements(agent_cfg)
            return agent_cfg.to_creation_payload()

        for agent in agent_index.values():
            collect_requirements(agent)

        for agent_id, agent_cfg in agent_index.items():
            if agent_id in sub_agent_ids:
                continue
            payload = agent_cfg.to_creation_payload()
            payload["enable_skills"] = _should_enable_skills(agent_id)
            agent_payloads[agent_id] = payload

        for sub_agent_id in sub_agent_ids:
            agent_cfg = agent_index.get(sub_agent_id)
            if not agent_cfg:
                try:
                    agent_cfg = self.file_manager.read_agent(sub_agent_id)
                except Exception as exc:
                    logger.error(f"Failed to load sub-agent {sub_agent_id}: {exc}")
                    missing_sub_agents.append(sub_agent_id)
                    continue
            sub_payload = payload_for(agent_cfg)
            if sub_payload:
                sub_payload["enable_skills"] = _should_enable_skills(sub_agent_id)
                sub_agent_payloads[sub_agent_id] = sub_payload

        return (
            agent_payloads,
            sub_agent_payloads,
            required_toolsets,
            required_mcp_servers,
            missing_sub_agents,
        )

    def validate_template_dict(self, template: dict) -> dict:
        """Validate a raw chatroom template dict (ChatRoom uses this)."""

        try:
            chatroom_config = self.dict_to_chatroom_config(template)

            if not chatroom_config.id or not chatroom_config.name:
                return {
                    "success": False,
                    "message": "Template validation failed: id and name are required",
                    "validation_errors": ["id and name are required"],
                }

            (
                agent_payloads,
                sub_payloads,
                required_toolsets,
                required_mcp_servers,
                missing_sub_agents,
            ) = self.prepare_team(chatroom_config)

            return {
                "success": True,
                "compatible": len(missing_sub_agents) == 0,
                "required_toolsets": sorted(required_toolsets),
                "required_mcp_servers": sorted(required_mcp_servers),
                "missing_sub_agents": missing_sub_agents,
                "agents": agent_payloads,
                "sub_agents": sub_payloads,
                "template": chatroom_config.to_dict(),
            }
        except Exception as exc:
            logger.error(f"Error validating template compatibility: {exc}")
            return {"success": False, "message": str(exc)}

    # ===== Template Discovery & Loading =====

    def list_templates(self) -> List[ChatroomConfig]:
        """
        List all available chatroom templates (user + system).

        Returns:
            List of ChatroomConfig objects
        """
        try:
            return self.file_manager.list_chatrooms()
        except Exception as e:
            logger.error(f"Failed to list templates: {e}")
            return []

    def get_template(self, template_id: str) -> Optional[ChatroomConfig]:
        """
        Get a specific chatroom template by ID.

        Searches user templates first, then system templates.

        Args:
            template_id: Template ID

        Returns:
            ChatroomConfig if found, None otherwise
        """
        try:
            return self.file_manager.read_chatroom(template_id)
        except Exception as e:
            logger.error(f"Failed to get template {template_id}: {e}")
            return None

    # ===== File Operations (for frontend editing) =====

    def list_template_files(self, file_type: str = "chatrooms") -> Dict[str, Any]:
        """
        List available template files.

        Args:
            file_type: "chatrooms", "agents", or "all"

        Returns:
            Response dict with list of template files
        """
        try:
            if file_type not in {"chatrooms", "agents", "all"}:
                return {"success": False, "error": f"Unknown file_type: {file_type}"}

            chatroom_files = [
                {"id": tmpl.id, "name": tmpl.name, "path": f"chatrooms/{tmpl.id}.md"}
                for tmpl in self.list_templates()
            ] if file_type in {"chatrooms", "all"} else []

            agent_files = [
                {"id": agent.id, "name": agent.name, "path": f"agents/{agent.id}.md"}
                for agent in self.file_manager.list_agents()
            ] if file_type in {"agents", "all"} else []

            files = chatroom_files + agent_files

            if file_type == "chatrooms":
                files = chatroom_files
            elif file_type == "agents":
                files = agent_files

            return {
                "success": True,
                "file_type": file_type,
                "files": files,
                "total": len(files),
            }

        except Exception as e:
            logger.error(f"Error listing template files: {e}")
            return {"success": False, "error": str(e)}

    def read_template_file(self, file_path: str) -> Dict[str, Any]:
        """
        Read a template markdown file.

        Args:
            file_path: Path to template file (e.g., "chatrooms/default.md" or "agents/analyzer.md")

        Returns:
            Response dict with file content
        """
        try:
            file_type, template_id = self._parse_template_file_path(file_path)

            if file_type == "chatrooms":
                chatroom = self.get_template(template_id)
                if not chatroom:
                    return {
                        "success": False,
                        "error": f"Template '{template_id}' not found",
                    }

                chatroom_dict = chatroom.to_dict()
                chatroom_dict["type"] = "chatroom"

                return {
                    "success": True,
                    "file_path": file_path,
                    "type": "chatroom",
                    "content": chatroom_dict,
                }

            agent = self.file_manager.read_agent(template_id)

            return {
                "success": True,
                "file_path": file_path,
                "type": "agent",
                "content": agent.to_dict(),
            }

        except (ValueError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error reading template file {file_path}: {e}")
            return {"success": False, "error": str(e)}

    def write_template_file(
        self, file_path: str, content: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Write/update a template markdown file.

        Args:
            file_path: Path to template file (e.g., "chatrooms/custom.md" or "agents/custom.md")
            content: Template content dict with all fields

        Returns:
            Response dict with operation results
        """
        try:
            file_type, template_id = self._parse_template_file_path(file_path)

            payload = dict(content)
            payload.setdefault("id", template_id)

            if file_type == "chatrooms":
                chatroom = self.dict_to_chatroom_config(payload)
                try:
                    self.file_manager.update_chatroom(template_id, chatroom)
                    operation = "update"
                except FileNotFoundError:
                    self.file_manager.create_chatroom(chatroom)
                    operation = "create"

                return {
                    "success": True,
                    "operation": operation,
                    "file_path": file_path,
                    "type": "chatroom",
                    "id": chatroom.id,
                }

            agent = AgentConfig.from_dict(payload)

            try:
                self.file_manager.update_agent(template_id, agent)
                operation = "update"
            except FileNotFoundError:
                self.file_manager.create_agent(agent)
                operation = "create"

            return {
                "success": True,
                "operation": operation,
                "file_path": file_path,
                "type": "agent",
                "id": agent.id,
            }

        except Exception as e:
            logger.error(f"Error writing template file {file_path}: {e}")
            return {"success": False, "error": str(e)}

    def delete_template_file(self, file_path: str) -> Dict[str, Any]:
        """
        Delete a template markdown file.

        Args:
            file_path: Path to template file (e.g., "chatrooms/custom.md" or "agents/custom.md")

        Returns:
            Response dict with operation results
        """
        try:
            file_type, template_id = self._parse_template_file_path(file_path)

            if file_type == "chatrooms":
                self.file_manager.delete_chatroom(template_id)
            else:
                self.file_manager.delete_agent(template_id)

            return {
                "success": True,
                "operation": "delete",
                "file_path": file_path,
                "type": "chatroom" if file_type == "chatrooms" else "agent",
            }

        except FileNotFoundError:
            return {
                "success": False,
                "error": f"Template file '{file_path}' not found",
            }
        except Exception as e:
            logger.error(f"Error deleting template file {file_path}: {e}")
            return {"success": False, "error": str(e)}

    def _parse_template_file_path(self, file_path: str) -> Tuple[str, str]:
        """Validate and split template file path (type/id.md)."""
        parts = file_path.split("/")
        if len(parts) != 2:
            raise ValueError("Invalid file_path format. Expected 'type/id.md'")

        file_type, filename = parts
        if file_type not in {"chatrooms", "agents"}:
            raise ValueError(f"Unknown file type: {file_type}")

        if not filename.endswith(".md"):
            raise ValueError("Filename must end with '.md'")

        template_id = filename[:-3]
        if not template_id:
            raise ValueError("Template id is required in file_path")

        return file_type, template_id


# Global template manager instance
_template_manager: Optional[TemplateManager] = None


def get_template_manager(work_dir: Optional[Path] = None) -> TemplateManager:
    """
    Get or create the global template manager instance.

    Args:
        work_dir: Working directory for user templates. If provided, creates new instance.

    Returns:
        TemplateManager instance
    """
    global _template_manager

    if work_dir is not None:
        # Create new instance with custom work_dir
        return TemplateManager(work_dir)

    if _template_manager is None:
        _template_manager = TemplateManager()

    return _template_manager


__all__ = ["TemplateManager", "get_template_manager"]
