"""
Unit tests for the ModelSelector module.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from pantheon.utils.model_selector import (
    CAPABILITY_MAP,
    DEFAULT_PROVIDER_MODELS,
    DEFAULT_PROVIDER_PRIORITY,
    ULTIMATE_FALLBACK,
    FALLBACK_TAG,
    QUALITY_TAGS,
    ModelSelector,
    get_default_model,
    get_model_selector,
    reset_model_selector,
)


@pytest.fixture
def mock_settings():
    """Create a mock Settings object."""
    settings = MagicMock()
    # Mock get() to return the default value when key is not found
    settings.get.side_effect = lambda key, default=None: default
    return settings


@pytest.fixture
def selector(mock_settings):
    """Create a ModelSelector with mock settings."""
    return ModelSelector(mock_settings)


class TestModelSelectorBasics:
    """Test basic ModelSelector functionality."""

    def test_quality_tags_defined(self):
        """Test that quality tags are defined."""
        assert "high" in QUALITY_TAGS
        assert "normal" in QUALITY_TAGS
        assert "low" in QUALITY_TAGS

    def test_capability_map_defined(self):
        """Test that capability map has expected entries."""
        assert "vision" in CAPABILITY_MAP
        assert "reasoning" in CAPABILITY_MAP
        assert "tools" in CAPABILITY_MAP
        assert CAPABILITY_MAP["vision"] == "supports_vision"

    def test_default_provider_priority(self):
        """Test that default provider priority is set."""
        assert len(DEFAULT_PROVIDER_PRIORITY) > 0
        assert "openai" in DEFAULT_PROVIDER_PRIORITY
        assert "anthropic" in DEFAULT_PROVIDER_PRIORITY

    def test_default_provider_models(self):
        """Test that default provider models are configured."""
        assert "openai" in DEFAULT_PROVIDER_MODELS
        assert "anthropic" in DEFAULT_PROVIDER_MODELS

        # Check structure
        openai_models = DEFAULT_PROVIDER_MODELS["openai"]
        assert "high" in openai_models
        assert "normal" in openai_models
        assert "low" in openai_models

        # Check that each level is a list
        assert isinstance(openai_models["high"], list)
        assert len(openai_models["high"]) > 0


class TestProviderDetection:
    """Test provider detection from environment."""

    def test_no_providers_returns_none(self, selector):
        """Test that no providers returns None."""
        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value=set(),
        ):
            result = selector.detect_available_provider()
            assert result is None

    def test_single_provider_detected(self, selector):
        """Test detection with single provider."""
        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai"},
        ):
            result = selector.detect_available_provider()
            assert result == "openai"

    def test_priority_order_respected(self, mock_settings):
        """Test that provider priority is respected."""
        # Override side_effect to return custom priority
        mock_settings.get.side_effect = lambda key, default=None: (
            ["anthropic", "openai"] if key == "models.provider_priority" else default
        )
        selector = ModelSelector(mock_settings)

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai", "anthropic"},
        ):
            result = selector.detect_available_provider()
            # anthropic is first in priority
            assert result == "anthropic"

    def test_fallback_to_available_not_in_priority(self, mock_settings):
        """Test fallback to available provider not in priority list."""
        mock_settings.get.side_effect = lambda key, default=None: (
            ["openai", "anthropic"] if key == "models.provider_priority" else default
        )
        selector = ModelSelector(mock_settings)

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"deepseek"},  # Not in priority list
        ):
            result = selector.detect_available_provider()
            assert result == "deepseek"


class TestModelResolution:
    """Test model tag resolution."""

    def test_resolve_quality_tag(self, mock_settings):
        """Test resolving a simple quality tag."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        models = selector.resolve_model("normal")
        assert isinstance(models, list)
        assert len(models) > 0
        # Should return OpenAI normal models
        assert all("openai" in m for m in models)

    def test_resolve_high_quality(self, mock_settings):
        """Test resolving high quality tag."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        models = selector.resolve_model("high")
        assert isinstance(models, list)
        assert len(models) > 0

    def test_resolve_no_provider_returns_fallback(self, mock_settings):
        """Test that no provider returns fallback model."""
        selector = ModelSelector(mock_settings)

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value=set(),
        ):
            models = selector.resolve_model("normal")
            assert models == [ULTIMATE_FALLBACK]

    def test_resolve_combined_tags(self, mock_settings):
        """Test resolving combined quality + capability tags."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        # Mock capability check to return True for vision
        with patch.object(selector, "_check_model_capability", return_value=True):
            models = selector.resolve_model("high,vision")
            assert isinstance(models, list)
            assert len(models) > 0

    def test_resolve_unknown_capability_returns_quality_models(self, mock_settings):
        """Test that unknown capability tag falls back to quality models."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        # "unknown" is not in CAPABILITY_MAP, should be ignored
        models = selector.resolve_model("normal,unknown_capability")
        assert isinstance(models, list)
        # Should just return normal models since unknown_capability is not in CAPABILITY_MAP
        assert len(models) > 0


class TestCapabilityFiltering:
    """Test capability-based model filtering."""

    def test_check_model_capability_unknown_capability(self, selector):
        """Test checking unknown capability returns False."""
        result = selector._check_model_capability("openai/gpt-4o", "unknown")
        assert result is False

    def test_find_models_with_capability_unknown(self, selector):
        """Test finding models with unknown capability."""
        result = selector.find_models_with_capability("unknown")
        assert result == []


class TestUserConfiguration:
    """Test user configuration overrides."""

    def test_user_priority_overrides_default(self, mock_settings):
        """Test that user priority configuration is used."""
        # User has configured google first
        mock_settings.get.side_effect = lambda key, default=None: (
            ["google", "openai"] if key == "models.provider_priority" else default
        )

        selector = ModelSelector(mock_settings)

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai", "google"},
        ):
            result = selector.detect_available_provider()
            assert result == "google"

    def test_user_models_override_defaults(self, mock_settings):
        """Test that user model configuration overrides defaults."""
        custom_models = {
            "high": ["openai/custom-model-1"],
            "normal": ["openai/custom-model-2"],
            "low": ["openai/custom-model-3"],
        }

        mock_settings.get.side_effect = lambda key, default=None: (
            custom_models if key == "models.provider_models.openai" else default
        )

        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        models = selector._get_provider_models("openai")
        assert models["high"] == ["openai/custom-model-1"]


class TestGetDefaultModel:
    """Test the convenience function get_default_model."""

    def test_get_default_model_returns_list(self):
        """Test that get_default_model returns a list."""
        reset_model_selector()

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai"},
        ):
            models = get_default_model()
            assert isinstance(models, list)
            assert len(models) > 0

    def test_get_model_selector_singleton(self):
        """Test that get_model_selector returns same instance."""
        reset_model_selector()

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai"},
        ):
            selector1 = get_model_selector()
            selector2 = get_model_selector()
            assert selector1 is selector2


class TestProviderInfo:
    """Test provider info retrieval."""

    def test_get_provider_info(self, mock_settings):
        """Test getting provider info."""
        selector = ModelSelector(mock_settings)

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai", "anthropic"},
        ):
            info = selector.get_provider_info()
            assert "detected_provider" in info
            assert "available_providers" in info
            assert "priority" in info
            assert isinstance(info["available_providers"], list)


class TestAutoGeneration:
    """Test auto-generation of provider config."""

    def test_auto_generate_for_unknown_provider(self, mock_settings):
        """Test auto-generation for provider not in defaults."""
        selector = ModelSelector(mock_settings)

        # Mock provider_registry functions
        mock_get_models = MagicMock(
            return_value=["custom_provider/model1", "custom_provider/model2", "custom_provider/model3"]
        )
        mock_model_info = MagicMock(
            return_value={
                "mode": "chat",
                "input_cost_per_token": 0.001,
            }
        )

        with (
            patch(
                "pantheon.utils.provider_registry.models_by_provider",
                mock_get_models,
            ),
            patch(
                "pantheon.utils.provider_registry.get_model_info",
                mock_model_info,
            ),
        ):
            config = selector._auto_generate_provider_config("custom_provider")
            assert "high" in config
            assert "normal" in config
            assert "low" in config


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_tag_uses_normal(self, mock_settings):
        """Test that empty tag defaults to normal."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        # Empty string should default to normal
        models = selector.resolve_model("")
        # Should return models (normal is default)
        assert isinstance(models, list)

    def test_whitespace_in_tags_handled(self, mock_settings):
        """Test that whitespace in tags is handled."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        models = selector.resolve_model("  high , vision  ")
        assert isinstance(models, list)

    def test_case_insensitive_tags(self, mock_settings):
        """Test that tags are case insensitive."""
        selector = ModelSelector(mock_settings)
        selector._detected_provider = "openai"

        models_lower = selector.resolve_model("high")
        models_upper = selector.resolve_model("HIGH")
        # Both should resolve similarly
        assert isinstance(models_lower, list)
        assert isinstance(models_upper, list)


class TestAgentWithTagStrings:
    """Test Agent constructor with tag strings."""

    def test_is_model_tag_quality_tags(self):
        """Test _is_model_tag with quality tags."""
        from pantheon.agent import _is_model_tag

        assert _is_model_tag("high") is True
        assert _is_model_tag("normal") is True
        assert _is_model_tag("low") is True

    def test_is_model_tag_capability_tags(self):
        """Test _is_model_tag with capability tags."""
        from pantheon.agent import _is_model_tag

        assert _is_model_tag("vision") is True
        assert _is_model_tag("reasoning") is True
        assert _is_model_tag("tools") is True

    def test_is_model_tag_combined_tags(self):
        """Test _is_model_tag with combined tags."""
        from pantheon.agent import _is_model_tag

        assert _is_model_tag("high,vision") is True
        assert _is_model_tag("normal,tools") is True
        assert _is_model_tag("low,reasoning") is True

    def test_is_model_tag_with_whitespace(self):
        """Test _is_model_tag handles whitespace."""
        from pantheon.agent import _is_model_tag

        assert _is_model_tag("high, vision") is True
        assert _is_model_tag("  normal  ") is True

    def test_is_model_tag_model_names(self):
        """Test _is_model_tag returns False for model names."""
        from pantheon.agent import _is_model_tag

        # Model names with "/" are not tags
        assert _is_model_tag("openai/gpt-4o") is False
        assert _is_model_tag("anthropic/claude-sonnet-4") is False
        assert _is_model_tag("gemini/gemini-2.5-pro") is False

    def test_is_model_tag_unknown_strings(self):
        """Test _is_model_tag returns False for unknown strings."""
        from pantheon.agent import _is_model_tag

        # Unknown strings are not tags
        assert _is_model_tag("gpt-4o-mini") is False
        assert _is_model_tag("custom-model") is False
        assert _is_model_tag("my-model") is False

    def test_agent_with_quality_tag(self):
        """Test Agent with quality tag string."""
        from pantheon.agent import Agent

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai"},
        ):
            agent = Agent(name="test", model="high", instructions="Test")
            assert isinstance(agent.models, list)
            assert len(agent.models) > 0

    def test_agent_with_combined_tags(self):
        """Test Agent with combined quality + capability tags."""
        from pantheon.agent import Agent

        with patch(
            "pantheon.utils.model_selector.ModelSelector._get_available_providers",
            return_value={"openai"},
        ):
            agent = Agent(name="test", model="normal,vision", instructions="Test")
            assert isinstance(agent.models, list)

    def test_agent_with_model_name_not_resolved(self):
        """Test that model names are not treated as tags."""
        from pantheon.agent import Agent

        agent = Agent(name="test", model="openai/gpt-4o", instructions="Test")
        assert agent.models == ["openai/gpt-4o"]

    def test_agent_with_simple_model_name(self):
        """Test that simple model names without slash are kept as-is."""
        from pantheon.agent import Agent

        agent = Agent(name="test", model="gpt-4o-mini", instructions="Test")
        # gpt-4o-mini is not a known tag, so treated as model name
        assert agent.models == ["gpt-4o-mini"]
