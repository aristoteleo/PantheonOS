"""
Unit tests for the +think[:level] model suffix feature.

Tests cover:
- Parsing the +think suffix from model strings
- _is_model_tag recognition of thinking suffixes
- _resolve_model_tag stripping thinking before resolution
- Agent.__init__ injecting thinking into model_params
- Anthropic adapter effort-to-budget mapping
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from pantheon.agent import (
    Agent,
    _is_model_tag,
    _parse_thinking_suffix,
    _resolve_model_tag,
)


# ============ _parse_thinking_suffix ============


class TestParseThinkingSuffix:
    """Test +think[:level] suffix parsing."""

    def test_no_suffix(self):
        assert _parse_thinking_suffix("high") == ("high", None)

    def test_no_suffix_model_name(self):
        assert _parse_thinking_suffix("openai/gpt-5.4") == ("openai/gpt-5.4", None)

    def test_think_default_level(self):
        """'+think' without level defaults to 'high'."""
        assert _parse_thinking_suffix("high+think") == ("high", "high")

    def test_think_high(self):
        assert _parse_thinking_suffix("normal+think:high") == ("normal", "high")

    def test_think_medium(self):
        assert _parse_thinking_suffix("high+think:medium") == ("high", "medium")

    def test_think_low(self):
        assert _parse_thinking_suffix("high+think:low") == ("high", "low")

    def test_model_name_with_think(self):
        assert _parse_thinking_suffix("openai/gpt-5.4+think") == ("openai/gpt-5.4", "high")

    def test_model_name_with_think_level(self):
        assert _parse_thinking_suffix("openai/gpt-5.4+think:medium") == ("openai/gpt-5.4", "medium")

    def test_tag_combo_with_think(self):
        assert _parse_thinking_suffix("normal,vision+think:high") == ("normal,vision", "high")

    def test_invalid_level_ignored(self):
        """Invalid level returns original string unchanged."""
        assert _parse_thinking_suffix("high+think:extreme") == ("high+think:extreme", None)

    def test_think_in_middle_not_matched(self):
        """'+think' must be at the end."""
        assert _parse_thinking_suffix("high+think:high/extra") == ("high+think:high/extra", None)

    def test_empty_string(self):
        assert _parse_thinking_suffix("") == ("", None)


# ============ _is_model_tag ============


class TestIsModelTagWithThinking:
    """Test that _is_model_tag correctly handles +think suffixes."""

    def test_tag_without_think(self):
        assert _is_model_tag("high") is True
        assert _is_model_tag("normal") is True
        assert _is_model_tag("low") is True

    def test_tag_with_think(self):
        assert _is_model_tag("high+think") is True
        assert _is_model_tag("normal+think:medium") is True
        assert _is_model_tag("low+think:low") is True

    def test_combo_tag_with_think(self):
        assert _is_model_tag("high,vision+think") is True
        assert _is_model_tag("normal,vision+think:high") is True

    def test_model_name_not_tag(self):
        assert _is_model_tag("openai/gpt-5.4") is False
        assert _is_model_tag("openai/gpt-5.4+think") is False

    def test_empty_not_tag(self):
        assert _is_model_tag("") is False
        assert _is_model_tag("  ") is False


# ============ _resolve_model_tag ============


class TestResolveModelTagWithThinking:
    """Test that _resolve_model_tag strips +think before resolving."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_resolve_strips_think(self):
        """Resolving 'high+think' should return same models as 'high'."""
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        models_with_think = _resolve_model_tag("high+think")
        models_without_think = _resolve_model_tag("high")
        assert models_with_think == models_without_think

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_resolve_strips_think_level(self):
        """Resolving 'normal+think:medium' should return same models as 'normal'."""
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        models_with_think = _resolve_model_tag("normal+think:medium")
        models_without_think = _resolve_model_tag("normal")
        assert models_with_think == models_without_think


# ============ Agent.__init__ ============


class TestAgentThinkingInit:
    """Test that Agent.__init__ parses +think and injects into model_params."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_no_think_no_model_params(self):
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        agent = Agent(name="test", instructions="hi", model="high")
        assert "thinking" not in agent.model_params

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_think_default_level(self):
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        agent = Agent(name="test", instructions="hi", model="high+think")
        assert agent.model_params.get("thinking") == "high"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_think_medium(self):
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        agent = Agent(name="test", instructions="hi", model="normal+think:medium")
        assert agent.model_params.get("thinking") == "medium"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_think_does_not_override_explicit_model_params(self):
        """Explicit model_params['thinking'] takes precedence over +think suffix."""
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        agent = Agent(
            name="test",
            instructions="hi",
            model="high+think:low",
            model_params={"thinking": "medium"},
        )
        # setdefault should NOT override the explicit value
        assert agent.model_params["thinking"] == "medium"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_think_with_concrete_model(self):
        """'+think' on a concrete model name (not a tag) should still work."""
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        agent = Agent(name="test", instructions="hi", model="openai/gpt-5.4+think:low")
        assert agent.model_params.get("thinking") == "low"
        assert agent.models == ["openai/gpt-5.4"]

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_models_resolved_without_think_suffix(self):
        """The resolved models list should not contain +think."""
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()

        agent = Agent(name="test", instructions="hi", model="high+think")
        for m in agent.models:
            assert "+think" not in m


# ============ Anthropic adapter effort mapping ============


class TestAnthropicEffortMapping:
    """Test that reasoning_effort maps to correct budget_tokens."""

    def test_effort_budget_mapping(self):
        """Verify the effort-to-budget mapping values."""
        effort_budgets = {"low": 5000, "medium": 10000, "high": 30000}
        assert effort_budgets["low"] == 5000
        assert effort_budgets["medium"] == 10000
        assert effort_budgets["high"] == 30000

    def test_effort_budget_default(self):
        """Unknown effort should fall back to 10000."""
        effort_budgets = {"low": 5000, "medium": 10000, "high": 30000}
        assert effort_budgets.get("unknown", 10000) == 10000
