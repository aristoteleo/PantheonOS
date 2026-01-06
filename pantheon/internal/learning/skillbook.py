"""
ACE Skillbook module for Pantheon.

This module provides the core Skillbook and Skill classes for the
Agentic Context Engineering (ACE) long-term memory system.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pantheon.utils.log import logger


# ===========================================================================
# Skillbook Injection Prompt Constants
# ===========================================================================

SKILLBOOK_USAGE_INSTRUCTIONS = """\
**How to use these strategies:**
- Review skills relevant to your current task
- **When applying a strategy, cite its ID in your reasoning** (e.g., "Following [content_extraction-00001], I will extract the title...")
  - Citations enable precise tracking of strategy effectiveness
  - Makes reasoning transparent and auditable
  - Improves learning quality through accurate attribution
- Prioritize strategies with high success rates (helpful > harmful)
- Apply strategies when they match your context
- Adapt general strategies to your specific situation
- Learn from both successful patterns and failure avoidance
**Important:** These are learned patterns, not rigid rules. Use judgment.
"""

SKILL_LOADING_GUIDANCE = """\
## 🔧 Skill Loading

When a user message starts with `/` followed by a skill ID (e.g., `/scrna-workflow`):

1. **Confirm loading**: Tell the user you are loading that skill
2. **Read details**: If the skill content contains a file path reference (e.g., `see skills/xxx.md`), use tools to read the full content
3. **Execute**: Follow the skill's guidance for the subsequent task

**Example**:
- User input: `/scrna-workflow`
- Match: `[scrna-workflow] Standard scRNA-seq analysis workflow (see skills/omics/scrna.md)`
- Action: Read `.pantheon/skills/omics/scrna.md` and follow its instructions
"""

SKILLBOOK_HEADER = """\
## 📚 Available Strategic Knowledge (Learned from Experience)
The following strategies have been learned from previous task executions.
Each skill shows its success rate: (stats: +helpful / -harmful / ~neutral)
"""

USER_RULES_HEADER = """\
## 📌 User Rules (MUST FOLLOW)

These are explicit preferences set by the user. Apply them unless there's a strong reason not to.
"""


def _format_skillbook_for_injection(
    user_rules_text: str,
    strategies_text: str,
    include_loading_guidance: bool = True,
) -> str:
    """Format skillbook content for injection into agent system prompt."""
    parts = []

    # User rules section (highest priority, MUST follow)
    if user_rules_text:
        parts.append(USER_RULES_HEADER)
        parts.append(user_rules_text)
        parts.append("")

    # Other learned skills
    if strategies_text:
        parts.append(SKILLBOOK_HEADER)
        parts.append(strategies_text)
        parts.append("")
        parts.append(SKILLBOOK_USAGE_INSTRUCTIONS)

    # Skill loading guidance (always include if we have any skills)
    if include_loading_guidance and (user_rules_text or strategies_text):
        parts.append("")
        parts.append(SKILL_LOADING_GUIDANCE)

    return "\n".join(parts).strip()


@dataclass
class Skill:
    """Single skillbook entry representing a learned strategy or insight."""

    id: str  # Unique identifier, also used for /xxx trigger
    section: str  # user_rules | strategies | patterns | workflows | guidelines | mistakes
    content: str  # Full skill content (no length limit)
    helpful: int = 0
    harmful: int = 0
    neutral: int = 0
    agent_scope: str = "global"  # "global" | specific agent name

    # Source identification
    type: Optional[str] = None  # "system" = auto-learned, None/other = user-defined
    sources: List[str] = field(default_factory=list)  # Source files (relative to skills dir)
    # Note: sources[0] is the primary file (with front matter for user-defined skills)
    
    # Short summary for long content (used in prompt injection)
    description: Optional[str] = None

    # Optional metadata
    tags: List[str] = field(default_factory=list)
    learned_from: Optional[str] = None  # Learning source (chat ID, trajectory ID)

    # Timestamps
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: Literal["active", "invalid"] = "active"

    def tag(self, tag: str, increment: int = 1) -> None:
        """Apply a tag (helpful/harmful/neutral) to this skill."""
        if tag not in ("helpful", "harmful", "neutral"):
            raise ValueError(f"Unsupported tag: {tag}")
        current = getattr(self, tag)
        setattr(self, tag, current + increment)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def is_system(self) -> bool:
        """Check if this skill was auto-learned by the system."""
        return self.type == "system"

    def is_user_defined(self) -> bool:
        """Check if this skill was defined by the user (from files)."""
        return self.type == "user"

    @property
    def primary_source(self) -> Optional[str]:
        """Get the primary source file (first .md file, or first file)."""
        if not self.sources:
            return None
        # Prefer .md files as primary
        for s in self.sources:
            if s.endswith('.md'):
                return s
        return self.sources[0]

    def to_prompt_dict(self) -> Dict[str, Any]:
        """Return dict with only LLM-relevant fields."""
        return {
            "id": self.id,
            "section": self.section,
            "content": self.content,
            "helpful": self.helpful,
            "harmful": self.harmful,
        }


class Skillbook:
    """
    Structured context store for ACE long-term memory.

    Manages a collection of learned skills that can be injected into
    agent prompts and updated based on agent performance.
    
    Unified storage:
    - skillbook.json: Learned skill metadata (JSON)
    - skills/*.md: User-defined skill files (Markdown)
    """

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        skillbook_path: Optional[Path] = None,
        max_skills_per_section: Optional[int] = None,
        max_content_length: Optional[int] = None,
        enable_agent_scope: bool = False,
        auto_load: bool = True,
    ):
        """
        Initialize Skillbook.
        
        Args:
            skills_dir: Directory for skill source files. If None, uses settings.skills_dir.
            skillbook_path: Path to skillbook.json. If None, uses settings.
            max_skills_per_section: Maximum skills per section before eviction.
            max_content_length: Maximum content length per skill.
            enable_agent_scope: Enable agent-specific skill scoping.
            auto_load: If True, automatically load from JSON and merge files.
        """
        # Internal state
        self._skills: Dict[str, Skill] = {}
        self._sections: Dict[str, List[str]] = {}
        self._next_id = 0
        
        # Configuration
        self.enable_agent_scope = enable_agent_scope
        
        # Will be set after settings resolution
        self.max_skills_per_section = 30
        self.max_content_length = 2000
        
        # Resolve paths from settings if not provided
        if skills_dir is None or skillbook_path is None:
            from pantheon.settings import get_settings
            settings = get_settings()
            learning_config = settings.get_learning_config()
            if skills_dir is None:
                skills_dir = settings.skills_dir
            if skillbook_path is None:
                skillbook_path = learning_config.get("skillbook_path")
            
            # Load limits from settings if not provided
            if max_skills_per_section is None:
                max_skills_per_section = learning_config.get("max_skills_per_section", 30)
            if max_content_length is None:
                max_content_length = learning_config.get("max_content_length", 2000)
        
        # Build defaults if still None (e.g. settings load failed or not used)
        self.max_skills_per_section = max_skills_per_section if max_skills_per_section is not None else 30
        self.max_content_length = max_content_length if max_content_length is not None else 2000
        
        self.skills_dir = Path(skills_dir) if skills_dir else None
        self.skillbook_path = Path(skillbook_path) if skillbook_path else None
        
        # Ensure directories exist
        if self.skills_dir:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        if self.skillbook_path:
            self.skillbook_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Auto-load if requested
        if auto_load and self.skillbook_path:
            self.load()

    def __repr__(self) -> str:
        return f"Skillbook(skills={len(self._skills)}, sections={list(self._sections.keys())})"

    # ------------------------------------------------------------------ #
    # CRUD Operations
    # ------------------------------------------------------------------ #

    def add_skill(
        self,
        section: str,
        content: str,
        agent_scope: str = "global",
        skill_id: Optional[str] = None,
        description: Optional[str] = None,
        sources: Optional[List[str]] = None,
    ) -> Optional[Skill]:
        """
        Add a new skill to the skillbook (used by ACE learning pipeline).

        Args:
            section: Skill category (strategies, workflows, etc.)
            content: Full skill content (no length limit)
            agent_scope: Agent scope for the skill
            skill_id: Optional custom ID
            description: Optional short summary for long content (max 15 words)
            sources: Optional source files (relative paths)

        Auto-conversion:
            If content > 2000 chars and sources not provided, automatically:
            1. Generate description if not provided (truncate to ~15 words)
            2. Write content to source file with front matter
            3. Set sources to the generated file

        Returns None if section is full, otherwise returns the created skill.
        Note: Skills added through this method are marked as type='system'
        (auto-learned), distinguishing them from user-defined file skills.
        """
        # Check section limit
        section_skills = self._sections.get(section, [])
        if len(section_skills) >= self.max_skills_per_section:
            # Try to evict worst skill
            evicted = self._evict_worst_skill(section)
            if not evicted:
                logger.warning(f"Section '{section}' is full, cannot add skill")
                return None

        skill_id = skill_id or self._generate_id(section)
        
        
        # Auto-conversion: long content without sources -> write to source file
        CONTENT_THRESHOLD = self.max_content_length  # characters
        final_sources = sources or []
        final_description = description
        final_content = content  # Default: store full content
        
        if len(content) > CONTENT_THRESHOLD and not sources:
            # Auto-generate description if not provided (truncate to ~15 words)
            if not final_description:
                words = content.split()[:15]
                final_description = " ".join(words)
                if len(words) == 15:
                    final_description += "..."
            
            # Write full content to source file with front matter
            source_file = self._write_content_to_source(
                skill_id, section, content, final_description
            )
            if source_file:
                final_sources = [source_file]
                # Store description as content (keep skillbook.json small)
                final_content = final_description
                logger.info(f"Auto-converted long content to source: {source_file}")
        
        skill = Skill(
            id=skill_id,
            section=section,
            content=final_content,  # description if converted, full content otherwise
            agent_scope=agent_scope,
            type="system",  # Mark as auto-learned by ACE pipeline
            description=final_description,
            sources=final_sources,
        )
        self._skills[skill_id] = skill
        self._sections.setdefault(section, []).append(skill_id)
        return skill

    def update_skill(
        self,
        skill_id: str,
        content: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Skill]:
        """
        Update an existing skill's content.
        
        Auto-conversion:
            If content > 500 chars and skill has no sources, automatically:
            1. Generate description if not provided
            2. Write content to source file with front matter
            3. Set sources to the generated file
            4. Update content to description (keep skillbook.json small)
            
        Returns None if skill not found or is user-defined.
        """
        skill = self._skills.get(skill_id)
        if skill is None:
            return None
        
        # Protect user-defined skills
        if skill.is_user_defined():
            logger.warning(f"Cannot modify user-defined skill '{skill_id}'. Edit source file directly.")
            return None
        
        if content is not None:
            CONTENT_THRESHOLD = self.max_content_length
            
            # Auto-conversion: long content + no existing sources
            if len(content) > CONTENT_THRESHOLD and not skill.sources:
                # Generate description if not provided
                final_description = description
                if not final_description:
                    words = content.split()[:15]
                    final_description = " ".join(words)
                    if len(words) == 15:
                        final_description += "..."
                
                # Write to source file
                source_file = self._write_content_to_source(
                    skill_id, skill.section, content, final_description
                )
                if source_file:
                    skill.sources = [source_file]
                    skill.content = final_description  # Store description as content
                    skill.description = final_description
                    logger.info(f"Auto-converted long content to source: {source_file}")
            else:
                # Short content or has sources: update directly
                skill.content = content
                if description:
                    skill.description = description
        
        skill.updated_at = datetime.now(timezone.utc).isoformat()
        return skill

    def tag_skill(self, skill_id: str, tag: str, increment: int = 1) -> Optional[Skill]:
        """Apply a tag to a skill."""
        skill = self._skills.get(skill_id)
        if skill is None:
            return None
        skill.tag(tag, increment)
        return skill

    def remove_skill(self, skill_id: str, soft: bool = True) -> bool:
        """
        Remove a skill from the skillbook.

        Args:
            skill_id: ID of the skill to remove
            soft: If True, mark as invalid; if False, delete entirely
            
        Returns:
            True if removed, False if not found or is user-defined
        """
        skill = self._skills.get(skill_id)
        if skill is None:
            return False
        
        # Protect user-defined skills
        if skill.is_user_defined():
            logger.warning(f"Cannot remove user-defined skill '{skill_id}'. Delete source file directly.")
            return False

        if soft:
            skill.status = "invalid"
            skill.updated_at = datetime.now(timezone.utc).isoformat()
        else:
            self._skills.pop(skill_id, None)
            section_list = self._sections.get(skill.section, [])
            self._sections[skill.section] = [
                sid for sid in section_list if sid != skill_id
            ]
            if not self._sections[skill.section]:
                del self._sections[skill.section]
        return True

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """Get a skill by ID."""
        return self._skills.get(skill_id)

    def skills(self, include_invalid: bool = False) -> List[Skill]:
        """Get all skills (active only by default)."""
        if include_invalid:
            return list(self._skills.values())
        return [s for s in self._skills.values() if s.status == "active"]

    def filter_skills(
        self,
        section: Optional[str] = None,
        tag: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[Skill]:
        """
        Filter skills by section, tag, or keyword.
        
        Args:
            section: Filter by skill section (strategies, patterns, etc.)
            tag: Filter by dominant feedback tag (helpful, harmful, neutral)
            keyword: Case-insensitive keyword search in content
            
        Returns:
            List of matching skills
        """
        skills = self.skills()
        
        if section:
            skills = [s for s in skills if s.section == section]
        
        if tag:
            def get_dominant_tag(s: Skill) -> Optional[str]:
                if s.harmful > s.helpful and s.harmful > s.neutral:
                    return "harmful"
                elif s.helpful > s.harmful and s.helpful > s.neutral:
                    return "helpful"
                elif s.neutral > 0:
                    return "neutral"
                return None
            skills = [s for s in skills if get_dominant_tag(s) == tag]
        
        if keyword:
            keyword_lower = keyword.lower()
            skills = [s for s in skills if keyword_lower in s.content.lower()]
        
        return skills

    # ------------------------------------------------------------------ #
    # Query Methods
    # ------------------------------------------------------------------ #

    def get_skills_for_agent(self, agent_name: str) -> List[Skill]:
        """
        Get skills applicable to a specific agent.

        If enable_agent_scope is False, returns all skills.
        If True, returns global skills + agent-specific skills.
        Skills are sorted by helpfulness.
        """
        if not self.enable_agent_scope:
            # Return all skills when scope filtering is disabled
            return self._sort_skills_by_helpfulness(self.skills())

        # Filter by scope
        applicable = [
            s
            for s in self.skills()
            if s.agent_scope == "global" or s.agent_scope == agent_name
        ]
        return self._sort_skills_by_helpfulness(applicable)

    def get_skills_by_section(self, section: str) -> List[Skill]:
        """Get all active skills in a section."""
        skill_ids = self._sections.get(section, [])
        return [
            self._skills[sid]
            for sid in skill_ids
            if sid in self._skills and self._skills[sid].status == "active"
        ]

    # ------------------------------------------------------------------ #
    # Presentation
    # ------------------------------------------------------------------ #

    def _format_skill_content(self, skill: Skill) -> str:
        """
        Format skill content for display in prompts.

        Priority:
        1. Use description if available (for long content)
        2. Truncate content if too long
        3. Add file reference if sources exist
        """
        # Include stats: (stats: +5/-0/~2)
        stats = f"(stats: +{skill.helpful}/-{skill.harmful}/~{skill.neutral})"
        
        # Determine display content
        if skill.description:
            # Use description for summary
            display = skill.description
        elif len(skill.content) > 500:
            # Truncate long content
            display = skill.content[:500] + "..."
        else:
            display = skill.content
        
        content = f"{stats} {display}"

        # Add file reference for file-based skills (exclude SKILLS.md)
        if skill.sources:
            primary = skill.primary_source
            if primary and not primary.endswith("SKILLS.md"):
                from pantheon.settings import get_settings
                if len(skill.sources) == 1:
                    # Single file: show full path
                    abs_path = get_settings().skills_dir / primary
                    content = f"{content} (see `{abs_path}` for details)"
                else:
                    # Multiple files: show directory or list
                    parts = primary.split('/')
                    if len(parts) > 1:
                        # Has subdirectory (e.g., "skill-id/workflow.md")
                        skill_dir = get_settings().skills_dir / parts[0]
                        content = f"{content} (see `{skill_dir}/` for details)"
                    else:
                        # Multiple files at root
                        abs_paths = [str(get_settings().skills_dir / s) for s in skill.sources]
                        content = f"{content} (see {', '.join(f'`{p}`' for p in abs_paths)} for details)"

        return content

    def as_prompt(self, agent_name: str) -> str:
        """
        Format skillbook as a prompt section for LLM injection.

        User rules (user_rules section) are presented as MUST FOLLOW rules.
        Other skills are presented as learned strategies with usage instructions.
        Skills are sorted by helpfulness (helpful - harmful), highest first.
        Returns empty string if no applicable skills.
        """

        skills = self.get_skills_for_agent(agent_name)
        if not skills:
            return ""

        # Separate user_rules from other sections
        user_rules = [s for s in skills if s.section == "user_rules"]
        other_skills = [s for s in skills if s.section != "user_rules"]

        # Format user_rules
        user_rules_text = ""
        if user_rules:
            user_rules_text = "\n".join(
                f"[{s.id}] {self._format_skill_content(s)}" for s in user_rules
            )

        # Format other skills by section
        strategies_text = ""
        if other_skills:
            sections: Dict[str, List[Skill]] = {}
            for skill in other_skills:
                sections.setdefault(skill.section, []).append(skill)

            parts = []
            for section_name in sorted(sections.keys()):
                section_skills = sections[section_name]
                parts.append(f"### {section_name.upper()}")
                for skill in section_skills:
                    parts.append(f"[{skill.id}] {self._format_skill_content(skill)}")
                parts.append("")
            strategies_text = "\n".join(parts).strip()

        return _format_skillbook_for_injection(user_rules_text, strategies_text)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save(self, path: Optional[str] = None) -> None:
        """Save skillbook to JSON file."""
        if path:
            save_path = Path(path)
        elif self.skillbook_path:
            save_path = self.skillbook_path
        else:
            logger.warning("No path specified for skillbook save")
            return

        save_path.parent.mkdir(parents=True, exist_ok=True)
        with save_path.open("w", encoding="utf-8") as f:
            json.dump(self._to_dict(), f, ensure_ascii=False, indent=2)
        logger.debug(f"Skillbook saved to {save_path}")

    def load(self, path: Optional[str] = None) -> None:
        """Load skillbook from JSON file and merge skills from files.
        
        Args:
            path: Optional path override. Uses self.skillbook_path if not provided.
        """
        load_path = Path(path) if path else self.skillbook_path
        if not load_path:
            logger.warning("No path specified for skillbook load")
            return
            
        if not load_path.exists():
            logger.info(f"Skillbook file not found: {load_path}, starting fresh")
        else:
            try:
                with load_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                self._from_dict(data)
                logger.info(f"Loaded skillbook with {len(self._skills)} skills from {load_path}")
            except Exception as e:
                logger.error(f"Failed to load skillbook: {e}")
        
        # Merge user-defined skills from files
        self._merge_from_files()
    
    def _merge_from_files(self) -> int:
        """Merge user-defined skills from skills/*.md files.
        
        Returns:
            Number of skills merged
        """
        if not self.skills_dir or not self.skills_dir.exists():
            return 0
        
        from .skill_loader import load_skills_into_skillbook
        loaded = load_skills_into_skillbook(self.skills_dir, self)
        if loaded > 0:
            logger.info(f"Merged {loaded} skills from {self.skills_dir}")
        return loaded

    def _to_dict(self) -> Dict[str, Any]:
        """Serialize skillbook to dictionary."""
        return {
            "skills": {
                skill_id: asdict(skill) for skill_id, skill in self._skills.items()
            },
            "sections": self._sections,
            "next_id": self._next_id,
        }

    def _from_dict(self, data: Dict[str, Any]) -> None:
        """Deserialize skillbook from dictionary."""
        skills_data = data.get("skills", {})
        for skill_id, skill_dict in skills_data.items():
            # Handle backwards compatibility for new fields
            if "status" not in skill_dict:
                skill_dict["status"] = "active"
            if "type" not in skill_dict:
                skill_dict["type"] = None
            if "sources" not in skill_dict:
                skill_dict["sources"] = []
            if "tags" not in skill_dict:
                skill_dict["tags"] = []
            if "learned_from" not in skill_dict:
                skill_dict["learned_from"] = None
            if "description" not in skill_dict:
                skill_dict["description"] = None

            self._skills[skill_id] = Skill(**skill_dict)

        sections_data = data.get("sections", {})
        self._sections = {section: list(ids) for section, ids in sections_data.items()}
        self._next_id = data.get("next_id", 0)

    # ------------------------------------------------------------------ #
    # Internal Helpers
    # ------------------------------------------------------------------ #

    def _generate_id(self, section: str) -> str:
        """Generate unique skill ID."""
        self._next_id += 1
        # Use first word of section as prefix
        prefix = re.sub(r"[^a-z]", "", section.split()[0].lower())[:3]
        return f"{prefix}-{self._next_id:05d}"

    def _sort_skills_by_helpfulness(self, skills: List[Skill]) -> List[Skill]:
        """Sort skills by (helpful - harmful), highest first."""
        return sorted(skills, key=lambda s: (s.helpful - s.harmful), reverse=True)

    def _evict_worst_skill(self, section: str) -> bool:
        """
        Evict the worst skill from a section if it has negative score.

        Returns True if a skill was evicted, False otherwise.
        """
        section_skills = self.get_skills_by_section(section)
        if not section_skills:
            return False

        worst = min(section_skills, key=lambda s: s.helpful - s.harmful)
        if worst.harmful > worst.helpful:
            self.remove_skill(worst.id, soft=False)
            logger.info(f"Evicted low-quality skill: {worst.id}")
            return True
        return False

    def stats(self) -> Dict[str, Any]:
        """Get detailed skillbook statistics."""
        active_skills = self.skills()

        # Section breakdown
        section_stats = {}
        for section, skill_ids in self._sections.items():
            active_in_section = [
                sid
                for sid in skill_ids
                if sid in self._skills and self._skills[sid].status == "active"
            ]
            section_stats[section] = len(active_in_section)

        # Calculate net score
        total_helpful = sum(s.helpful for s in self._skills.values())
        total_harmful = sum(s.harmful for s in self._skills.values())

        return {
            "total_skills": len(self._skills),
            "active_skills": len(active_skills),
            "sections": len(self._sections),
            "section_breakdown": section_stats,
            "tags": {
                "helpful": total_helpful,
                "harmful": total_harmful,
                "neutral": sum(s.neutral for s in self._skills.values()),
            },
            "net_score": total_helpful - total_harmful,
        }

    def summary_line(self) -> str:
        """Get a one-line summary of the skillbook for logging."""
        s = self.stats()
        sections_str = (
            ", ".join(f"{k}:{v}" for k, v in s["section_breakdown"].items()) or "empty"
        )
        return (
            f"Skillbook: {s['active_skills']} skills | "
            f"Sections: [{sections_str}] | "
            f"Score: +{s['tags']['helpful']}/-{s['tags']['harmful']}"
        )

    # ------------------------------------------------------------------ #
    # File Management Methods
    # ------------------------------------------------------------------ #

    def _generate_front_matter(
        self,
        skill_id: str,
        section: str,
        description: str,
        is_new: bool = True,
    ) -> str:
        """
        Generate consistent YAML front matter for skill files.
        
        This is the single source of truth for front matter format,
        ensuring consistency between auto-conversion and sync operations.
        
        Args:
            skill_id: Skill ID
            section: Skill section
            description: Short description
            is_new: If True, include created_at timestamp
            
        Returns:
            YAML front matter string (including --- delimiters)
        """
        import yaml
        now = datetime.now(timezone.utc).isoformat()
        
        fm = {
            "id": skill_id,
            "description": description,
            "section": section,
            "type": "system",
        }
        if is_new:
            fm["created_at"] = now
        fm["updated_at"] = now
        
        yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
        return f"---\n{yaml_str}---\n"

    def _write_content_to_source(
        self,
        skill_id: str,
        section: str,
        content: str,
        description: str,
    ) -> str:
        """
        Write long content to a sources file with proper front matter.
        
        Creates a markdown file in skills_dir with YAML front matter that
        matches the skill's metadata, ensuring read/write consistency.
        
        Args:
            skill_id: Skill ID (used as filename)
            section: Skill section
            content: Full content to write
            description: Short description for front matter
            
        Returns:
            Relative path to the created file
        """
        if not self.skills_dir:
            logger.warning("skills_dir not set, cannot write content to source")
            return ""
        
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        
        # Use unified front matter generation
        front_matter = self._generate_front_matter(skill_id, section, description, is_new=True)
        file_content = front_matter + "\n" + content
        
        # Write to file
        file_name = f"{skill_id}.md"
        file_path = self.skills_dir / file_name
        file_path.write_text(file_content, encoding="utf-8")
        logger.debug(f"Wrote content to source: {file_path}")
        
        return file_name

    def copy_sources(self, skill_id: str, sources: List[str]) -> List[str]:
        """
        Copy source files to skills directory.
        
        Single file: skills/filename.md
        Multiple files: skills/{skill_id}/filename.md
        
        Args:
            skill_id: Skill ID for directory naming (when multiple files)
            sources: List of source file paths to copy
            
        Returns:
            List of relative paths in skills directory
        """
        import shutil
        
        if not sources or not self.skills_dir:
            return []

        relative_paths = []

        if len(sources) == 1:
            # Single file: copy directly to skills/
            src_path = Path(sources[0])
            if src_path.exists():
                dest_name = src_path.name
                dest_path = self.skills_dir / dest_name
                shutil.copy2(src_path, dest_path)
                relative_paths.append(dest_name)
                logger.debug(f"Copied {src_path} to {dest_path}")
        else:
            # Multiple files: create subdirectory
            skill_dir = self.skills_dir / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            for src in sources:
                src_path = Path(src)
                if src_path.exists():
                    dest_path = skill_dir / src_path.name
                    shutil.copy2(src_path, dest_path)
                    relative_paths.append(f"{skill_id}/{src_path.name}")
                    logger.debug(f"Copied {src_path} to {dest_path}")

        return relative_paths

    def delete_sources(self, sources: List[str]) -> None:
        """Delete source files from skills directory.
        
        Args:
            sources: List of relative paths in skills directory
        """
        if not sources or not self.skills_dir:
            return

        # Track directories to potentially clean up
        dirs_to_check = set()

        for source in sources:
            file_path = self.skills_dir / source
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Deleted {file_path}")
                # Track parent if it's a subdirectory
                if file_path.parent != self.skills_dir:
                    dirs_to_check.add(file_path.parent)

        # Clean up empty directories
        for dir_path in dirs_to_check:
            if dir_path.exists() and not any(dir_path.iterdir()):
                dir_path.rmdir()
                logger.debug(f"Removed empty directory {dir_path}")

    def sync_front_matter(
        self,
        file_path: Path,
        skill_id: Optional[str] = None,
        description: Optional[str] = None,
        section: Optional[str] = None,
    ) -> None:
        """
        Sync front matter in a skill file.
        
        Updates description, type=system, and timestamps.
        
        Args:
            file_path: Path to the skill file
            skill_id: Optional skill ID to set
            description: Optional description to set
            section: Optional section to set
        """
        import yaml
        
        if not file_path.exists():
            return

        content = file_path.read_text(encoding="utf-8")
        
        # Parse existing front matter
        fm = None
        body = content
        if content.startswith("---"):
            try:
                end_idx = content.index("---", 3)
                yaml_content = content[3:end_idx].strip()
                body = content[end_idx + 3:].lstrip("\n")
                fm = yaml.safe_load(yaml_content)
            except (ValueError, yaml.YAMLError):
                pass

        if fm is None:
            # Create new front matter using unified generator
            if description:
                front_matter = self._generate_front_matter(
                    skill_id=skill_id or file_path.stem,
                    section=section or "workflows",
                    description=description,
                    is_new=True,
                )
                new_content = front_matter + body
                file_path.write_text(new_content, encoding="utf-8")
        else:
            # Update existing front matter
            now = datetime.now(timezone.utc).isoformat()
            updates = {"type": "system", "updated_at": now}
            if description:
                updates["description"] = description
            if skill_id:
                updates["id"] = skill_id
            if section:
                updates["section"] = section
            
            fm.update(updates)
            yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
            new_content = f"---\n{yaml_str}---\n{body}"
            file_path.write_text(new_content, encoding="utf-8")

        logger.debug(f"Synced front matter in {file_path}")

