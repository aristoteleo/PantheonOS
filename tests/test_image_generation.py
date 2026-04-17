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
    from pantheon.toolsets.image import ImageGenerationToolSet
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
    async def test_dalle_bypasses_proxy_and_uses_openai_provider(self, image_toolset, monkeypatch):
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
        monkeypatch.setenv("LLM_PROXY_ENABLED", "true")
        monkeypatch.setenv("LLM_PROXY_URL", "https://proxy.example/v1")
        monkeypatch.setenv("LLM_PROXY_KEY", "proxy-key")
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
        assert calls["base_url"] == "https://api.openai.com/v1"


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
        from pantheon.toolsets.image import ImageGenerationToolSet
        
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
