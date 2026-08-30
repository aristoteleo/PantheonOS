"""
Tests for ImageGenerationToolSet.

Covers:
- ToolSet unit tests
- Gemini multimodal (no ref, with 2 refs)
- Gemini Imagen text-only
- DALL-E text-only
- OpenAI with reference images (Agent integration for call_agent fallback)
"""

import os
import pytest
from pathlib import Path
from dotenv import load_dotenv
from types import SimpleNamespace

# Load environment variables from .env
load_dotenv()

# Check available API keys
HAS_GEMINI = bool(os.environ.get("GEMINI_API_KEY"))
HAS_OPENAI = bool(os.environ.get("OPENAI_API_KEY"))


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def image_toolset():
    """Create ImageGenerationToolSet instance."""
    from pantheon.apps.builtin.image import ImageGenerationToolSet
    return ImageGenerationToolSet()


@pytest.fixture
def test_images(tmp_path: Path) -> list[str]:
    """Create 2 test images for reference scenarios."""
    from PIL import Image
    
    paths = []
    for i, color in enumerate(["red", "blue"]):
        img = Image.new("RGB", (100, 100), color=color)
        path = tmp_path / f"test_{color}.png"
        img.save(path)
        paths.append(str(path))
    return paths


# ============================================================================
# ToolSet Unit Tests
# ============================================================================


class TestToolSetBasics:
    """Basic unit tests for ImageGenerationToolSet."""
    
    def test_init(self, image_toolset):
        """Test toolset initialization."""
        assert image_toolset is not None
        assert image_toolset.fallback_vision_model == "gemini/gemini-2.5-flash"
    
    def test_multimodal_model_detection(self, image_toolset):
        """Test multimodal model detection."""
        assert image_toolset._is_multimodal_model("gemini/gemini-2.5-flash-image-preview")
        assert not image_toolset._is_multimodal_model("dall-e-3")

    @pytest.mark.asyncio
    async def test_dalle_prefers_openai_provider_key_over_llm_fallback(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def aimage_generation(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model="dall-e-3",
                    usage=None,
                )

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("LLM_API_BASE", "https://proxy.example/v1")
        monkeypatch.setenv("LLM_API_KEY", "proxy-key")
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A yellow star on white background",
            model="dall-e-3",
        )

        assert result["success"] is True
        assert calls["model"] == "dall-e-3"
        assert calls["api_key"] == "test-openai-key"
        assert calls["base_url"] == "https://proxy.example/v1"

    @pytest.mark.asyncio
    async def test_dalle_uses_openai_provider_base_when_configured(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def aimage_generation(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model="dall-e-3",
                    usage=None,
                )

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("OPENAI_API_BASE", "https://openai-proxy.example/v1")
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A yellow star on white background",
            model="dall-e-3",
        )

        assert result["success"] is True
        assert calls["model"] == "dall-e-3"
        assert calls["api_key"] == "test-openai-key"
        assert calls["base_url"] == "https://openai-proxy.example/v1"

    @pytest.mark.asyncio
    async def test_openai_model_shortcut_resolves_to_default_image_model(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def aimage_generation(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model=kwargs["model"],
                    usage=None,
                )

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A precise vector-like icon of a microscope",
            model="openai",
        )

        assert result["success"] is True
        assert calls["model"] == "gpt-image-2"
        assert result["model_used"] == "gpt-image-2"

    @pytest.mark.asyncio
    async def test_openai_model_args_are_forwarded_to_image_generation(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def aimage_generation(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model=kwargs["model"],
                    usage=None,
                )

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A landscape scientific workflow diagram",
            model="openai",
            model_args={
                "size": "1536x1024",
                "quality": "high",
                "output_format": "webp",
            },
        )

        assert result["success"] is True
        assert calls["size"] == "1536x1024"
        assert calls["quality"] == "high"
        assert calls["output_format"] == "webp"

    @pytest.mark.asyncio
    async def test_unsupported_image_provider_returns_available_options(self, image_toolset):
        result = await image_toolset.generate_image(
            prompt="A test image",
            model="anthropic/claude-sonnet-4-6",
        )

        assert result["success"] is False
        assert "Unsupported image generation provider" in result["error"]
        assert result["available_model_shortcuts"] == ["openai", "gemini"]
        assert "openai" in result["available_models"]
        assert "gemini" in result["available_models"]

    @pytest.mark.asyncio
    async def test_provider_shortcut_without_builtin_image_default_returns_actionable_error(self, image_toolset):
        result = await image_toolset.generate_image(
            prompt="A test image",
            model="openrouter",
        )

        assert result["success"] is False
        assert "Provider shortcut 'openrouter' has no built-in image default" in result["error"]
        assert "openrouter/<model-name>" in result["error"]

    @pytest.mark.asyncio
    async def test_openai_compatible_provider_model_is_attempted(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def aimage_generation(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model=kwargs["model"],
                    usage=None,
                )

        monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A test image",
            model="openrouter/some-image-model",
            model_args={"size": "1024x1024"},
        )

        assert result["success"] is True
        assert calls["model"] == "some-image-model"
        assert calls["api_key"] == "openrouter-key"
        assert calls["base_url"] == "https://openrouter.ai/api/v1"

    @pytest.mark.asyncio
    async def test_missing_image_provider_key_returns_available_options(self, image_toolset, monkeypatch):
        monkeypatch.setattr(
            image_toolset,
            "_resolve_model_connection",
            lambda _model: (None, None),
        )

        result = await image_toolset.generate_image(
            prompt="A test image",
            model="openai",
        )

        assert result["success"] is False
        assert result["error"] == "Missing API key for image generation provider 'openai'"
        assert result["available_model_shortcuts"] == ["openai", "gemini"]
        assert "gpt-image-2" in result["available_models"]["openai"]

    @pytest.mark.asyncio
    async def test_gemini_multimodal_uses_gemini_provider_base_when_configured(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def acompletion(self, **kwargs):
                calls.update(kwargs)
                return [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "ok",
                                    "images": [
                                        {
                                            "image_url": {
                                                "url": "data:image/png;base64,ZmFrZQ=="
                                            }
                                        }
                                    ],
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "model": kwargs["model"],
                    },
                    {
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                        "choices": [],
                    },
                ]

        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("GEMINI_API_BASE", "https://gemini-proxy.example")
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A simple red circle on white background",
            model="gemini/gemini-2.5-flash-image",
        )

        assert result["success"] is True
        assert calls["model"] == "gemini-2.5-flash-image"
        assert calls["api_key"] == "gemini-key"
        assert calls["base_url"] == "https://gemini-proxy.example"

    @pytest.mark.asyncio
    async def test_gemini_model_args_are_forwarded_as_image_config(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def acompletion(self, **kwargs):
                calls.update(kwargs)
                return [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "ok",
                                    "images": [
                                        {
                                            "image_url": {
                                                "url": "data:image/png;base64,ZmFrZQ=="
                                            }
                                        }
                                    ],
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "model": kwargs["model"],
                    }
                ]

        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A widescreen lab automation scene",
            model="gemini",
            model_args={"aspect_ratio": "16:9", "image_size": "2K"},
        )

        assert result["success"] is True
        assert calls["image_config"] == {
            "aspect_ratio": "16:9",
            "image_size": "2K",
        }


    @pytest.mark.asyncio
    async def test_gemini_multimodal_uses_global_llm_base_as_fallback(self, image_toolset, monkeypatch):
        calls = {}

        class FakeAdapter:
            async def acompletion(self, **kwargs):
                calls.update(kwargs)
                return [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "ok",
                                    "images": [
                                        {
                                            "image_url": {
                                                "url": "data:image/png;base64,ZmFrZQ=="
                                            }
                                        }
                                    ],
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "model": kwargs["model"],
                    },
                    {
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                        "choices": [],
                    },
                ]

        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
        monkeypatch.delenv("GEMINI_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="A simple red circle on white background",
            model="gemini/gemini-2.5-flash-image",
        )

        assert result["success"] is True
        assert calls["api_key"] == "gemini-key"
        assert calls["base_url"] == "https://fallback.example/v1"

    @pytest.mark.asyncio
    async def test_gpt_image_2_with_references_uses_native_image_edit_without_fallback(
        self, image_toolset, monkeypatch, test_images
    ):
        calls = {}

        class FakeAdapter:
            async def aimage_edit(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model="gpt-image-2",
                    usage=None,
                )

        async def fail_if_called(_reference_images):
            raise AssertionError("_describe_reference_images should not be used for gpt-image-2")

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )
        monkeypatch.setattr(image_toolset, "_describe_reference_images", fail_if_called)

        result = await image_toolset.generate_image(
            prompt="Turn the first image into a watercolor illustration",
            reference_images=test_images,
            model="gpt-image-2",
        )

        assert result["success"] is True
        assert calls["model"] == "gpt-image-2"
        assert calls["image"] == [Path(p) for p in test_images]
        assert calls["api_key"] == "test-openai-key"

    @pytest.mark.asyncio
    async def test_gpt_image_2_edit_passes_path_objects_to_openai_sdk(
        self, image_toolset, monkeypatch, test_images
    ):
        calls = {}

        class FakeAdapter:
            async def aimage_edit(self, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model="gpt-image-2",
                    usage=None,
                )

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )

        result = await image_toolset.generate_image(
            prompt="Make the input image look like a watercolor painting",
            reference_images=test_images,
            model="gpt-image-2",
        )

        assert result["success"] is True
        assert all(isinstance(item, Path) for item in calls["image"])

    @pytest.mark.asyncio
    async def test_gpt_image_2_edit_emits_info_logs(
        self, image_toolset, monkeypatch, test_images
    ):
        entries = []

        class FakeLogger:
            def debug(self, message, *args, **kwargs):
                entries.append(("debug", str(message)))

            def info(self, message, *args, **kwargs):
                entries.append(("info", str(message)))

            def warning(self, message, *args, **kwargs):
                entries.append(("warning", str(message)))

            def error(self, message, *args, **kwargs):
                entries.append(("error", str(message)))

        class FakeAdapter:
            async def aimage_edit(self, **kwargs):
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json="ZmFrZQ==", url=None)],
                    model="gpt-image-2",
                    usage=None,
                )

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setattr(
            "pantheon.utils.adapters.get_adapter",
            lambda sdk: FakeAdapter(),
        )
        monkeypatch.setattr("pantheon.apps.builtin.image.image_gen.logger", FakeLogger(), raising=False)

        result = await image_toolset.generate_image(
            prompt="Make the input image look like a watercolor painting",
            reference_images=test_images,
            model="gpt-image-2",
        )

        assert result["success"] is True
        info_messages = [message for level, message in entries if level == "info"]
        assert any("Starting native image edit" in message for message in info_messages)
        assert any("Native image edit completed" in message for message in info_messages)


# ============================================================================
# Gemini Tests (Multimodal + Imagen)
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_GEMINI, reason="GEMINI_API_KEY not set")
class TestGeminiGeneration:
    """Tests for Gemini image generation."""
    
    async def test_multimodal_no_reference(self, image_toolset):
        """Gemini multimodal without reference images."""
        result = await image_toolset.generate_image(
            prompt="A simple red circle on white background",
            model="gemini/gemini-2.5-flash-image-preview",
        )
        
        assert result["success"] is True
        assert len(result["images"]) >= 1
        print(f"✅ Generated: {result['images']}")
    
    async def test_multimodal_with_references(self, image_toolset, test_images):
        """Gemini multimodal with 2 reference images."""
        result = await image_toolset.generate_image(
            prompt="Blend these two colors into a gradient",
            reference_images=test_images,
            model="gemini/gemini-2.5-flash-image-preview",
        )
        
        assert result["success"] is True
        print(f"✅ Generated with refs: {result['images']}")
    
    async def test_imagen_text_only(self, image_toolset):
        """Gemini Imagen text-only generation."""
        result = await image_toolset.generate_image(
            prompt="A blue square on white background",
            model="gemini/imagen-4.0-generate-001",
        )
        
        assert result["success"] is True
        print(f"✅ Imagen generated: {result['images']}")


# ============================================================================
# OpenAI DALL-E Tests
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_OPENAI, reason="OPENAI_API_KEY not set")
class TestDallEGeneration:
    """Tests for DALL-E image generation."""
    
    async def test_dalle_text_only(self, image_toolset):
        """DALL-E text-only generation."""
        result = await image_toolset.generate_image(
            prompt="A yellow star on white background",
            model="dall-e-3",
        )
        
        assert result["success"] is True
        print(f"✅ DALL-E generated: {result['images']}")
    
    async def test_dalle_with_references_via_agent(self, test_images):
        """DALL-E with references requires Agent for vision fallback."""
        from pantheon.agent import Agent
        from pantheon.apps.builtin.image import ImageGenerationToolSet
        
        agent = Agent(
            name="dalle_test",
            model="gpt-4.1-mini",  # Cheap model for agent
            instructions="Use generate_image with DALL-E.",
        )
        await agent.toolset(ImageGenerationToolSet())
        
        # This triggers call_agent for vision fallback
        result = await agent.run(
            f"Generate a blue version of this image using dall-e-3: {test_images[0]}",
        )
        
        assert result is not None
        print(f"✅ DALL-E with ref via Agent: {result}")


# ============================================================================
# Run Tests
# ============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
