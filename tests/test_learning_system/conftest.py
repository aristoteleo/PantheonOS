"""Shared fixtures for learning system tests."""

import pytest
from pathlib import Path

from pantheon.internal.learning_system.store import SkillStore


SAMPLE_SKILL_CONTENT = """\
---
name: test-skill
description: A test skill for unit tests
version: 1.0.0
tags: [test, example]
---

# Test Skill

## When to Use
When running unit tests.

## Procedure
1. Create the skill
2. Verify it works

## Pitfalls
- Don't forget the frontmatter

## Verification
- Check the file exists
"""

SAMPLE_SKILL_V2 = """\
---
name: test-skill
description: Updated test skill
version: 2.0.0
tags: [test, updated]
---

# Test Skill v2

Updated content.
"""

MINIMAL_SKILL = """\
---
name: minimal
description: Minimal skill
---

Do the thing.
"""


@pytest.fixture
def tmp_pantheon_dir(tmp_path):
    pd = tmp_path / ".pantheon"
    pd.mkdir()
    return pd


@pytest.fixture
def store(tmp_pantheon_dir):
    skills_dir = tmp_pantheon_dir / "skills"
    runtime_dir = tmp_pantheon_dir / "skills-runtime"
    return SkillStore(skills_dir, runtime_dir)


@pytest.fixture
def store_with_skill(store):
    """Store with one skill already created."""
    store.create_skill("test-skill", SAMPLE_SKILL_CONTENT)
    return store


@pytest.fixture
def runtime_config():
    return {
        "enabled": True,
        "model": "gpt-4o-mini",
        "extract_enabled": True,
        "extract_model": "gpt-4o-mini",
        "extract_nudge_interval": 5,
        "disabled_skills": [],
    }
