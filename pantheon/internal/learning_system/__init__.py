"""
Learning system for Pantheon Agents.

Provides cross-session procedural knowledge management with:
- SKILL.md storage with YAML frontmatter
- Atomic writes with validation and security scanning
- Progressive disclosure (list → view → supporting files)
- Background auto-extraction of skills from conversations
- Agent tools for CRUD operations
- Shared runtime for both ChatRoom and PantheonTeam
"""

from .config import DEFAULT_CONFIG, get_learning_system_config
from .extractor import SkillExtractor
from .injector import SkillInjector
from .plugin import LearningPlugin
from .runtime import LearningRuntime
from .store import SkillStore
from .toolset import SkillToolSet
from .types import SkillEntry, SkillHeader

__all__ = [
    "DEFAULT_CONFIG",
    "get_learning_system_config",
    "LearningPlugin",
    "LearningRuntime",
    "SkillEntry",
    "SkillExtractor",
    "SkillHeader",
    "SkillInjector",
    "SkillStore",
    "SkillToolSet",
]
