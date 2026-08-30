"""
Template Manager for Pantheon

Provides interface for template discovery, loading, file operations, and bootstrap.
- Template discovery and loading
- File-based template operations (CRUD)
- Bootstrap initialization on startup
"""

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pantheon.constant import PROJECT_ROOT
from pantheon.utils.log import logger
from .template_io import (
    FileBasedTemplateManager,
    _is_path_reference,
    init_prompt_resolver,
    resolve_prompts_for_team,
)
from .models import AgentConfig, TeamConfig


# Templates the factory has withdrawn, relative to a scope's template root.
#
# Deleting a tree from the package is NOT enough to delete anyone's copy of it
# — see `_remove_retired_templates`. Add a path here when removing one, and it
# can come back out once no live workspace could still be carrying it.
RETIRED_TEMPLATES = (
    # The live view plane: skills for `live_view_*` tools that no longer exist.
    "skills/live_view",
    # The SCFM toolset was removed (2026-08 toolset cleanup): its router agent
    # and its skills document tools that no longer exist.
    "agents/single_cell/fm_router.md",
    "skills/omics/scfm",
)


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

        # Initialize prompt resolver: project > global > system
        init_prompt_resolver(
            user_prompts_dir=self.prompts_dir,
            system_prompts_dir=self.system_templates_dir / "prompts",
            global_prompts_dir=self.settings.global_prompts_dir,
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

        # Snapshot hashes before any optional materialization so reclaim can
        # distinguish a user-modified project override from a stale factory copy.
        pre_sync_hashes = self._load_factory_hashes()

        # Optional materialization. The default is runtime factory fallback, so
        # sandbox startup does not copy package templates into ephemeral HOME.
        self._ensure_default_templates()

        # Reclaim: older builds copied factory templates into the PROJECT scope
        # On Modal those froze on the persistent volume and shadowed the fresh
        # factory fallback. Remove factory-origin project files, preserving
        # user-created and user-modified content.
        self._reclaim_factory_from_project(pre_sync_hashes)

        # Reclaim cannot remove what the factory no longer ships: it only
        # deletes a project file whose path still exists in the package. So a
        # RETIRED tree would survive in every workspace that ever synced it.
        self._remove_retired_templates()

        # One-time per-workspace migration: older builds hash-tracked factory
        # agents/teams/prompts in the PROJECT scope, but on some workspaces those
        # recorded hashes drifted so the reclaim above can't tell a STALE frozen
        # factory copy from a real user override (e.g. a frozen v1.3.0 team kept
        # shadowing the current v1.4.0). Clear them once, with backup.
        self._reclaim_legacy_frozen_factory()

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

    def _factory_template_mode(self) -> str:
        """Return how factory templates are materialized on startup.

        Default is runtime fallback: do not copy factory templates anywhere at
        startup. Loading resolves project -> global -> packaged factory.
        """
        raw_mode = os.environ.get("PANTHEON_FACTORY_TEMPLATE_MODE", "")
        mode = raw_mode.strip().lower()
        if not mode:
            return "runtime"
        if mode not in {"runtime", "global"}:
            logger.warning(
                f"Invalid PANTHEON_FACTORY_TEMPLATE_MODE={mode!r}; falling back to 'runtime'"
            )
            return "runtime"
        return mode

    def _factory_template_targets(self, *, mode: str | None = None) -> list[tuple[str, Path, str]]:
        """Return optional factory template copy targets for materialization."""
        mode = self._factory_template_mode() if mode is None else mode
        if mode != "global":
            return []

        return [
            ("agents", self.settings.global_agents_dir, "agent(s)"),
            ("teams", self.settings.global_teams_dir, "team(s)"),
            ("prompts", self.settings.global_prompts_dir, "prompt(s)"),
            ("skills", self.settings.global_skills_dir, "skill(s)"),
        ]

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
            # Compiled artifacts: their hash changes on every build (embedded
            # mtimes), so they re-synced on every boot forever. Python
            # regenerates __pycache__ next to the .py sources on first use.
            if "__pycache__" in src_file.parts:
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
            ("skills", self.skills_dir, "skills"),
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
            "skills": (self.system_templates_dir / "skills", self.skills_dir),
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
        """Optionally materialize factory defaults according to factory mode.

        Respects the `default_template_auto_update` setting for all categories
        (agents/teams/prompts/skills):
        - True (default): smart-overwrite using factory hash tracking — files
          the user has modified are preserved; unmodified factory copies are
          updated to the latest version.
        - False: only copy files that don't exist yet (preserves all user edits).

        Default mode is runtime fallback, which performs no startup copy:
        project → global (~/.pantheon/) → packaged factory.
        """
        mode = self._factory_template_mode()
        if mode == "runtime":
            logger.info(
                "PANTHEON_FACTORY_TEMPLATE_MODE=runtime: using packaged factory fallback; "
                "skipping startup template sync"
            )
            return

        overwrite = self.settings.default_template_auto_update

        # Fingerprint short-circuit: the sweep below exists to propagate image
        # upgrades, but it re-reads every packaged factory file to hash it —
        # on a cold-layer Modal worker that is seconds of lazy pulls, paid on
        # every boot even when nothing changed. The image bakes one aggregate
        # fingerprint of the factory tree at build time; when it matches what
        # this volume last synced, the whole sweep is provably a no-op.
        # (A stale/corrupted cache still has the escape hatch:
        # PANTHEON_FORCE_FACTORY_REFRESH clears the fingerprint with the rest.)
        image_fp = self._image_factory_fingerprint()
        fp_marker = self.settings.pantheon_dir / ".factory_fingerprint"
        if overwrite and image_fp:
            try:
                if fp_marker.exists() and fp_marker.read_text(encoding="utf-8").strip() == image_fp:
                    logger.info(
                        "Factory fingerprint unchanged since last sync; skipping template sweep"
                    )
                    return
            except Exception:
                pass  # unreadable marker -> fall through to the full sweep

        if overwrite:
            logger.info(
                "default_template_auto_update=true: smart-overwriting "
                f"agents/teams/prompts/skills with latest factory defaults (mode={mode})"
            )

        failed = False
        for subdir, dest_dir, label in self._factory_template_targets(mode=mode):
            try:
                self._copy_missing_templates(
                    self.system_templates_dir / subdir, dest_dir, label, overwrite=overwrite
                )
            except Exception as e:
                failed = True
                logger.error(f"Failed to copy default {label}: {e}")

        # Record what this volume is now synced to — only after a clean sweep,
        # so a partial failure retries next boot instead of being masked.
        if overwrite and image_fp and not failed:
            try:
                fp_marker.write_text(image_fp, encoding="utf-8")
            except Exception as e:
                logger.debug(f"Could not persist factory fingerprint: {e}")

    @staticmethod
    def _image_factory_fingerprint() -> str | None:
        """Aggregate factory-content fingerprint baked into the image at build
        time (docker/Dockerfile). None on images that predate it, or outside
        Docker — callers then fall back to the per-file sweep."""
        fp_file = Path("/etc/pantheon-factory-fingerprint")
        try:
            if fp_file.exists():
                value = fp_file.read_text(encoding="utf-8").strip()
                return value or None
        except Exception:
            pass
        return None

    def _remove_retired_templates(self):
        """Delete templates the factory has WITHDRAWN, from every writable scope.

        `_reclaim_factory_from_project` deliberately only removes a project file
        whose relative path still exists in the packaged factory — that is what
        makes it safe for user-created content. The consequence is that deleting
        a tree from the factory does not delete anyone's copy of it: it stops
        looking factory-origin and starts looking user-created, so it is kept
        and keeps being loaded. On Modal that copy lives on a volume that
        outlives every deploy.

        For a retired SKILL that is not cosmetic. The live_view skills document
        tools that no longer exist on the toolset, so a workspace still holding
        them hands the agent a manual for a machine that was dismantled.
        """
        for rel in RETIRED_TEMPLATES:
            for scope in (self.skills_dir.parent, self.settings.global_skills_dir.parent):
                victim = scope / rel
                if not victim.exists():
                    continue
                try:
                    shutil.rmtree(victim) if victim.is_dir() else victim.unlink()
                    logger.info(f"Removed retired template '{rel}' from {scope}")
                except Exception as e:
                    logger.error(f"retire: failed to remove {victim}: {e}")

    def _reclaim_factory_from_project(self, prior_hashes: dict | None = None):
        """Remove factory-origin templates that an older build copied into the
        PROJECT scope, so they stop shadowing the fresh FACTORY fallback (read
        order is project -> global -> factory).

        Safety:
        - A project file is only removed when the SAME relative path exists in
          the packaged factory (factory-origin). User-created files with no
          factory counterpart (e.g. project-specific skills) are preserved.
        - It is only removed when packaged factory still has the same relative
          path, so the content is still served by runtime factory fallback.
        - User-MODIFIED factory files are preserved: `prior_hashes` is the hash
          snapshot from BEFORE this run's global sync; if a file has a recorded
          hash and no longer matches it, the user edited it on purpose (a
          project override) and it is kept. Files with no recorded hash (the
          Modal-freeze case) or that still match are safe to reclaim.
        """
        factory_hashes = prior_hashes if prior_hashes is not None else self._load_factory_hashes()
        targets = [
            (self.agents_dir, self.system_templates_dir / "agents", "agent(s)"),
            (self.teams_dir, self.system_templates_dir / "teams", "team(s)"),
            (self.prompts_dir, self.system_templates_dir / "prompts", "prompt(s)"),
            (self.skills_dir, self.system_templates_dir / "skills", "skill(s)"),
        ]
        removed = 0
        for project_dir, factory_dir, label in targets:
            if not project_dir.exists() or not factory_dir.exists():
                continue
            for project_file in list(project_dir.rglob("*")):
                if not project_file.is_file():
                    continue
                rel = project_file.relative_to(project_dir)
                if not (factory_dir / rel).exists():
                    continue  # user-created → keep
                # Preserve USER-MODIFIED factory files: if a last-synced hash is
                # recorded and the project copy no longer matches it, the user
                # edited it on purpose (an override) — keep it. Files with no
                # recorded hash (legacy project copies, the Modal-freeze case)
                # or that still match the recorded hash are safe to reclaim.
                stored = factory_hashes.get(f"{label}/{rel}")
                if stored is not None and self._file_hash(project_file) != stored:
                    continue
                try:
                    project_file.unlink()
                    removed += 1
                except Exception as e:
                    logger.error(f"reclaim: failed to remove {project_file}: {e}")
            # prune directories left empty by the removals
            for d in sorted((p for p in project_dir.rglob("*") if p.is_dir()), reverse=True):
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                except Exception:
                    pass
        if removed:
            logger.info(
                f"Reclaimed {removed} factory-origin file(s) from the project scope; "
                "now served from packaged factory fallback."
            )

    def _reclaim_legacy_frozen_factory(self):
        """ONE-TIME per-workspace migration for LEGACY frozen factory templates.

        Older builds (PANTHEON_TEMPLATE_SYNC_SCOPE=project) copied factory
        agents/teams/prompts into the persistent PROJECT scope AND recorded their
        hashes. On some workspaces those recorded hashes DRIFTED (buggy/partial
        syncs across image versions), so `_reclaim_factory_from_project`'s
        user-modified guard (file != recorded hash) can no longer tell a STALE
        factory copy from a real user override — and the stale copy keeps
        shadowing the fresh factory (project -> global -> factory read order),
        e.g. a frozen v1.3.0 team hiding the current v1.4.0. (Skills escaped this:
        older builds never hash-tracked them, so the no-hash reclaim path cleared
        them already.)

        Runs ONCE per workspace (sentinel file). For each project file matching a
        current packaged-factory path, it backs the project copy up to
        `.overrides_backup/` (zero data loss — a genuine override is recoverable)
        and removes it so the fresh factory/global copy is served. After this, the
        normal reclaim preserves NEW user overrides as usual.
        """
        import shutil
        sentinel = self.settings.pantheon_dir / ".legacy_factory_reclaim_done"
        if sentinel.exists():
            return
        backup_root = self.settings.pantheon_dir / ".overrides_backup"
        targets = [
            (self.agents_dir, self.system_templates_dir / "agents", "agents"),
            (self.teams_dir, self.system_templates_dir / "teams", "teams"),
            (self.prompts_dir, self.system_templates_dir / "prompts", "prompts"),
        ]
        cleared = 0
        backed_up = 0
        for project_dir, factory_dir, kind in targets:
            if not project_dir.exists() or not factory_dir.exists():
                continue
            for project_file in list(project_dir.rglob("*")):
                if not project_file.is_file():
                    continue
                rel = project_file.relative_to(project_dir)
                factory_file = factory_dir / rel
                if not factory_file.exists():
                    continue  # no factory counterpart → user-created → keep
                try:
                    # Back up only when the project copy differs from the current
                    # factory (identical copies need no backup). Then remove so the
                    # fresh factory/global copy is served.
                    if self._file_hash(project_file) != self._file_hash(factory_file):
                        backup = backup_root / kind / rel
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(project_file, backup)
                        backed_up += 1
                    project_file.unlink()
                    cleared += 1
                except Exception as e:
                    logger.error(f"legacy reclaim: failed on {project_file}: {e}")
            for d in sorted((p for p in project_dir.rglob("*") if p.is_dir()), reverse=True):
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                except Exception:
                    pass
        try:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(
                "legacy factory-origin project templates reclaimed; "
                "drifted copies backed up under .overrides_backup\n",
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"legacy reclaim: failed to write sentinel: {e}")
        if cleared:
            logger.info(
                f"Legacy factory reclaim: cleared {cleared} frozen factory-origin "
                f"project file(s) (agents/teams/prompts), {backed_up} backed up to "
                f".overrides_backup; fresh factory/global copies now served."
            )

    def force_sync_factory_templates(self):
        """Force-sync ALL factory templates (including skills) to global.

        Clears hash tracking and copies everything with overwrite=True.
        Used for image upgrades where stale templates need to be replaced.
        """
        hash_file = self.settings.pantheon_dir / ".factory_hashes.json"
        if hash_file.exists():
            hash_file.unlink()

        total = 0
        for subdir, dest_dir, label in self._factory_template_targets(mode="global"):
            try:
                total += self._copy_missing_templates(
                    self.system_templates_dir / subdir, dest_dir, label, overwrite=True
                )
            except Exception as e:
                logger.error(f"Failed to force-sync {label}: {e}")
        logger.info(f"Force-sync complete: {total} file(s) synced (mode=global)")
        return total

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

        # "think" is a plugin-managed local tool, not an endpoint toolset
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

    def list_template_files(self, file_type: str = "teams", view: str = "files") -> Dict[str, Any]:
        """
        List available template files.

        Args:
            file_type: "teams", "agents", or "all"
            view: "files" for legacy file metadata, "summary" for lightweight UI metadata

        Returns:
            Response dict with list of template files
        """
        try:
            if file_type not in {"teams", "agents", "all"}:
                return {"success": False, "error": f"Unknown file_type: {file_type}"}
            if view not in {"files", "summary"}:
                return {"success": False, "error": f"Unknown view: {view}"}

            def _get_rel_path(source_path: str, fallback_id: str, kind: str) -> str:
                """Return a ``<kind>/…/<filename>.md`` path that preserves any
                subdirectory structure under ``<kind>/``. Falls back to the
                flat ``<kind>/<id>.md`` shape when source_path is missing.

                Agents can live in subdirs (e.g. ``agents/single_cell/leader.md``);
                returning just the basename would hide nested files from the UI.
                """
                from pathlib import Path
                if not source_path:
                    return f"{kind}/{fallback_id}.md"
                p = Path(source_path)
                user_base = self.agents_dir if kind == "agents" else self.teams_dir
                global_base = self.settings.global_agents_dir if kind == "agents" else self.settings.global_teams_dir
                system_base = self.system_templates_dir / kind
                for base in (user_base, global_base, system_base):
                    try:
                        rel = p.relative_to(base)
                        return f"{kind}/{rel.as_posix()}"
                    except ValueError:
                        continue
                # Source lives outside either known root — last resort: basename.
                return f"{kind}/{p.name}"

            def _agent_ref_summary(agent: AgentConfig, base_path: Path | None = None) -> Dict[str, Any]:
                is_reference = not bool(agent.name)
                resolved = agent
                if is_reference:
                    try:
                        if _is_path_reference(agent.id):
                            resolved = self.file_manager._load_agent_from_path(agent.id, base_path or self.teams_dir)
                        else:
                            resolved = self.file_manager.read_agent(agent.id)
                    except Exception:
                        resolved = agent

                return {
                    "id": resolved.id,
                    "name": resolved.name or agent.id,
                    "icon": resolved.icon,
                    "source_path": resolved.source_path,
                    "is_reference": is_reference,
                }

            def _team_file(tmpl: TeamConfig) -> Dict[str, Any]:
                item = {
                    "id": tmpl.id,
                    "name": tmpl.name,
                    "path": _get_rel_path(tmpl.source_path, tmpl.id, "teams"),
                    "source_path": tmpl.source_path,
                    "scope": getattr(tmpl, 'scope', 'project'),
                }
                if view == "summary":
                    base_path = Path(tmpl.source_path).parent if tmpl.source_path else None
                    agent_refs = [
                        _agent_ref_summary(agent, base_path)
                        for agent in tmpl.agents
                    ]
                    item.update({
                        "description": tmpl.description,
                        "icon": tmpl.icon,
                        "category": tmpl.category,
                        "tags": tmpl.tags,
                        "version": tmpl.version,
                        "agent_count": len(tmpl.agents),
                        "agent_refs": agent_refs,
                    })
                return item

            team_files = (
                [_team_file(tmpl) for tmpl in self.file_manager.list_teams(resolve_refs=False)]
                if file_type in {"teams", "all"}
                else []
            )

            agent_files = (
                [
                    {
                        "id": agent.id,
                        "name": agent.name,
                        "path": _get_rel_path(agent.source_path, agent.id, "agents"),
                        "source_path": agent.source_path,
                        "scope": getattr(agent, 'scope', 'project'),
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
                "view": view,
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
