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

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .log import logger

if TYPE_CHECKING:
    from pantheon.settings import Settings


# ============ Custom Endpoint Configuration ============

@dataclass
class CustomEndpointConfig:
    """Configuration for custom API endpoints (e.g., custom Anthropic/OpenAI proxies)."""
    provider_key: str      # e.g., "custom_anthropic"
    display_name: str      # e.g., "Custom Anthropic"
    api_key_env: str       # e.g., "CUSTOM_ANTHROPIC_API_KEY"
    api_base_env: str      # e.g., "CUSTOM_ANTHROPIC_API_BASE"
    model_env: str         # e.g., "CUSTOM_ANTHROPIC_MODEL"


# Custom endpoint environment variable configurations
CUSTOM_ENDPOINT_ENVS: dict[str, CustomEndpointConfig] = {
    "custom_anthropic": CustomEndpointConfig(
        provider_key="custom_anthropic",
        display_name="Custom Anthropic",
        api_key_env="CUSTOM_ANTHROPIC_API_KEY",
        api_base_env="CUSTOM_ANTHROPIC_API_BASE",
        model_env="CUSTOM_ANTHROPIC_MODEL",
    ),
    "custom_openai": CustomEndpointConfig(
        provider_key="custom_openai",
        display_name="Custom OpenAI",
        api_key_env="CUSTOM_OPENAI_API_KEY",
        api_base_env="CUSTOM_OPENAI_API_BASE",
        model_env="CUSTOM_OPENAI_MODEL",
    ),
}

# Sentinel object for negative cache (better than empty string)
_NOT_FOUND = object()

# ============ Default Configuration ============
# Built-in defaults based on February 2026 flagship models
# Users can override in settings.json

DEFAULT_PROVIDER_PRIORITY = ["openai", "anthropic", "gemini", "zai", "deepseek", "minimax", "moonshot"]

# Quality levels map to MODEL LISTS (not single models) for fallback chains
# Models within each level are ordered by preference
DEFAULT_PROVIDER_MODELS = {
    # OpenAI: GPT-5.4 series
    # https://platform.openai.com/docs/models
    "openai": {
        "high": ["openai/gpt-5.4-pro", "openai/gpt-5.4", "openai/gpt-5.2-pro", "openai/gpt-5.2"],
        "normal": ["openai/gpt-5.4", "openai/gpt-5.2-codex", "openai/gpt-5.2", "openai/gpt-5"],
        "low": ["openai/gpt-5.3-chat-latest", "openai/gpt-5-mini", "openai/gpt-5-nano", "openai/gpt-4.1-mini"],
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
ULTIMATE_FALLBACK = "openai/gpt-5.4"

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
    # Note: Use gemini/ prefix to ensure correct provider detection (same as chat models)
    "gemini": {
        "high": ["gemini/gemini-3-pro-image-preview"],                                        # Nano Banana Pro
        "normal": ["gemini/gemini-3.1-flash-image-preview"],                                  # Nano Banana 2
        "low": ["gemini/gemini-2.5-flash-image"],                                             # Nano Banana stable
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
        """Get set of providers with valid API keys or OAuth tokens (cached)."""
        if self._available_providers is not None:
            return self._available_providers

        import os

        self._available_providers = set()

        for provider, env_key in PROVIDER_API_KEYS.items():
            api_key_value = os.environ.get(env_key, "")
            if api_key_value:
                self._available_providers.add(provider)

        # Check custom endpoint keys
        for provider_key, config in CUSTOM_ENDPOINT_ENVS.items():
            if os.environ.get(config.api_key_env, ""):
                self._available_providers.add(provider_key)

        # Check for OAuth tokens (currently only OpenAI)
        if self._check_oauth_token_available("openai"):
            self._available_providers.add("openai")
            logger.info("OpenAI OAuth token detected as available provider")

        # Universal proxy: LLM_API_KEY makes openai provider available
        # (most third-party proxies are OpenAI-compatible)
        # Note: LLM_API_BASE is deprecated, warn user to use custom endpoints instead
        if not self._available_providers and os.environ.get("LLM_API_KEY", ""):
            if os.environ.get("LLM_API_BASE", ""):
                logger.warning(
                    "LLM_API_BASE is deprecated. Consider using CUSTOM_OPENAI_API_BASE or "
                    "CUSTOM_ANTHROPIC_API_BASE for better control over custom endpoints."
                )
            self._available_providers.add("openai")

        return self._available_providers

    def _check_oauth_token_available(self, provider: str) -> bool:
        """Check if OAuth token is available for a provider."""
        if provider != "openai":
            return False
        try:
            from pantheon.auth.openai_oauth_manager import get_oauth_manager
            oauth_manager = get_oauth_manager()
            if oauth_manager.auth_path.exists():
                logger.debug(f"OpenAI OAuth token found at {oauth_manager.auth_path}")
                return True
        except Exception as e:
            logger.debug(f"Failed to check OAuth token: {e}")
        return False

    def detect_available_provider(self) -> str | None:
        """Detect first available provider based on API keys.

        Priority:
        1. Custom endpoints (if configured with model) - highest priority
        2. User-configured priority list
        3. Code default priority list
        4. Any other available provider

        Returns:
            Provider name if found, None otherwise
        """
        import os

        if self._detected_provider is not None:
            return self._detected_provider

        # Get available providers from environment
        available = self._get_available_providers()
        if not available:
            logger.warning("No LLM providers detected from environment API keys")
            return None

        # 1. Check custom endpoints first (highest priority if model is configured)
        for provider_key, config in CUSTOM_ENDPOINT_ENVS.items():
            if provider_key in available:
                model = os.environ.get(config.model_env, "")
                if model:
                    self._detected_provider = provider_key
                    logger.info(f"Selected custom endpoint '{provider_key}' with model '{model}'")
                    return provider_key

        # 2. Priority: user config > code defaults
        priority = self.settings.get(
            "models.provider_priority", DEFAULT_PROVIDER_PRIORITY
        )

        # 3. Check priority list
        for provider in priority:
            if provider in available:
                self._detected_provider = provider
                logger.info(f"Selected provider '{provider}' from priority list")
                return provider

        # 4. Check any available provider not in priority list (excluding custom endpoints without model)
        for provider in available:
            if provider not in priority and provider not in CUSTOM_ENDPOINT_ENVS:
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
        # Custom endpoints don't have predefined model lists in litellm
        # They use environment-specified models instead
        if provider in CUSTOM_ENDPOINT_ENVS:
            return {}

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
        - Custom tag: "custom" - uses custom endpoint model if configured
        - Combinations: "high,vision", "low,reasoning", "custom,vision"

        Args:
            tag: Single tag or comma-separated tags (e.g., "normal", "high,vision")

        Returns:
            List of models as fallback chain, can be passed directly to Agent(model=...)
        """
        import os

        # Parse tags
        tags = [t.strip().lower() for t in tag.split(",")]

        # Check if explicitly requesting custom model
        # Custom endpoint models only activate when "custom" tag is used
        if "custom" in tags:
            for provider_key, config in CUSTOM_ENDPOINT_ENVS.items():
                model = os.environ.get(config.model_env, "")
                if model:
                    logger.info(f"Using custom model: {provider_key}/{model}")
                    return [f"{provider_key}/{model}"]

        provider = self._detected_provider or self.detect_available_provider()
        if not provider:
            logger.warning(
                f"No provider available, using fallback model: {ULTIMATE_FALLBACK}"
            )
            return [ULTIMATE_FALLBACK]

        # Check if provider is a custom endpoint
        # Note: Custom endpoints should ONLY be used when "custom" tag is explicitly requested
        # (handled above at lines 445-450). Skip auto-selection for custom endpoint providers.
        if provider in CUSTOM_ENDPOINT_ENVS:
            # Find next available non-custom provider
            available = self._get_available_providers()
            priority = self.settings.get("models.provider_priority", DEFAULT_PROVIDER_PRIORITY)
            fallback_found = False
            for fallback_provider in priority:
                if fallback_provider in available and fallback_provider not in CUSTOM_ENDPOINT_ENVS:
                    provider = fallback_provider
                    fallback_found = True
                    logger.info(
                        f"Custom endpoint detected as default provider. "
                        f"Falling back to '{provider}' for non-custom request."
                    )
                    break

            if not fallback_found:
                # No other provider available, use custom endpoint as fallback
                config = CUSTOM_ENDPOINT_ENVS[provider]
                model = os.environ.get(config.model_env, "")
                if model:
                    logger.info(
                        f"No standard provider available. Using custom endpoint as fallback: "
                        f"{provider}/{model}"
                    )
                    return [f"{provider}/{model}"]
                return [ULTIMATE_FALLBACK]

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
                    "openai": ["openai/gpt-5.4", "openai/gpt-5.2", ...],
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
    "CUSTOM_ENDPOINT_ENVS",
    "CustomEndpointConfig",
]
