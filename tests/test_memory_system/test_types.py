"""Tests for memory type system and frontmatter parsing."""

import textwrap
from pathlib import Path

import pytest

from pantheon.internal.memory_system.types import (
    MemoryEntry,
    MemoryType,
    _generate_id,
    make_filename,
    parse_frontmatter_only,
    parse_memory_file,
    write_memory_file,
)


class TestMemoryType:
    def test_values(self):
        assert MemoryType.USER.value == "user"
        assert MemoryType.FEEDBACK.value == "feedback"
        assert MemoryType.PROJECT.value == "project"
        assert MemoryType.REFERENCE.value == "reference"
        assert MemoryType.WORKFLOW.value == "workflow"

    def test_from_str_valid(self):
        assert MemoryType.from_str("user") == MemoryType.USER
        assert MemoryType.from_str("feedback") == MemoryType.FEEDBACK
        assert MemoryType.from_str("  WORKFLOW  ") == MemoryType.WORKFLOW

    def test_from_str_pattern_maps_to_workflow(self):
        assert MemoryType.from_str("pattern") == MemoryType.WORKFLOW

    def test_from_str_invalid_defaults_workflow(self):
        assert MemoryType.from_str("unknown") == MemoryType.WORKFLOW
        assert MemoryType.from_str("") == MemoryType.WORKFLOW

    def test_is_str_enum(self):
        assert isinstance(MemoryType.USER, str)
        assert MemoryType.USER == "user"


class TestMemoryEntry:
    def test_to_frontmatter_dict(self, sample_user_entry):
        d = sample_user_entry.to_frontmatter_dict()
        assert d["title"] == "Senior Go engineer"
        assert d["type"] == "user"
        assert "summary" in d
        assert "id" in d

    def test_legacy_properties(self, sample_user_entry):
        assert sample_user_entry.name == sample_user_entry.title
        assert sample_user_entry.description == sample_user_entry.summary


class TestGenerateId:
    def test_basic(self):
        assert _generate_id("Mobile release freeze", "project") == "project-mobile-release-freeze"

    def test_empty_title(self):
        assert _generate_id("", "workflow") == "workflow-unnamed"

    def test_special_chars(self):
        result = _generate_id("Don't mock! Use real DB.", "feedback")
        assert result.startswith("feedback-")
        assert "!" not in result


class TestWriteAndParse:
    def test_roundtrip(self, tmp_pantheon_dir, sample_feedback_entry):
        path = tmp_pantheon_dir / "test.md"
        write_memory_file(path, sample_feedback_entry)
        assert path.exists()

        content = path.read_text()
        assert "---" in content
        assert "title:" in content  # Phase 1 canonical

        loaded = parse_memory_file(path)
        assert loaded.title == sample_feedback_entry.title
        assert loaded.summary == sample_feedback_entry.summary
        assert loaded.type == MemoryType.FEEDBACK
        assert loaded.entry_id is not None
        assert loaded.mtime > 0

    def test_legacy_format_readable(self, tmp_pantheon_dir):
        """Verify old worktree v0 format is still readable."""
        path = tmp_pantheon_dir / "legacy.md"
        path.write_text(textwrap.dedent("""\
            ---
            name: Old Memory
            description: A legacy memory
            type: pattern
            ---

            Legacy content here.
        """))
        entry = parse_memory_file(path)
        assert entry.title == "Old Memory"
        assert entry.summary == "A legacy memory"
        assert entry.type == MemoryType.WORKFLOW  # pattern → workflow

    def test_creates_parent_dirs(self, tmp_pantheon_dir, sample_user_entry):
        path = tmp_pantheon_dir / "sub" / "dir" / "test.md"
        write_memory_file(path, sample_user_entry)
        assert path.exists()

    def test_parse_nonexistent_raises(self, tmp_pantheon_dir):
        with pytest.raises(FileNotFoundError):
            parse_memory_file(tmp_pantheon_dir / "nonexistent.md")


class TestParseFrontmatterOnly:
    def test_reads_frontmatter(self, tmp_pantheon_dir):
        path = tmp_pantheon_dir / "test.md"
        path.write_text(textwrap.dedent("""\
            ---
            title: Test Memory
            summary: A test memory
            type: user
            ---

            Content not read.
        """))
        meta = parse_frontmatter_only(path)
        assert meta["title"] == "Test Memory"
        assert meta["summary"] == "A test memory"

    def test_no_frontmatter(self, tmp_pantheon_dir):
        path = tmp_pantheon_dir / "plain.md"
        path.write_text("# Just a plain markdown file")
        assert parse_frontmatter_only(path) == {}

    def test_nonexistent_file(self, tmp_pantheon_dir):
        assert parse_frontmatter_only(tmp_pantheon_dir / "nope.md") == {}


class TestMakeFilename:
    def test_basic(self):
        assert make_filename("Test Memory", MemoryType.USER) == "user_test_memory.md"

    def test_workflow_type(self):
        name = make_filename("QC Pipeline", MemoryType.WORKFLOW)
        assert name == "workflow_qc_pipeline.md"

    def test_empty_name(self):
        assert make_filename("", MemoryType.WORKFLOW) == "workflow_unnamed.md"

    def test_long_name_truncated(self):
        name = make_filename("a" * 100, MemoryType.PROJECT)
        assert len(name) < 80
