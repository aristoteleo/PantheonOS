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

from pantheon.constant import PROJECT_ROOT
from pantheon.utils.log import logger
from .template_io import FileBasedTemplateManager, resolve_prompts_for_team, init_prompt_resolver
from .models import AgentConfig, TeamConfig


class TemplateManager:
    """Template manager for discovery, loading, file operations, and bootstrap"""

    def __init__(self, work_dir: Optional[Path] = None):
        """
        Initialize template manager.

        Args:
            work_dir: Working directory for user templates.
                      Defaults to PROJECT_ROOT (captured at module load, before any chdir).
        """

        # Get settings instance
        from pantheon.settings import get_settings
        self.settings = get_settings(work_dir)

        self.system_templates_dir = Path(__file__).parent / "templates"

        self.file_manager = FileBasedTemplateManager(self.work_dir)

        # Auto-bootstrap template system on initialization
        self.bootstrap()

        # Initialize prompt resolver with user prompts directory (higher priority)
        init_prompt_resolver(
            user_prompts_dir=self.prompts_dir,
            system_prompts_dir=self.system_templates_dir / "prompts",
        )

    @property
    def work_dir(self) -> Path:
        """Get work directory from settings (dynamically updated)."""
        return self.settings.work_dir

    @property
    def agents_dir(self) -> Path:
        """Get agents directory from settings (dynamically updated)."""
        return self.settings.agents_dir

    @property
    def teams_dir(self) -> Path:
        """Get teams directory from settings (dynamically updated)."""
        return self.settings.teams_dir

    @property
    def prompts_dir(self) -> Path:
        """Get prompts directory from settings (dynamically updated)."""
        return self.settings.prompts_dir

    @property
    def skills_dir(self) -> Path:
        """Get skills directory from settings (dynamically updated)."""
        return self.settings.skills_dir

    # ===== Bootstrap =====

    def bootstrap(self):
        """
        Bootstrap the template system.

        Creates necessary user directories and copies system templates on first run.
        Also copies settings.json and mcp.json if they don't exist.
        """
        logger.info("Bootstrapping template system...")

        # Ensure user directories exist
        self._ensure_directories()

        # Ensure config files exist (copy from templates if missing)
        self._ensure_settings()
        self._ensure_mcp_config()

        # Ensure packaged templates exist locally (copy missing ones)
        self._ensure_default_templates()

        logger.info("Template system bootstrap complete")


    def _ensure_directories(self):
        """Ensure user template directories exist"""
        try:
            for dest_dir in [self.agents_dir, self.teams_dir, self.prompts_dir, self.skills_dir]:
                dest_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured template directories exist at {self.work_dir}")
        except Exception as e:
            logger.error(f"Failed to create template directories: {e}")
            raise

    def _load_factory_hashes(self) -> dict:
        """Load stored factory file hashes from .pantheon/.factory_hashes.json."""
        import json
        hash_file = self.settings.pantheon_dir / ".factory_hashes.json"
        if hash_file.exists():
            try:
                return json.loads(hash_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_factory_hashes(self, hashes: dict):
        """Save factory file hashes to .pantheon/.factory_hashes.json."""
        import json
        hash_file = self.settings.pantheon_dir / ".factory_hashes.json"
        hash_file.write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    @staticmethod
    def _file_hash(path: Path) -> str:
        """Compute MD5 hash of a file."""
        import hashlib
        return hashlib.md5(path.read_bytes()).hexdigest()

    def _copy_missing_templates(self, src_dir: Path, dest_dir: Path, label: str, overwrite: bool = False):
        """Copy templates from src to dest.

        When overwrite=True, uses factory hash tracking to distinguish between
        factory updates and user modifications:
        - New files: always copy
        - Factory changed, user didn't modify: update to latest factory version
        - Factory changed, user also modified: skip (preserve user changes)
        - Factory unchanged: skip

        When overwrite=False, only copies files that don't exist yet.
        """
        if not src_dir.exists():
            return 0

        factory_hashes = self._load_factory_hashes() if overwrite else {}
        hashes_changed = False

        copied_files = []
        updated_files = []
        skipped_files = []

        for src_file in src_dir.rglob('*'):
            if not src_file.is_file():
                continue

            rel_path = src_file.relative_to(src_dir)
            dest_file = dest_dir / rel_path
            hash_key = f"{label}/{rel_path}"

            if not dest_file.exists():
                # New file: always copy
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                copied_files.append(str(rel_path))
                if overwrite:
                    factory_hashes[hash_key] = self._file_hash(src_file)
                    hashes_changed = True
            elif overwrite:
                src_hash = self._file_hash(src_file)
                stored_hash = factory_hashes.get(hash_key)

                if stored_hash == src_hash:
                    # Factory hasn't changed since last sync, skip
                    continue

                # Factory has changed. Check if user modified the file.
                dest_hash = self._file_hash(dest_file)
                if stored_hash is not None and dest_hash != stored_hash:
                    # User has modified the file since last sync, skip
                    skipped_files.append(str(rel_path))
                else:
                    # User hasn't modified (dest matches last synced factory),
                    # or first sync (no stored hash). Safe to update.
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
                    updated_files.append(str(rel_path))

                factory_hashes[hash_key] = src_hash
                hashes_changed = True

        if hashes_changed:
            self._save_factory_hashes(factory_hashes)

        total_changes = len(copied_files) + len(updated_files)

        if total_changes > 0:
            msg_parts = [f"Synced {total_changes} {label} from factory"]
            if copied_files:
                msg_parts.append(f"{len(copied_files)} new: {', '.join(copied_files)}")
            if updated_files:
                msg_parts.append(f"{len(updated_files)} updated: {', '.join(updated_files)}")
            logger.info(" | ".join(msg_parts))
        if skipped_files:
            logger.debug(f"Skipped {len(skipped_files)} user-modified {label}: {', '.join(skipped_files)}")

        return total_changes

    def get_updatable_templates(self) -> list[dict]:
        """Compare factory templates with user copies and return a list of updatable files.

        Returns:
            List of dicts with keys: rel_path, category, status ('new', 'updated', 'modified')
            - new: file exists in factory but not in user dir
            - updated: factory changed, user hasn't modified (safe to update)
            - modified: factory changed, user also modified (will overwrite user changes)
        """
        factory_hashes = self._load_factory_hashes()
        results = []

        for subdir, dest_dir, category in [
            ("agents", self.agents_dir, "agents"),
            ("teams", self.teams_dir, "teams"),
            ("prompts", self.prompts_dir, "prompts"),
        ]:
            src_dir = self.system_templates_dir / subdir
            if not src_dir.exists():
                continue
            for src_file in src_dir.rglob('*'):
                if not src_file.is_file():
                    continue
                rel_path = src_file.relative_to(src_dir)
                dest_file = dest_dir / rel_path
                hash_key = f"{category}/{rel_path}"

                if not dest_file.exists():
                    results.append({"rel_path": str(rel_path), "category": category, "status": "new"})
                else:
                    src_hash = self._file_hash(src_file)
                    dest_hash = self._file_hash(dest_file)
                    if src_hash == dest_hash:
                        continue  # identical, nothing to do
                    stored_hash = factory_hashes.get(hash_key)
                    if stored_hash is not None and dest_hash != stored_hash:
                        results.append({"rel_path": str(rel_path), "category": category, "status": "modified"})
                    else:
                        results.append({"rel_path": str(rel_path), "category": category, "status": "updated"})

        return results

    def force_update_templates(self, items: list[dict]):
        """Force update selected template files from factory.

        Args:
            items: List of dicts from get_updatable_templates() to update.
        """
        factory_hashes = self._load_factory_hashes()

        dir_map = {
            "agents": (self.system_templates_dir / "agents", self.agents_dir),
            "teams": (self.system_templates_dir / "teams", self.teams_dir),
            "prompts": (self.system_templates_dir / "prompts", self.prompts_dir),
        }

        for item in items:
            category = item["category"]
            rel_path = item["rel_path"]
            src_dir, dest_dir = dir_map[category]
            src_file = src_dir / rel_path
            dest_file = dest_dir / rel_path

            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest_file)

            hash_key = f"{category}/{rel_path}"
            factory_hashes[hash_key] = self._file_hash(src_file)

        self._save_factory_hashes(factory_hashes)

    def _ensure_default_templates(self):
        """Copy all default templates (agents, teams, prompts, skills).

        Respects the `default_template_auto_update` setting for agents/teams/prompts:
        - True (default): overwrite existing files with latest factory versions.
        - False: only copy files that don't exist yet (preserves user edits).
        Skills are always copy-missing-only regardless of the setting,
        since they contain user-generated data.
        """
        overwrite = self.settings.default_template_auto_update
        if overwrite:
            logger.info("default_template_auto_update=true: overwriting agents/teams/prompts with latest factory defaults")
        # agents/teams/prompts: respect overwrite flag
        for subdir, dest_dir, label in [
            ("agents", self.agents_dir, "agent(s)"),
            ("teams", self.teams_dir, "team(s)"),
            ("prompts", self.prompts_dir, "prompt(s)"),
        ]:
            try:
                self._copy_missing_templates(
                    self.system_templates_dir / subdir, dest_dir, label, overwrite=overwrite
                )
            except Exception as e:
                logger.error(f"Failed to copy default {label}: {e}")
        # skills: always copy-missing-only (user-generated data, never overwrite)
        try:
            self._copy_missing_templates(
                self.system_templates_dir / "skills", self.settings.skills_dir, "skill(s)", overwrite=False
            )
        except Exception as e:
            logger.error(f"Failed to copy default skill(s): {e}")

    def _ensure_settings(self):
        """Copy settings.json from templates if it doesn't exist in .pantheon/"""
        try:
            dest = self.settings.pantheon_dir / "settings.json"
            if not dest.exists():
                src = self.system_templates_dir / "settings.json"
                if src.exists():
                    shutil.copy(src, dest)
                    logger.info("Copied settings.json from system templates")
        except Exception as e:
            logger.error(f"Failed to copy settings.json: {e}")

    def _ensure_mcp_config(self):
        """Copy mcp.json from templates if it doesn't exist in .pantheon/"""
        try:
            dest = self.settings.pantheon_dir / "mcp.json"
            if not dest.exists():
                src = self.system_templates_dir / "mcp.json"
                if src.exists():
                    shutil.copy(src, dest)
                    logger.info("Copied mcp.json from system templates")
        except Exception as e:
            logger.error(f"Failed to copy mcp.json: {e}")

    # ===== Helper Methods =====

    def parse_template_content(self, content: str, file_path: Path = None) -> TeamConfig:
        """
        Parse template markdown content into TeamConfig.

        Supports both team templates and agent templates.
        If an agent template is provided, it will be wrapped in a TeamConfig.

        Args:
            content: Markdown string with YAML frontmatter
            file_path: Optional file path for resolving relative paths in prompts

        Returns:
            TeamConfig object
        """
        import frontmatter

        post = frontmatter.loads(content)
        entry_type = str(post.metadata.get("type", "")).lower()

        # Set file path for prompt resolution
        if file_path:
            self.file_manager.parser._current_file_path = file_path

        if entry_type in ("chatroom", "team"):
            return self.file_manager.parser.parse_team(post)

        # Agent template - wrap in TeamConfig
        agent_config = self.file_manager.parser.parse_agent(post)
        return TeamConfig(
            id=agent_config.id,
            name=agent_config.name or agent_config.id,
            description=f"Single agent: {agent_config.name}",
            agents=[agent_config],
        )

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
            source_path=template_dict.get("source_path"),
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

        # "think" is a built-in tool handled by Agent, not a remote toolset
        required_toolsets.discard("think")
        # "task" and "skills" are local-only toolsets, not managed by Endpoint
        required_toolsets.discard("task")
        required_toolsets.discard("skills")

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

            def _get_filename(source_path: str, fallback_id: str) -> str:
                """Extract filename from source_path, fallback to id if not available."""
                if source_path:
                    from pathlib import Path
                    return Path(source_path).stem
                return fallback_id

            team_files = (
                [
                    {
                        "id": tmpl.id,
                        "name": tmpl.name,
                        "path": f"teams/{_get_filename(tmpl.source_path, tmpl.id)}.md",
                    }
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
                        "path": f"agents/{_get_filename(agent.source_path, agent.id)}.md",
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
        """Validate and split template file path (xx/xx/id.md)."""
        parts = file_path.split("/")

        file_type, filename = parts[0], "/".join(parts[1:])
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
