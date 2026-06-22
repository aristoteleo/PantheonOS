"""
SkillToolSet — Agent tools for skill management.

3 tools only (Hermes pattern):
- skill_list: List all available skills
- skill_view: View full skill content or supporting file
- skill_manage: Create, update, patch, or delete a skill

Supporting file operations (references/, scripts/, etc.) use file_manager directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

from .runtime import LearningRuntime


SKILL_MANAGE_DESCRIPTION = (
    "Manage skills: create, update (full rewrite), patch (find-replace), or delete. "
    "Skills are your procedural memory — reusable approaches for recurring tasks.\n\n"
    "Actions:\n"
    "  create — New SKILL.md (name + content required, must have YAML frontmatter)\n"
    "  update — Full rewrite of SKILL.md (name + content required)\n"
    "  patch  — Find-and-replace in SKILL.md (name + old_string + new_string required)\n"
    "  delete — Remove skill entirely (name required)\n\n"
    "Name supports hierarchical paths: use 'category/skill-name' to organize skills "
    "(e.g. 'bioinformatics/scrna-qc'). Use the 'path' value from skill_list().\n\n"
    "Create when: complex task succeeded (3+ tool calls), errors overcome, "
    "user-corrected approach worked, non-trivial workflow discovered.\n\n"
    "Good skills: trigger conditions, numbered steps, pitfalls, verification.\n\n"
    "For supporting files inside the skill directory (for example references/, scripts/, styles/), "
    "use file_manager tools to read/write them directly in the skill directory."
)


class SkillToolSet(ToolSet):
    """Agent tools for skill (procedural knowledge) management — 3 tools."""

    def __init__(self, runtime: LearningRuntime):
        super().__init__("skills")
        self._runtime = runtime

    def _json(self, data: dict) -> str:
        return json.dumps(data, ensure_ascii=False)

    @tool
    async def skill_list(self) -> str:
        """List all available skills with names and descriptions.

        Use this to discover what skills exist before starting a task.
        If a skill matches your task, load it with skill_view(name=<path>).

        Returns:
            JSON with skills list: [{display_name, path, identifier, description, tags}]
        """
        store = self._runtime.store
        if not store:
            return self._json({"success": False, "error": "Learning system not initialized"})

        headers = store.scan_headers()
        skills = [
            {
                "name": h.name,
                "display_name": h.name,
                "path": h.path,
                "identifier": h.path,
                "description": h.description,
                "tags": h.tags,
                "scope": getattr(h, 'scope', 'project'),
            }
            for h in headers
        ]
        return self._json({
            "success": True,
            "count": len(skills),
            "skills": skills,
            "hint": "Use skill_view(name=<identifier>) with the 'identifier' or 'path' value to load a skill's full content. 'display_name' is human-readable only.",
        })

    @tool
    async def skill_view(
        self, name: str, file_path: str | None = None
    ) -> str:
        """View a skill's full content or a specific supporting file.

        Args:
            name: Skill identifier/path from skill_list() (e.g., "bio/high-mito-qc").
                  Leaf-name lookup is still accepted for backwards compatibility.
            file_path: Optional relative path to a supporting file inside the skill directory
                       (e.g., "references/thresholds.md" or "styles/neurips_plot.md").
                       If omitted, returns the full SKILL.md content.

        Returns:
            JSON with skill content, metadata, and linked files list.
        """
        store = self._runtime.store
        if not store:
            return self._json({"success": False, "error": "Learning system not initialized"})

        # Load supporting file
        if file_path:
            entry = store.load_skill(name)
            if not entry:
                return self._json({
                    "success": False,
                    "error": self._not_found_msg(name),
                })
            # A SKILL.md path points at a (possibly nested) skill's MAIN file —
            # itself a skill, not a supporting file. Resolve it to that skill so
            # e.g. skill_view(name="omics", file_path="database_access/SKILL.md")
            # works, instead of erroring on load_file()'s reserved-name guard.
            fp_parts = Path(file_path).parts
            if fp_parts and fp_parts[-1] == "SKILL.md":
                nested = "/".join(fp_parts[:-1])
                target = f"{entry.path}/{nested}" if nested else entry.path
                target_entry = store.load_skill(target)
                if target_entry:
                    return self._skill_view_result(target_entry)
                return self._json({
                    "success": False,
                    "error": (
                        f"'{file_path}' is a skill's main file, not a supporting file. "
                        f"View that skill directly: skill_view(name='{target}') (no file_path)."
                    ),
                })
            try:
                content = store.load_file(entry.path, file_path)
                if content is None:
                    return self._json({
                        "success": False,
                        "error": f"File '{file_path}' not found in skill '{entry.path}'.",
                    })
                return self._json({
                    "success": True,
                    "path": entry.path,
                    "identifier": entry.path,
                    "name": entry.name,
                    "display_name": entry.name,
                    "file_path": file_path,
                    "content": content,
                })
            except ValueError as e:
                return self._json({"success": False, "error": str(e)})

        # Load full skill
        entry = store.load_skill(name)
        if not entry:
            return self._json({
                "success": False,
                "error": self._not_found_msg(name),
            })

        return self._skill_view_result(entry)

    def _not_found_msg(self, name: str) -> str:
        """Not-found error enriched with concrete skill_view() suggestions when the
        name looks like a parent-relative reference followed from a SKILL.md link."""
        store = self._runtime.store
        suggestions = []
        if store:
            try:
                suggestions = store.suggest_for(name)
            except Exception:
                suggestions = []
        if suggestions:
            return (
                f"Skill '{name}' not found. Did you mean: "
                + " | ".join(suggestions)
                + " ? (Nested skills/files need the full path, or use "
                "skill_view(name=<parent>, file_path=...).)"
            )
        return f"Skill '{name}' not found. Use skill_list() to see available skills."

    def _skill_view_result(self, entry) -> str:
        """Build the full-skill JSON result. Shared by the no-file_path branch
        and the SKILL.md redirect (a SKILL.md path is itself a skill)."""
        result: dict[str, Any] = {
            "success": True,
            "path": entry.path,
            "identifier": entry.path,
            "name": entry.name,
            "display_name": entry.name,
            "description": entry.description,
            "content": entry.content,
        }
        if entry.tags:
            result["tags"] = entry.tags
        if entry.related_skills:
            result["related_skills"] = entry.related_skills
        if entry.linked_files:
            result["linked_files"] = entry.linked_files
            result["hint"] = "Use file_manager or skill_view(name=<identifier>, file_path=...) to read linked files in the skill directory."
        if entry.version:
            result["version"] = entry.version

        return self._json(result)

    @tool(description=SKILL_MANAGE_DESCRIPTION)
    async def skill_manage(
        self,
        action: str,
        name: str,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        replace_all: bool = False,
    ) -> str:
        """Manage skills: create, update, patch, or delete.

        Args:
            action: One of "create", "update", "patch", "delete".
            name: Skill name (lowercase, hyphens/dots/underscores, ≤64 chars).
            content: Full SKILL.md content (required for create/update).
            old_string: Text to find (required for patch).
            new_string: Replacement text (required for patch).
            replace_all: Replace all occurrences when patching (default: False).

        Returns:
            JSON confirmation or error with actionable hint.
        """
        store = self._runtime.store
        if not store:
            return self._json({"success": False, "error": "Learning system not initialized"})

        try:
            if action == "create":
                if not content:
                    return self._json({"success": False, "error": "content is required for create."})
                path = store.create_skill(name, content)
                self._runtime.on_skill_tool_used()
                if self._runtime.injector:
                    self._runtime.injector.invalidate_cache()
                return self._json({
                    "success": True,
                    "name": name,
                    "path": str(path),
                    "message": f"Skill '{name}' created.",
                })

            elif action == "update":
                if not content:
                    return self._json({"success": False, "error": "content is required for update."})
                path = store.update_skill(name, content)
                self._runtime.on_skill_tool_used()
                if self._runtime.injector:
                    self._runtime.injector.invalidate_cache()
                return self._json({
                    "success": True,
                    "name": name,
                    "message": f"Skill '{name}' rewritten.",
                })

            elif action == "patch":
                if old_string is None or new_string is None:
                    return self._json({"success": False, "error": "old_string and new_string are required for patch."})
                store.patch_skill(name, old_string, new_string, replace_all)
                self._runtime.on_skill_tool_used()
                if self._runtime.injector:
                    self._runtime.injector.invalidate_cache()
                return self._json({
                    "success": True,
                    "name": name,
                    "message": f"Skill '{name}' patched.",
                })

            elif action == "delete":
                deleted = store.delete_skill(name)
                if not deleted:
                    return self._json({
                        "success": False,
                        "error": f"Skill '{name}' not found.",
                        "hint": "Use skill_list() to see available skills.",
                    })
                self._runtime.on_skill_tool_used()
                if self._runtime.injector:
                    self._runtime.injector.invalidate_cache()
                return self._json({
                    "success": True,
                    "message": f"Skill '{name}' deleted.",
                })

            else:
                return self._json({
                    "success": False,
                    "error": f"Unknown action '{action}'. Use: create, update, patch, delete.",
                })

        except ValueError as e:
            return self._json({
                "success": False,
                "error": str(e),
                "hint": "Use skill_list() to see existing skills.",
            })
