"""
Model Selector - Smart default model selection based on environment API keys.

This module provides intelligent model selection that:
1. Auto-detects available providers based on environment API keys
2. Uses a tag system for model selection (quality + capability tags)
3. Returns fallback chains for robust operation

Usage:
    from pantheon.utils.model_selector import get_model_selector

    selector = get_model_selector()
    models = selector.resolve_model("normal")  # Returns list[str] fallback chain
    models = selector.resolve_model("high,vision")  # Quality + capability combo
"""

from typing import TYPE_CHECKING

from .log import logger

if TYPE_CHECKING:
    from pantheon.settings import Settings

# ============ Default Configuration ============
# Built-in defaults based on February 2026 flagship models
# Users can override in settings.json

DEFAULT_PROVIDER_PRIORITY = ["openai", "anthropic", "gemini", "zai", "deepseek", "minimax", "moonshot"]

# Quality levels map to MODEL LISTS (not single models) for fallback chains
# Models within each level are ordered by preference
DEFAULT_PROVIDER_MODELS = {
    # OpenAI: GPT-5.2 series
    # https://platform.openai.com/docs/models
    "openai": {
        "high": ["openai/gpt-5.2-pro", "openai/gpt-5.2", "openai/gpt-5"],
        "normal": ["openai/gpt-5.2-codex", "openai/gpt-5.2", "openai/gpt-5"],
        "low": ["openai/gpt-5-mini", "openai/gpt-5-nano", "openai/gpt-4.1-mini"],
    },
    # Anthropic: Claude 4.6 series
    # https://docs.anthropic.com/en/docs/about-claude/models/overview
    "anthropic": {
        "high": [
            "anthropic/claude-opus-4-6",
            "anthropic/claude-opus-4-5-20251101",
            "anthropic/claude-opus-4-20250514",
        ],
        "normal": [
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-sonnet-4-5-20250929",
            "anthropic/claude-sonnet-4-20250514",
        ],
        "low": [
            "anthropic/claude-haiku-4-5",
        ],
    },
    # Gemini: Gemini 3.1/3/2.5 series
    # https://ai.google.dev/gemini-api/docs/models
    "gemini": {
        "high": ["gemini/gemini-3.1-pro-preview", "gemini/gemini-3-pro-preview", "gemini/gemini-2.5-pro"],
        "normal": ["gemini/gemini-3-flash-preview", "gemini/gemini-2.5-flash"],
        "low": ["gemini/gemini-3-flash-preview", "gemini/gemini-2.5-flash-lite"],
    },
    # Z.ai (Zhipu): GLM-4.6/4.5 series
    # https://open.bigmodel.cn/
    "zai": {
        "high": ["zai/glm-5", "zai/glm-4.6", "zai/glm-4.5", "zai/glm-4.5v"],
        "normal": ["zai/glm-5", "zai/glm-4.6", "zai/glm-4.5", "zai/glm-4.5v"],
        "low": ["zai/glm-4.5-air", "zai/glm-4.5-flash"],
    },
    # DeepSeek: V3/R1 series
    # https://api-docs.deepseek.com/
    "deepseek": {
        "high": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"],
        "normal": ["deepseek/deepseek-chat"],
        "low": ["deepseek/deepseek-chat"],
    },
    # MiniMax: M2.5/M2.1 series
    # https://platform.minimaxi.com/
    "minimax": {
        "high": [
            "minimax/MiniMax-M2.5-highspeed",
            "minimax/MiniMax-M2.5",
            "minimax/MiniMax-M2.1-highspeed",
            "minimax/MiniMax-M2.1",
        ],
        "normal": [
            "minimax/MiniMax-M2.5-highspeed",
            "minimax/MiniMax-M2.5",
            "minimax/MiniMax-M2.1-highspeed",
            "minimax/MiniMax-M2.1",
        ],
        "low": [
            "minimax/MiniMax-M2.5",
            "minimax/MiniMax-M2.1",
        ],
    },
    # Moonshot: Kimi K2.5/K2 series
    # https://platform.moonshot.cn/
    "moonshot": {
        "high": ["moonshot/kimi-k2.5", "moonshot/kimi-k2-0905-preview"],
        "normal": ["moonshot/kimi-k2.5", "moonshot/kimi-k2-0905-preview"],
        "low": ["moonshot/kimi-k2.5", "moonshot/kimi-k2-0905-preview"],
    },
}

# Capability tags map to litellm's supports_* fields
CAPABILITY_MAP = {
    "vision": "supports_vision",
    "reasoning": "supports_reasoning",
    "audio_in": "supports_audio_input",
    "audio_out": "supports_audio_output",
    "computer": "supports_computer_use",
    "web": "supports_web_search",
    "pdf": "supports_pdf_input",
    "tools": "supports_function_calling",
    "schema": "supports_response_schema",
    "prefill": "supports_assistant_prefill",
}

# Quality level tags
QUALITY_TAGS = {"high", "normal", "low"}

# Ultimate fallback model when nothing else works (must be concrete model, not tag)
ULTIMATE_FALLBACK = "gpt-4.1-mini"

# Recommended fallback tag for general use
FALLBACK_TAG = "low"

# Provider name -> environment variable for API key
PROVIDER_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "azure": "AZURE_API_KEY",
    "cohere": "COHERE_API_KEY",
    "replicate": "REPLICATE_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "together_ai": "TOGETHER_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "zai": "ZAI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}

# ============ Image Generation Model Defaults ============
# Quality levels for image generation models
DEFAULT_IMAGE_GEN_MODELS = {
    # Gemini: Nano Banana series
    # https://ai.google.dev/gemini-api/docs/image-generation
    # Note: gemini-2.5-flash-image has quota issues, use 3.1-flash-image-preview instead
    "gemini": {
        "high": ["gemini/gemini-3-pro-image-preview"],                                        # Nano Banana Pro
        "normal": ["gemini/gemini-3.1-flash-image-preview"],                                  # Nano Banana 2 (stable)
        "low": ["gemini/gemini-3.1-flash-image-preview"],                                     # Nano Banana 2 (stable)
    },
    # OpenAI: GPT-Image series
    # https://platform.openai.com/docs/models
    "openai": {
        "high": ["chatgpt-image-latest", "gpt-image-1.5"],
        "normal": ["chatgpt-image-latest", "gpt-image-1.5"],
        "low": ["gpt-image-1"],
    },
}


class ModelSelector:
    """Smart model selector based on environment API keys and tags."""

    def __init__(self, settings: "Settings"):
        """Initialize selector with settings.

        Args:
            settings: Pantheon Settings instance for reading configuration
        """
        self.settings = settings
        self._detected_provider: str | None = None
        self._available_providers: set[str] | None = None

    def _get_available_providers(self) -> set[str]:
        """Get set of providers with valid API keys (cached)."""
        if self._available_providers is not None:
            return self._available_providers

        import os

        self._available_providers = set()

        for provider, env_key in PROVIDER_API_KEYS.items():
            api_key_value = os.environ.get(env_key, "")
            if api_key_value:
                self._available_providers.add(provider)

        return self._available_providers

    def detect_available_provider(self) -> str | None:
        """Detect first available provider based on API keys.

        Priority: user-configured priority > code default priority > any available

        Returns:
            Provider name if found, None otherwise
        """
        if self._detected_provider is not None:
            return self._detected_provider

        # Get available providers from environment
        available = self._get_available_providers()
        if not available:
            logger.warning("No LLM providers detected from environment API keys")
            return None

        # Priority: user config > code defaults
        priority = self.settings.get(
            "models.provider_priority", DEFAULT_PROVIDER_PRIORITY
        )

        # 1. Check priority list first
        for provider in priority:
            if provider in available:
                self._detected_provider = provider
                logger.info(f"Selected provider '{provider}' from priority list")
                return provider

        # 2. Check any available provider not in priority list
        for provider in available:
            if provider not in priority:
                self._detected_provider = provider
                logger.info(
                    f"Selected provider '{provider}' (not in priority list, "
                    f"consider adding to settings.json)"
                )
                return provider

        return None

    def _get_provider_models(self, provider: str) -> dict[str, list[str]]:
        """Get model configuration for a provider.

        Priority: user config > code defaults > auto-generated

        Args:
            provider: Provider name (e.g., "openai", "anthropic")

        Returns:
            Dict mapping quality levels to model lists
        """
        # Try user configuration first
        user_config = self.settings.get(f"models.provider_models.{provider}", {})

        # Get code defaults
        default_config = DEFAULT_PROVIDER_MODELS.get(provider, {})

        # Merge: user config overrides defaults
        if user_config or default_config:
            merged = {**default_config, **user_config}
            return merged

        # No configuration - auto-generate from litellm
        return self._auto_generate_provider_config(provider)

    def _auto_generate_provider_config(self, provider: str) -> dict[str, list[str]]:
        """Auto-generate provider config from litellm (sorted by price).

        Used when provider has API key but no configuration.

        Args:
            provider: Provider name

        Returns:
            Dict mapping quality levels to model lists
        """
        try:
            from litellm import models_by_provider
            from litellm.utils import get_model_info
        except ImportError:
            logger.warning("litellm not available for auto-generation")
            return {}

        logger.warning(
            f"Provider '{provider}' not configured. Auto-generating from litellm. "
            f"Consider adding it to settings.json models.provider_models for better control."
        )

        if provider not in models_by_provider:
            logger.warning(f"Provider '{provider}' not found in litellm")
            return {}

        # Collect chat models with prices
        models_with_prices: list[tuple[str, float]] = []
        for model in models_by_provider[provider]:
            try:
                info = get_model_info(model)
                if info.get("mode") == "chat":
                    input_cost = info.get("input_cost_per_token", 0) or 0
                    models_with_prices.append((model, input_cost))
            except Exception:
                pass

        if not models_with_prices:
            return {}

        # Sort by price descending (most expensive first)
        models_with_prices.sort(key=lambda x: x[1], reverse=True)

        # Split into thirds for quality levels
        n = len(models_with_prices)
        third = max(1, n // 3)

        config = {
            "high": [m[0] for m in models_with_prices[:third]],
            "normal": [m[0] for m in models_with_prices[third : 2 * third]],
            "low": [m[0] for m in models_with_prices[2 * third :]],
        }

        # Ensure each level has at least one model
        if not config["normal"]:
            config["normal"] = config["high"][-1:] if config["high"] else []
        if not config["low"]:
            config["low"] = config["normal"][-1:] if config["normal"] else []

        logger.info(f"Auto-generated config for '{provider}': {config}")
        return config

    def _check_model_capability(self, model: str, capability: str) -> bool:
        """Check if a model supports a specific capability.

        Args:
            model: Model name (e.g., "openai/gpt-4o")
            capability: Capability tag (e.g., "vision", "reasoning")

        Returns:
            True if model supports the capability
        """
        if capability not in CAPABILITY_MAP:
            return False

        try:
            from litellm.utils import get_model_info

            info = get_model_info(model)
            litellm_field = CAPABILITY_MAP[capability]
            return bool(info.get(litellm_field))
        except Exception:
            # If we can't check, assume it doesn't support
            return False

    def resolve_model(self, tag: str) -> list[str]:
        """Resolve tag(s) to a model fallback chain.

        Supports:
        - Quality tags: "high", "normal", "low"
        - Capability tags: "vision", "reasoning", "tools", etc.
        - Combinations: "high,vision", "low,reasoning"

        Args:
            tag: Single tag or comma-separated tags (e.g., "normal", "high,vision")

        Returns:
            List of models as fallback chain, can be passed directly to Agent(model=...)
        """
        provider = self._detected_provider or self.detect_available_provider()
        if not provider:
            logger.warning(
                f"No provider available, using fallback model: {ULTIMATE_FALLBACK}"
            )
            return [ULTIMATE_FALLBACK]

        # Parse tags
        tags = [t.strip().lower() for t in tag.split(",")]

        # Get provider configuration
        provider_models = self._get_provider_models(provider)
        if not provider_models:
            logger.warning(
                f"No models configured for provider '{provider}', "
                f"using fallback: {ULTIMATE_FALLBACK}"
            )
            return [ULTIMATE_FALLBACK]

        # Separate quality and capability tags
        quality_tag = next((t for t in tags if t in QUALITY_TAGS), "normal")
        capability_tags = [t for t in tags if t in CAPABILITY_MAP]

        # Get models for the quality level
        models = provider_models.get(quality_tag, [])
        if isinstance(models, str):
            models = [models]

        # If no capability tags, return the full quality level list
        if not capability_tags:
            return models if models else [ULTIMATE_FALLBACK]

        # Filter models by capability requirements
        result: list[str] = []
        for model in models:
            if all(
                self._check_model_capability(model, cap) for cap in capability_tags
            ):
                result.append(model)

        # If no models match at current level, search higher levels
        if not result:
            quality_order = ["high", "normal", "low"]
            try:
                start_idx = quality_order.index(quality_tag)
            except ValueError:
                start_idx = 1  # Default to normal

            # Search higher quality levels
            for quality in quality_order[:start_idx]:
                higher_models = provider_models.get(quality, [])
                if isinstance(higher_models, str):
                    higher_models = [higher_models]

                for model in higher_models:
                    if all(
                        self._check_model_capability(model, cap)
                        for cap in capability_tags
                    ):
                        result.append(model)

        # Append remaining models from original quality level as fallback
        for model in models:
            if model not in result:
                result.append(model)

        return result if result else [ULTIMATE_FALLBACK]

    def get_default_model(self) -> list[str]:
        """Get default model fallback chain (normal quality).

        Returns:
            List of models for fallback chain
        """
        return self.resolve_model("normal")

    def find_models_with_capability(self, capability: str) -> list[str]:
        """Find all models supporting a specific capability.

        Args:
            capability: Capability tag (e.g., "vision", "reasoning")

        Returns:
            List of models supporting the capability
        """
        if capability not in CAPABILITY_MAP:
            logger.warning(f"Unknown capability: {capability}")
            return []

        provider = self._detected_provider or self.detect_available_provider()
        if not provider:
            return []

        provider_models = self._get_provider_models(provider)
        result: list[str] = []

        # Check all quality levels
        for quality in QUALITY_TAGS:
            models = provider_models.get(quality, [])
            if isinstance(models, str):
                models = [models]

            for model in models:
                if model not in result and self._check_model_capability(
                    model, capability
                ):
                    result.append(model)

        return result

    def resolve_image_gen_model(self, quality: str = "normal") -> list[str]:
        """Resolve image generation model based on available providers.

        Args:
            quality: Quality level ("high" or "normal")

        Returns:
            List of image generation models as fallback chain
        """
        available = self._get_available_providers()
        
        # Priority order for image generation providers
        priority = ["gemini", "openai"]
        
        for provider in priority:
            if provider in available:
                user_config = self.settings.get(f"image_gen_models.{provider}", {})
                provider_models = user_config or DEFAULT_IMAGE_GEN_MODELS.get(provider, {})
                models = provider_models.get(quality, [])
                if models:
                    return models if isinstance(models, list) else [models]
        
        # Ultimate fallback
        return ["gemini/gemini-3-pro-image-preview"]

    def get_provider_info(self) -> dict:
        """Get information about current provider selection.

        Returns:
            Dict with provider info for debugging
        """
        return {
            "detected_provider": self._detected_provider
            or self.detect_available_provider(),
            "available_providers": list(self._get_available_providers()),
            "priority": self.settings.get(
                "models.provider_priority", DEFAULT_PROVIDER_PRIORITY
            ),
        }

    def list_available_models(self) -> dict:
        """List all available models grouped by provider.

        Returns models from providers that have valid API keys configured.

        Returns:
            {
                "success": True,
                "available_providers": ["openai", "anthropic"],
                "current_provider": "openai",
                "models_by_provider": {
                    "openai": ["openai/gpt-5.2", "openai/gpt-5.1", ...],
                    "anthropic": ["anthropic/claude-opus-4-5-20251101", ...]
                },
                "supported_tags": ["high", "normal", "low", "vision", ...]
            }
        """
        available_providers = list(self._get_available_providers())
        current_provider = self._detected_provider or self.detect_available_provider()

        # Collect models for each available provider
        models_by_provider: dict[str, list[str]] = {}
        for provider in available_providers:
            provider_config = self._get_provider_models(provider)
            # Merge all quality levels and deduplicate while preserving order
            all_models: list[str] = []
            seen: set[str] = set()
            for quality in ["high", "normal", "low"]:
                models = provider_config.get(quality, [])
                if isinstance(models, str):
                    models = [models]
                for model in models:
                    if model not in seen:
                        all_models.append(model)
                        seen.add(model)
            models_by_provider[provider] = all_models

        # Collect supported tags
        supported_tags = list(QUALITY_TAGS) + list(CAPABILITY_MAP.keys())

        return {
            "success": True,
            "available_providers": available_providers,
            "current_provider": current_provider,
            "models_by_provider": models_by_provider,
            "supported_tags": supported_tags,
        }


# ============ Module-level Helpers ============

_selector_instance: ModelSelector | None = None


def get_model_selector() -> ModelSelector:
    """Get or create the global ModelSelector instance.

    Returns:
        ModelSelector instance
    """
    global _selector_instance

    if _selector_instance is None:
        from pantheon.settings import get_settings

        _selector_instance = ModelSelector(get_settings())

    return _selector_instance


def reset_model_selector() -> None:
    """Reset the global ModelSelector instance (for testing)."""
    global _selector_instance
    _selector_instance = None


def get_default_model() -> list[str]:
    """Convenience function to get default model fallback chain.

    Returns:
        List of models for fallback chain
    """
    return get_model_selector().get_default_model()


__all__ = [
    "ModelSelector",
    "get_model_selector",
    "reset_model_selector",
    "get_default_model",
    "PROVIDER_API_KEYS",
    "CAPABILITY_MAP",
    "QUALITY_TAGS",
    "DEFAULT_PROVIDER_PRIORITY",
    "DEFAULT_PROVIDER_MODELS",
    "DEFAULT_IMAGE_GEN_MODELS",
    "ULTIMATE_FALLBACK",
    "FALLBACK_TAG",
]
