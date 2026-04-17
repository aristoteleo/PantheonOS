"""
System prompt rendering utilities.

Centralises all runtime system prompt composition:
  1. ${{...}} template variable substitution
  2. Standard context block injection (user_information, workdir_constraint, etc.)

Usage:
    from pantheon.internal.system_prompt import render_system_prompt
    system_prompt = render_system_prompt(agent.instructions, context_variables)
"""

from __future__ import annotations

import re
from typing import Any

from pantheon.utils.log import logger

# ── Prompt block templates ────────────────────────────────────────────────────

USER_INFORMATION_BLOCK = """\

<user_information>
{content}
</user_information>"""

WORKDIR_CONSTRAINT_BLOCK = """\

<workdir_constraint>
IMPORTANT: You are operating in a restricted workspace environment.
- Your working directory is: {workdir}
- All file operations (read/write/create/delete) MUST be within this directory
- Paths outside this directory are not accessible and will fail
- When specifying file paths, use relative paths or absolute paths within {workdir}
- The file manager and shell tools enforce this restriction at the code level
</workdir_constraint>"""

IMAGE_OUTPUT_CONSTRAINT_BLOCK = """\

<image_output_constraint>
When you generate or save images (plots, charts, figures, etc.), ALWAYS save them to: {img_dir}
This directory is monitored so images saved here are automatically sent back to the user.
</image_output_constraint>"""

# ── Public API ────────────────────────────────────────────────────────────────

def render_system_prompt(prompt: str, context_variables: dict[str, Any]) -> str:
    """Render a system prompt string.

    Two steps:
    1. Replace ``${{ expr }}`` template expressions with values from
       *context_variables* (supports bare variable names and format strings).
    2. Append standard context blocks derived from *context_variables*:
       - ``<user_information>``  — os, workspace
       - ``<workdir_constraint>`` — sandboxed filesystem (when *workdir* is set)
       - ``<image_output_constraint>`` — image output dir (when set)
    """
    prompt = _substitute_template_vars(prompt, context_variables)
    prompt = _append_context_blocks(prompt, context_variables)
    return prompt


# ── Internal helpers ──────────────────────────────────────────────────────────

_TEMPLATE_PATTERN = re.compile(r"\$\{\{(.*?)\}\}")


def _substitute_template_vars(prompt: str, ctx: dict[str, Any]) -> str:
    """Replace ``${{ expr }}`` blocks with values from *ctx*."""
    if not prompt or "${{" not in prompt:
        return prompt

    def _replace(match: re.Match) -> str:
        content = match.group(1).strip()
        is_quoted = (content.startswith('"') and content.endswith('"')) or (
            content.startswith("'") and content.endswith("'")
        )
        if is_quoted:
            content = content[1:-1]
        elif "{" not in content:
            # Bare variable name: ${{ client_id }} → {client_id}
            content = "{" + content + "}"
        try:
            return content.format(**ctx, context_variables=ctx)
        except Exception as e:
            logger.warning(f"Failed to render system prompt block '${{{{ {content} }}}}': {e}")
            return match.group(0)

    return _TEMPLATE_PATTERN.sub(_replace, prompt)


def _append_context_blocks(prompt: str, ctx: dict[str, Any]) -> str:
    """Append runtime context blocks to *prompt* based on *ctx* values."""
    # <user_information>
    os_ver = ctx.get("os", "")
    workspace = ctx.get("workspace", "")
    if os_ver or workspace:
        lines = []
        if os_ver:
            lines.append(f"The USER's OS version is {os_ver}.")
        if workspace:
            lines.append(f"The workspace root is {workspace}")
        prompt += USER_INFORMATION_BLOCK.format(content="\n".join(lines))

    # <workdir_constraint>
    workdir = ctx.get("workdir", "")
    if workdir:
        prompt += WORKDIR_CONSTRAINT_BLOCK.format(workdir=workdir)
        img_dir = ctx.get("image_output_dir", "")
        if img_dir:
            prompt += IMAGE_OUTPUT_CONSTRAINT_BLOCK.format(img_dir=img_dir)

    return prompt
