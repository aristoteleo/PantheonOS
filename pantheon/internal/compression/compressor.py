"""
Context Compressor for managing conversation history compression.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from pantheon.utils.log import logger
from pantheon.utils.message_formatter import format_messages_to_text
from .prompts import (
    COMPRESSION_SYSTEM_PROMPT,
    COMPRESSION_USER_PROMPT,
    COMPRESSION_MESSAGE_TEMPLATE,
)


class CompressionStatus(Enum):
    """Compression operation status."""

    COMPRESSED = "compressed"
    FAILED_INFLATED = "failed_inflated"
    FAILED_ERROR = "failed_error"
    SKIPPED = "skipped"


@dataclass
class CompressionConfig:
    """Configuration for context compression."""

    enable: bool = True
    threshold: float = 0.8  # Compress when usage reaches 80% (0 = test mode)
    preserve_recent_messages: int = 5  # Keep last N messages uncompressed
    max_tool_arg_length: int = 2000  # Max length per tool argument value
    max_tool_output_length: int = 5000  # Max length for tool output
    retry_after_messages: int = 10  # Wait N messages before retrying after failure


@dataclass
class CompressionResult:
    """Result of a compression operation."""

    status: CompressionStatus
    original_tokens: int
    new_tokens: int
    compression_message: Optional[dict] = None
    error: Optional[str] = None


class ContextCompressor:
    """Context compressor for managing conversation history."""

    def __init__(self, config: CompressionConfig, model: str):
        """Initialize the compressor.

        Args:
            config: Compression configuration
            model: Model to use for compression (main LLM model)
        """
        self.config = config
        self.model = model
        self._failed_attempt_count = 0
        self._messages_since_last_compression = 0

    def should_compress(self, messages: list[dict], model: str | None = None) -> bool:
        """Check if compression is needed based on token usage.

        Reads token counts from last assistant message's _metadata
        (populated by count_tokens_in_messages in agent.py).

        Special test mode: when threshold=0, compress based on message count only
        (controlled by min_messages_for_compression config, default 2).
        """
        if not self.config.enable:
            return False

        # After recent failure, wait for enough messages before retrying
        if self._failed_attempt_count > 0:
            if self._messages_since_last_compression < self.config.retry_after_messages:
                return False

        # TEST MODE: threshold=0 means compress based on message count
        if self.config.threshold == 0:
            min_messages = 4  # 2 rounds of conversation (User+Asst * 2)

            # Find the last compression message
            last_compression_idx = -1
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "compression":
                    last_compression_idx = i
                    break

            # Count non-system messages since last compression
            uncompressed_messages = messages[last_compression_idx + 1 :]
            valid_count = sum(
                1 for m in uncompressed_messages if m.get("role") != "system"
            )

            return valid_count >= min_messages

        # NORMAL MODE: compress based on token usage ratio
        # Find last assistant message with _metadata
        last_assistant = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and "_metadata" in msg:
                last_assistant = msg
                break

        if not last_assistant:
            return False

        metadata = last_assistant.get("_metadata", {})

        # Read raw token counts (populated by count_tokens_in_messages)
        total_tokens = metadata.get("total_tokens", 0)
        max_tokens = metadata.get("max_tokens", 0)

        if max_tokens == 0:
            # Fallback: try to fetch from model info if available
            if model:
                try:
                    from pantheon.utils.provider_registry import get_model_info

                    info = get_model_info(model)
                    max_tokens = (info.get("max_input_tokens") or 0) + (
                        info.get("max_output_tokens") or 0
                    )
                except Exception:
                    pass

            if max_tokens == 0:
                return False

        # Calculate usage ratio dynamically (adapts to model changes)
        usage_ratio = total_tokens / max_tokens

        return usage_ratio >= self.config.threshold

    async def compress(
        self,
        messages: list[dict],
        compression_dir: str | None = None,
        force: bool = False,
    ) -> CompressionResult:
        """Execute compression.

        Args:
            messages: Full message history
            compression_dir: Directory to save original message details
            force: Force compression even if result is larger

        Returns:
            CompressionResult with status and compression message
        """
        # 1. Determine compression range
        compress_start, compress_end = self._get_compression_range(messages)

        chunk_size = compress_end - compress_start
        min_chunk_size = self.config.preserve_recent_messages

        # Test mode: allow compressing even small chunks
        if self.config.threshold == 0:
            min_chunk_size = 1

        if chunk_size < min_chunk_size:
            return CompressionResult(
                status=CompressionStatus.SKIPPED, original_tokens=0, new_tokens=0
            )

        # 2. Extract messages to compress
        messages_to_compress = messages[compress_start:compress_end]
        original_tokens = self._estimate_tokens(messages_to_compress)

        # 3. Format conversation and extract file info using unified function
        compression_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        details_path = (
            f"{compression_dir}/compression_{compression_id}.json"
            if compression_dir
            else None
        )

        result = format_messages_to_text(
            messages_to_compress,
            max_arg_length=self.config.max_tool_arg_length,
            max_output_length=self.config.max_tool_output_length,
            extract_files=True,
            save_details_to=details_path,
            include_footer_note=False,  # Disable footer note to avoid confusing the compression LLM
            use_smart_truncate=True,  # Enable smart truncation to preserve JSON structure
        )

        # 4. Build files section with hints for LLM to describe each file
        files_section = ""
        if result.viewed_files or result.edited_files:
            viewed_list = (
                "\\n  - ".join(f"`{f}`" for f in result.viewed_files[:20])
                if result.viewed_files
                else "None"
            )
            edited_list = (
                "\\n  - ".join(f"`{f}`" for f in result.edited_files[:20])
                if result.edited_files
                else "None"
            )
            files_section = f"""## Files Referenced in Conversation
**Viewed** (describe what was learned from each):
  - {viewed_list}

**Edited** (describe what was changed in each):
  - {edited_list}
"""

        # 5. Build compression agent input
        user_message = COMPRESSION_USER_PROMPT.format(
            files_section=files_section, conversation=result.text
        )

        # Add reference to full details if saved
        if result.details_path:
            user_message += f"\n\n[FULL_DETAILS: {result.details_path}]"

        # 6. Use temporary Agent to generate summary
        try:
            from pantheon.agent import Agent

            compression_agent = Agent(
                name="_compressor",
                instructions=COMPRESSION_SYSTEM_PROMPT,
                model=self.model,
            )

            response = await compression_agent.run(
                user_message,
                use_memory=False,  # Don't persist this
                tool_use=False,  # No tools needed
            )
            summary = response.content
        except Exception as e:
            self._failed_attempt_count += 1
            logger.error(f"Compression failed: {e}")
            return CompressionResult(
                status=CompressionStatus.FAILED_ERROR,
                original_tokens=original_tokens,
                new_tokens=original_tokens,
                error=str(e),
            )

        # 7. Validate compression effectiveness
        new_tokens = self._estimate_tokens([{"content": summary}])

        if new_tokens >= original_tokens and not force and self.config.threshold > 0:
            self._failed_attempt_count += 1
            return CompressionResult(
                status=CompressionStatus.FAILED_INFLATED,
                original_tokens=original_tokens,
                new_tokens=new_tokens,
            )

        # 8. Format final compression content using template
        compression_index = self._count_existing_compressions(messages) + 1
        final_content = COMPRESSION_MESSAGE_TEMPLATE.format(
            checkpoint_number=compression_index,
            summary=summary,
            details_path=result.details_path or "(not saved)",
        )

        # ✅ Simplified: Only store compression LLM cost
        # Old messages remain in memory and will be counted when using for_llm=False
        # No need to track compressed_messages_cost separately (avoids double counting)
        compression_llm_cost = 0.0
        if hasattr(response, "_metadata"):
            compression_llm_cost = response._metadata.get("current_cost", 0.0)

        # 10. Create compression message
        compression_index = self._count_existing_compressions(messages) + 1
        compression_message = {
            "role": "compression",
            "content": final_content,
            "_metadata": {
                "original_message_count": len(messages_to_compress),
                "original_token_count": original_tokens,
                "compressed_token_count": new_tokens,
                "timestamp": datetime.now().isoformat(),
                "viewed_files": result.viewed_files,
                "edited_files": result.edited_files,
                "compression_index": compression_index,
                "details_path": result.details_path,
                # ✅ Simplified: Only store compression cost as current_cost
                "current_cost": compression_llm_cost,
            },
        }

        # Reset failure count on success
        self._failed_attempt_count = 0
        self._messages_since_last_compression = 0

        logger.info(
            f"Context compressed: {original_tokens} -> {new_tokens} tokens "
            f"({len(messages_to_compress)} messages), "
            f"compression cost: ${compression_llm_cost:.4f}"
        )

        return CompressionResult(
            status=CompressionStatus.COMPRESSED,
            original_tokens=original_tokens,
            new_tokens=new_tokens,
            compression_message=compression_message,
        )

    def _get_compression_range(self, messages: list[dict]) -> tuple[int, int]:
        """Get the range of messages to compress.

        Returns:
            (start_index, end_index) - messages[start:end] will be compressed
        """
        # Find the last compression message
        last_compression_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "compression":
                last_compression_idx = i
                break

        # Compression range: from after last compression to before preserving recent
        preserve_count = self.config.preserve_recent_messages
        if self.config.threshold == 0:
            preserve_count = 1

        start = last_compression_idx + 1
        end = len(messages) - preserve_count

        return start, max(start, end)

    def _count_existing_compressions(self, messages: list[dict]) -> int:
        """Count existing compression messages."""
        return sum(1 for msg in messages if msg.get("role") == "compression")

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token count for messages using tiktoken when available."""
        from pantheon.utils.llm import _safe_token_counter
        return max(1, _safe_token_counter(model=self.model, messages=messages))

    def increment_message_count(self):
        """Increment message count since last compression (call after each message)."""
        self._messages_since_last_compression += 1
