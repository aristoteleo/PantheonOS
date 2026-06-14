"""WorkflowToolSet — the 5 tools the Leader agent uses to drive workflows.

The Leader never deals with the engine directly; it calls these five tools.
Each tool resolves the calling ``chat_id`` from the execution context (the
Leader never passes it — it is injected by the framework, exactly like
``live_view``) and delegates to the injected :class:`WorkflowEngine`.

Design notes (Phase-1 design decision 8):

  * **chat_id is implicit.** Resolved from context via :meth:`_chat_id`
    (``session_id`` or ``chat_id`` in the execution context). If absent, the
    tool returns ``{"error": "no chat context"}`` rather than calling the
    engine with ``None``.
  * **created_at is generated HERE.** The engine is determinism-constrained and
    must not call ``time`` / ``datetime.now``; the toolset is the boundary where
    a real timestamp legitimately enters, so ``workflow_create`` and
    ``workflow_edit`` stamp ``datetime.now(timezone.utc).isoformat()`` and pass
    it to the engine.
  * **ownership is enforced in the engine.** It raises ``PermissionError`` on a
    cross-chat access and ``FileNotFoundError`` for an unknown workflow. The
    toolset converts both (and any other unexpected exception) into a friendly
    ``{"error": ...}`` dict via :meth:`_safe` so the Leader can explain it to
    the user instead of seeing a raw tool error. Error dicts the engine returns
    itself (e.g. ``{"error", "line"}`` validation failures) pass straight
    through so the Leader can rewrite the script.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

#: Path to the Leader-facing script-authoring guide shipped beside this module.
SCRIPT_GUIDE_PATH = Path(__file__).with_name("SCRIPT_GUIDE.md")


def load_script_guide() -> str:
    """Return the SCRIPT_GUIDE.md text (the Leader's script-authoring contract).

    The guide lives beside this module so it can be injected into the Leader's
    context (e.g. via the team plugin's system instructions) so the Leader writes
    correct, accurate workflow scripts. Returns ``""`` if the file is missing,
    so a packaging glitch never crashes toolset construction.
    """
    try:
        return SCRIPT_GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("workflow: SCRIPT_GUIDE.md not found at {}", SCRIPT_GUIDE_PATH)
        return ""


#: The script-authoring guide text, loaded once at import. Exposed so the plugin
#: (or any Leader-context assembler) can include it in the Leader's instructions.
SCRIPT_GUIDE: str = load_script_guide()


class WorkflowToolSet(ToolSet):
    """Leader-facing toolset delegating to a :class:`WorkflowEngine`."""

    def __init__(self, engine: Any, name: str = "workflow", **kwargs):
        super().__init__(name, **kwargs)
        self._engine = engine

    # ── internals ─────────────────────────────────────────────────────────

    def _chat_id(self) -> str | None:
        """Resolve the chat id from the execution context.

        Agent tool calls carry it as ``chat_id`` (injected by room.chat); the
        UI proxy path injects it as ``session_id``. NOT ``client_id`` (that is
        the UI connection id, stable across chats).
        """
        ctx = self.get_context() or {}
        return ctx.get("session_id") or ctx.get("chat_id")

    @staticmethod
    def _now() -> str:
        """A real UTC timestamp — the toolset is the allowed time boundary."""
        return datetime.now(timezone.utc).isoformat()

    async def _safe(self, coro: Awaitable[dict]) -> dict:
        """Await ``coro`` turning ownership / unknown errors into error dicts.

        ``PermissionError`` (cross-chat denial, Codex F2) and
        ``FileNotFoundError`` (unknown workflow) become friendly
        ``{"error": ...}`` dicts. Any other unexpected exception is logged and
        wrapped too, so a tool never raises a raw traceback at the Leader. The
        engine's own ``{"error", ...}`` return dicts pass through untouched.
        """
        try:
            return await coro
        except PermissionError as e:
            return {"error": str(e) or "permission denied"}
        except FileNotFoundError as e:
            return {"error": str(e) or "workflow not found"}
        except Exception as e:  # noqa: BLE001 — tool boundary must stay robust
            logger.exception("workflow tool failed: {}", e)
            return {"error": f"workflow tool failed: {e}"}

    # ── tools ─────────────────────────────────────────────────────────────

    @tool
    async def workflow_create(
        self,
        goal: str,
        script: str,
        args: dict | None = None,
        auto_start: bool = True,
    ) -> dict:
        """Validate and (by default) start a new dynamic workflow from a script.

        The ``script`` is a Pantheon workflow script: an async-style program
        that calls the workflow ``node(...)`` API to fan out delegated work. It
        MUST begin with a ``meta = {...}`` literal whose ``phases`` list names
        the high-level phases — those phases are returned so you can narrate the
        plan to the user. See the bundled SCRIPT_GUIDE (``SCRIPT_GUIDE`` constant
        in ``pantheon.workflow.toolset``) for the full, authoritative script API,
        the determinism rules, and worked examples.

        The script is validated before it runs. On a validation failure this
        returns ``{"error": <message>, "line": <line>}`` — REWRITE the script to
        fix the reported line and call this again; nothing was started. On
        success it returns ``{"workflow_id", "phases", "status"}``; use the
        ``workflow_id`` for every other workflow tool.

        Args:
            goal: A short human-readable statement of what the workflow achieves.
            script: The workflow script source (see above; leading ``meta``).
            args: Optional input arguments passed to the script as its inputs.
            auto_start: Start running immediately (default). Pass ``False`` to
                validate and persist without launching.

        Returns:
            ``{"workflow_id", "phases", "status"}`` on success, or
            ``{"error", "line"}`` if the script failed validation.
        """
        chat_id = self._chat_id()
        if not chat_id:
            return {"error": "no chat context"}
        return await self._safe(
            self._engine.create(
                chat_id,
                goal,
                script,
                args,
                auto_start=auto_start,
                created_at=self._now(),
            )
        )

    @tool
    async def workflow_status(self, workflow_id: str | None = None) -> dict:
        """Get a status overview of this chat's workflows, or one workflow's trace.

        With no ``workflow_id``: a one-line overview of every workflow in this
        chat (id, status, goal, progress). With a ``workflow_id``: a trace
        summary for that workflow — overall status, current phase, progress, and
        a per-node list of ``{node_id, label, status}`` plus any errors and the
        artifact directory. This NEVER inlines full node output; use
        ``workflow_get_output`` to read a specific node's result.

        Args:
            workflow_id: Omit for an all-workflows overview, or pass an id for a
                single workflow's trace summary.

        Returns:
            ``{"workflows": [...]}`` (overview) or a single-workflow trace dict;
            ``{"error": ...}`` if the workflow is unknown or not owned by this
            chat.
        """
        chat_id = self._chat_id()
        if not chat_id:
            return {"error": "no chat context"}
        return await self._safe(self._engine.status(workflow_id, chat_id))

    @tool
    async def workflow_get_output(
        self,
        workflow_id: str,
        node_id: int | None = None,
        summary_only: bool = True,
    ) -> dict:
        """Read a workflow's final result, or one node's output.

        With no ``node_id`` you get the workflow's final result; with a
        ``node_id`` (the integer id shown in ``workflow_status``) you get that
        node's output. By default (``summary_only=True``) only the artifact
        ``path`` and a truncated ``preview`` are returned — full content is not
        dumped into the conversation. Pass ``summary_only=False`` to get the
        full ``content`` when you genuinely need it.

        Args:
            workflow_id: The workflow to read from.
            node_id: A node's integer id (from ``workflow_status``); omit for the
                workflow's final result.
            summary_only: Return only path + preview (default), or full content.

        Returns:
            A dict with ``path``, ``exists``, and either ``preview`` (+
            ``truncated``) or ``content``; ``{"error": ...}`` if unknown / not
            owned.
        """
        chat_id = self._chat_id()
        if not chat_id:
            return {"error": "no chat context"}
        return await self._safe(
            self._engine.get_output(workflow_id, chat_id, node_id, summary_only)
        )

    @tool
    async def workflow_edit(self, workflow_id: str, script: str) -> dict:
        """Replace a workflow's script and resume it, reusing cached node results.

        Pass the FULL modified script (not a diff). The new script is validated
        and stored, then the workflow resumes against its existing journal: any
        node whose inputs are unchanged is served from cache, so only the parts
        you actually changed (and anything downstream of them) re-run. Use the
        returned counts to tell the user how much prior work was preserved.

        On a validation failure this returns ``{"error", "line"}`` (same as
        ``workflow_create``) — fix that line and call again.

        Args:
            workflow_id: The workflow to edit.
            script: The complete new script source.

        Returns:
            ``{"resumed_from", "cached_nodes", "will_rerun"}`` on success
            (``cached_nodes`` = nodes reused from cache, ``will_rerun`` = node
            ids re-executed), ``{"error", "line"}`` on validation failure, or
            ``{"error": ...}`` if unknown / not owned.
        """
        chat_id = self._chat_id()
        if not chat_id:
            return {"error": "no chat context"}
        return await self._safe(
            self._engine.resume(workflow_id, chat_id, new_script=script)
        )

    @tool
    async def workflow_control(
        self,
        workflow_id: str,
        action: str,
        node_id: int | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        """Pause, resume, cancel, or re-run a node of a running workflow.

        Actions:
          * ``pause``       — stop the running workflow (resumable later).
          * ``resume``      — continue a paused / interrupted workflow.
          * ``cancel``      — permanently stop the workflow.
          * ``skip_node``   — mark a node skipped, then resume (requires
            ``node_id``).
          * ``retry_node``  — invalidate a node's cached result and resume so it
            re-runs (requires ``node_id``).

        ``node_id`` addresses a node by its integer id (from
        ``workflow_status``), NOT its label.

        Concurrency (§A.3): every control routes through the same serialized,
        revision-CAS-guarded engine path the UI uses. ``expected_revision`` is
        the optimistic-concurrency token from ``workflow_status``'s ``revision``:
          * Pass it to guard against acting on a stale view — if the workflow's
            revision has advanced since you read it, the engine REJECTS the
            action and returns ``{"accepted": False, "status", "revision"}`` with
            the current authoritative state (re-read and decide again).
          * OMIT it (the default) to act on the workflow's current state
            unconditionally — appropriate when you are reacting to the latest
            ``workflow_status`` and just want the action applied.

        Args:
            workflow_id: The workflow to control.
            action: One of pause / resume / cancel / skip_node / retry_node.
            node_id: Required for skip_node / retry_node; the node's integer id.
            expected_revision: Optional CAS token; see Concurrency above.

        Returns:
            ``{"workflow_id", "accepted", "status", "revision"}`` (resume-like
            actions add ``cached_nodes`` / ``will_rerun``); ``{"error": ...}`` if
            unknown / not owned or the action is invalid.
        """
        chat_id = self._chat_id()
        if not chat_id:
            return {"error": "no chat context"}
        return await self._safe(
            self._engine.control(
                workflow_id, chat_id, action, node_id, expected_revision
            )
        )
