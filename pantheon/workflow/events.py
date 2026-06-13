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

import asyncio
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

    Headless graceful degradation
    -----------------------------
    The shared NATS backend is configured for *infinite* reconnect (good for
    long-running pods, bad for a one-shot/headless run with no NATS: the
    reconnect loop spams logs and keeps the event loop alive so the process
    never exits cleanly). To stay robust off-cluster, this publisher LATCHES
    ITSELF DISABLED on the first publish failure and best-effort tears the
    backend down, so subsequent publishes are instant no-ops and no background
    reconnect loop survives. Call :meth:`aclose` on engine shutdown for an
    explicit teardown.
    """

    def __init__(self, nats: Any = None, *, connect_timeout: float = 5.0) -> None:
        # When None, the real adapter is lazily created on first publish.
        self._nats = nats
        # Latched True after a publish failure (or explicit aclose): all further
        # publishes become no-ops. Prevents repeated connect attempts + log spam
        # in a no-NATS environment.
        self._disabled = False
        # Until one publish succeeds, each attempt is time-bounded. The shared
        # NATS client connects with INFINITE reconnect, so a no-NATS publish
        # never raises — it blocks inside connect() forever (the reconnect loop
        # is internal). Bounding the await lets us detect "no NATS" and disable.
        self._proven = False
        self._connect_timeout = connect_timeout

    async def publish(self, chat_id: str, event: dict) -> None:
        """Broadcast a ``workflow.*`` event to the UI over the NATS chat stream."""
        if self._disabled:
            return
        if not chat_id:
            logger.warning(
                "workflow: no chat_id, cannot publish {}", event.get("type")
            )
            return
        if self._nats is None:
            from pantheon.chatroom.stream import NATSStreamAdapter

            self._nats = NATSStreamAdapter()
        try:
            coro = self._nats.publish(chat_id, event["type"], event)
            if self._proven:
                await coro
            else:
                # First publish: bound it. Cancelling on timeout aborts the
                # in-flight connect()'s internal retry loop (no dangling task).
                await asyncio.wait_for(coro, timeout=self._connect_timeout)
                self._proven = True
        except (Exception, asyncio.TimeoutError) as e:  # best-effort
            # First failure / timeout (typically "no NATS server"): go silent and
            # tear the backend down so an infinite-reconnect loop can't keep
            # spamming or block process exit. The production room re-creates the
            # publisher per process where NATS is actually present.
            logger.error(
                "workflow: publish failed, disabling event stream: {}", e
            )
            await self._teardown()

    async def aclose(self) -> None:
        """Explicitly disable the publisher and tear down its backend."""
        await self._teardown()

    async def _teardown(self) -> None:
        self._disabled = True
        nats = self._nats
        self._nats = None
        if nats is None:
            return
        # NATSStreamAdapter holds the backend on ``_backend``; close it so the
        # nats client's reconnect task stops and the loop can exit.
        backend = getattr(nats, "_backend", None)
        close = getattr(backend, "close", None)
        if close is None:
            return
        try:
            await close()
        except Exception as e:  # noqa: BLE001 — teardown must never raise
            logger.warning("workflow: backend close during teardown failed: {}", e)
