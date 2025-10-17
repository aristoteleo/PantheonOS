"""
Attachment detection system for automatically identifying resources in messages.

This module provides various detectors for different types of attachments:
- ImageDetector: Embedded base64 images (data URI and raw base64)
- PathDetector: Local file/image paths with extension-based classification
- LinkDetector: Web links (excluding files and images)
- StructuredAttachmentExtractor: Structured fields from raw_content and Jupyter notebooks

Also defines attachment types and data structures.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.log import logger


# ==================== Centralized Constants ====================

# Image file extensions (used by multiple detectors)
COMMON_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico"}

# Common file extensions and their MIME types
COMMON_FILE_EXTENSIONS = {
    # Documents
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    # Data
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "xml": "application/xml",
    # Code
    "py": "text/x-python",
    "js": "text/javascript",
    "java": "text/x-java",
    "cpp": "text/x-c++src",
    # Other
    "zip": "application/zip",
    "tar": "application/x-tar",
    "log": "text/plain",
}

# All extensions to exclude from link detection (files + images)
ALL_EXCLUDED_EXTENSIONS = COMMON_IMAGE_EXTENSIONS | set(COMMON_FILE_EXTENSIONS.keys())


# ==================== Type Definitions ====================


class AttachmentType(str, Enum):
    """Type of attachment resource"""
    # Media types
    IMAGE = "image"

    # File types
    FILE = "file"
    FOLDER = "folder"

    # Web resources
    LINK = "link"


class AttachmentSourceType(str, Enum):
    """Source type of attachment data"""
    # Local sources
    BASE64 = "base64"
    FILE_PATH = "file_path"
    FOLDER_PATH = "folder_path"

    # Remote sources
    HTTP_URL = "http_url"
    HTTPS_URL = "https_url"


@dataclass(frozen=True)
class DetectedAttachment:
    """
    A detected attachment/resource in a message.

    This represents any type of resource (image, file, link, code, etc.)
    that was automatically detected from message content.
    """

    # Core fields
    attachment_type: AttachmentType
    source_type: AttachmentSourceType
    data: str  # base64 string / URL / file path / code

    # Metadata
    mime_type: Optional[str] = None  # e.g., "image/png", "application/pdf"
    size: Optional[int] = None  # Size in bytes
    name: Optional[str] = None  # Filename or display name
    description: Optional[str] = None  # User-friendly description

    # Detection metadata
    detected_from: Optional[str] = None  # Source field (for debugging)
    confidence: float = 1.0  # Confidence score (0-1)
    is_valid: bool = True  # Whether validation passed

    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Can contain: language (for code), width/height (for images), etc.

    def __hash__(self):
        """Hash for deduplication"""
        return hash((self.attachment_type, self.source_type, self.data))

    def __eq__(self, other):
        """Equality for deduplication"""
        if not isinstance(other, DetectedAttachment):
            return False
        return (
            self.attachment_type == other.attachment_type
            and self.source_type == other.source_type
            and self.data == other.data
        )

    def __repr__(self):
        return (
            f"DetectedAttachment("
            f"type={self.attachment_type.value}, "
            f"source={self.source_type.value}, "
            f"confidence={self.confidence:.2f}"
            f")"
        )


# ==================== Base Detector Class ====================


class AttachmentDetector(ABC):
    """Base class for attachment detectors"""

    @abstractmethod
    async def detect(self, content: any) -> List[DetectedAttachment]:
        """Detect attachments in content"""
        pass


# ==================== Helper Patterns ====================

# ✅ Markdown image/link exclusion pattern - to avoid detecting markdown syntax items
# Pattern matches: ![...](path) and [...](url) - these are rendered by markdown, not by attachments
_PATTERN_MARKDOWN_IMAGES = re.compile(r'!\[[^\]]*\]\([^\)]+\)', re.MULTILINE)
_PATTERN_MARKDOWN_LINKS = re.compile(r'\[[^\]]*\]\([^\)]+\)', re.MULTILINE)


# ==================== Image Detection ====================


class ImageDetector(AttachmentDetector):
    """Detect embedded base64 images (data URI and raw base64 with magic numbers)"""

    # ✅ Compiled regex patterns for performance (cached at class level)
    _PATTERN_BASE64_DATA_URI = re.compile(r"data:image/([a-zA-Z0-9+-]+);base64,([A-Za-z0-9+/=]+)")
    _PATTERN_BASE64_MAGIC_PNG = re.compile(r'(iVBORw0KGgo[A-Za-z0-9+/=]{50,}?)(?:["\'\s]|$)', re.MULTILINE)
    _PATTERN_BASE64_MAGIC_JPEG = re.compile(r'(/9j/[A-Za-z0-9+/=]{50,}?)(?:["\'\s]|$)', re.MULTILINE)
    _PATTERN_BASE64_MAGIC_GIF = re.compile(r'(R0lGODlh[A-Za-z0-9+/=]{50,}?)(?:["\'\s]|$)', re.MULTILINE)

    async def detect(self, content: str) -> List[DetectedAttachment]:
        """Detect embedded base64 images in content"""
        if not isinstance(content, str):
            return []

        attachments = []
        attachments.extend(await self._detect_base64_images(content))
        attachments.extend(await self._detect_base64_in_content(content))

        return attachments

    async def _detect_base64_images(self, content: str) -> List[DetectedAttachment]:
        """Detect data:image/... base64 images"""
        matches = self._PATTERN_BASE64_DATA_URI.finditer(content)

        attachments = []
        for match in matches:
            mime_format = match.group(1)
            full_data_uri = match.group(0)

            att = DetectedAttachment(
                attachment_type=AttachmentType.IMAGE,
                source_type=AttachmentSourceType.BASE64,
                data=full_data_uri,
                mime_type=f"image/{mime_format}",
                detected_from="inline_base64",
                confidence=0.99,
            )
            attachments.append(att)

        return attachments

    async def _detect_base64_in_content(self, content: str) -> List[DetectedAttachment]:
        """✅ 通过正则在 content 字符串中检测 base64 图片数据"""
        attachments = []
        seen = set()

        # 匹配 base64 模式：以 PNG/JPEG/GIF magic number 开头，后跟 base64 字符
        # ✅ OPTIMIZATION: Using pre-compiled patterns
        patterns = [
            (self._PATTERN_BASE64_MAGIC_PNG, 'png', 'PNG image'),
            (self._PATTERN_BASE64_MAGIC_JPEG, 'jpeg', 'JPEG image'),
            (self._PATTERN_BASE64_MAGIC_GIF, 'gif', 'GIF image'),
        ]

        for pattern_obj, ext, description in patterns:
            matches = pattern_obj.finditer(content)
            for match in matches:
                base64_data = match.group(1)

                # 避免重复
                if base64_data in seen:
                    continue
                seen.add(base64_data)

                # 长度合理性检查（base64 编码的图片通常 > 100 字符）
                if len(base64_data) < 100:
                    continue

                try:
                    att = DetectedAttachment(
                        attachment_type=AttachmentType.IMAGE,
                        source_type=AttachmentSourceType.BASE64,
                        data=f"data:image/{ext};base64,{base64_data}",
                        name=f"embedded_{ext}_image",
                        mime_type=f"image/{ext}",
                        detected_from="base64_in_content",
                        confidence=0.75,  # 中等置信度（在字符串中检测）
                    )
                    attachments.append(att)
                except (ValueError, OSError):
                    continue

        return attachments


# ==================== Unified Path Detection ====================


class PathDetector(AttachmentDetector):
    """
    ✅ Unified detector for both file and image paths.

    Detects file paths and image paths in plain text (excluding Markdown syntax).
    Classifies based on file extension:
    - Image extensions (png, jpg, gif, etc.) → AttachmentType.IMAGE
    - Other extensions (pdf, csv, txt, etc.) → AttachmentType.FILE
    """

    def __init__(self):
        """Initialize compiled patterns with all extensions"""
        all_extensions = COMMON_IMAGE_EXTENSIONS | set(COMMON_FILE_EXTENSIONS.keys())
        extensions_pattern = "|".join(all_extensions)

        # Match simple filenames: word.ext, my-file.txt, data_2024.csv
        self._PATTERN_SIMPLE_PATHS = re.compile(
            rf'\b([a-zA-Z0-9_\-]+\.(?:{extensions_pattern}))\b',
            re.IGNORECASE
        )

        # Match paths with directories: dir/file.txt, ./output/chart.png, /tmp/data.csv
        self._PATTERN_COMPLEX_PATHS = re.compile(
            rf"(?:^|[\s\(\[\{{])(\.?/?[a-zA-Z0-9_\-./\\]+?)\.(?:{extensions_pattern})(?:[\s\)\]\}}]|$)",
            re.IGNORECASE | re.MULTILINE
        )

    async def detect(self, content: str) -> List[DetectedAttachment]:
        """Detect file and image paths, classify by extension"""
        if not isinstance(content, str):
            return []

        attachments = []

        # Remove markdown syntax first to avoid detecting paths in markdown
        cleaned_content = self._remove_markdown_syntax(content)

        # Detect paths and classify by extension
        attachments.extend(await self._detect_and_classify_paths(cleaned_content))

        return attachments

    def _remove_markdown_syntax(self, content: str) -> str:
        """Remove markdown image/link syntax to avoid detecting them as attachments"""
        # Remove markdown images: ![alt](path)
        content = _PATTERN_MARKDOWN_IMAGES.sub('', content)
        # Remove markdown links: [text](url)
        content = _PATTERN_MARKDOWN_LINKS.sub('', content)
        return content

    async def _detect_and_classify_paths(self, content: str) -> List[DetectedAttachment]:
        """Detect all file/image paths and classify by extension"""
        attachments = []
        seen = set()

        # First pass: detect simple filenames (highest confidence)
        matches = self._PATTERN_SIMPLE_PATHS.finditer(content)
        for match in matches:
            filename = match.group(1).lower()
            if filename not in seen:
                seen.add(filename)
                att = self._classify_path(filename, "simple_path", 0.65)
                if att:
                    attachments.append(att)

        # Second pass: detect complex paths (with directories)
        matches = self._PATTERN_COMPLEX_PATHS.finditer(content)
        for match in matches:
            path = match.group(1).strip()
            extension = path.split(".")[-1].lower()

            # Skip invalid paths
            if any(invalid in path for invalid in ["..", "~/www"]):
                continue

            # Normalize and deduplicate
            try:
                path_obj = Path(path)
                normalized_path = str(path_obj.as_posix())

                if normalized_path not in seen:
                    seen.add(normalized_path)
                    att = self._classify_path(normalized_path, "complex_path", 0.70)
                    if att:
                        attachments.append(att)
            except (ValueError, OSError):
                continue

        return attachments

    def _classify_path(self, path: str, detected_from: str, base_confidence: float) -> Optional[DetectedAttachment]:
        """
        Classify a path as IMAGE or FILE based on extension.
        Returns DetectedAttachment or None if extension is not recognized.
        """
        if "." not in path:
            return None

        extension = path.split(".")[-1].lower()

        # ✅ Classify by extension
        if extension in COMMON_IMAGE_EXTENSIONS:
            mime_type = f"image/{extension}"
            att_type = AttachmentType.IMAGE
            confidence = base_confidence
        elif extension in COMMON_FILE_EXTENSIONS:
            mime_type = COMMON_FILE_EXTENSIONS[extension]
            att_type = AttachmentType.FILE
            confidence = base_confidence
        else:
            # Unknown extension
            return None

        att = DetectedAttachment(
            attachment_type=att_type,
            source_type=AttachmentSourceType.FILE_PATH,
            data=path,
            name=Path(path).name if "/" in path or "\\" in path else path,
            mime_type=mime_type,
            detected_from=detected_from,
            confidence=confidence,
        )
        return att


# ==================== Link Detection ====================


class LinkDetector(AttachmentDetector):
    """Detect web links (excluding Markdown syntax, files, and images)"""

    # ✅ Compiled regex pattern for performance (cached at class level)
    _PATTERN_HTTP_LINKS = re.compile(r"https?://[^\s\)]+?(?=[\s\)\]]|$)", re.IGNORECASE | re.MULTILINE)

    async def detect(self, content: str) -> List[DetectedAttachment]:
        """Detect links in content (excludes Markdown links and file/image URLs)"""
        if not isinstance(content, str):
            return []

        attachments = []
        attachments.extend(await self._detect_http_links(content))
        # ✅ Removed: _detect_markdown_links() - Markdown links are rendered by renderMarkdown()

        return attachments

    async def _detect_http_links(self, content: str) -> List[DetectedAttachment]:
        """Detect HTTP(S) URLs (excluding files and images)"""
        # ✅ OPTIMIZATION: Using pre-compiled pattern
        matches = self._PATTERN_HTTP_LINKS.finditer(content)

        attachments = []
        for match in matches:
            url = match.group(0)

            # ✅ Skip URLs pointing to files or images - handled by specialized detectors
            if self._is_file_or_image_url(url):
                continue

            att = DetectedAttachment(
                attachment_type=AttachmentType.LINK,
                source_type=AttachmentSourceType.HTTPS_URL,
                data=url,
                detected_from="http_link",
                confidence=0.85,
            )
            attachments.append(att)

        return attachments

    def _is_file_or_image_url(self, url: str) -> bool:
        """Check if URL points to a file or image"""
        # Extract file extension
        path_part = url.split("?")[0]  # Remove query parameters
        if "." not in path_part:
            return False
        extension = path_part.split(".")[-1].lower()
        return extension in ALL_EXCLUDED_EXTENSIONS


# ==================== Structured Field Extraction ====================


class StructuredAttachmentExtractor(AttachmentDetector):
    """Extract attachments from structured fields (raw_content, etc.)"""

    FIELD_PATTERNS = {
        # Image fields - Common variations
        "image": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        "image_url": (AttachmentType.IMAGE, AttachmentSourceType.HTTPS_URL),
        "base64_uri": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        "image_path": (AttachmentType.IMAGE, AttachmentSourceType.FILE_PATH),
        # Tool output images - Common tool framework patterns
        "output_image": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        "plot_image": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        "figure": (AttachmentType.IMAGE, AttachmentSourceType.FILE_PATH),
        "chart": (AttachmentType.IMAGE, AttachmentSourceType.FILE_PATH),
        "plot": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        "visualization": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        "result_image": (AttachmentType.IMAGE, AttachmentSourceType.BASE64),
        # File fields
        "file": (AttachmentType.FILE, AttachmentSourceType.FILE_PATH),
        "output_file": (AttachmentType.FILE, AttachmentSourceType.FILE_PATH),
        "result_file": (AttachmentType.FILE, AttachmentSourceType.FILE_PATH),
        # Link fields
        "url": (AttachmentType.LINK, AttachmentSourceType.HTTPS_URL),
        "link": (AttachmentType.LINK, AttachmentSourceType.HTTPS_URL),
        # Folder fields
        "folder": (AttachmentType.FOLDER, AttachmentSourceType.FOLDER_PATH),
        "directory": (AttachmentType.FOLDER, AttachmentSourceType.FOLDER_PATH),
    }

    async def detect(self, content: any) -> List[DetectedAttachment]:
        """Extract attachments from structured content"""
        if not isinstance(content, dict):
            return []

        attachments = []

        # Extract from raw_content
        if "raw_content" in content:
            attachments.extend(
                await self._extract_from_dict(content["raw_content"], "raw_content")
            )

        # Extract from top-level fields
        attachments.extend(await self._extract_from_dict(content, "top_level"))

        # Extract from nested data dict (for Jupyter notebook outputs)
        if "data" in content and isinstance(content["data"], dict):
            attachments.extend(
                await self._extract_from_dict(content["data"], "data")
            )

        # Extract from outputs array (Jupyter notebooks)
        if "outputs" in content and isinstance(content["outputs"], list):
            attachments.extend(
                await self._extract_from_notebook_outputs(content["outputs"], "outputs")
            )

        return attachments

    async def _extract_from_dict(
        self, data: dict, source: str
    ) -> List[DetectedAttachment]:
        """Extract attachments from dictionary (with recursive handling for nested structures)"""
        if not isinstance(data, dict):
            return []

        attachments = []

        for key, value in data.items():
            if not value:
                continue

            field_lower = key.lower()

            # FIRST: Check for MIME types (e.g., "image/png", "image/jpeg")
            # These have higher priority than general field patterns
            if key.startswith("image/"):
                # Handle Jupyter notebook MIME type format: "image/png": "base64_string"
                attachments.extend(
                    await self._extract_mime_type_image(key, value, source)
                )
                continue

            # Check for "outputs" array (Jupyter notebooks)
            if key == "outputs" and isinstance(value, list):
                attachments.extend(
                    await self._extract_from_notebook_outputs(value, source)
                )
                continue

            # SECOND: Check against field patterns
            for pattern_key, (att_type, source_type) in self.FIELD_PATTERNS.items():
                if pattern_key not in field_lower:
                    continue

                # Handle single string value
                if isinstance(value, str):
                    att = DetectedAttachment(
                        attachment_type=att_type,
                        source_type=source_type,
                        data=value,
                        name=value.split("/")[-1] if "/" in value else value,
                        detected_from=f"{source}.{key}",
                        confidence=0.85,
                    )
                    attachments.append(att)

                # Handle list of values - with recursive dict support
                elif isinstance(value, list):
                    for idx, item in enumerate(value):
                        if isinstance(item, str):
                            att = DetectedAttachment(
                                attachment_type=att_type,
                                source_type=source_type,
                                data=item,
                                name=item.split("/")[-1] if "/" in item else item,
                                detected_from=f"{source}.{key}[{idx}]",
                                confidence=0.85,
                            )
                            attachments.append(att)
                        # ✅ NEW: Recursively extract from dict items in list
                        elif isinstance(item, dict):
                            # Search for "name" field in dict (for file listings)
                            if "name" in item and isinstance(item["name"], str):
                                name = item["name"]
                                # Check if it looks like a file (has extension or common file pattern)
                                if "." in name or any(ext in name.lower() for ext in COMMON_FILE_EXTENSIONS | COMMON_IMAGE_EXTENSIONS):
                                    att = DetectedAttachment(
                                        attachment_type=att_type,
                                        source_type=source_type,
                                        data=name,
                                        name=name,
                                        detected_from=f"{source}.{key}[{idx}].name",
                                        confidence=0.80,
                                    )
                                    attachments.append(att)

                break

        return attachments

    async def _extract_mime_type_image(
        self, mime_type: str, value: any, source: str
    ) -> List[DetectedAttachment]:
        """Extract images from MIME type keys (e.g., "image/png": "base64_string")"""
        attachments = []

        if isinstance(value, str):
            # Convert to data URI format
            full_data_uri = f"data:{mime_type};base64,{value}"
            att = DetectedAttachment(
                attachment_type=AttachmentType.IMAGE,
                source_type=AttachmentSourceType.BASE64,
                data=full_data_uri,
                mime_type=mime_type,
                detected_from=f"{source}.{mime_type}",
                confidence=0.99,  # High confidence for MIME type format
            )
            attachments.append(att)

        return attachments

    async def _extract_from_notebook_outputs(
        self, outputs: list, source: str
    ) -> List[DetectedAttachment]:
        """Extract attachments from Jupyter notebook outputs array"""
        attachments = []

        for output_idx, output in enumerate(outputs):
            if not isinstance(output, dict):
                continue

            # Handle display_data and execute_result output types
            if output.get("output_type") in ["display_data", "execute_result"]:
                data = output.get("data", {})
                if isinstance(data, dict):
                    # Process MIME types in the data dict
                    for mime_type, content in data.items():
                        if mime_type.startswith("image/") and isinstance(content, str):
                            full_data_uri = f"data:{mime_type};base64,{content}"
                            att = DetectedAttachment(
                                attachment_type=AttachmentType.IMAGE,
                                source_type=AttachmentSourceType.BASE64,
                                data=full_data_uri,
                                mime_type=mime_type,
                                detected_from=f"{source}.outputs[{output_idx}].{mime_type}",
                                confidence=0.99,
                            )
                            attachments.append(att)

        return attachments
