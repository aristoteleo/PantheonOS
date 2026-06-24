"""File-based skill storage with atomic writes and validation."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from pantheon.utils.log import logger

from .types import (
    MAX_FILE_SIZE,
    SkillEntry,
    SkillHeader,
    parse_frontmatter_only,
    parse_skill_file,
    security_scan,
    validate_content_size,
    validate_file_path,
    validate_frontmatter,
    validate_name,
)

MAX_SKILLS = 200


class SkillStore:
    """Skill filesystem management with atomic writes and validation.

    Supports layered scanning: project skills override global skills, which
    override packaged factory skills.
    """

    def __init__(
        self,
        skills_dir: Path,
        runtime_dir: Path,
        global_skills_dir: Path | None = None,
        factory_skills_dir: Path | None = None,
        excluded_skills: list[str] | None = None,
    ):
        self.skills_dir = skills_dir
        self.runtime_dir = runtime_dir
        self.global_skills_dir = global_skills_dir
        self.factory_skills_dir = factory_skills_dir
        # Per-deployment skill denylist (e.g. a host app whose own agent reaches
        # its data via a dedicated MCP and must NOT also see the packaged factory
        # skill for that app). Keyed by the skill's path/dir key (e.g.
        # "virtualembryo"); a name N also hides any nested skill under "N/".
        # Applied at this choke point so the skill stays in the factory tree for
        # every other agent while being invisible + unloadable here.
        self.excluded_skills = set(excluded_skills or [])
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def _is_excluded(self, path_key: str) -> bool:
        """True if a skill path key is in the deployment denylist (exact dir
        match, or nested under an excluded dir)."""
        if not self.excluded_skills:
            return False
        return any(
            path_key == name or path_key.startswith(f"{name}/")
            for name in self.excluded_skills
        )

    # ── Discovery ──

    def scan_headers(self) -> list[SkillHeader]:
        """Scan all SKILL.md files from project, global, and factory."""
        headers: list[SkillHeader] = []
        seen_paths: set[str] = set()

        for base_dir, scope in self._skill_layers():
            if not base_dir or not base_dir.exists():
                continue
            for skill_md in self._iter_skill_files(base_dir):
                header = parse_frontmatter_only(skill_md, skills_dir=base_dir)
                if header and header.path not in seen_paths:
                    if self._is_excluded(header.path):
                        continue
                    header.scope = scope
                    headers.append(header)
                    seen_paths.add(header.path)

        headers.sort(key=lambda h: h.mtime, reverse=True)
        return headers[:MAX_SKILLS]

    def load_skill(self, name: str) -> SkillEntry | None:
        """Load full skill content by name using project -> global -> factory.

        Resolution is forgiving so an agent can follow a SKILL.md's relative links
        verbatim: a parent-relative path ("sc_best_practices/chromatin_accessibility"),
        a bare nested leaf ("database_access"), or a link with a trailing "/SKILL.md"
        all resolve to the right (possibly nested) skill — see
        _find_skill_dir_forgiving().
        """
        found = self._find_skill_dir_with_base(name) or self._find_skill_dir_forgiving(name)
        if not found:
            return None
        skill_dir, base_dir, scope = found
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None
        try:
            entry = parse_skill_file(skill_md, skills_dir=base_dir)
            # Denylisted skills are invisible in scan_headers; also refuse to load
            # them here so a stale/guessed name can't pull the content back in.
            if self._is_excluded(entry.path):
                return None
            entry.scope = scope
            return entry
        except Exception as e:
            logger.warning(f"Failed to parse skill '{name}': {e}")
            return None

    def load_file(self, name: str, file_path: str) -> str | None:
        """Load a supporting file from a skill directory.

        Returns content string, or None if not found.
        Raises ValueError for binary files.
        """
        err = validate_file_path(file_path)
        if err:
            raise ValueError(err)

        skill_dir = self._find_skill_dir(name)
        if not skill_dir:
            # Forgiving fallback for parent-relative skill refs (read-only path).
            forgiving = self._find_skill_dir_forgiving(name)
            skill_dir = forgiving[0] if forgiving else None
        if not skill_dir:
            return None

        target = (skill_dir / file_path).resolve()
        # Security: ensure resolved path is within skill_dir
        if not target.is_relative_to(skill_dir.resolve()):
            raise ValueError("Path traversal detected.")

        if not target.exists():
            return None

        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            size = target.stat().st_size
            raise ValueError(f"Binary file: {target.name} ({size:,} bytes)")

    # ── Write Operations ──

    def create_skill(self, name: str, content: str) -> Path:
        """Create a new skill. Validates, checks collision, writes atomically.

        Returns path to created SKILL.md.
        Raises ValueError on validation failure.
        """
        # Validate
        for check, arg in [
            (validate_name, name),
            (validate_frontmatter, content),
            (validate_content_size, content),
        ]:
            err = check(arg)
            if err:
                raise ValueError(err)

        # Security scan
        err = security_scan(content)
        if err:
            raise ValueError(err)

        # Collision check
        existing = self._find_skill_dir(name)
        if existing:
            raise ValueError(f"Skill '{name}' already exists at {existing}.")

        # Create
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"

        try:
            self._atomic_write(skill_md, content)
        except Exception:
            # Rollback: remove created directory
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            raise

        logger.info(f"Created skill '{name}' at {skill_md}")
        return skill_md

    def update_skill(self, name: str, content: str) -> Path:
        """Full rewrite of SKILL.md. Validates and writes atomically.

        Returns path to updated SKILL.md.
        """
        for check, arg in [
            (validate_frontmatter, content),
            (validate_content_size, content),
        ]:
            err = check(arg)
            if err:
                raise ValueError(err)

        err = security_scan(content)
        if err:
            raise ValueError(err)

        skill_dir = self._find_writable_skill_dir(name)
        if not skill_dir:
            raise ValueError(f"Skill '{name}' not found.")

        skill_md = skill_dir / "SKILL.md"
        backup = skill_md.read_text(encoding="utf-8") if skill_md.exists() else None

        try:
            self._atomic_write(skill_md, content)
        except Exception:
            # Rollback atomically
            if backup is not None:
                self._atomic_write(skill_md, backup)
            raise

        logger.info(f"Updated skill '{name}'")
        return skill_md

    def patch_skill(
        self, name: str, old_str: str, new_str: str, replace_all: bool = False
    ) -> Path:
        """Targeted find-and-replace in SKILL.md.

        Returns path to patched SKILL.md.
        """
        skill_dir = self._find_writable_skill_dir(name)
        if not skill_dir:
            raise ValueError(f"Skill '{name}' not found.")

        skill_md = skill_dir / "SKILL.md"
        original = skill_md.read_text(encoding="utf-8")

        count = original.count(old_str)
        if count == 0:
            raise ValueError(f"Text not found in skill '{name}'.")
        if count > 1 and not replace_all:
            raise ValueError(
                f"Found {count} matches. Use replace_all=True to replace all, "
                "or provide a more specific string."
            )

        patched = original.replace(old_str, new_str) if replace_all else original.replace(old_str, new_str, 1)

        # Re-validate frontmatter after patch
        err = validate_frontmatter(patched)
        if err:
            raise ValueError(f"Patch would break frontmatter: {err}")

        err = security_scan(patched)
        if err:
            raise ValueError(err)

        try:
            self._atomic_write(skill_md, patched)
        except Exception:
            # Rollback atomically
            self._atomic_write(skill_md, original)
            raise

        logger.info(f"Patched skill '{name}'")
        return skill_md

    def delete_skill(self, name: str) -> bool:
        """Delete a skill directory entirely."""
        skill_dir = self._find_writable_skill_dir(name)
        if not skill_dir:
            return False

        shutil.rmtree(skill_dir)
        logger.info(f"Deleted skill '{name}'")

        # Clean up empty parent if it was in a category subdirectory
        parent = skill_dir.parent
        if parent != self.skills_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

        return True

    def write_supporting_file(
        self, name: str, file_path: str, content: str
    ) -> Path:
        """Write a supporting file inside a skill directory.

        Returns path to written file.
        """
        err = validate_file_path(file_path)
        if err:
            raise ValueError(err)

        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            raise ValueError(f"File exceeds {MAX_FILE_SIZE:,} byte limit.")

        skill_dir = self._find_writable_skill_dir(name)
        if not skill_dir:
            raise ValueError(f"Skill '{name}' not found.")

        target = (skill_dir / file_path).resolve()
        if not target.is_relative_to(skill_dir.resolve()):
            raise ValueError("Path traversal detected.")

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, content)
        logger.info(f"Wrote supporting file '{file_path}' for skill '{name}'")
        return target

    def remove_supporting_file(self, name: str, file_path: str) -> bool:
        """Remove a supporting file from a skill."""
        err = validate_file_path(file_path)
        if err:
            raise ValueError(err)

        skill_dir = self._find_writable_skill_dir(name)
        if not skill_dir:
            return False

        target = (skill_dir / file_path).resolve()
        if not target.is_relative_to(skill_dir.resolve()):
            raise ValueError("Path traversal detected.")

        if not target.exists():
            return False

        target.unlink()

        # Clean up empty subdirectories
        parent = target.parent
        while parent != skill_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

        logger.info(f"Removed supporting file '{file_path}' from skill '{name}'")
        return True

    # ── Internal ──

    def _find_skill_dir(self, name: str) -> Path | None:
        """Find a skill directory by name (project > global > factory)."""
        found = self._find_skill_dir_with_base(name)
        return found[0] if found else None

    def _find_writable_skill_dir(self, name: str) -> Path | None:
        """Find a mutable skill directory by name (project > global).

        Factory skills are read-only fallback content and must not be patched,
        deleted, or receive supporting files.
        """
        result = self._find_skill_dir_in(name, self.skills_dir)
        if not result and self.global_skills_dir:
            result = self._find_skill_dir_in(name, self.global_skills_dir)
        return result

    def _find_skill_dir_with_base(self, name: str) -> tuple[Path, Path, str] | None:
        for base_dir, scope in self._skill_layers():
            result = self._find_skill_dir_in(name, base_dir)
            if result:
                return result, base_dir, scope
        return None

    def _skill_layers(self) -> list[tuple[Path, str]]:
        layers: list[tuple[Path, str]] = [(self.skills_dir, "project")]
        if self.global_skills_dir:
            layers.append((self.global_skills_dir, "global"))
        if self.factory_skills_dir:
            layers.append((self.factory_skills_dir, "factory"))
        return layers

    @staticmethod
    def _find_skill_dir_in(name: str, base_dir: Path) -> Path | None:
        """Find a skill directory by name or relative path within a single base dir."""
        if not base_dir or not base_dir.exists():
            return None
        direct = base_dir / name
        if (direct / "SKILL.md").exists():
            return direct
        for skill_md in SkillStore._iter_skill_files(base_dir):
            if skill_md.parent.name == name:
                return skill_md.parent
        return None

    @staticmethod
    def _iter_skill_files(base_dir: Path):
        """Iterate all SKILL.md files in a directory."""
        if not base_dir or not base_dir.exists():
            return
        for root, dirs, files in os.walk(base_dir):
            if "SKILL.md" in files:
                yield Path(root) / "SKILL.md"
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".")]

    @staticmethod
    def _walk_skill_dirs(base_dir: Path):
        """Yield (skill_dir, rel_parts) for EVERY skill in base_dir, nested included.

        Unlike _iter_skill_files (which prunes at the first SKILL.md to list only
        top-level skills), this descends fully so nested sub-skills stay visible —
        required to resolve a parent-relative reference like "database_access" or
        "sc_best_practices/chromatin_accessibility" back to its full path.
        """
        if not base_dir or not base_dir.exists():
            return
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if "SKILL.md" in files:
                d = Path(root)
                yield d, d.relative_to(base_dir).parts

    @staticmethod
    def _norm_skill_ref(name: str) -> str:
        """Strip a trailing '/SKILL.md' (a SKILL.md link names its own skill)."""
        norm = name.strip().strip("/")
        if norm.endswith("/SKILL.md"):
            norm = norm[: -len("/SKILL.md")]
        return norm

    def _find_skill_dir_forgiving(self, name: str) -> tuple[Path, Path, str] | None:
        """Resolve a parent-relative skill reference to its full nested path.

        For agents that follow a SKILL.md's relative links verbatim and drop the
        parent prefix (e.g. "database_access" or "sc_best_practices/..." instead of
        "omics/database_access"). Strips a trailing "/SKILL.md", then matches the
        UNIQUE skill whose path ends with the requested segments. Refuses to guess
        when ambiguous. Read-only — not used on the write path, so create()'s
        collision check stays exact.
        """
        norm = self._norm_skill_ref(name)
        if not norm or norm == "SKILL.md":
            return None
        want = tuple(Path(norm).parts)
        for base_dir, scope in self._skill_layers():
            matches = [
                d
                for d, rel in self._walk_skill_dirs(base_dir)
                if len(want) <= len(rel) and rel[-len(want):] == want
            ]
            if len(matches) == 1:
                return matches[0], base_dir, scope
            if len(matches) > 1:
                return None
        return None

    def suggest_for(self, name: str, limit: int = 4) -> list[str]:
        """Suggest the correct skill_view() call(s) for a name that didn't resolve.

        Matches nested skills by path-suffix and, when the name's tail looks like a
        supporting file, the skill that owns it — so a wrong guess gets the exact
        call to make next instead of a bare 'not found'.
        """
        norm = self._norm_skill_ref(name)
        if not norm:
            return []
        parts = tuple(Path(norm).parts)

        def suffix_skill_matches(want: tuple) -> list[str]:
            hits: list[str] = []
            for base_dir, _scope in self._skill_layers():
                for _d, rel in self._walk_skill_dirs(base_dir):
                    if rel and len(want) <= len(rel) and rel[-len(want):] == want:
                        hits.append("/".join(rel))
            return hits

        out: list[str] = []
        seen: set[str] = set()

        def add(s: str) -> None:
            if s not in seen:
                seen.add(s)
                out.append(s)

        # (a) the name (minus /SKILL.md) is itself a nested skill
        for ident in suffix_skill_matches(parts):
            add(f"skill_view(name='{ident}')")
        # (b) the tail looks like a supporting file -> point at its owning skill
        tail = parts[-1] if parts else ""
        if len(parts) >= 2 and "." in tail:
            for ident in suffix_skill_matches(parts[:-1]):
                add(f"skill_view(name='{ident}', file_path='{tail}')")
        return out[:limit]

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write content atomically: temp file + os.replace()."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
