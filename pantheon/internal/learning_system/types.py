"""Data models for the learning system."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ── Constants ──

VALID_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
VALID_NAME_RE = VALID_SEGMENT_RE  # kept for backwards compat
MAX_NAME_LENGTH = 128  # extended to support category/skill-name paths
MAX_DESCRIPTION_LENGTH = 1024
MAX_CONTENT_SIZE = 100_000  # ~36k tokens
MAX_FILE_SIZE = 1_048_576  # 1 MiB
ALLOWED_SUBDIRS = frozenset({"references", "templates", "scripts", "assets"})

# ── Injection safety patterns ──

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"IMPORTANT:\s*override", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?prior", re.IGNORECASE),
]


# ── Data classes ──


@dataclass
class SkillHeader:
    """Lightweight skill metadata from frontmatter scan."""

    name: str
    description: str
    skill_dir: Path
    path: str = ""  # relative path key, e.g. "bioinformatics/scrna-qc"
    tags: list[str] = field(default_factory=list)
    related_skills: list[str] = field(default_factory=list)
    agent_scope: list[str] | None = None  # None = all agents
    mtime: float = 0.0


@dataclass
class SkillEntry(SkillHeader):
    """Full skill entry with body content."""

    content: str = ""  # Markdown body (after frontmatter)
    version: str = ""
    linked_files: dict[str, list[str]] = field(default_factory=dict)


# ── Parsing ──


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from SKILL.md content.

    Returns (metadata_dict, body_text).
    """
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    yaml_str = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_str)
        if not isinstance(meta, dict):
            return {}, text
    except yaml.YAMLError:
        return {}, text

    return meta, body


def parse_skill_file(path: Path, skills_dir: Path | None = None) -> SkillEntry:
    """Parse a SKILL.md file into a SkillEntry."""
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    stat = path.stat()
    skill_dir = path.parent

    # Compute relative path key (e.g. "bioinformatics/scrna-qc")
    if skills_dir is not None:
        try:
            rel = skill_dir.relative_to(skills_dir)
            skill_path = str(rel).replace("\\", "/")
        except ValueError:
            skill_path = skill_dir.name
    else:
        skill_path = skill_dir.name

    # Extract metadata fields
    hermes_meta = meta.get("metadata", {}).get("hermes", {}) if isinstance(meta.get("metadata"), dict) else {}

    tags = meta.get("tags", hermes_meta.get("tags", []))
    related = meta.get("related_skills", hermes_meta.get("related_skills", []))
    scope = meta.get("agent_scope")

    # Discover linked files
    linked = _discover_linked_files(skill_dir)

    return SkillEntry(
        name=meta.get("name", skill_dir.name),
        description=meta.get("description", ""),
        skill_dir=skill_dir,
        path=skill_path,
        tags=tags if isinstance(tags, list) else [],
        related_skills=related if isinstance(related, list) else [],
        agent_scope=scope if isinstance(scope, list) else None,
        mtime=stat.st_mtime,
        content=body,
        version=str(meta.get("version", "")),
        linked_files=linked,
    )


def parse_frontmatter_only(path: Path, max_lines: int = 30, skills_dir: Path | None = None) -> SkillHeader | None:
    """Read only frontmatter (first N lines) for lightweight scanning."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
    except (OSError, UnicodeDecodeError):
        return None

    text = "".join(lines)
    if not text.startswith("---"):
        return None

    meta, _ = parse_frontmatter(text)
    if not meta.get("name") and not meta.get("description"):
        return None

    stat = path.stat()
    skill_dir = path.parent

    # Compute relative path key
    if skills_dir is not None:
        try:
            rel = skill_dir.relative_to(skills_dir)
            skill_path = str(rel).replace("\\", "/")
        except ValueError:
            skill_path = skill_dir.name
    else:
        skill_path = skill_dir.name

    hermes_meta = meta.get("metadata", {}).get("hermes", {}) if isinstance(meta.get("metadata"), dict) else {}
    tags = meta.get("tags", hermes_meta.get("tags", []))
    related = meta.get("related_skills", hermes_meta.get("related_skills", []))
    scope = meta.get("agent_scope")

    return SkillHeader(
        name=meta.get("name", skill_dir.name),
        description=meta.get("description", ""),
        skill_dir=skill_dir,
        path=skill_path,
        tags=tags if isinstance(tags, list) else [],
        related_skills=related if isinstance(related, list) else [],
        agent_scope=scope if isinstance(scope, list) else None,
        mtime=stat.st_mtime,
    )


# ── Validation ──


def validate_name(name: str) -> str | None:
    """Validate skill name or path (e.g. 'scrna-qc' or 'bioinformatics/scrna-qc')."""
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name must be ≤{MAX_NAME_LENGTH} characters."
    segments = name.split("/")
    for seg in segments:
        if not VALID_SEGMENT_RE.match(seg):
            return "Each path segment must be lowercase alphanumeric with dots, hyphens, underscores."
    return None


def validate_frontmatter(content: str) -> str | None:
    """Validate SKILL.md content has valid frontmatter. Returns error or None."""
    if not content.startswith("---"):
        return "Content must start with YAML frontmatter (---)."

    end = content.find("\n---", 3)
    if end == -1:
        return "Missing closing frontmatter delimiter (---)."

    meta, body = parse_frontmatter(content)
    if not meta.get("name"):
        return "Frontmatter must include 'name' field."
    if not meta.get("description"):
        return "Frontmatter must include 'description' field."
    desc = meta["description"]
    if isinstance(desc, str) and len(desc) > MAX_DESCRIPTION_LENGTH:
        return f"Description must be ≤{MAX_DESCRIPTION_LENGTH} characters."

    if not body.strip():
        return "Skill body (after frontmatter) cannot be empty."

    return None


def validate_content_size(content: str) -> str | None:
    """Validate content size. Returns error or None."""
    if len(content) > MAX_CONTENT_SIZE:
        return f"Content exceeds {MAX_CONTENT_SIZE} character limit ({len(content):,} chars)."
    return None


def validate_file_path(file_path: str) -> str | None:
    """Validate supporting file path. Returns error or None."""
    if ".." in Path(file_path).parts:
        return "Path traversal (..) not allowed."
    parts = Path(file_path).parts
    if not parts:
        return "File path is empty."
    if parts[0] not in ALLOWED_SUBDIRS:
        return f"File must be under one of: {', '.join(sorted(ALLOWED_SUBDIRS))}."
    return None


def security_scan(content: str) -> str | None:
    """Lightweight prompt injection scan. Returns warning or None."""
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(content)
        if match:
            return f"Potential prompt injection detected: '{match.group()}'. Skill blocked."
    return None


# ── Helpers ──


def _discover_linked_files(skill_dir: Path) -> dict[str, list[str]]:
    """Discover supporting files in skill directory."""
    linked: dict[str, list[str]] = {}
    for subdir_name in sorted(ALLOWED_SUBDIRS):
        subdir = skill_dir / subdir_name
        if subdir.is_dir():
            files = sorted(
                str(p.relative_to(subdir))
                for p in subdir.rglob("*")
                if p.is_file()
            )
            if files:
                linked[subdir_name] = files
    return linked
