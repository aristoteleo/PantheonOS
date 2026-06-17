"""Project registry — tracks known project directories.

A project is a directory containing (or that will contain) a `.pantheon/` folder.
The global registry lives at `~/.pantheon/projects.json`.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger


def _global_pantheon_dir() -> Path:
    return Path.home() / ".pantheon"


def _registry_path() -> Path:
    return _global_pantheon_dir() / "projects.json"


class ProjectInfo:
    def __init__(
        self,
        path: str,
        name: str = "",
        created_at: str = "",
        last_accessed: str = "",
    ):
        self.path = str(Path(path).resolve())
        self.name = name or Path(self.path).name
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.last_accessed = last_accessed or self.created_at

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectInfo":
        return cls(
            path=d["path"],
            name=d.get("name", ""),
            created_at=d.get("created_at", ""),
            last_accessed=d.get("last_accessed", ""),
        )


class ProjectManager:
    """Manages the global project registry and active project state."""

    def __init__(self, active_path: Optional[str] = None):
        self._registry_path = _registry_path()
        self._projects: dict[str, ProjectInfo] = {}
        self._active_path: Optional[str] = None
        # The "home" project — the directory the server was started in (work_dir).
        # The UI is always "in" some project; when no project is otherwise active
        # (e.g. the active one was just removed), we fall back to home rather than
        # leaving the UI with "No Project".
        self._default_path: Optional[str] = None
        self._load()

        if active_path:
            resolved = str(Path(active_path).resolve())
            self._default_path = resolved
            self.register(resolved)
            self.set_active(resolved)

    def _load(self):
        if self._registry_path.exists():
            try:
                data = json.loads(self._registry_path.read_text(encoding="utf-8"))
                for entry in data.get("projects", []):
                    info = ProjectInfo.from_dict(entry)
                    self._projects[info.path] = info
                self._active_path = data.get("active")
            except Exception as e:
                logger.warning(f"[Projects] Failed to load registry: {e}")

    def _save(self):
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active": self._active_path,
            "projects": [p.to_dict() for p in self._projects.values()],
        }
        self._registry_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @property
    def active_project(self) -> Optional[ProjectInfo]:
        if self._active_path and self._active_path in self._projects:
            return self._projects[self._active_path]
        # Fallback: if active is set but not registered, auto-register it
        if self._active_path and Path(self._active_path).is_dir():
            self.register(self._active_path)
            return self._projects.get(self._active_path)
        # Never leave the UI projectless: fall back to the home (work_dir) project.
        if self._default_path and Path(self._default_path).is_dir():
            if self._default_path not in self._projects:
                self.register(self._default_path)
            self._active_path = self._default_path
            return self._projects.get(self._default_path)
        return None

    @property
    def default_project(self) -> Optional[ProjectInfo]:
        """The home (work_dir) project — owns chats that have no project."""
        if self._default_path and self._default_path in self._projects:
            return self._projects[self._default_path]
        if self._default_path and Path(self._default_path).is_dir():
            return self.register(self._default_path)
        return None

    def list_projects(self) -> list[dict]:
        result = []
        for p in sorted(self._projects.values(), key=lambda x: x.last_accessed, reverse=True):
            d = p.to_dict()
            d["is_active"] = p.path == self._active_path
            d["exists"] = Path(p.path).exists()
            d["has_pantheon"] = (Path(p.path) / ".pantheon").is_dir()
            result.append(d)
        return result

    def register(self, path: str, name: str = "") -> ProjectInfo:
        resolved = str(Path(path).resolve())
        if resolved in self._projects:
            if name:
                self._projects[resolved].name = name
                self._save()
            return self._projects[resolved]

        info = ProjectInfo(path=resolved, name=name)
        self._projects[resolved] = info
        self._save()
        logger.info(f"[Projects] Registered: {info.name} ({resolved})")
        return info

    def remove(self, path: str) -> bool:
        resolved = str(Path(path).resolve())
        if resolved in self._projects:
            del self._projects[resolved]
            if self._active_path == resolved:
                # Don't orphan the active pointer — fall back to home (work_dir)
                # so the UI stays "in" a project.
                self._active_path = (
                    self._default_path if self._default_path != resolved else None
                )
            self._save()
            return True
        return False

    def set_active(self, path: str) -> Optional[ProjectInfo]:
        resolved = str(Path(path).resolve())
        if resolved not in self._projects:
            return None
        self._active_path = resolved
        self._projects[resolved].last_accessed = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info(f"[Projects] Active: {self._projects[resolved].name} ({resolved})")
        return self._projects[resolved]

    def get_project(self, path: str) -> Optional[ProjectInfo]:
        resolved = str(Path(path).resolve())
        return self._projects.get(resolved)

    def get_config_scope(self, project_path: str) -> dict:
        """Return settings with scope annotations (global vs project)."""
        global_settings_path = _global_pantheon_dir() / "settings.json"
        project_settings_path = Path(project_path) / ".pantheon" / "settings.json"

        global_settings = {}
        project_settings = {}

        if global_settings_path.exists():
            try:
                global_settings = json.loads(global_settings_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if project_settings_path.exists():
            try:
                project_settings = json.loads(project_settings_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        return {
            "global": global_settings,
            "project": project_settings,
        }
