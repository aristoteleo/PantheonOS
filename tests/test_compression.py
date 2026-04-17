"""
Comprehensive tests for Context Compression module.

Tests cover:
1. CompressionConfig, CompressionResult, CompressionStatus
2. format_messages_to_text utility function
3. ContextCompressor.should_compress logic
4. ContextCompressor._get_compression_range
5. Memory.get_messages(for_llm=True) integration
6. Settings.get_compression_config

Run with: pytest tests/test_compression.py -v
"""

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

from pantheon.internal.compression import (
    CompressionConfig,
    CompressionResult,
    CompressionStatus,
    ContextCompressor,
)
from pantheon.internal.memory import Memory
from pantheon.settings import Settings, get_settings
from pantheon.utils.message_formatter import (
    FormattedConversation,
    format_messages_to_text,
    VIEW_TOOLS,
    EDIT_TOOLS,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_messages():
    """Sample conversation messages for testing."""
    return [
        {"role": "user", "content": "How do I read a CSV file?"},
        {
            "role": "assistant",
            "content": "I'll help you read a CSV file.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {
                        "name": "view_file",
                        "arguments": '{"AbsolutePath": "/path/to/data.csv"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "col1,col2\n1,2\n3,4",
        },
        {
            "role": "assistant",
            "content": "The CSV file contains 2 columns and 2 rows.",
        },
    ]


@pytest.fixture
def messages_with_metadata():
    """Messages with _metadata for compression testing."""
    return [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": "Hi there!",
            "_metadata": {"total_tokens": 1000, "max_tokens": 200000},
        },
    ]


@pytest.fixture
def high_usage_messages():
    """Messages with high token usage for triggering compression."""
    return [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": "Hi there!",
            "_metadata": {"total_tokens": 170000, "max_tokens": 200000},
        },
    ]


@pytest.fixture
def compressor():
    """Create a ContextCompressor for testing."""
    config = CompressionConfig(
        enable=True,
        threshold=0.8,
        preserve_recent_messages=5,
        max_tool_arg_length=200,
        max_tool_output_length=500,
    )
    return ContextCompressor(config, "gpt-4o-mini")


# ===========================================================================
# FormattedConversation Tests
# ===========================================================================


class TestFormattedConversation:
    """Tests for FormattedConversation dataclass."""

    def test_create_with_defaults(self):
        """Test creating FormattedConversation with defaults."""
        fc = FormattedConversation(text="test content")
        assert fc.text == "test content"
        assert fc.viewed_files == []
        assert fc.edited_files == []
        assert fc.skill_ids == []
        assert fc.question == ""
        assert fc.final_answer == ""
        assert fc.details_path == ""

    def test_create_with_all_fields(self):
        """Test creating FormattedConversation with all fields."""
        fc = FormattedConversation(
            text="formatted text",
            viewed_files=["/path/a.py"],
            edited_files=["/path/b.py"],
            skill_ids=["str-00001"],
            question="How do I?",
            final_answer="Do this.",
            details_path="/tmp/details.json",
        )
        assert fc.viewed_files == ["/path/a.py"]
        assert fc.edited_files == ["/path/b.py"]
        assert fc.skill_ids == ["str-00001"]
        assert fc.question == "How do I?"
        assert fc.final_answer == "Do this."
        assert fc.details_path == "/tmp/details.json"


# ===========================================================================
# format_messages_to_text Tests
# ===========================================================================


class TestFormatMessagesToText:
    """Tests for format_messages_to_text function."""

    def test_basic_formatting(self, sample_messages):
        """Test basic message formatting."""
        result = format_messages_to_text(sample_messages)
        
        assert isinstance(result, FormattedConversation)
        assert "[USER]" in result.text
        assert "[ASSISTANT]" in result.text
        assert "[TOOL_CALL: view_file]" in result.text
        assert "[TOOL_RESULT]" in result.text

    def test_extracts_question(self, sample_messages):
        """Test that first user message is extracted as question."""
        result = format_messages_to_text(sample_messages)
        assert result.question == "How do I read a CSV file?"

    def test_extracts_final_answer(self, sample_messages):
        """Test that last assistant response is extracted as final_answer."""
        result = format_messages_to_text(sample_messages)
        assert "2 columns and 2 rows" in result.final_answer

    def test_extracts_viewed_files(self, sample_messages):
        """Test extraction of viewed files from tool calls."""
        result = format_messages_to_text(sample_messages, extract_files=True)
        assert "/path/to/data.csv" in result.viewed_files

    def test_extracts_edited_files(self):
        """Test extraction of edited files from tool calls."""
        messages = [
            {"role": "user", "content": "Create a file"},
            {
                "role": "assistant",
                "content": "Creating file...",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "write_to_file",
                            "arguments": '{"TargetFile": "/path/to/new.py", "content": "hello"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "File created"},
            {"role": "assistant", "content": "Done!"},
        ]
        
        result = format_messages_to_text(messages, extract_files=True)
        assert "/path/to/new.py" in result.edited_files

    def test_extracts_skill_ids(self):
        """Test extraction of skill IDs from content."""
        messages = [
            {"role": "user", "content": "Help me"},
            {
                "role": "assistant",
                "content": "Using [str-00001] and [pat-00002] strategies...",
            },
        ]
        
        result = format_messages_to_text(messages, extract_skills=True)
        assert "str-00001" in result.skill_ids
        assert "pat-00002" in result.skill_ids

    def test_truncates_long_args_per_value(self):
        """Test that long argument values are truncated per-value."""
        long_code = "x" * 500
        messages = [
            {"role": "user", "content": "Run code"},
            {
                "role": "assistant",
                "content": "Running...",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "execute_code",
                            "arguments": json.dumps({
                                "code": long_code,
                                "filename": "test.py",  # Short, should be preserved
                            }),
                        },
                    }
                ],
            },
        ]
        
        result = format_messages_to_text(messages, max_arg_length=100)
        # Should contain truncation marker
        assert "... [" in result.text and "chars]" in result.text
        # Short filename should be preserved
        assert "test.py" in result.text

    def test_truncates_long_tool_output(self):
        """Test that long tool output is truncated."""
        long_output = "x" * 1000
        messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "Done", "tool_calls": [
                {"id": "call_1", "function": {"name": "test", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": long_output},
            {"role": "assistant", "content": "Result processed"},
        ]
        
        result = format_messages_to_text(messages, max_output_length=100)
        assert "... [" in result.text

    def test_handles_multimodal_content(self):
        """Test handling of multimodal content (list of items)."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this image?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            },
            {"role": "assistant", "content": "It's a cat."},
        ]
        
        result = format_messages_to_text(messages)
        assert "What is this image?" in result.text
        assert result.question == "What is this image?"

    def test_handles_llm_content_field(self):
        """Test that _llm_content field is preferred over content."""
        messages = [
            {"role": "user", "content": "Original", "_llm_content": "LLM version"},
            {"role": "assistant", "content": "Reply"},
        ]
        
        result = format_messages_to_text(messages)
        assert "LLM version" in result.text

    def test_handles_compression_role(self):
        """Test handling of compression role messages."""
        messages = [
            {"role": "compression", "content": "Previous context summary..."},
            {"role": "user", "content": "New question"},
            {"role": "assistant", "content": "Answer"},
        ]
        
        result = format_messages_to_text(messages)
        assert "[PREVIOUS_SUMMARY]" in result.text
        assert "Previous context summary" in result.text

    def test_saves_details_to_file(self, temp_dir):
        """Test saving original messages to file."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        details_path = f"{temp_dir}/test_details.json"
        result = format_messages_to_text(messages, save_details_to=details_path)

        # Compare paths using Path for cross-platform compatibility
        assert Path(result.details_path) == Path(details_path)
        assert os.path.exists(details_path)
        
        with open(details_path) as f:
            data = json.load(f)
        assert "messages" in data
        assert len(data["messages"]) == 2

    def test_adds_unified_note_when_details_saved(self, temp_dir):
        """Test that unified note is added at end when details_path exists."""
        messages = [
            {"role": "user", "content": "Test"},
            {"role": "tool", "tool_call_id": "call_1", "content": "x" * 1000},
            {"role": "assistant", "content": "Done"},
        ]
        
        details_path = f"{temp_dir}/test_note.json"
        result = format_messages_to_text(
            messages, 
            max_output_length=100, 
            save_details_to=details_path
        )
        
        # Should have truncation marker without 'see details_path'
        assert "... [1000 chars]" in result.text
        # Should have unified note at end
        assert "[NOTE: Some content was truncated." in result.text
        # Check path is in text (normalize for cross-platform)
        assert str(Path(details_path)) in result.text or details_path in result.text

    def test_no_note_without_details_path(self):
        """Test that no note is added when no details_path."""
        messages = [
            {"role": "user", "content": "Test"},
            {"role": "tool", "tool_call_id": "call_1", "content": "x" * 1000},
            {"role": "assistant", "content": "Done"},
        ]
        
        result = format_messages_to_text(messages, max_output_length=100)
        
        # Should have truncation but no note
        assert "... [1000 chars]" in result.text
        assert "[NOTE:" not in result.text

    def test_no_files_when_extract_disabled(self, sample_messages):
        """Test that files are not extracted when extract_files=False."""
        result = format_messages_to_text(sample_messages, extract_files=False)
        assert result.viewed_files == []
        assert result.edited_files == []

    def test_file_tool_classification(self):
        """Test that VIEW_TOOLS and EDIT_TOOLS are properly defined."""
        assert "view_file" in VIEW_TOOLS
        assert "view_file_outline" in VIEW_TOOLS
        assert "view_code_item" in VIEW_TOOLS
        assert "write_to_file" in EDIT_TOOLS
        assert "replace_file_content" in EDIT_TOOLS
        assert "multi_replace_file_content" in EDIT_TOOLS


# ===========================================================================
# CompressionConfig Tests
# ===========================================================================


class TestCompressionConfig:
    """Tests for CompressionConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = CompressionConfig()
        assert config.enable is True
        assert config.threshold == 0.8
        assert config.preserve_recent_messages == 5
        assert config.max_tool_arg_length == 2000
        assert config.max_tool_output_length == 5000
        assert config.retry_after_messages == 10

    def test_custom_values(self):
        """Test custom configuration values."""
        config = CompressionConfig(
            enable=False,
            threshold=0.9,
            preserve_recent_messages=3,
            max_tool_arg_length=1000,
            max_tool_output_length=2000,
            retry_after_messages=5,
        )
        assert config.enable is False
        assert config.threshold == 0.9
        assert config.preserve_recent_messages == 3


# ===========================================================================
# ContextCompressor Tests
# ===========================================================================


class TestContextCompressor:
    """Tests for ContextCompressor class."""

    def test_should_compress_no_metadata(self, compressor, sample_messages):
        """Test should_compress returns False when no metadata."""
        result = compressor.should_compress(sample_messages)
        assert result is False

    def test_should_compress_low_usage(self, compressor, messages_with_metadata):
        """Test should_compress returns False for low token usage."""
        result = compressor.should_compress(messages_with_metadata)
        assert result is False  # 1000/200000 = 0.5% < 80%

    def test_should_compress_high_usage(self, compressor, high_usage_messages):
        """Test should_compress returns True for high token usage."""
        result = compressor.should_compress(high_usage_messages)
        assert result is True  # 170000/200000 = 85% > 80%

    def test_should_compress_disabled_config(self, high_usage_messages):
        """Test should_compress returns False when disabled."""
        config = CompressionConfig(enable=False)
        compressor = ContextCompressor(config, "gpt-4o")
        result = compressor.should_compress(high_usage_messages)
        assert result is False

    def test_should_compress_no_max_tokens(self, compressor):
        """Test should_compress handles missing max_tokens."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": "Hi",
                "_metadata": {"total_tokens": 1000, "max_tokens": 0},
            },
        ]
        result = compressor.should_compress(messages)
        assert result is False

    def test_get_compression_range_no_compression(self, compressor):
        """Test compression range with no prior compression."""
        messages = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "1"},
            {"role": "user", "content": "2"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "3"},
        ]
        # preserve_recent_messages = 5
        start, end = compressor._get_compression_range(messages)
        assert start == 0
        assert end == 1  # len(6) - 5 = 1

    def test_get_compression_range_with_existing_compression(self, compressor):
        """Test compression range with existing compression message."""
        messages = [
            {"role": "user", "content": "1"},
            {"role": "compression", "content": "Previous summary..."},
            {"role": "user", "content": "2"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "3"},
            {"role": "user", "content": "4"},
            {"role": "assistant", "content": "4"},
        ]
        # preserve_recent_messages = 5
        start, end = compressor._get_compression_range(messages)
        assert start == 2  # After compression at idx 1
        assert end == 3  # len(8) - 5 = 3

    def test_count_existing_compressions(self, compressor):
        """Test counting existing compression messages."""
        messages = [
            {"role": "user", "content": "1"},
            {"role": "compression", "content": "First summary"},
            {"role": "user", "content": "2"},
            {"role": "compression", "content": "Second summary"},
            {"role": "user", "content": "3"},
        ]
        count = compressor._count_existing_compressions(messages)
        assert count == 2

    def test_estimate_tokens(self, compressor):
        """Test token estimation."""
        messages = [
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        tokens = compressor._estimate_tokens(messages)
        # Token count depends on the tokenizer; verify it's a reasonable positive value
        assert tokens > 0
        assert tokens < 100

    def test_increment_message_count(self, compressor):
        """Test message count increment after compression."""
        assert compressor._messages_since_last_compression == 0
        compressor.increment_message_count()
        assert compressor._messages_since_last_compression == 1
        compressor.increment_message_count()
        assert compressor._messages_since_last_compression == 2


# ===========================================================================
# Memory Integration Tests
# ===========================================================================


class TestMemoryIntegration:
    """Tests for Memory.get_messages(for_llm=True) integration."""

    def test_get_messages_basic(self):
        """Test basic get_messages(for_llm=True) without compression."""
        memory = Memory("test")
        memory._messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        
        result = memory.get_messages(for_llm=True)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"


    def test_get_messages_with_compression(self):
        """Test get_messages(for_llm=True) handles compression messages."""
        memory = Memory("test")
        memory._messages = [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "compression", "content": "Summary of old conversation"},
            {"role": "user", "content": "New question"},
            {"role": "assistant", "content": "New answer"},
        ]
        
        result = memory.get_messages(for_llm=True)
        
        # Should start from compression, convert to user role
        assert len(result) == 3
        assert result[0]["role"] == "user"  # Compression converted to user
        # Compression content passed through without extra prefix (CHECKPOINT header already present)
        assert "Summary of old" in result[0]["content"]
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"

    def test_get_messages_preserves_metadata_for_cost_tracking(self):
        """Test that _metadata is preserved for cost tracking.

        Note: _metadata is removed later by call_llm_provider before sending to API.
        Memory.get_messages keeps it for cost tracking purposes.
        """
        memory = Memory("test")
        memory._messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!", "_metadata": {"cost": 0.01}},
        ]

        result = memory.get_messages(for_llm=True)

        # _metadata is preserved for cost tracking (removed later by call_llm_provider)
        assert "_metadata" not in result[0]  # User message has no metadata
        assert "_metadata" in result[1]  # Assistant metadata preserved
        assert result[1]["_metadata"]["cost"] == 0.01

    def test_get_messages_skips_system_in_history(self):
        """Test that system messages in history are skipped."""
        memory = Memory("test")
        memory._messages = [
            {"role": "system", "content": "Old system prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        
        result = memory.get_messages(for_llm=True)
        
        # Should skip system in history
        assert len(result) == 2
        assert result[0]["role"] == "user"


# ===========================================================================
# Settings Integration Tests
# ===========================================================================


class TestSettingsIntegration:
    """Tests for Settings.get_compression_config."""

    def test_get_compression_config_defaults(self, tmp_path):
        """Test get_compression_config returns code defaults when no config."""
        # Use a temporary directory without any .pantheon config
        # This ensures we test the code-level defaults, not project config
        settings = Settings(work_dir=tmp_path)
        config = settings.get_compression_config()
        
        assert isinstance(config, dict)
        assert "enable" in config
        assert "threshold" in config
        assert "preserve_recent_messages" in config
        assert "max_tool_arg_length" in config
        assert "max_tool_output_length" in config
        assert "retry_after_messages" in config
        
        # Check code-level defaults (not affected by project settings.json)
        assert config["threshold"] == 0.8
        assert config["preserve_recent_messages"] == 5


# ===========================================================================
# CompressionResult Tests
# ===========================================================================


class TestCompressionResult:
    """Tests for CompressionResult dataclass."""

    def test_create_skipped_result(self):
        """Test creating a skipped compression result."""
        result = CompressionResult(
            status=CompressionStatus.SKIPPED,
            original_tokens=1000,
            new_tokens=1000,
        )
        assert result.status == CompressionStatus.SKIPPED
        assert result.compression_message is None
        assert result.error is None

    def test_create_success_result(self):
        """Test creating a successful compression result."""
        result = CompressionResult(
            status=CompressionStatus.COMPRESSED,
            original_tokens=10000,
            new_tokens=500,
            compression_message={
                "role": "compression",
                "content": "Summary...",
            },
        )
        assert result.status == CompressionStatus.COMPRESSED
        assert result.compression_message is not None

    def test_create_error_result(self):
        """Test creating an error compression result."""
        result = CompressionResult(
            status=CompressionStatus.FAILED_ERROR,
            original_tokens=10000,
            new_tokens=10000,
            error="LLM call failed",
        )
        assert result.status == CompressionStatus.FAILED_ERROR
        assert result.error == "LLM call failed"


# ===========================================================================
# CompressionStatus Tests
# ===========================================================================


class TestCompressionStatus:
    """Tests for CompressionStatus enum."""

    def test_status_values(self):
        """Test all status enum values exist."""
        assert CompressionStatus.COMPRESSED.value == "compressed"
        assert CompressionStatus.FAILED_INFLATED.value == "failed_inflated"
        assert CompressionStatus.FAILED_ERROR.value == "failed_error"
        assert CompressionStatus.SKIPPED.value == "skipped"


# ===========================================================================
# Multi-Compression Scenario Tests
# ===========================================================================


class TestMultiCompressionScenarios:
    """Tests for first/second compression scenarios to verify expected behavior."""

    @pytest.fixture
    def compressor_preserve_3(self):
        """Compressor with preserve_recent_messages=3 for easier testing."""
        config = CompressionConfig(
            enable=True,
            threshold=0.8,
            preserve_recent_messages=3,
        )
        return ContextCompressor(config, "gpt-4o-mini")

    def test_first_compression_range(self, compressor_preserve_3):
        """Test compression range for FIRST compression (no prior compression)."""
        # Simulate 10 messages, preserve last 3
        messages = [
            {"role": "user", "content": f"Q{i}"}
            if i % 2 == 0 else {"role": "assistant", "content": f"A{i}"}
            for i in range(10)
        ]
        
        start, end = compressor_preserve_3._get_compression_range(messages)
        
        # First compression: start=0, end=10-3=7
        assert start == 0, "First compression should start at index 0"
        assert end == 7, "First compression should end at len-preserve_recent (10-3=7)"
        
        # Messages to compress: [0, 1, 2, 3, 4, 5, 6]
        # Messages to preserve: [7, 8, 9]
        messages_to_compress = messages[start:end]
        assert len(messages_to_compress) == 7

    def test_second_compression_range(self, compressor_preserve_3):
        """Test compression range for SECOND compression (after first compression)."""
        # Simulate state AFTER first compression:
        # [compression_1] + 8 new messages, preserve last 3
        messages = [
            {"role": "compression", "content": "Summary of first batch..."},  # idx 0
            {"role": "user", "content": "Q1"},      # idx 1
            {"role": "assistant", "content": "A1"}, # idx 2
            {"role": "user", "content": "Q2"},      # idx 3
            {"role": "assistant", "content": "A2"}, # idx 4
            {"role": "user", "content": "Q3"},      # idx 5
            {"role": "assistant", "content": "A3"}, # idx 6
            {"role": "user", "content": "Q4"},      # idx 7
            {"role": "assistant", "content": "A4"}, # idx 8 (last assistant)
        ]
        
        start, end = compressor_preserve_3._get_compression_range(messages)
        
        # Second compression: start after compression_1 (idx 1), end=9-3=6
        assert start == 1, "Second compression should start after last compression (idx 1)"
        assert end == 6, "Second compression should end at len-preserve_recent (9-3=6)"
        
        # Messages to compress: [1, 2, 3, 4, 5]
        # Messages to preserve: [6, 7, 8]
        messages_to_compress = messages[start:end]
        assert len(messages_to_compress) == 5
        assert messages_to_compress[0]["content"] == "Q1"

    def test_third_compression_range(self, compressor_preserve_3):
        """Test compression range for THIRD compression (multiple prior compressions)."""
        # State after TWO compressions + more messages
        messages = [
            {"role": "compression", "content": "Summary 1..."},  # idx 0 - old, will be kept
            {"role": "compression", "content": "Summary 2..."},  # idx 1 - most recent compression
            {"role": "user", "content": "Q1"},      # idx 2
            {"role": "assistant", "content": "A1"}, # idx 3
            {"role": "user", "content": "Q2"},      # idx 4
            {"role": "assistant", "content": "A2"}, # idx 5
            {"role": "user", "content": "Q3"},      # idx 6
            {"role": "assistant", "content": "A3"}, # idx 7
        ]
        
        start, end = compressor_preserve_3._get_compression_range(messages)
        
        # Third compression: start after LAST compression (idx 2), end=8-3=5
        assert start == 2, "Third compression should start after last compression (idx 2)"
        assert end == 5, "Third compression should end at len-preserve_recent (8-3=5)"

    def test_memory_with_first_compression(self):
        """Test Memory.get_messages(for_llm=True) after FIRST compression."""
        memory = Memory("test")
        memory._messages = [
            # After first compression: 1 compression + 3 preserved messages
            {"role": "compression", "content": "Summary: User asked about CSV files, assistant helped read them."},
            {"role": "user", "content": "Now show me the statistics"},
            {"role": "assistant", "content": "Here are the stats..."},
        ]
        
        result = memory.get_messages(for_llm=True)
        
        # Should have 3 messages: [compression->user, user, assistant]
        assert len(result) == 3
        assert result[0]["role"] == "user"  # compression converted to user
        # Compression content already has CHECKPOINT header (no extra prefix)
        assert "CSV files" in result[0]["content"]

    def test_memory_with_second_compression(self):
        """Test Memory.get_messages(for_llm=True) after SECOND compression."""
        memory = Memory("test")
        memory._messages = [
            # Old compression (should be ignored by get_messages(for_llm=True))
            {"role": "compression", "content": "First summary..."},
            # Second compression (most recent, should be used)
            {"role": "compression", "content": "Second summary: continued work on data analysis."},
            {"role": "user", "content": "Generate report"},
            {"role": "assistant", "content": "Report generated."},
        ]
        
        result = memory.get_messages(for_llm=True)
        
        # Should start from LAST compression
        assert len(result) == 3
        assert result[0]["role"] == "user"  # last compression converted to user
        # Compression content already has CHECKPOINT header (no extra prefix)
        assert "Second summary" in result[0]["content"]
        assert "First summary" not in result[0]["content"]

    def test_compression_index_increments(self, compressor_preserve_3):
        """Test that compression_index in metadata increments correctly."""
        # No prior compressions
        messages_no_compression = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        count = compressor_preserve_3._count_existing_compressions(messages_no_compression)
        assert count == 0
        # Next compression would be index 1
        
        # One prior compression
        messages_one_compression = [
            {"role": "compression", "content": "Summary 1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        count = compressor_preserve_3._count_existing_compressions(messages_one_compression)
        assert count == 1
        # Next compression would be index 2
        
        # Two prior compressions
        messages_two_compressions = [
            {"role": "compression", "content": "Summary 1"},
            {"role": "compression", "content": "Summary 2"},
            {"role": "user", "content": "Q3"},
            {"role": "assistant", "content": "A3"},
        ]
        count = compressor_preserve_3._count_existing_compressions(messages_two_compressions)
        assert count == 2
        # Next compression would be index 3

    def test_not_enough_messages_to_compress(self, compressor_preserve_3):
        """Test that compression is skipped when not enough messages beyond preserved."""
        # Only 4 messages, preserve 3, so only 1 message to compress - not worth it
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        
        start, end = compressor_preserve_3._get_compression_range(messages)
        
        # end - start = 1 < preserve_recent_messages (3)
        assert end - start < compressor_preserve_3.config.preserve_recent_messages

    def test_format_messages_handles_compression_in_history(self):
        """Test format_messages_to_text correctly formats compression in trajectory."""
        messages = [
            {"role": "compression", "content": "Previous context summary..."},
            {"role": "user", "content": "New question"},
            {"role": "assistant", "content": "New answer"},
        ]
        
        result = format_messages_to_text(messages)

        # Compression should be marked as PREVIOUS_SUMMARY
        assert "[PREVIOUS_SUMMARY]" in result.text
        assert "Previous context summary" in result.text
        assert "[USER]" in result.text
        assert "[ASSISTANT]" in result.text


# ===========================================================================
# CompressionPlugin + MemorySystemPlugin 集成测试（真实 Gemini LLM）
# ===========================================================================

import os
from pathlib import Path
from dotenv import load_dotenv

_repo_root = Path(__file__).parent.parent
load_dotenv(_repo_root / ".env")

GEMINI_MODEL = "gemini/gemini-3-flash-preview"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

_requires_gemini = pytest.mark.skipif(not GEMINI_KEY, reason="GEMINI_API_KEY not set")


def _make_memory_runtime(workspace: Path):
    from pantheon.internal.memory_system.runtime import MemoryRuntime
    config = {
        "enabled": True,
        "selection_model": GEMINI_MODEL,
        "flush_enabled": True,
        "flush_model": GEMINI_MODEL,
        "dream_enabled": False,
        "dream_model": GEMINI_MODEL,
        "dream_min_hours": 9999,
        "dream_min_sessions": 9999,
        "session_note_init_tokens": 100,
        "session_note_update_tokens": 50,
        "session_note_tool_calls": 1,
    }
    rt = MemoryRuntime(config)
    rt.initialize(
        pantheon_dir=workspace / ".pantheon",
        runtime_dir=workspace / ".pantheon" / "memory-runtime",
    )
    return rt


def _make_compression_plugin(model: str = GEMINI_MODEL):
    from pantheon.internal.compression.plugin import CompressionPlugin
    config = {
        "enable": True,
        "threshold": 0.0,  # always trigger in tests
        "preserve_recent_messages": 2,
        "compression_model": model,
        "retry_after_messages": 0,
    }
    return CompressionPlugin(config)


def _make_team_with_compression(workspace: Path):
    """Build a PantheonTeam with MemorySystemPlugin + CompressionPlugin."""
    from pantheon.agent import Agent
    from pantheon.team import PantheonTeam
    from pantheon.internal.memory_system.plugin import MemorySystemPlugin
    from pantheon.internal.compression.plugin import CompressionPlugin

    mem_rt = _make_memory_runtime(workspace)
    mem_plugin = MemorySystemPlugin(mem_rt)
    comp_plugin = _make_compression_plugin()

    agent = Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)
    team = PantheonTeam(
        agents=[agent],
        plugins=[mem_plugin, comp_plugin],
    )
    return team, mem_rt, mem_plugin, comp_plugin


@pytest.mark.asyncio
class TestCompressionPluginIntegration:
    """Integration tests for CompressionPlugin + MemorySystemPlugin collaboration."""

    @_requires_gemini
    async def test_session_note_compact_path(self, tmp_path):
        """
        Path A: Session Note Compact (zero-LLM compression).

        pre_compression now force-updates the session note internally, so no
        pre-seeding is required. The compact path should succeed automatically.
        """
        from pantheon.internal.memory import Memory

        (tmp_path / ".pantheon").mkdir()
        team, mem_rt, mem_plugin, comp_plugin = _make_team_with_compression(tmp_path)
        await team.async_setup()

        memory = Memory(name="compact-test")

        # Build enough history (more than preserve_recent_messages=2)
        for i in range(8):
            memory._messages.append({"role": "user", "content": f"Q{i}: Introduce Python asyncio module"})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: asyncio is Python's async IO framework providing event loop, coroutines, and tasks.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        # Add more messages so boundary < total after force_update
        for i in range(8, 14):
            memory._messages.append({"role": "user", "content": f"Q{i}: What about Python threading?"})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: Threading in Python is limited by the GIL for CPU-bound tasks.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        original_count = len(memory._messages)
        session_id = memory.id

        result = await comp_plugin._perform_compression(team, memory)

        print(f"\n[compression result] {result}")
        print(f"[message count after] {len(memory._messages)}")

        assert result.get("success"), f"Compression should succeed: {result}"
        assert result.get("method") == "session_note_compact"
        assert len(memory._messages) > original_count, "Message count should increase (checkpoint inserted)"

        compression_msgs = [m for m in memory._messages if m.get("role") == "compression"]
        assert len(compression_msgs) == 1, "Should have exactly one compression checkpoint"
        assert "CHECKPOINT" in compression_msgs[0]["content"]
        assert compression_msgs[0]["_metadata"]["method"] == "session_note_compact"

    @_requires_gemini
    async def test_llm_fallback_compression_path(self, tmp_path):
        """
        Path B: LLM fallback compression (when Session Note Compact is unavailable).

        Disable session_note on the runtime so force_update produces no CompactHint,
        forcing the LLM compressor to run.
        """
        from pantheon.internal.memory import Memory

        (tmp_path / ".pantheon").mkdir()
        team, mem_rt, mem_plugin, comp_plugin = _make_team_with_compression(tmp_path)
        await team.async_setup()

        # Disable session note so pre_compression returns no CompactHint
        mem_rt.session_note = None

        memory = Memory(name="llm-compress-test")

        for i in range(6):
            memory._messages.append({"role": "user", "content": f"Q{i}: Explain the Python GIL."})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: The GIL is CPython's global interpreter lock; only one thread executes bytecode at a time.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        print(f"\n[original message count] {len(memory._messages)}")

        result = await comp_plugin._perform_compression(team, memory, force=True)

        print(f"\n[compression result] {result}")
        roles = [m["role"] for m in memory._messages]
        print(f"[message roles] {roles}")

        assert result.get("success"), f"LLM compression should succeed: {result}"
        assert "compression" in roles, "role:compression checkpoint should be inserted"

        comp_msg = next(m for m in memory._messages if m["role"] == "compression")
        assert len(comp_msg["content"]) > 50, "compression message should have real content"

    @_requires_gemini
    async def test_pre_compression_flush_before_llm_compress(self, tmp_path):
        """Verify pre_compression hook writes daily log before LLM compression runs."""
        from pantheon.internal.memory import Memory

        (tmp_path / ".pantheon").mkdir()
        team, mem_rt, mem_plugin, comp_plugin = _make_team_with_compression(tmp_path)
        await team.async_setup()

        memory = Memory(name="flush-then-compress")
        for i in range(6):
            memory._messages.append({"role": "user", "content": f"Our API endpoint is /api/v{i}/users"})
            memory._messages.append({
                "role": "assistant",
                "content": f"Noted: /api/v{i}/users endpoint recorded.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        result = await comp_plugin._perform_compression(team, memory, force=True)

        print(f"\n[compression result] {result}")

        logs = mem_rt.store.list_daily_logs()
        print(f"[daily logs] {[l.name for l in logs]}")
        assert logs, "pre_compression hook should write daily log"

        log_content = logs[0].read_text()
        print(f"[Log 内容]\n{log_content[:300]}")
        assert len(log_content) > 0

