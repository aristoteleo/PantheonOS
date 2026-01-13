"""
ACE Skill Loader module for Pantheon.

This module provides functionality to load and merge skills from multiple sources:
1. skillbook.json (auto-learned skills)
2. SKILLS.md (user-defined simple rules)
3. skills/*.md files (user-defined detailed skills with front_matter)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import frontmatter

from pantheon.utils.log import logger
from .skillbook import Skill, Skillbook


# ===========================================================================
# Helper Functions
# ===========================================================================


def _get_relative_path(file_path: Path, base_dir: Path) -> Path:
    """Get relative path from base directory (skills_dir)."""
    try:
        return file_path.relative_to(base_dir)
    except ValueError:
        return Path(file_path.name)


def parse_front_matter(file_path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Parse YAML front matter from a Markdown file using python-frontmatter.
    
    Args:
        file_path: Path to the Markdown file
    
    Returns:
        Tuple of (front_matter dict or None, body content)
    """
    try:
        post = frontmatter.load(file_path)
        if post.metadata:
            return dict(post.metadata), post.content
        return None, post.content
    except Exception as e:
        logger.warning(f"Failed to parse front matter in {file_path}: {e}")
        return None, ""


# ===========================================================================
# Section Normalization
# ===========================================================================

# Known section name normalization (e.g., "User Rules" -> "user_rules")
KNOWN_SECTIONS = {
    "user rules": "user_rules",
    "strategies": "strategies",
    "patterns": "patterns",
    "workflows": "workflows",
}


def _normalize_section(section_name: str) -> str:
    """Normalize section name: use known mapping or lowercase with underscores."""
    return KNOWN_SECTIONS.get(section_name, section_name.replace(" ", "_"))


# ===========================================================================
# SKILLS.md Parsing
# ===========================================================================


def parse_skills_md(file_path: Path, skills_dir: Path) -> List[Skill]:
    """
    Parse SKILLS.md file to extract simple rule-based skills.
    
    Args:
        file_path: Path to SKILLS.md
        skills_dir: Base skills directory (for sources)
    
    Returns:
        List of Skill objects
    """
    if not file_path.exists():
        return []
    
    _, body = parse_front_matter(file_path)
    
    skills = []
    current_section = "strategies"
    skill_counter: Dict[str, int] = {}
    relative_path = _get_relative_path(file_path, skills_dir)
    
    # Track multi-line comment state
    in_comment_block = False
    
    for line in body.split("\n"):
        stripped = line.strip()
        
        # Handle multi-line HTML comment blocks
        if "<!--" in line:
            in_comment_block = True
        if in_comment_block:
            if "-->" in line:
                in_comment_block = False
            continue
        
        # Section header (only valid ## headers outside comments)
        if stripped.startswith("## "):
            current_section = _normalize_section(stripped[3:].strip().lower())
            continue
        
        # Rule item
        if stripped.startswith("- "):
            content = stripped[2:].strip()
            if not content:
                continue
            
            # Generate unique ID
            prefix = current_section[:3]
            skill_counter[prefix] = skill_counter.get(prefix, 0) + 1
            skill_id = f"user-{prefix}-{skill_counter[prefix]:03d}"
            
            # SKILLS.md rules: short content + sources for traceability
            # Special case: has both content and sources
            # Display logic will NOT render sources for these
            skills.append(Skill(
                id=skill_id,
                section=current_section,
                content=content,
                sources=[],  # No sources needed for simple rules
                type="user",  # User-defined skill from SKILLS.md
            ))
    
    return skills


# ===========================================================================
# Skill File Scanning
# ===========================================================================


def scan_skill_files(skills_dir: Path) -> List[Path]:
    """
    Recursively scan skills directory for .md files.
    
    Skips hidden files/directories and SKILLS.md in root.
    """
    if not skills_dir.exists():
        return []
    
    result = []
    for path in skills_dir.rglob("*.md"):
        # Get relative path from skills_dir (not from cwd)
        try:
            rel_path = path.relative_to(skills_dir)
        except ValueError:
            continue
        
        # Skip hidden files/dirs (check relative path parts only)
        if any(p.startswith(('.', '_')) for p in rel_path.parts):
            continue
        
        # Skip SKILLS.md in root (not in subdirs)
        if rel_path.name == "SKILLS.md" and len(rel_path.parts) == 1:
            continue
        
        result.append(path)
    
    return result


def parse_skill_from_file(file_path: Path, skills_dir: Path) -> Optional[Skill]:
    """
    Parse a skill file and create a Skill object from its front matter.
    
    Requires 'id' in front matter. Description is optional but recommended.
    
    For file-based skills (with sources):
    - content = "" (empty - full content stays in source file)
    - description = from front matter (recommended for display)
    - sources = [this file] (full content accessed via source)
    
    This ensures:
    - skillbook.json stays lightweight (no file content duplication)
    - Full content is read from source files when needed
    - All data stored as-is, no conversion
    """
    front_matter, _body = parse_front_matter(file_path)
    
    if not front_matter:
        return None
    
    skill_id = front_matter.get("id")
    if not skill_id:
        return None
    
    relative_path = _get_relative_path(file_path, skills_dir)
    
    # For file-based skills: content is None, full content in source file
    return Skill(
        id=skill_id,
        section=front_matter.get("section", "workflows"),
        content=None,  # None - full content in source file
        description=front_matter.get("description"),  # Optional summary from front matter
        type=front_matter.get("type", "user"),
        sources=[str(relative_path)],  # Source file contains full content
        tags=front_matter.get("tags", []),
        learned_from=front_matter.get("learned_from"),
        created_at=front_matter.get("created_at", ""),
    )


# ===========================================================================
# Skill Loader
# ===========================================================================


class SkillLoader:
    """
    Loads and merges skills from multiple sources.

    Loading order:
    1. Load package-level upstream skills (from pantheon package)
    2. Scan skills/*.md files (user can override package skills)
    3. Parse SKILLS.md (simple rules)
    4. Cleanup orphan skills
    """

    def __init__(self, skills_dir: Path, skillbook: Skillbook):
        self.skills_dir = skills_dir
        self.skillbook = skillbook
        self._loaded_skill_ids: Set[str] = set()

    def load_and_merge(self, cleanup_orphans: bool = True) -> int:
        """Load skills from all sources and merge into skillbook."""
        loaded_count = 0
        self._loaded_skill_ids.clear()

        # 1. Load packaged template skills (shipped with pantheon)
        # These are loaded first so user skills can override them
        package_skills_dir = (
            Path(__file__).parent.parent.parent / "factory" / "templates" / "skills"
        )
        if package_skills_dir.exists():
            for file_path in scan_skill_files(package_skills_dir):
                skill = parse_skill_from_file(file_path, package_skills_dir)
                if skill:
                    self._merge_skill(skill)
                    self._loaded_skill_ids.add(skill.id)
                    loaded_count += 1
            logger.debug(f"Loaded {loaded_count} upstream skills from {package_skills_dir}")

        # 2. Scan and process user skill files (can override upstream skills)
        user_skill_count = 0
        for file_path in scan_skill_files(self.skills_dir):
            skill = parse_skill_from_file(file_path, self.skills_dir)
            if skill:
                self._merge_skill(skill)
                self._loaded_skill_ids.add(skill.id)
                user_skill_count += 1
        loaded_count += user_skill_count

        # 3. Parse SKILLS.md (simple rules)
        skills_md = self.skills_dir / "SKILLS.md"
        if skills_md.exists():
            for skill in parse_skills_md(skills_md, self.skills_dir):
                self._merge_skill(skill)
                self._loaded_skill_ids.add(skill.id)
                loaded_count += 1

        # 4. Cleanup orphan skills
        if cleanup_orphans:
            orphan_count = self._cleanup_orphan_skills()
            if orphan_count > 0:
                logger.info(f"Cleaned up {orphan_count} orphan skills")

        logger.info(f"Loaded {loaded_count} skills from files")
        return loaded_count
    
    def _merge_skill(self, skill: Skill) -> None:
        """
        Merge skill into skillbook.
        
        For file-based skills (with sources):
        - Updates description, section, sources, tags, type from file
        - NEVER updates content (stays empty)
        - Preserves ratings (helpful/harmful/neutral)
        
        For programmatic skills (without sources):
        - Updates all fields including content
        - Preserves ratings
        """
        existing = self.skillbook.get_skill(skill.id)
        
        if existing:
            # Update metadata, preserve ratings
            existing.description = skill.description
            existing.section = skill.section
            existing.sources = skill.sources
            existing.tags = skill.tags if skill.tags else existing.tags
            existing.type = skill.type  # Always use file's type
            
            # Only update content for programmatic skills (no sources)
            # File-based skills keep content empty
            if not skill.sources:
                existing.content = skill.content
        else:
            self._add_skill_to_skillbook(skill)
    
    def _add_skill_to_skillbook(self, skill: Skill) -> None:
        """Add new skill directly to skillbook internal structures."""
        self.skillbook._skills[skill.id] = skill
        self.skillbook._sections.setdefault(skill.section, []).append(skill.id)
    
    def _cleanup_orphan_skills(self) -> int:
        """Remove skills whose source files no longer exist.
        
        For skills with sources, the source files ARE the skill content -
        the 'content' field is just a description. If source files are
        deleted, the skill is considered invalid and should be removed.
        
        This applies to ALL skill types (system and user). Skills without
        sources (e.g., simple system-learned strategies) are never affected.
        """
        orphan_ids = [
            skill_id for skill_id, skill in self.skillbook._skills.items()
            if skill.sources and skill_id not in self._loaded_skill_ids
        ]
        
        for skill_id in orphan_ids:
            skill = self.skillbook._skills.pop(skill_id, None)
            if skill:
                section_list = self.skillbook._sections.get(skill.section, [])
                if skill_id in section_list:
                    section_list.remove(skill_id)
                logger.debug(f"Removed orphan skill: {skill_id}")
        
        return len(orphan_ids)


def load_skills_into_skillbook(
    skills_dir: Path, 
    skillbook: Skillbook,
    cleanup_orphans: bool = True,
) -> int:
    """Convenience function to load skills into a skillbook."""
    loader = SkillLoader(skills_dir, skillbook)
    return loader.load_and_merge(cleanup_orphans=cleanup_orphans)
