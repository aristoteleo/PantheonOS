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
    "For supporting files (references/, scripts/, templates/, assets/), "
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
        If a skill matches your task, load it with skill_view(name).

        Returns:
            JSON with skills list: [{name, description, tags}]
        """
        store = self._runtime.store
        if not store:
            return self._json({"success": False, "error": "Learning system not initialized"})

        headers = store.scan_headers()
        skills = [
            {
                "name": h.name,
                "path": h.path,  # relative path key, e.g. "bioinformatics/scrna-qc"
                "description": h.description,
                "tags": h.tags,
            }
            for h in headers
        ]
        return self._json({
            "success": True,
            "count": len(skills),
            "skills": skills,
            "hint": "Use skill_view(name) with the 'path' value to load a skill's full content.",
        })

    @tool
    async def skill_view(
        self, name: str, file_path: str | None = None
    ) -> str:
        """View a skill's full content or a specific supporting file.

        Args:
            name: Skill name (e.g., "high-mito-qc").
            file_path: Optional path to a supporting file (e.g., "references/thresholds.md").
                       If omitted, returns the full SKILL.md content.

        Returns:
            JSON with skill content, metadata, and linked files list.
        """
        store = self._runtime.store
        if not store:
            return self._json({"success": False, "error": "Learning system not initialized"})

        # Load supporting file
        if file_path:
            try:
                content = store.load_file(name, file_path)
                if content is None:
                    return self._json({
                        "success": False,
                        "error": f"File '{file_path}' not found in skill '{name}'.",
                    })
                return self._json({
                    "success": True,
                    "name": name,
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
                "error": f"Skill '{name}' not found. Use skill_list() to see available skills.",
            })

        result: dict[str, Any] = {
            "success": True,
            "name": entry.name,
            "description": entry.description,
            "content": entry.content,
        }
        if entry.tags:
            result["tags"] = entry.tags
        if entry.related_skills:
            result["related_skills"] = entry.related_skills
        if entry.linked_files:
            result["linked_files"] = entry.linked_files
            result["hint"] = "Use file_manager to read linked files in the skill directory."
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
