"""Tests for pantheon.internal.system_prompt."""

import pytest

from pantheon.internal.system_prompt import (
    render_system_prompt,
    _substitute_template_vars,
    _append_context_blocks,
    USER_INFORMATION_BLOCK,
    WORKDIR_CONSTRAINT_BLOCK,
    IMAGE_OUTPUT_CONSTRAINT_BLOCK,
)


class TestSubstituteTemplateVars:
    def test_bare_variable(self):
        result = _substitute_template_vars("Hello ${{name}}", {"name": "Alice"})
        assert result == "Hello Alice"

    def test_quoted_format_string(self):
        result = _substitute_template_vars(
            'Path: ${{"{base}/{file}"}}',
            {"base": "/tmp", "file": "out.txt"},
        )
        assert result == "Path: /tmp/out.txt"

    def test_no_template_returns_unchanged(self):
        prompt = "No template here."
        assert _substitute_template_vars(prompt, {}) is prompt

    def test_empty_prompt_returns_unchanged(self):
        assert _substitute_template_vars("", {}) == ""

    def test_missing_variable_returns_original_block(self):
        result = _substitute_template_vars("${{missing}}", {})
        assert result == "${{missing}}"

    def test_multiple_variables(self):
        result = _substitute_template_vars(
            "${{os}} / ${{workspace}}", {"os": "macOS", "workspace": "/home"}
        )
        assert result == "macOS / /home"


class TestAppendContextBlocks:
    def test_user_information_os_and_workspace(self):
        result = _append_context_blocks("base", {"os": "macOS 14", "workspace": "/home"})
        assert "<user_information>" in result
        assert "macOS 14" in result
        assert "/home" in result

    def test_user_information_os_only(self):
        result = _append_context_blocks("base", {"os": "Linux"})
        assert "<user_information>" in result
        assert "Linux" in result
        assert "workspace" not in result

    def test_user_information_workspace_only(self):
        result = _append_context_blocks("base", {"workspace": "/proj"})
        assert "<user_information>" in result
        assert "/proj" in result

    def test_no_user_information_when_empty(self):
        result = _append_context_blocks("base", {})
        assert "<user_information>" not in result

    def test_workdir_constraint(self):
        result = _append_context_blocks("base", {"workdir": "/sandbox"})
        assert "<workdir_constraint>" in result
        assert "/sandbox" in result

    def test_image_output_constraint_only_with_workdir(self):
        result = _append_context_blocks(
            "base", {"workdir": "/sandbox", "image_output_dir": "/sandbox/imgs"}
        )
        assert "<image_output_constraint>" in result
        assert "/sandbox/imgs" in result

    def test_image_output_constraint_skipped_without_workdir(self):
        result = _append_context_blocks("base", {"image_output_dir": "/imgs"})
        assert "<image_output_constraint>" not in result

    def test_no_workdir_no_constraint(self):
        result = _append_context_blocks("base", {})
        assert "<workdir_constraint>" not in result

    def test_base_prompt_preserved(self):
        result = _append_context_blocks("ORIGINAL", {"os": "macOS"})
        assert result.startswith("ORIGINAL")


class TestRenderSystemPrompt:
    def test_substitution_and_blocks_combined(self):
        prompt = "Agent for ${{client_id}}"
        ctx = {"client_id": "alice", "os": "macOS", "workspace": "/home/alice"}
        result = render_system_prompt(prompt, ctx)
        assert "Agent for alice" in result
        assert "<user_information>" in result
        assert "macOS" in result

    def test_empty_context_returns_prompt_unchanged(self):
        prompt = "Static prompt."
        result = render_system_prompt(prompt, {})
        assert result == "Static prompt."

    def test_workdir_appended(self):
        result = render_system_prompt("base", {"workdir": "/box"})
        assert "<workdir_constraint>" in result
        assert "/box" in result

    def test_full_workdir_with_image_dir(self):
        result = render_system_prompt(
            "base",
            {"workdir": "/box", "image_output_dir": "/box/imgs"},
        )
        assert "<workdir_constraint>" in result
        assert "<image_output_constraint>" in result
        assert "/box/imgs" in result

    def test_block_order(self):
        """user_information appears before workdir_constraint."""
        result = render_system_prompt(
            "base",
            {"os": "macOS", "workdir": "/box"},
        )
        assert result.index("<user_information>") < result.index("<workdir_constraint>")
