"""Configuration for the learning system."""

from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "model": "low",
    "extract_enabled": False,
    "extract_model": None,
    "extract_nudge_interval": 5,
    "disabled_skills": [],
    # Skill index injection limits
    "skill_index_max_items": 50,    # Max number of skills to list in system prompt
    "skill_index_max_tokens": 2000, # Approx token budget for skill index (~4 chars/token)
}


def resolve_model(model_tag: str) -> str:
    """Resolve a quality tag ('low', 'normal', 'high') or model name."""
    try:
        from pantheon.agent import _is_model_tag, _resolve_model_tag

        if _is_model_tag(model_tag):
            models = _resolve_model_tag(model_tag)
            if models:
                return models[0]
    except ImportError:
        pass
    return model_tag


def resolve_skills_dir(pantheon_dir: Path) -> Path:
    """.pantheon/skills/ — skill files."""
    return pantheon_dir / "skills"


def resolve_skills_runtime_dir(pantheon_dir: Path) -> Path:
    """.pantheon/skills-runtime/ — extraction state."""
    return pantheon_dir / "skills-runtime"


def get_learning_system_config(settings: Any) -> dict[str, Any]:
    """Extract learning_system config from Settings, with defaults."""
    from pantheon.internal.memory_system.config import LazyModel

    raw = settings.get_section("learning_system")
    config = {**DEFAULT_CONFIG, **raw}

    base_model = config["model"]
    if config["extract_model"] is None:
        config["extract_model"] = base_model

    # Wrap models as LazyModel for dynamic resolution
    config["model"] = LazyModel(config["model"])
    config["extract_model"] = LazyModel(config["extract_model"])

    return config
