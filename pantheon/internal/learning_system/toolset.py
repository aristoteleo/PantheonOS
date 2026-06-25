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

    # ---- Pantheon Store marketplace (broader than the local skill set) ----

    def _hub_url(self) -> str:
        import os
        return os.environ.get(
            "PANTHEON_HUB_URL", "https://app.pantheonos.stanford.edu"
        ).rstrip("/")

    def _store_credentials(self):
        """Resolve the user's store identity for WRITE ops (feedback) only.

        Read ops (search/adopt) are anonymous; only feedback is posted as the
        user, so only it requires login. Hosted runtimes inject the logged-in
        user's store JWT via PANTHEON_STORE_TOKEN (+ optional PANTHEON_STORE_USER
        for attribution); the local CLI uses the `pantheon store login`
        credentials file. Returns (token, username), or (None, None) when the
        user isn't logged in.
        """
        import os
        token = os.environ.get("PANTHEON_STORE_TOKEN")
        if token:
            return token, os.environ.get("PANTHEON_STORE_USER")
        try:
            from pantheon.store.auth import StoreAuth
            auth = StoreAuth()
            if auth.is_logged_in:
                return auth.token, auth.username
        except Exception:
            pass
        return None, None

    @tool
    async def skill_search_store(self, query: str, limit: int = 8) -> str:
        """Search the Pantheon Store MARKETPLACE for skills relevant to your task.

        Your LOCAL skills (skill_list) are the trusted default — use this when the
        local set doesn't cover the task; the marketplace is broader. Returns ranked
        candidates with the AI reviewer's decision payload: verdict, rating, best_for,
        not_for, caveats. Adopt a good one with skill_adopt(name=...).

        Choosing: prefer a candidate whose `best_for` matches your task with a good
        `verdict`/rating; avoid ones whose `not_for`/`caveats` exclude your case (e.g.
        a caveat that an organism is hardcoded). Don't adopt one that duplicates a
        local skill — prefer the local one.

        Args:
            query: task description / capability keywords.
            limit: max candidates (default 8).
        """
        import httpx
        exclude = []
        if self._runtime.store:
            try:
                exclude = [h.path for h in self._runtime.store.scan_headers()]
            except Exception:
                exclude = []
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"{self._hub_url()}/api/store/recommend",
                    params={"q": query, "limit": limit, "exclude": ",".join(exclude)},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return self._json({"success": False, "error": f"store search failed: {e}"})
        results = data.get("results", [])
        return self._json({
            "success": True,
            "count": len(results),
            "results": results,
            "hint": (
                "Adopt one with skill_adopt(name=...). Prefer verdict=recommended with a "
                "matching best_for and good rating; check not_for/caveats don't exclude your "
                "task. Skip any that duplicate a local skill from skill_list()."
            ),
        })

    @tool
    async def skill_browse_store(self, query: str = None, category: str = None, limit: int = 15) -> str:
        """Look up Pantheon Store skills by KEYWORD or CATEGORY — exact/filter
        search, NOT semantic ranking.

        Use this when you know a name, a keyword, or a category and want precise
        matches or to see what exists in a category. For a fuzzy "find me something
        for task X" with quality signals, use skill_search_store instead (semantic
        + reviewer verdict/best_for). Adopt any result with skill_adopt(name=...).

        Args:
            query: keyword matched in name/display/description (optional).
            category: restrict to one category, e.g. "bioinformatics" (optional).
            limit: max results (default 15).
        """
        import httpx
        params = {"type": "skill", "limit": limit}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(f"{self._hub_url()}/api/store/packages", params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return self._json({"success": False, "error": f"store browse failed: {e}"})
        results = [{
            "name": p.get("name"),
            "display_name": p.get("display_name"),
            "description": p.get("description"),
            "category": p.get("category"),
            "rating_avg": p.get("rating_avg"),
            "rating_count": p.get("rating_count"),
        } for p in data.get("packages", [])]
        return self._json({
            "success": True,
            "count": len(results),
            "total": data.get("total"),
            "results": results,
            "hint": (
                "Exact keyword/category matches (no semantic ranking, no reviewer verdict). "
                "Adopt one with skill_adopt(name=...). For task-intent discovery with quality "
                "signals use skill_search_store. Prefer a local skill from skill_list() if it fits."
            ),
        })

    @tool
    async def skill_adopt(self, name: str) -> str:
        """Adopt a Pantheon Store skill for THIS task (ephemeral — NOT installed locally).

        Downloads the skill's content into your context so you can follow it as a
        playbook for the current task. It is not added to your local skills. Use
        skill_search_store first to find candidates.

        Args:
            name: the store `name` of a candidate from skill_search_store.
        """
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{self._hub_url()}/api/store/packages/{name}/download")
                if r.status_code == 404:
                    return self._json({"success": False, "error": f"Skill '{name}' not found in the store"})
                r.raise_for_status()
                dl = r.json()
        except Exception as e:
            return self._json({"success": False, "error": f"adopt failed: {e}"})
        # Remember which store skills were used this session (for post-task feedback, later).
        try:
            adopted = getattr(self._runtime, "adopted_store_skills", None)
            if adopted is None:
                adopted = []
                setattr(self._runtime, "adopted_store_skills", adopted)
            if name not in adopted:
                adopted.append(name)
        except Exception:
            pass
        files = dl.get("files") or {}
        token, _user = self._store_credentials()
        if token:
            fb = ("When the task is done, leave usage feedback with "
                  "skill_rate(name, rating, comment) — it's posted as the user's review.")
        else:
            fb = ("To leave usage feedback when done (it becomes the user's own review and "
                  "improves the store), the user must be logged in — mention they can run "
                  "`pantheon store login`.")
        return self._json({
            "success": True,
            "name": dl.get("name"),
            "version": dl.get("version"),
            "content": dl.get("content", ""),
            "bundled_files": list(files.keys()),
            "hint": (
                "EPHEMERAL store skill (not installed). Follow its content as a playbook "
                "for this task. " + fb
            ),
        })

    @tool
    async def skill_rate(self, name: str, rating: int, comment: str = None) -> str:
        """Leave usage feedback on a Pantheon Store skill you ADOPTED and used this task.

        Call this AFTER finishing a task in which you used skill_adopt(), to report
        whether the skill actually helped: did it work, was its best_for accurate, did
        its caveats hold? Your feedback becomes a real review (rating + comment) — the
        store's quality signal is usage-validated this way, not just the static AI
        review. Only rate skills you actually used.

        Args:
            name: the store name of an adopted skill.
            rating: 1-5 (1 = useless/wrong/misleading, 5 = exactly what was needed).
            comment: optional — what worked, what didn't, whether the caveats held.
        """
        try:
            rating = int(rating)
        except Exception:
            return self._json({"success": False, "error": "rating must be an integer 1-5"})
        if not (1 <= rating <= 5):
            return self._json({"success": False, "error": "rating must be 1-5"})
        # Feedback is posted as the USER's own review, so it requires login.
        token, username = self._store_credentials()
        if not token:
            return self._json({
                "success": False,
                "needs_login": True,
                "error": (
                    "Feedback becomes the user's own review, which requires login. Tell the "
                    "user they can run `pantheon store login` to enable it — don't silently "
                    "skip. (Hosted runtimes inject PANTHEON_STORE_TOKEN automatically.)"
                ),
            })
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    f"{self._hub_url()}/api/store/packages/{name}/reviews",
                    json={"rating": rating, "comment": (comment or "").strip() or None},
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code == 404:
                return self._json({"success": False, "error": f"Skill '{name}' not found in the store"})
            if r.status_code in (401, 403):
                return self._json({
                    "success": False,
                    "needs_login": True,
                    "error": "Store session expired or invalid — tell the user to run "
                             "`pantheon store login` again to leave feedback.",
                })
            r.raise_for_status()
        except Exception as e:
            return self._json({"success": False, "error": f"feedback failed: {e}"})
        who = f" as {username}" if username else ""
        return self._json({
            "success": True,
            "hint": f"Recorded your usage feedback{who} on '{name}' (rating {rating}/5). "
                    "This usage-validates the store's quality signal for everyone.",
        })
