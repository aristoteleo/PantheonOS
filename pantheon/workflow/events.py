"""The ``workflow.*`` event protocol (decision 12).

The dynamic-workflow engine emits ``workflow.*`` events over the existing chat
NATS stream (parallel to ``live_view.*``) to drive the frontend trace canvas.
Events are constructed deterministically by the engine during the ``node()``
lifecycle — no LLM is involved.

This module provides:
  * Module constants for each event ``type`` string.
  * A constructor function per event type, each returning a plain ``dict`` whose
    ``type`` field equals the matching constant.
  * :class:`WorkflowEventPublisher`, a best-effort publisher that mirrors the
    lazy-init + swallow pattern of ``live_view``'s ``_publish``.
"""

from __future__ import annotations

from typing import Any

from pantheon.utils.log import logger

# ── Event type constants ──────────────────────────────────────────────────

WORKFLOW_CREATED = "workflow.created"
WORKFLOW_NODE_STARTED = "workflow.node_started"
WORKFLOW_NODE_FINISHED = "workflow.node_finished"
WORKFLOW_PHASE_CHANGED = "workflow.phase_changed"
WORKFLOW_LOG = "workflow.log"
WORKFLOW_STATUS = "workflow.status"
WORKFLOW_RESUMED = "workflow.resumed"


# ── Constructors ──────────────────────────────────────────────────────────


def make_created(workflow_id: str, goal: str, phases: list) -> dict:
    """``workflow.created`` — a workflow began.

    ``phases`` is the list of phase dicts/strings from the script meta.
    """
    return {
        "type": WORKFLOW_CREATED,
        "workflow_id": workflow_id,
        "goal": goal,
        "phases": phases,
    }


def make_node_started(
    workflow_id: str, node_id: int, label: str, phase: str
) -> dict:
    """``workflow.node_started`` — a node began executing.

    ``node_id`` addresses the node (addressing int); ``label`` is the display
    string (may be empty). The two are kept distinct on purpose.
    """
    return {
        "type": WORKFLOW_NODE_STARTED,
        "workflow_id": workflow_id,
        "node_id": node_id,
        "label": label,
        "phase": phase,
    }


def make_node_finished(
    workflow_id: str,
    node_id: int,
    label: str,
    status: str,
    result_ref: str | None,
) -> dict:
    """``workflow.node_finished`` — a node finished.

    ``status`` is one of ``completed`` / ``failed`` / ``skipped``.
    ``result_ref`` is a relative path to the node result, or ``None``.
    """
    return {
        "type": WORKFLOW_NODE_FINISHED,
        "workflow_id": workflow_id,
        "node_id": node_id,
        "label": label,
        "status": status,
        "result_ref": result_ref,
    }


def make_phase_changed(workflow_id: str, phase: str) -> dict:
    """``workflow.phase_changed`` — the active phase changed."""
    return {
        "type": WORKFLOW_PHASE_CHANGED,
        "workflow_id": workflow_id,
        "phase": phase,
    }


def make_log(workflow_id: str, message: str) -> dict:
    """``workflow.log`` — a free-form log line."""
    return {
        "type": WORKFLOW_LOG,
        "workflow_id": workflow_id,
        "message": message,
    }


def make_status(workflow_id: str, status: str, progress: dict) -> dict:
    """``workflow.status`` — workflow-level status update.

    ``progress`` is a dict like ``{"total": int, "done": int}``.
    """
    return {
        "type": WORKFLOW_STATUS,
        "workflow_id": workflow_id,
        "status": status,
        "progress": progress,
    }


def make_resumed(workflow_id: str, cached_nodes: int, will_rerun: list) -> dict:
    """``workflow.resumed`` — resume statistics for a re-run."""
    return {
        "type": WORKFLOW_RESUMED,
        "workflow_id": workflow_id,
        "cached_nodes": cached_nodes,
        "will_rerun": will_rerun,
    }


# ── Publisher ─────────────────────────────────────────────────────────────


class WorkflowEventPublisher:
    """Best-effort publisher for ``workflow.*`` events over the chat stream.

    Mirrors ``live_view``'s ``_publish``: lazy-inits a ``NATSStreamAdapter`` on
    first use and swallows publish errors (streaming is best-effort; the test
    env has no NATS). The adapter may be dependency-injected for testing.
    """

    def __init__(self, nats: Any = None) -> None:
        # When None, the real adapter is lazily created on first publish.
        self._nats = nats

    async def publish(self, chat_id: str, event: dict) -> None:
        """Broadcast a ``workflow.*`` event to the UI over the NATS chat stream."""
        if not chat_id:
            logger.warning(
                "workflow: no chat_id, cannot publish {}", event.get("type")
            )
            return
        if self._nats is None:
            from pantheon.chatroom.stream import NATSStreamAdapter

            self._nats = NATSStreamAdapter()
        try:
            await self._nats.publish(chat_id, event["type"], event)
        except Exception as e:  # streaming is best-effort
            logger.error("workflow: publish failed: {}", e)
