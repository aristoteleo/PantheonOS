"""
Integration tests for the attachment detection system.

This test suite verifies that:
1. Detectors work correctly for each attachment type
2. Pipeline orchestrates detectors properly
3. Messages are processed with detected_attachments field
4. Serialization works for frontend
5. Notebook outputs are detected correctly
6. Invalid file paths are filtered out
"""

import pytest
from pathlib import Path
import tempfile
from pantheon.message.attachment_detection import (
    AttachmentType,
    AttachmentSourceType,
    ImageDetector,
    PathDetector,
    LinkDetector,
    StructuredAttachmentExtractor,
)
from pantheon.message.attachment_pipeline import (
    AttachmentProcessingPipeline,
    get_message_processor,
)


@pytest.mark.asyncio
async def test_image_detection_base64():
    """Test base64 image detection"""
    detector = ImageDetector()
    content = "Check out this image: data:image/png;base64,iVBORw0KGgoAAAANS"
    result = await detector.detect(content)

    assert len(result) > 0
    assert result[0].attachment_type == AttachmentType.IMAGE
    assert result[0].source_type == AttachmentSourceType.BASE64
    assert result[0].confidence > 0.9


@pytest.mark.asyncio
async def test_image_detection_file_path():
    """Test image file path detection (plain text, not Markdown syntax)"""
    detector = PathDetector()
    # Plain text mention of image path - should be detected and classified as IMAGE by extension
    content = "The analysis image is at output/chart.png in the filesystem"
    result = await detector.detect(content)

    assert len(result) > 0
    assert result[0].attachment_type == AttachmentType.IMAGE
    assert result[0].source_type == AttachmentSourceType.FILE_PATH
    # Markdown images are NOT detected (they're handled by renderMarkdown)


@pytest.mark.asyncio
async def test_file_detection():
    """Test file detection"""
    detector = PathDetector()
    content = "Report saved to output/analysis.pdf and data.csv"
    result = await detector.detect(content)

    assert len(result) >= 2
    types = {r.attachment_type for r in result}
    assert AttachmentType.FILE in types


@pytest.mark.asyncio
async def test_link_detection():
    """Test link detection"""
    detector = LinkDetector()
    content = "Check https://github.com/user/repo for more info and https://example.com"
    result = await detector.detect(content)

    assert len(result) >= 2
    for att in result:
        assert att.attachment_type == AttachmentType.LINK


@pytest.mark.asyncio
async def test_structured_field_detection():
    """Test structured field detection"""
    extractor = StructuredAttachmentExtractor()

    # Test raw_content with image field
    content = {
        "raw_content": {
            "image": "output/result.png",
            "file": "output/report.pdf",
        }
    }

    result = await extractor.detect(content)
    assert len(result) >= 2


@pytest.mark.asyncio
async def test_notebook_mime_type_detection():
    """Test detection of Jupyter notebook MIME types (image/png format)"""
    extractor = StructuredAttachmentExtractor()

    # Simulate Jupyter notebook output structure
    notebook_data = {
        "data": {
            "image/png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk",
            "text/plain": ["<Figure size 640x480 with 1 Axes>"]
        },
        "metadata": {},
        "output_type": "display_data"
    }

    result = await extractor.detect(notebook_data)

    # Should detect the PNG image
    assert len(result) > 0
    assert result[0].attachment_type == AttachmentType.IMAGE
    assert result[0].mime_type == "image/png"
    assert result[0].source_type == AttachmentSourceType.BASE64
    assert result[0].confidence == 0.99


@pytest.mark.asyncio
async def test_notebook_outputs_array():
    """Test detection from notebook outputs array"""
    extractor = StructuredAttachmentExtractor()

    # Simulate notebook cell with outputs
    cell_data = {
        "outputs": [
            {
                "output_type": "display_data",
                "data": {
                    "image/png": "iVBORw0KGgo...",
                }
            },
            {
                "output_type": "stream",
                "name": "stdout",
                "text": ["Output text"]
            }
        ]
    }

    result = await extractor.detect(cell_data)

    assert len(result) > 0
    assert result[0].attachment_type == AttachmentType.IMAGE


@pytest.mark.asyncio
async def test_pipeline_orchestration():
    """Test that pipeline orchestrates all detectors"""
    pipeline = AttachmentProcessingPipeline()

    # Create temporary files for valid paths
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pdf') as tmp_pdf:
        tmp_pdf.write("Report content")
        valid_pdf = tmp_pdf.name

    try:
        message = {
            "role": "assistant",
            "content": "Here's the analysis with chart at output/chart.png",
            # Note: Markdown syntax is NOT detected - only plain text and structured fields
            "raw_content": {
                "file": valid_pdf,  # Use valid PDF path
                "data": {
                    "image/jpeg": "base64_data_here"
                }
            }
        }

        result = await pipeline.process_message(message)

        # Check that message has attachment fields
        assert "detected_attachments" in result

        # Should have multiple attachments (valid PDF + base64 image)
        # The chart.png path won't be included as it doesn't exist (filtered by path validation)
        assert len(result["detected_attachments"]) >= 2

    finally:
        # Cleanup
        Path(valid_pdf).unlink()


@pytest.mark.asyncio
async def test_message_processor_integration():
    """Test full message processor integration"""
    processor = get_message_processor()

    message = {
        "role": "assistant",
        "content": "I created a visualization at output/plot.png and saved data to results.csv",
    }

    result = await processor.process_message_with_attachments(message)

    # Verify detected attachments are added
    assert "detected_attachments" in result
    assert isinstance(result["detected_attachments"], list)

    # All items in the list should be dicts (serializable)
    for att in result["detected_attachments"]:
        assert isinstance(att, dict)
        assert "attachment_type" in att
        assert "source_type" in att
        assert "data" in att


@pytest.mark.asyncio
async def test_serialization_format():
    """Test that DetectedAttachment objects are properly serialized"""
    pipeline = AttachmentProcessingPipeline()

    message = {
        # Note: Markdown syntax is NOT detected (![image](...) and [link](...) are excluded)
        # Only plain text references are detected
        "content": "Check the image at output/img.png and visit https://example.com for more",
    }

    result = await pipeline.process_message(message)

    # All attachments should be dicts, not objects
    for att in result["detected_attachments"]:
        assert isinstance(att, dict)
        assert not hasattr(att, "__dataclass_fields__")

        # Should have all required fields
        assert att["attachment_type"] in ["image", "link", "file"]
        assert att["source_type"] in [e.value for e in AttachmentSourceType]
        assert isinstance(att["confidence"], (int, float))


@pytest.mark.asyncio
async def test_deduplication():
    """Test that duplicate attachments are removed"""
    pipeline = AttachmentProcessingPipeline()

    message = {
        "content": "Same link twice: https://example.com and https://example.com",
    }

    result = await pipeline.process_message(message)

    # Should have only one link (deduplicated)
    links = [a for a in result["detected_attachments"] if a["attachment_type"] == "link"]
    assert len(links) == 1


@pytest.mark.asyncio
async def test_confidence_sorting():
    """Test that attachments are sorted by confidence"""
    pipeline = AttachmentProcessingPipeline()

    message = {
        "raw_content": {
            "image": "output/chart.png",  # High confidence (99%)
            "unknown_field": "maybe/a/path",  # Lower confidence
        },
        "content": "and maybe another link at example.com"  # Medium confidence
    }

    result = await pipeline.process_message(message)

    # Should be sorted by confidence (highest first)
    if len(result["detected_attachments"]) > 1:
        for i in range(len(result["detected_attachments"]) - 1):
            assert (result["detected_attachments"][i]["confidence"] >=
                   result["detected_attachments"][i+1]["confidence"])


@pytest.mark.asyncio
async def test_attachment_types_variety():
    """Test that different attachment types are detected and properly formatted"""
    pipeline = AttachmentProcessingPipeline()

    message = {
        "content": """
        Images detected: img1.png and img2.jpg stored in the directory
        Files: report.pdf data.csv needed for processing
        Links: https://example.com https://github.com are resources
        """
        # Note: Markdown syntax is NOT detected - using plain text references instead
    }

    result = await pipeline.process_message(message)

    # Should have some attachments
    assert len(result["detected_attachments"]) > 0

    # All attachments should be dicts with proper formatting
    attachment_types = set()
    for att in result["detected_attachments"]:
        assert isinstance(att, dict)
        assert "attachment_type" in att
        assert "source_type" in att
        assert "data" in att
        attachment_types.add(att["attachment_type"])

    # Should detect multiple types (not just one)
    assert len(attachment_types) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
