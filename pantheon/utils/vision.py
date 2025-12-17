"""
Vision utilities for multimodal agent input.

This module provides:
1. VisionInput: Pydantic model for vision input
2. ImageStore: Disk-based image storage with deduplication
3. Utilities for converting between paths, base64, and OpenAI format
"""

import io
import re
import copy
import base64
import hashlib
from pathlib import Path
from typing import Optional

from PIL import Image
from pydantic import BaseModel

from .log import logger


# ============================================================================
# Constants
# ============================================================================

MAX_IMAGE_DIMENSION = 1568  # Claude/OpenAI recommended max (avoids internal resize)


# ============================================================================
# VisionInput Model
# ============================================================================


class VisionInput(BaseModel):
    """Vision input model containing images and prompt."""

    images: list[str]  # List of image URLs (base64 data URIs or HTTP URLs)
    prompt: str


def vision_input(
    prompt: str, image_paths: list[str] | str, from_path: bool = False
) -> VisionInput:
    """Create a VisionInput from prompt and image paths/URLs.

    Args:
        prompt: Text prompt
        image_paths: Image paths or URLs
        from_path: If True, use file:// paths (will be expanded to Base64 before LLM call)

    Returns:
        VisionInput instance

    Note:
        When from_path=True, paths are stored as file:// URIs. The actual Base64
        conversion happens in expand_image_references_for_llm() just before the
        LLM API call, ensuring efficient Memory storage.
    """
    if isinstance(image_paths, str):
        image_paths = [image_paths]

    if from_path:
        # Use file:// paths - will be expanded to Base64 before LLM call
        images = []
        for path in image_paths:
            abs_path = str(Path(path).resolve())
            if not abs_path.startswith("file://"):
                abs_path = f"file://{abs_path}"
            images.append(abs_path)
        return VisionInput(images=images, prompt=prompt)
    else:
        return VisionInput(images=image_paths, prompt=prompt)


def vision_to_openai(vision: VisionInput) -> list[dict]:
    """Convert VisionInput to OpenAI message format.

    Args:
        vision: VisionInput instance

    Returns:
        List of message dicts in OpenAI format
    """
    messages = [{"role": "user", "content": [{"type": "text", "text": vision.prompt}]}]
    for img in vision.images:
        messages[0]["content"].append(
            {
                "type": "image_url",
                "image_url": {"url": img},
            }
        )
    return messages


# ============================================================================
# Image Base64 Utilities (Unified PIL-based)
# ============================================================================


def get_image_base64(file_path: str, max_size: int = MAX_IMAGE_DIMENSION) -> str:
    """
    Read a local image file and return its base64 data URI.

    Automatically resizes large images to reduce transmission cost.
    All images are processed through PIL for consistent output.

    Args:
        file_path: Path to image file (with or without file:// prefix)
        max_size: Maximum dimension (width or height). Default: 1568px

    Returns:
        Data URI string (data:image/...;base64,...)
    """
    # Strip file:// prefix if present
    if file_path.startswith("file://"):
        file_path = file_path[7:]

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {file_path}")

    # Open with PIL
    img = Image.open(path)

    # Resize if exceeds max dimension
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    # Encode to buffer
    buffer = io.BytesIO()

    if img.mode in ("RGBA", "LA", "P"):
        # Preserve transparency with PNG
        img.save(buffer, format="PNG", optimize=True)
        mime = "png"
    else:
        # Use JPEG for RGB (smaller file size)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        mime = "jpeg"

    return f"data:image/{mime};base64,{base64.b64encode(buffer.getvalue()).decode()}"


# Backward compatibility alias
path_to_image_url = get_image_base64


def path_to_vision(
    prompt: str, image_paths: list[str] | str | Path | list[Path]
) -> VisionInput:
    """Create VisionInput from local image paths.

    Args:
        prompt: Text prompt
        image_paths: One or more local image paths

    Returns:
        VisionInput with file:// paths (expanded to Base64 before LLM call)
    """
    if isinstance(image_paths, (str, Path)):
        image_paths = [image_paths]

    images = [f"file://{Path(p).resolve()}" for p in image_paths]
    return VisionInput(images=images, prompt=prompt)


def parse_image_mentions(
    message: str, workspace: Path | str | None = None
) -> list[dict]:
    """
    Parse @image:path tokens from message and build OpenAI multimodal format.

    Args:
        message: User input string (may contain @image: tokens)
        workspace: Workspace directory for resolving relative paths.
                   If None, uses workspace from settings

    Returns:
        List of message dicts in OpenAI format

    Example:
        >>> parse_image_mentions("@image:photo.png describe this")
        [{"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "file:///abs/path/photo.png"}}
        ]}]
    """
    import re

    # Pattern to match @image:path
    image_pattern = r"@image:([^\s]+)"
    matches = re.findall(image_pattern, message)

    if not matches:
        # No images - plain text message
        return [{"role": "user", "content": message}]

    # Get workspace
    if workspace is None:
        from pantheon.settings import get_settings

        workspace = get_settings().workspace
    workspace = Path(workspace)

    # Remove @image: tokens from text
    clean_text = re.sub(image_pattern, "", message).strip()

    # Build multimodal content list
    content = []

    # Add text if present
    if clean_text:
        content.append({"type": "text", "text": clean_text})

    # Add images as file:// paths
    for image_path in matches:
        try:
            # Resolve relative paths to workspace
            if not image_path.startswith("/"):
                image_path = str(workspace / image_path)

            path = Path(image_path)

            # Verify file exists
            if not path.exists():
                content.append(
                    {"type": "text", "text": f"[Error: Image not found: {image_path}]"}
                )
                continue

            # Use file:// path (Agent will handle expansion to Base64)
            content.append(
                {"type": "image_url", "image_url": {"url": f"file://{path.resolve()}"}}
            )

        except Exception as e:
            content.append(
                {"type": "text", "text": f"[Error loading image {image_path}: {e}]"}
            )

    return [{"role": "user", "content": content}]


# ============================================================================
# ImageStore - Disk-based Image Storage
# ============================================================================


class ImageStore:
    """
    Manages storage of images for chat sessions.

    Storage location: ~/.pantheon/images/<chat_id>/<md5_hash>.<ext>

    Handles:
    1. Saving base64 images to disk (deduplicated by hash)
    2. Validating and resolving local file paths
    3. Processing message dicts to convert images to file:// references
    """

    def __init__(self, storage_root: str | Path | None = None):
        if storage_root is None:
            storage_root = Path.home() / ".pantheon" / "images"
        self.storage_root = Path(storage_root).resolve()

    def _get_chat_dir(self, chat_id: str) -> Path:
        """Get or create the image directory for a specific chat."""
        path = self.storage_root / chat_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_base64_image(self, chat_id: str, base64_data: str) -> str:
        """
        Save a base64 string image to disk.

        Args:
            chat_id: The ID of the chat
            base64_data: Full data URI (data:image/png;base64,...)

        Returns:
            Absolute local file path to the saved image
        """
        try:
            # Parse header
            header = None
            data_str = base64_data
            if "," in base64_data:
                parts = base64_data.split(",", 1)
                header = parts[0]
                data_str = parts[1]

            # Determine extension from header
            ext = "png"
            if header:
                match = re.search(r"data:image/(\w+);base64", header)
                if match:
                    ext = match.group(1)
                    if ext == "jpeg":
                        ext = "jpg"

            # Decode
            image_bytes = base64.b64decode(data_str)

            # Compute hash for deduplication
            file_hash = hashlib.md5(image_bytes).hexdigest()
            filename = f"{file_hash}.{ext}"

            # Save
            chat_dir = self._get_chat_dir(chat_id)
            file_path = chat_dir / filename

            if not file_path.exists():
                with open(file_path, "wb") as f:
                    f.write(image_bytes)
                logger.debug(f"Saved image to {file_path}")

            return str(file_path.absolute())

        except Exception as e:
            logger.error(f"Failed to save base64 image: {e}")
            raise

    def normalize_local_path(self, path_str: str) -> str:
        """
        Normalize and verify a local file path.

        Args:
            path_str: Raw path (e.g. "file:///tmp/a.png", "/tmp/a.png")

        Returns:
            Absolute file path

        Raises:
            FileNotFoundError: If file does not exist
        """
        if path_str.startswith("file://"):
            path_str = path_str[7:]

        path = Path(path_str).resolve()

        if not path.exists():
            # Try relative to cwd
            rel_path = Path.cwd() / path_str
            if rel_path.exists():
                return str(rel_path.resolve())
            raise FileNotFoundError(f"Image file not found: {path_str}")

        return str(path)

    def process_message_images(self, message: dict, chat_id: str) -> None:
        """
        Process a single message dict in-place.

        - Extracts content from message
        - Skips if content is not a list (plain text)
        - For each image_url item:
          - Base64 → save to disk → replace with file:// path
          - Local path → verify → standardize to file:// path
          - HTTP URL → pass through
        """
        content = message.get("content")
        if not isinstance(content, list):
            return

        for item in content:
            if not isinstance(item, dict):
                continue

            if item.get("type") == "image_url" and "image_url" in item:
                url = item["image_url"].get("url", "")

                try:
                    if url.startswith("data:image/"):
                        # Base64 → save to disk
                        saved_path = self.save_base64_image(chat_id, url)
                        item["image_url"]["url"] = f"file://{saved_path}"

                    elif url.startswith("file://") or url.startswith("/"):
                        # Local path → normalize
                        norm_path = self.normalize_local_path(url)
                        item["image_url"]["url"] = f"file://{norm_path}"

                    # HTTP URLs pass through unchanged

                except Exception as e:
                    logger.error(f"Error processing image: {e}")


# Global Singleton
_image_store: Optional[ImageStore] = None


def get_image_store() -> ImageStore:
    """Get or create global ImageStore instance."""
    global _image_store
    if _image_store is None:
        _image_store = ImageStore()
    return _image_store


# ============================================================================
# LLM Message Expansion
# ============================================================================


def expand_image_references_for_llm(messages: list[dict]) -> list[dict]:
    """
    Expand file:// image references to Base64 data URIs for LLM consumption.

    Called just before sending messages to the LLM API.

    Args:
        messages: List of message dicts (will be deep copied)

    Returns:
        New list with file:// paths converted to base64 data URIs
    """
    result = copy.deepcopy(messages)

    for msg in result:
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict):
                continue

            if item.get("type") == "image_url" and "image_url" in item:
                url = item["image_url"].get("url", "")

                if url.startswith("file://"):
                    try:
                        base64_uri = get_image_base64(url)
                        item["image_url"]["url"] = base64_uri
                    except Exception as e:
                        logger.error(f"Failed to expand image {url}: {e}")

    return result
