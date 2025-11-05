"""
Attachment processing pipeline for unified resource detection in messages.

This pipeline applies multiple detectors in sequence to extract all types of
attachments (images, files, links, etc.) from message content.

Also includes message processor for integration with the agent system.
"""

from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Tuple
from ..utils.log import logger
from .attachment_detection import (
    AttachmentType,
    AttachmentSourceType,
    DetectedAttachment,
    AttachmentDetector,
    StructuredAttachmentExtractor,
    ImageDetector,
    PathDetector,
    LinkDetector,
)


class AttachmentProcessingPipeline:
    """Pipeline for detecting and processing attachments in messages"""

    def __init__(self):
        """Initialize the pipeline with all detectors"""
        # Detection order matters! More accurate detectors first
        self.detectors: List[AttachmentDetector] = [
            # 1. Structured fields (99% accuracy)
            StructuredAttachmentExtractor(),
            # 2. Unified path detector (files + images, excludes markdown syntax)
            PathDetector(),
            # 3. General purpose detectors
            ImageDetector(),
            LinkDetector(),
        ]

        self.deduplicate = True

    async def process_message(self, message: dict) -> dict:
        """
        Process a message to extract all attachments.

        Args:
            message: Message dict with text, raw_content, etc.

        Returns:
            Message dict with added detected_attachments field
        """
        all_attachments = []

        content_sources = [
            ("raw_content", message.get("raw_content")),  # Tool structured data
            ("content", message.get("content")),  # Text content
        ]

        for source_name, content in content_sources:
            if not content:
                continue

            try:
                attachments = await self._detect_from_content(content)
                for att in attachments:
                    # Enrich detected_from information by creating a new instance
                    # (DetectedAttachment is frozen, so we can't modify it)
                    updated_detected_from = (
                        f"{source_name}:{att.detected_from}"
                        if att.detected_from
                        else source_name
                    )
                    # Create new attachment with updated detected_from
                    enriched_att = DetectedAttachment(
                        attachment_type=att.attachment_type,
                        source_type=att.source_type,
                        data=att.data,
                        mime_type=att.mime_type,
                        size=att.size,
                        name=att.name,
                        description=att.description,
                        detected_from=updated_detected_from,
                        confidence=att.confidence,
                        is_valid=att.is_valid,
                        metadata=att.metadata,
                    )
                    all_attachments.append(enriched_att)
            except Exception as e:
                logger.warning(f"Error detecting attachments from {source_name}: {e}")
                continue

        # Deduplicate
        if self.deduplicate:
            all_attachments = self._deduplicate(all_attachments)

        # Sort by confidence (highest first)
        all_attachments.sort(key=lambda x: x.confidence, reverse=True)

        # Convert to serializable format for JSON
        serializable_attachments = self._convert_to_serializable(all_attachments)

        # Add to message
        message["detected_attachments"] = serializable_attachments

        # Log completion summary
        if all_attachments:
            logger.debug(
                f"📎 [Attachment Detection] Completed: {len(all_attachments)} attachments detected"
            )

        return message

    async def _detect_from_content(self, content: any) -> List[DetectedAttachment]:
        """
        Detect attachments using all detectors.

        Returns a list of all detected attachments.
        """
        all_attachments = []

        for detector in self.detectors:
            try:
                attachments = await detector.detect(content)
                all_attachments.extend(attachments)
            except Exception as e:
                detector_name = detector.__class__.__name__
                logger.warning(f"Detector {detector_name} error: {e}")
                continue

        return all_attachments

    def _deduplicate(
        self, attachments: List[DetectedAttachment]
    ) -> List[DetectedAttachment]:
        """Remove duplicate attachments"""
        seen: Set[DetectedAttachment] = set()
        unique = []

        for att in attachments:
            if att not in seen:
                seen.add(att)
                unique.append(att)

        return unique

    def _convert_to_serializable(
        self, attachments: List[DetectedAttachment]
    ) -> List[Dict]:
        """Convert DetectedAttachment objects to JSON-serializable dicts"""
        result = []
        for att in attachments:
            att_dict = {
                "attachment_type": att.attachment_type.value,
                "source_type": att.source_type.value,
                "data": att.data,
                "mime_type": att.mime_type,
                "size": att.size,
                "name": att.name,
                "description": att.description,
                "detected_from": att.detected_from,
                "confidence": att.confidence,
                "is_valid": att.is_valid,
                "metadata": att.metadata,
            }
            result.append(att_dict)
        return result


class MessageProcessor:
    """Processor for messages with attachment detection"""

    def __init__(self):
        """Initialize with attachment processing pipeline"""
        self.attachment_pipeline = AttachmentProcessingPipeline()

    async def process_message_with_attachments(self, message: dict) -> dict:
        """
        Process a message to extract attachments.

        Args:
            message: Message dict with text, raw_content, etc.

        Returns:
            Message dict with detected_attachments field added
        """
        try:
            # Apply attachment detection
            processed_message = await self.attachment_pipeline.process_message(
                message.copy()
            )
            return processed_message
        except Exception as e:
            logger.error(f"Error processing message attachments: {e}")
            # Return original message if processing fails
            message["detected_attachments"] = []
            return message

    def convert_detected_attachments_for_display(
        self, detected_attachments: list
    ) -> list:
        """
        Convert internal DetectedAttachment objects to display format.

        This converts the internal format to a simpler format suitable for
        sending to the frontend.

        Args:
            detected_attachments: List of DetectedAttachment objects

        Returns:
            List of dicts suitable for JSON serialization
        """
        display_attachments = []

        for att in detected_attachments:
            if not isinstance(att, DetectedAttachment):
                continue

            display_att = {
                "attachment_type": att.attachment_type.value,
                "source_type": att.source_type.value,
                "data": att.data,
                "mime_type": att.mime_type,
                "name": att.name,
                "description": att.description,
                "detected_from": att.detected_from,
                "confidence": att.confidence,
                "is_valid": att.is_valid,
                "metadata": att.metadata,
            }
            display_attachments.append(display_att)

        return display_attachments


# Singleton instance
_message_processor: Optional[MessageProcessor] = None


def get_message_processor() -> MessageProcessor:
    """Get or create the message processor singleton"""
    global _message_processor
    if _message_processor is None:
        _message_processor = MessageProcessor()
    return _message_processor
