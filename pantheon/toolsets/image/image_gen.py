"""
Image Generation ToolSet.

Provides image generation capabilities via generate_image tool.
Supports text-only models (DALL-E, Imagen), multimodal models (Gemini Nano Banana series),
and native image editing models (OpenAI gpt-image).
"""

from pathlib import Path
from typing import Any

from pantheon.toolset import ToolSet, tool
from pantheon.utils.llm_providers import (
    get_openai_effective_config,
    get_provider_api_key,
    resolve_provider_base_url,
)
from pantheon.utils.vision import (
    ImageStore,
    get_image_store,
    expand_image_references_for_llm,
)
from pantheon.utils.provider_registry import find_provider_for_model
from pantheon.utils.log import logger

IMAGE_MODEL_SHORTCUTS = ("openai", "gemini")
OPENAI_IMAGE_MODEL_ARGS = {
    "size",
    "quality",
    "output_format",
    "background",
}
GEMINI_IMAGE_MODEL_ARGS = {"aspect_ratio", "image_size"}

# Multimodal models that support image input + output via acompletion API
# Gemini Nano Banana series: Pro / Nano Banana 2 / Nano Banana first-gen
MULTIMODAL_IMAGE_MODELS = {
    "gemini/gemini-3-pro-image-preview",      # Nano Banana Pro
    "gemini/gemini-3.1-flash-image-preview",  # Nano Banana 2
    "gemini/gemini-2.5-flash-image",          # Nano Banana first-gen
}

# Models that support native image editing via aimage_edit API (accept reference images)
IMAGE_EDIT_MODELS = {
    "gpt-image-2",
    "gpt-image-1",
    "gpt-image-1.5",
    "chatgpt-image-latest",
}


class ImageGenerationToolSet(ToolSet):
    """Image generation toolset supporting text-only and multimodal models."""

    def __init__(
        self,
        name: str = "image_generation",
        fallback_vision_model: str | None = None,
    ):
        super().__init__(name)
        self._image_store: ImageStore | None = None
        self.fallback_vision_model = fallback_vision_model or "gemini/gemini-2.5-flash"

    @property
    def default_model(self) -> str:
        """Dynamically resolve image generation model from Settings."""
        try:
            from pantheon.settings import get_settings
            from pantheon.utils.model_selector import get_model_selector

            settings = get_settings()
            config = settings.get("image_gen_model", "normal")

            if config in ("high", "normal", "low"):
                selector = get_model_selector()
                if hasattr(selector, "resolve_image_gen_model"):
                    models = selector.resolve_image_gen_model(config)
                    return (
                        models[0] if models else "gemini/gemini-3-pro-image-preview"
                    )
            return config
        except Exception:
            # Fallback if settings not available
            return "gemini/gemini-2.0-flash-exp-image-generation"

    @property
    def image_store(self) -> ImageStore:
        if self._image_store is None:
            self._image_store = get_image_store()
        return self._image_store

    def _is_multimodal_model(self, model: str) -> bool:
        """Check if model supports multimodal image generation."""
        return any(m in model for m in MULTIMODAL_IMAGE_MODELS)

    def _supports_image_edit(self, model: str) -> bool:
        """Check if model supports native image editing (reference image input)."""
        return any(m in model for m in IMAGE_EDIT_MODELS)

    def _available_image_models(self) -> dict[str, list[str]]:
        """Return supported image-generation defaults by provider."""
        try:
            from pantheon.utils.model_selector import DEFAULT_IMAGE_GEN_MODELS

            return {
                provider: [
                    model
                    for quality in ("high", "normal", "low")
                    for model in DEFAULT_IMAGE_GEN_MODELS.get(provider, {}).get(quality, [])
                ]
                for provider in IMAGE_MODEL_SHORTCUTS
            }
        except Exception:
            return {
                "openai": ["gpt-image-2", "chatgpt-image-latest", "gpt-image-1.5", "gpt-image-1"],
                "gemini": [
                    "gemini/gemini-3-pro-image-preview",
                    "gemini/gemini-3.1-flash-image-preview",
                    "gemini/gemini-2.5-flash-image",
                ],
            }

    def _image_config_error(self, error: str) -> dict:
        """Build an actionable image-generation configuration error."""
        return {
            "success": False,
            "error": error,
            "available_model_shortcuts": list(IMAGE_MODEL_SHORTCUTS),
            "available_models": self._available_image_models(),
        }

    def _is_openai_compatible_image_provider(self, provider_key: str, provider_config: dict) -> bool:
        """Return whether the provider can be attempted via OpenAI Images APIs."""
        return provider_key in ("openai", "unknown") or bool(
            provider_config.get("openai_compatible")
        )

    def _resolve_requested_image_model(self, model: str | None) -> tuple[str | None, dict | None]:
        """Resolve model names and provider shortcuts to a concrete image model."""
        if not model:
            return self.default_model, None

        normalized = model.strip().lower()
        if normalized in IMAGE_MODEL_SHORTCUTS:
            try:
                from pantheon.utils.model_selector import DEFAULT_IMAGE_GEN_MODELS

                provider_models = DEFAULT_IMAGE_GEN_MODELS.get(normalized, {})
                models = provider_models.get("normal") or provider_models.get("high") or []
                if models:
                    return models[0], None
            except Exception:
                fallback = self._available_image_models().get(normalized, [])
                if fallback:
                    return fallback[0], None
            return None, self._image_config_error(
                f"Unsupported image generation provider shortcut: {model}"
            )

        try:
            from pantheon.utils.provider_registry import get_provider_config

            provider_config_for_shortcut = get_provider_config(normalized)
        except Exception:
            provider_config_for_shortcut = {}
        if provider_config_for_shortcut:
            return None, self._image_config_error(
                f"Provider shortcut '{model}' has no built-in image default. "
                f"Use a concrete image model like '{model}/<model-name>' or configure image_gen_models.{model}."
            )

        provider_key, _model_name, provider_config = find_provider_for_model(model)
        if provider_key == "unknown":
            # Bare unknown model names are treated as OpenAI-compatible image
            # models so user-provided or newly released model IDs can still run.
            return model, None

        if provider_key in IMAGE_MODEL_SHORTCUTS:
            return model, None

        if not self._is_openai_compatible_image_provider(provider_key, provider_config):
            return None, self._image_config_error(
                f"Unsupported image generation provider for model '{model}': {provider_key}. "
                "Use 'openai', 'gemini', or a concrete OpenAI-compatible provider/model."
            )

        return model, None

    def _validate_model_connection(self, model: str) -> dict | None:
        """Return an error dict when the selected provider has no credentials."""
        provider_key, _model_name, provider_config = find_provider_for_model(model)
        if provider_key == "unknown":
            provider_key = "openai"
        if provider_key not in IMAGE_MODEL_SHORTCUTS and not provider_config.get("openai_compatible"):
            return self._image_config_error(
                f"Unsupported image generation provider for model '{model}': {provider_key}"
            )

        _base_url, api_key = self._resolve_model_connection(model)
        if api_key:
            return None

        return self._image_config_error(
            f"Missing API key for image generation provider '{provider_key}'"
        )

    def _split_model_args(self, model: str, model_args: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict | None]:
        """Validate and route provider-specific model arguments."""
        if not model_args:
            return {}, None

        provider_key, _model_name, _provider_config = find_provider_for_model(model)
        if provider_key == "unknown":
            provider_key = "openai"

        allowed = (
            GEMINI_IMAGE_MODEL_ARGS
            if provider_key == "gemini"
            else OPENAI_IMAGE_MODEL_ARGS
            if provider_key != "gemini"
            else set()
        )
        unsupported = sorted(set(model_args) - allowed)
        if unsupported:
            return None, self._image_config_error(
                f"Unsupported model_args for image generation provider '{provider_key}': {unsupported}"
            )

        return dict(model_args), None

    def _get_chat_id(self) -> str:
        """Get chat_id from context (per-conversation isolation)."""
        context = self.get_context()
        if context:
            # Prefer chat_id; client_id is the UI connection id (shared across
            # a user's chats) and only a fallback.
            return context.get("chat_id") or context.get("client_id") or "default"
        return "default"

    def _resolve_model_connection(self, model: str) -> tuple[str | None, str | None]:
        """Resolve effective base URL and API key for image generation entrypoints."""
        provider_key, _model_name, provider_config = find_provider_for_model(model)
        if provider_key in ("openai", "unknown"):
            base_url, api_key = get_openai_effective_config()
            return base_url or provider_config.get("base_url"), api_key or None

        provider_key_value = get_provider_api_key(
            provider_key,
            provider_config.get("api_key_env"),
        )

        return resolve_provider_base_url(provider_key, provider_config.get("base_url")), provider_key_value

    def _extract_cost_from_response(self, response) -> float:
        """Extract cost from API response.

        Args:
            response: Response object from acompletion or aimage_generation

        Returns:
            Cost in USD, or 0.0 if calculation fails
        """
        try:
            from pantheon.utils.provider_registry import completion_cost
            cost = completion_cost(completion_response=response) or 0.0
            from pantheon.utils.log import logger
            logger.debug(f"Image generation cost: ${cost:.6f}")
            return cost
        except Exception as e:
            from pantheon.utils.log import logger
            logger.debug(f"Cost calculation unavailable: {e}")
            return 0.0

    @tool
    async def generate_image(
        self,
        prompt: str,
        reference_images: list[str] | None = None,
        model: str | None = None,
        model_args: dict[str, Any] | None = None,
    ) -> dict:
        """Generate an image from a text description.

        Use this tool to create images based on your description. You can also
        provide reference images for style transfer or image editing.

        Args:
            prompt: Detailed image instruction. Include the subject,
                setting/context, composition/layout, visual style,
                colors/materials, lighting, camera/viewpoint, aspect-ratio
                intent, and any required or forbidden text. For diagrams or
                scientific figures, specify panels, labels, arrows, relative
                placement, and visual hierarchy. For edits or reference images,
                state what to preserve and what to change. Avoid vague prompts
                like "make it better"; describe the exact desired result.
                When using reference_images, refer to them by order in prompt:
                "first image", "second image", etc.
                Example: "Combine the style of the first image with the subject of the second image"
            reference_images: File paths of existing images to use as reference.
                Images are passed to the model in array order.
                Example: ["/path/to/style.png", "/path/to/subject.png"]
            model: Model selector for image generation.
                Leave empty to use the configured default image model.
                Use "gemini" to select the default Gemini image model.
                Use "openai" to select the default OpenAI image model.
                Use a concrete model name such as "gpt-image-2" or
                "gemini/gemini-3.1-flash-image-preview" when you need a
                specific model.
                Use "provider/model-name" for other OpenAI-compatible image
                endpoints; the tool will try that provider's configured
                base_url and API key. Do not pass a provider name by itself
                unless it is "gemini" or "openai".
            model_args: Optional provider-specific image generation arguments.
                For Gemini models, supported keys are:
                - aspect_ratio: e.g. "1:1", "16:9", "9:16"
                  Default: provider/model default, usually square.
                - image_size: e.g. "1K", "2K", "4K" when supported by the model
                  Default: provider/model default.
                For OpenAI or OpenAI-compatible image endpoints, supported keys are:
                - size: e.g. "1024x1024", "1536x1024", "1024x1536", "auto"
                  Default: "1024x1024".
                - quality: e.g. "low", "medium", "high", "auto"
                  Default: provider/model default.
                - output_format: e.g. "png", "jpeg", "webp"
                  Default: provider/model default, usually PNG/base64.
                - background: e.g. "auto", "transparent", "opaque"
                  Default: provider/model default.
                Unsupported keys return a structured error with available options.

        Returns:
            Dictionary with:
            - success: Whether generation succeeded
            - images: List of file paths to generated images
            - error: Error message if failed
        """
        model, error = self._resolve_requested_image_model(model)
        if error:
            return error
        assert model is not None

        error = self._validate_model_connection(model)
        if error:
            return error

        routed_model_args, error = self._split_model_args(model, model_args)
        if error:
            return error
        routed_model_args = routed_model_args or {}

        try:
            logger.info(
                f"[IMAGE_GEN] Starting generate_image | model={model} "
                f"reference_images={len(reference_images or [])}"
            )
            if self._is_multimodal_model(model):
                # Multimodal model: use acompletion API (supports image in/out)
                return await self._multimodal_image_gen(
                    prompt, model, reference_images, routed_model_args
                )
            else:
                # Text-only model: use aimage_generation / aimage_edit API
                if reference_images and self._supports_image_edit(model):
                    # Native image edit: pass reference images directly
                    return await self._image_edit_gen(
                        prompt, model, reference_images, routed_model_args
                    )
                elif reference_images:
                    # Fallback: describe reference images using vision model
                    logger.warning(
                        f"[IMAGE_GEN] Model {model} does not support native image edit; "
                        f"falling back to reference-image description"
                    )
                    description = await self._describe_reference_images(
                        reference_images
                    )
                    if description:
                        prompt = f"{prompt}. Reference: {description}"
                return await self._text_input_image_gen(prompt, model, routed_model_args)
        except Exception as e:
            logger.error(f"[IMAGE_GEN] generate_image failed | model={model} error={e}")
            return {"success": False, "error": str(e)}

    async def _text_input_image_gen(
        self,
        prompt: str,
        model: str,
        model_args: dict[str, Any] | None = None,
    ) -> dict:
        """Text-only image generation (DALL-E, Imagen)."""
        from pantheon.utils.adapters import get_adapter

        provider_key, model_name, provider_config = find_provider_for_model(model)
        base_url, api_key = self._resolve_model_connection(model)
        adapter = get_adapter("openai")
        response = await adapter.aimage_generation(
            model=model_name if provider_key != "unknown" else model,
            prompt=prompt,
            size=(model_args or {}).pop("size", "1024x1024"),
            n=1,
            base_url=base_url,
            api_key=api_key,
            **(model_args or {}),
        )

        # Extract cost from response
        cost = self._extract_cost_from_response(response)

        chat_id = self._get_chat_id()
        saved_paths = []
        data_uris = []
        for item in response.data:
            if item.b64_json:
                uri = f"data:image/png;base64,{item.b64_json}"
                path = self.image_store.save_base64_image(chat_id, uri)
                saved_paths.append(path)
                data_uris.append(uri)
            elif item.url:
                saved_paths.append(item.url)

        result = {
            "success": True,
            "images": saved_paths,
            "model_used": model,
            "_metadata": {
                "current_cost": cost,
            }
        }
        if data_uris:
            result["base64_uri"] = data_uris
            result["hidden_to_model"] = ["base64_uri"]
        return result

    async def _multimodal_image_gen(
        self,
        prompt: str,
        model: str,
        reference_images: list[str] | None,
        model_args: dict[str, Any] | None = None,
    ) -> dict:
        """Multimodal image generation (Gemini Nano Banana series).

        Uses chat completion API with modalities parameter to generate images.
        This goes through the provider's configured endpoint and supports image generation.

        Supported models:
        - gemini-3-pro-image-preview (Nano Banana Pro, up to 4K)
        - gemini-3.1-flash-image-preview (Nano Banana 2)
        - gemini-2.5-flash-image (Nano Banana original)
        """
        # Build multimodal message
        content = [{"type": "text", "text": prompt}]
        if reference_images:
            for ref in reference_images:
                # Just pass the path - normalization happens in process_message_images
                content.append({"type": "image_url", "image_url": {"url": ref}})

        messages = [{"role": "user", "content": content}]

        # Normalize paths (raw path → file://) then expand (file:// → base64)
        chat_id = self._get_chat_id()
        self.image_store.process_message_images(messages[0], chat_id)
        messages = expand_image_references_for_llm(messages)

        from pantheon.utils.adapters import get_adapter
        from pantheon.utils.provider_registry import find_provider_for_model

        provider_key, model_name, provider_config = find_provider_for_model(model)
        base_url, api_key = self._resolve_model_connection(model)
        sdk_type = "openai" if provider_key == "openai" and api_key and base_url else provider_config.get("sdk", "openai")
        adapter = get_adapter(sdk_type)

        collected_chunks = await adapter.acompletion(
            model=model if sdk_type == "openai" else model_name,
            messages=messages,
            stream=True,
            base_url=base_url,
            api_key=api_key,
            modalities=["text", "image"],
            image_config=model_args or None,
        )
        from pantheon.utils.llm import stream_chunk_builder
        response = stream_chunk_builder(collected_chunks)

        # Extract cost from response
        cost = self._extract_cost_from_response(response)

        message = response.choices[0].message
        images = getattr(message, "images", None) or []

        # Save generated images
        # Format: [{'image_url': {'url': 'data:image/png;base64,...'}}]
        saved_paths = []
        data_uris = []
        for img in images:
            if isinstance(img, dict):
                url = img.get("image_url", {}).get("url", "")
            else:
                url = img
            if url:
                path = self.image_store.save_base64_image(chat_id, url)
                saved_paths.append(path)
                if url.startswith("data:"):
                    data_uris.append(url)

        result = {
            "success": True,
            "images": saved_paths,
            "text": message.content,
            "model_used": model,
            "_metadata": {
                "current_cost": cost,
            }
        }
        if data_uris:
            result["base64_uri"] = data_uris
            result["hidden_to_model"] = ["base64_uri"]
        return result

    async def _image_edit_gen(
        self,
        prompt: str,
        model: str,
        reference_images: list[str],
        model_args: dict[str, Any] | None = None,
    ) -> dict:
        """Native image editing via aimage_edit API (OpenAI gpt-image models)."""
        logger.info(
            f"[IMAGE_GEN] Starting native image edit | model={model} "
            f"reference_images={len(reference_images)}"
        )
        # Resolve file paths (strip file:// prefix, normalize)
        resolved_paths = []
        for ref in reference_images:
            path = ref
            if path.startswith("file://"):
                path = path[7:]
            resolved = self.image_store.normalize_local_path(path)
            resolved_paths.append(Path(resolved))

        from pantheon.utils.adapters import get_adapter

        provider_key, model_name, provider_config = find_provider_for_model(model)
        base_url, api_key = self._resolve_model_connection(model)
        adapter = get_adapter("openai")
        response = await adapter.aimage_edit(
            model=model_name if provider_key != "unknown" else model,
            image=resolved_paths,
            prompt=prompt,
            size=(model_args or {}).pop("size", "1024x1024"),
            n=1,
            base_url=base_url,
            api_key=api_key,
            **(model_args or {}),
        )

        # Extract cost from response
        cost = self._extract_cost_from_response(response)

        chat_id = self._get_chat_id()
        saved_paths = []
        data_uris = []
        for item in response.data:
            if item.b64_json:
                uri = f"data:image/png;base64,{item.b64_json}"
                path = self.image_store.save_base64_image(chat_id, uri)
                saved_paths.append(path)
                data_uris.append(uri)
            elif item.url:
                saved_paths.append(item.url)

        result = {
            "success": True,
            "images": saved_paths,
            "model_used": model,
            "_metadata": {
                "current_cost": cost,
            }
        }
        if data_uris:
            result["base64_uri"] = data_uris
            result["hidden_to_model"] = ["base64_uri"]
        logger.info(
            f"[IMAGE_GEN] Native image edit completed | model={model} "
            f"outputs={len(saved_paths)}"
        )
        return result

    async def _describe_reference_images(self, reference_images: list[str]) -> str:
        """Vision fallback: describe reference images for text-only models."""
        context = self.get_context()
        if not context:
            return ""

        # Check if call_agent is available
        call_agent = context.get("_call_agent")
        if not call_agent:
            return ""

        # Build message
        content = [
            {"type": "text", "text": "Describe these images in detail for recreation:"}
        ]
        for ref in reference_images:
            content.append({"type": "image_url", "image_url": {"url": ref}})

        messages = [{"role": "user", "content": content}]

        # Normalize paths then expand to base64
        chat_id = self._get_chat_id()
        self.image_store.process_message_images(messages[0], chat_id)
        messages = expand_image_references_for_llm(messages)

        try:
            result = await call_agent(
                messages=messages,
                model=self.fallback_vision_model,
            )
            return result if isinstance(result, str) else str(result)
        except Exception as e:
            return f"[Error describing images: {e}]"
