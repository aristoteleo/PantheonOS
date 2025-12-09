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

from ..utils.log import logger
from .template_io import FileBasedTemplateManager, resolve_prompts_for_team, init_prompt_resolver
from .models import AgentConfig, TeamConfig


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
        self.teams_dir = self.work_dir / ".pantheon" / "teams"
        self.prompts_dir = self.work_dir / ".pantheon" / "prompts"
        self.system_templates_dir = Path(__file__).parent / "templates"

        self.file_manager = FileBasedTemplateManager(work_dir)

        # Auto-bootstrap template system on initialization
        self.bootstrap()

        # Initialize prompt resolver with user prompts directory (higher priority)
        init_prompt_resolver(
            user_prompts_dir=self.prompts_dir,
            system_prompts_dir=self.system_templates_dir / "prompts",
        )

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
        self._ensure_default_teams()
        self._ensure_default_prompts()

        logger.info("Template system bootstrap complete")

    def _ensure_directories(self):
        """Ensure user template directories exist"""
        try:
            self.agents_dir.mkdir(parents=True, exist_ok=True)
            self.teams_dir.mkdir(parents=True, exist_ok=True)
            self.prompts_dir.mkdir(parents=True, exist_ok=True)
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

    def _ensure_default_teams(self):
        try:
            self._copy_missing_templates(
                self.system_templates_dir / "teams",
                self.teams_dir,
                "team(s)",
            )
        except Exception as e:
            logger.error(f"Failed to copy default teams: {e}")

    def _ensure_default_prompts(self):
        try:
            self._copy_missing_templates(
                self.system_templates_dir / "prompts",
                self.prompts_dir,
                "prompt(s)",
            )
        except Exception as e:
            logger.error(f"Failed to copy default prompts: {e}")

    # ===== Helper Methods =====

    def dict_to_team_config(self, template_dict: dict) -> TeamConfig:
        """Convert frontend template dict to TeamConfig object."""
        agents = [
            AgentConfig.from_dict(agent_data)
            for agent_data in template_dict.get("agents", [])
        ]

        return TeamConfig(
            id=template_dict.get("id", ""),
            name=template_dict.get("name", ""),
            description=template_dict.get("description", ""),
            icon=template_dict.get("icon", "💬"),
            category=template_dict.get("category", "general"),
            version=template_dict.get("version", "1.0.0"),
            agents=agents,
            tags=template_dict.get("tags", []),
        )

    def prepare_team(self, team_config: TeamConfig) -> Tuple[dict, set[str], set[str]]:
        """Resolve agents and required services for a team."""

        resolve_prompts_for_team(team_config)

        agent_payloads: dict[str, dict] = {}
        required_toolsets: set[str] = set()
        required_mcp_servers: set[str] = set()

        def collect_requirements(agent_cfg: AgentConfig | None):
            if not agent_cfg:
                return
            required_toolsets.update(agent_cfg.toolsets or [])
            required_mcp_servers.update(agent_cfg.mcp_servers or [])

        for agent in team_config.agents:
            collect_requirements(agent)
            payload = agent.to_creation_payload()
            agent_payloads[agent.id] = payload

        return (
            agent_payloads,
            required_toolsets,
            required_mcp_servers,
        )

    def validate_template_dict(self, template: dict) -> dict:
        """Validate a raw team template dict."""

        try:
            team_config = self.dict_to_team_config(template)

            if not team_config.id or not team_config.name:
                return {
                    "success": False,
                    "message": "Template validation failed: id and name are required",
                    "validation_errors": ["id and name are required"],
                }

            (
                agent_payloads,
                required_toolsets,
                required_mcp_servers,
            ) = self.prepare_team(team_config)

            return {
                "success": True,
                "compatible": True,
                "required_toolsets": sorted(required_toolsets),
                "required_mcp_servers": sorted(required_mcp_servers),
                "agents": agent_payloads,
                "template": team_config.to_dict(),
            }
        except Exception as exc:
            logger.error(f"Error validating template compatibility: {exc}")
            return {"success": False, "message": str(exc)}

    # ===== Template Discovery & Loading =====

    def list_templates(self) -> List[TeamConfig]:
        """
        List all available team templates (user + system).

        Returns:
            List of TeamConfig objects
        """
        try:
            return self.file_manager.list_teams()
        except Exception as e:
            logger.error(f"Failed to list templates: {e}")
            return []

    def get_template(self, template_id: str) -> Optional[TeamConfig]:
        """
        Get a specific team template by ID.

        Searches user templates first, then system templates.

        Args:
            template_id: Template ID

        Returns:
            TeamConfig if found, None otherwise
        """
        try:
            return self.file_manager.read_team(template_id)
        except Exception as e:
            logger.error(f"Failed to get template {template_id}: {e}")
            return None

    # ===== File Operations (for frontend editing) =====

    def list_template_files(self, file_type: str = "teams") -> Dict[str, Any]:
        """
        List available template files.

        Args:
            file_type: "teams", "agents", or "all"

        Returns:
            Response dict with list of template files
        """
        try:
            if file_type not in {"teams", "agents", "all"}:
                return {"success": False, "error": f"Unknown file_type: {file_type}"}

            team_files = (
                [
                    {"id": tmpl.id, "name": tmpl.name, "path": f"teams/{tmpl.id}.md"}
                    for tmpl in self.file_manager.list_teams(resolve_refs=False)
                ]
                if file_type in {"teams", "all"}
                else []
            )

            agent_files = (
                [
                    {
                        "id": agent.id,
                        "name": agent.name,
                        "path": f"agents/{agent.id}.md",
                    }
                    for agent in self.file_manager.list_agents()
                ]
                if file_type in {"agents", "all"}
                else []
            )

            files = team_files + agent_files

            if file_type == "teams":
                files = team_files
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

    def read_template_file(
        self, file_path: str, resolve_refs: bool = False
    ) -> Dict[str, Any]:
        """
        Read a template markdown file.

        Args:
            file_path: Path to template file (e.g., "teams/default.md" or "agents/analyzer.md")
            resolve_refs: If True, resolve agent references (agents with empty model field)
                         to full agent configs. Use False for editing, True for applying.

        Returns:
            Response dict with file content
        """
        try:
            file_type, template_id = self._parse_template_file_path(file_path)

            if file_type == "teams":
                team = self.file_manager.read_team(template_id, resolve_refs=resolve_refs)
                if not team:
                    return {
                        "success": False,
                        "error": f"Template '{template_id}' not found",
                    }

                team_dict = team.to_dict()
                team_dict["type"] = "team"

                return {
                    "success": True,
                    "file_path": file_path,
                    "type": "team",
                    "content": team_dict,
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
            file_path: Path to template file (e.g., "teams/custom.md" or "agents/custom.md")
            content: Template content dict with all fields

        Returns:
            Response dict with operation results
        """
        try:
            file_type, template_id = self._parse_template_file_path(file_path)

            payload = dict(content)
            payload.setdefault("id", template_id)

            if file_type == "teams":
                team = self.dict_to_team_config(payload)
                try:
                    self.file_manager.update_team(template_id, team)
                    operation = "update"
                except FileNotFoundError:
                    self.file_manager.create_team(team)
                    operation = "create"

                return {
                    "success": True,
                    "operation": operation,
                    "file_path": file_path,
                    "type": "team",
                    "id": team.id,
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
            file_path: Path to template file (e.g., "teams/custom.md" or "agents/custom.md")

        Returns:
            Response dict with operation results
        """
        try:
            file_type, template_id = self._parse_template_file_path(file_path)

            if file_type == "teams":
                self.file_manager.delete_team(template_id)
            else:
                self.file_manager.delete_agent(template_id)

            return {
                "success": True,
                "operation": "delete",
                "file_path": file_path,
                "type": "team" if file_type == "teams" else "agent",
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
        if file_type not in {"teams", "agents"}:
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
