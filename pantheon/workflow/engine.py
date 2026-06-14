"""WorkflowEngine — the integration hub for the dynamic-workflow module (Task 8).

The engine ties every other module together and owns workflow *sessions*:
lifecycle (create / run / resume / control), persistence, restart recovery, and
the key-event callback the room wires to the Leader's queue. It runs each
workflow as a background ``asyncio.Task``, fully autonomously (the Leader stays
lightweight — design decision 2).

Coupling contract (decision 13): this module does NOT import ``chatroom``. The
only outward coupling is an injected ``on_key_event`` callback (decision 14) the
room sets to wake the Leader on key events. The engine just calls the callback;
it knows nothing about SteerQueue.

Determinism (decision 16): the engine never calls ``uuid4`` / ``time`` /
``datetime.now``. ``workflow_id`` and ``created_at`` are injected by the caller
(the toolset/room); when no ``workflow_id`` is supplied the engine derives one
from a per-instance monotonic counter (``wf-{n}``).

Resource caps (decision §4.1):
  * ``timeout``    — overall script wall-clock, passed to ``run_script``.
  * ``concurrency``— max concurrent ``runner.run`` calls, passed to ``make_api``.
  * ``max_nodes``  — hard cap on ``node()`` calls. Enforced in the engine by
    wrapping the api's ``node`` with a counting guard that raises
    :class:`WorkflowLimitError` once the cap is exceeded (the api's own counter
    is internal; wrapping here is the simplest correct Phase-1 enforcement).

Final result persistence: ``WorkflowState`` has no field for the script's
return value, so the final value is persisted via
``WorkflowStorage.write_result`` to ``{workflow_dir}/result.json`` as
``{"result": <value>}`` (atomic temp+replace, json ``default=str``).
``get_output`` reads it back.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import events as ev
from .api import NodeError, make_api
from .events import WorkflowEventPublisher
from .journal import Journal
from .models import WorkflowMeta, WorkflowState
from .runner import InProcessRunner, NodeRunContext, NodeRunner
from .sandbox import run_script, validate_script
from .storage import WorkflowStorage, scan_workflows

logger = logging.getLogger(__name__)

__all__ = [
    "WorkflowEngine",
    "WorkflowSession",
    "WorkflowLimitError",
    "extract_phases",
    "extract_blueprint",
    "validate_blueprint",
    "_nonliteral_slot_lines",
    "TERMINAL_STATUSES",
    "UNSETTLED_STATUSES",
]

#: Defaults for the resource caps (decision §4.1).
DEFAULT_MAX_NODES = 200
DEFAULT_TIMEOUT = 1800.0  # seconds (overall script wall-clock)
DEFAULT_CONCURRENCY = 8

#: How many characters of a result/preview to surface when ``summary_only``.
_PREVIEW_CHARS = 500

#: Statuses from which a workflow never transitions again (§A.4). Used to guard
#: the control() cancel branch so a cancel arriving AFTER a natural terminal
#: status cannot clobber it (completed/failed/cancelled). NOTE: ``interrupted``
#: is deliberately NOT terminal — per §A.4 it is an UNSETTLED, resumable status,
#: so a recover()'d / timed-out workflow stays discoverable by workflow_list and
#: _terminal_status returns None for it (cancel can still promote it).
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: Statuses that are NOT settled (§A.4): the workflow can still transition, and
#: workflow_list surfaces these as live/actionable. ``awaiting_intervention`` is
#: a persistent non-terminal failure awaiting a user retry/skip/cancel decision.
UNSETTLED_STATUSES = frozenset(
    {"running", "paused", "interrupted", "awaiting_intervention"}
)


class WorkflowLimitError(Exception):
    """Raised when a workflow exceeds the ``max_nodes`` cap (decision §4.1)."""


def extract_phases(source: str) -> list:
    """Extract ``meta["phases"]`` from a script's leading ``meta = {...}`` literal.

    Parses ``source`` and finds the module-level assignment to a name ``meta``
    whose value is a *pure literal* dict. ``ast.literal_eval`` is used so a
    non-literal value (a name reference, a call, …) safely yields ``[]`` rather
    than raising. A missing ``meta`` also yields ``[]`` (Phase-1: missing meta →
    empty phases is clearer than a confusing hard-fail).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = stmt.targets
        if not (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and targets[0].id == "meta"
            and isinstance(stmt.value, ast.Dict)
        ):
            continue
        try:
            meta = ast.literal_eval(stmt.value)
        except (ValueError, TypeError, SyntaxError):
            return []
        if isinstance(meta, dict):
            phases = meta.get("phases", [])
            return list(phases) if isinstance(phases, (list, tuple)) else []
        return []
    return []


def extract_blueprint(source: str) -> list:
    """Extract ``meta["blueprint"]`` from a script's ``meta = {...}`` literal.

    Same pure-literal contract as :func:`extract_phases` (§A.8): parse the
    source, find the module-level ``meta`` assignment whose value is a literal
    dict, ``ast.literal_eval`` it, and return ``meta["blueprint"]`` as a list.
    A non-literal value, a missing ``meta``, or a ``SyntaxError`` all safely
    yield ``[]`` *without ever executing the script body*.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = stmt.targets
        if not (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and targets[0].id == "meta"
            and isinstance(stmt.value, ast.Dict)
        ):
            continue
        try:
            meta = ast.literal_eval(stmt.value)
        except (ValueError, TypeError, SyntaxError):
            return []
        if isinstance(meta, dict):
            blueprint = meta.get("blueprint", [])
            return list(blueprint) if isinstance(blueprint, (list, tuple)) else []
        return []
    return []


def _referenced_slots(source: str) -> set[str]:
    """Statically collect ``slot=`` literal values from ``node(...)`` calls.

    AST-only: walks every call named ``node`` and collects the *literal* string
    value of any ``slot=`` keyword argument. Non-literal slot values (a name,
    an expression) are not statically knowable and are skipped. The script is
    never executed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    slots: set[str] = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name != "node":
            continue
        for kw in call.keywords:
            if kw.arg != "slot":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(
                kw.value.value, str
            ):
                slots.add(kw.value.value)
    return slots


def _nonliteral_slot_lines(source: str) -> list[int]:
    """Line numbers of ``node(slot=<non-literal>)`` calls (orphan escape, §A.7).

    A ``slot=`` whose value is NOT a literal string (a name, an f-string, any
    expression) is not statically knowable. If we let it through, the slot
    silently resolves to ``None`` at runtime → an orphan node the blueprint
    never declared and the UI can't reconcile. C1 only *collected* literal slots
    and silently dropped non-literals; this surfaces them so create can reject.

    A literal ``None`` (``slot=None``) is allowed — it is the explicit "no slot"
    value and is statically knowable. Every other non-string-constant value is
    reported. Sorted, de-duplicated line numbers are returned.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines: set[int] = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name != "node":
            continue
        for kw in call.keywords:
            if kw.arg != "slot":
                continue
            v = kw.value
            # Allowed: a string literal, or a literal None.
            if isinstance(v, ast.Constant) and (
                isinstance(v.value, str) or v.value is None
            ):
                continue
            lines.add(getattr(call, "lineno", 0))
    return sorted(lines)


def _slot_ref_line(source: str, slot_id: str) -> int | None:
    """Best-effort line number of the first ``node(slot="<slot_id>")`` ref."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name != "node":
            continue
        for kw in call.keywords:
            if (
                kw.arg == "slot"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == slot_id
            ):
                return getattr(call, "lineno", None)
    return None


def validate_blueprint(
    blueprint: list, referenced_slots: set[str]
) -> tuple[bool, str | None, int | None]:
    """Validate a blueprint declaration against §A.8 rules.

    Returns ``(ok, error, line)``. ``line`` is populated only when the failure
    can be tied to a specific script reference (rule ③). Rules:
      ① ``slot_id`` unique across declarations.
      ② the same ``slot_id`` must not mix ``kind``.
      ③ every script-referenced slot must be declared.

    Rule ④ (script has ``slot=`` but blueprint missing/illegal) is enforced by
    the caller: an empty/invalid blueprint combined with a non-empty
    ``referenced_slots`` falls through rule ③ here (every referenced slot is
    undeclared), yielding a structured rejection.
    """
    declared: dict[str, str] = {}  # slot_id -> kind (first seen)
    for entry in blueprint:
        if not isinstance(entry, dict):
            return False, f"blueprint entry is not a mapping: {entry!r}", None
        slot_id = entry.get("slot_id")
        if not isinstance(slot_id, str) or not slot_id:
            return False, f"blueprint entry missing slot_id: {entry!r}", None
        kind = entry.get("kind")
        if slot_id in declared:
            if declared[slot_id] != kind:
                return (
                    False,
                    (
                        f"slot_id {slot_id!r} declared with conflicting kinds "
                        f"({declared[slot_id]!r} vs {kind!r})"
                    ),
                    None,
                )
            return False, f"duplicate slot_id {slot_id!r} in blueprint", None
        declared[slot_id] = kind

    for slot_id in sorted(referenced_slots):
        if slot_id not in declared:
            return (
                False,
                f"node references undeclared slot {slot_id!r}",
                None,
            )
    return True, None, None


@dataclass
class WorkflowSession:
    """In-memory handle for one workflow run.

    Holds identity, the background ``asyncio.Task`` (or ``None`` when pending /
    settled), the live :class:`Journal`, the script source, the args, and the
    api's ``run_state`` once a run is in flight (for live progress reads).
    """

    workflow_id: str
    chat_id: str
    script: str
    args: Any
    journal: Journal
    status: str = "pending"
    task: asyncio.Task | None = None
    run_state: Any = None  # api.RunState while running


class WorkflowEngine:
    """Owns workflow sessions, lifecycle, resume, control, and recovery."""

    def __init__(
        self,
        base_dir: Path,
        *,
        runner: NodeRunner | None = None,
        publisher: WorkflowEventPublisher | None = None,
        agent_factory: Any = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_nodes: int = DEFAULT_MAX_NODES,
        timeout: float | None = DEFAULT_TIMEOUT,
        on_key_event: Callable[[str, str, str, str], Any] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.storage = WorkflowStorage(self.base_dir)
        if runner is None:
            runner = (
                InProcessRunner(agent_factory)
                if agent_factory is not None
                else InProcessRunner()
            )
        self.runner = runner
        self.publisher = publisher or WorkflowEventPublisher()
        self.concurrency = concurrency
        self.max_nodes = max_nodes
        self.timeout = timeout
        self._on_key_event = on_key_event

        self.sessions: dict[str, WorkflowSession] = {}
        self._by_chat: dict[str, list[str]] = {}
        self._id_counter = 0
        # §A.3: per-workflow control serialization. control() runs its whole
        # body under self._lock_for(workflow_id) so concurrent UI/Leader calls
        # on the same workflow never interleave. asyncio.Lock is NON-reentrant,
        # so the lock is held ONLY at the control() top level; the resume() /
        # _cascade_invalidate() it calls inside MUST NOT re-acquire it.
        self._control_locks: dict[str, asyncio.Lock] = {}

    # --- id / ownership helpers ------------------------------------------- #

    def _next_id(self) -> str:
        wid = f"wf-{self._id_counter}"
        self._id_counter += 1
        return wid

    def _read_meta(self, workflow_id: str) -> WorkflowMeta:
        return self.storage.read_meta(workflow_id)

    def _check_owner(self, workflow_id: str, chat_id: str) -> WorkflowMeta:
        """Ownership gate (decision 8 / Codex F2): meta.chat_id must match.

        Enforced at the engine layer on EVERY caller-facing method (status,
        get_output, resume, control).

        Raises:
            FileNotFoundError: no ``meta.json`` exists for ``workflow_id``
                (unknown workflow).
            PermissionError: the workflow exists but ``meta.chat_id`` does not
                match the calling ``chat_id``.
        """
        meta = self._read_meta(workflow_id)
        if meta.chat_id != chat_id:
            raise PermissionError(
                f"workflow {workflow_id!r} is not owned by chat {chat_id!r}"
            )
        return meta

    def _lock_for(self, workflow_id: str) -> asyncio.Lock:
        """Lazily create + return the per-workflow control lock (§A.3)."""
        lock = self._control_locks.get(workflow_id)
        if lock is None:
            lock = asyncio.Lock()
            self._control_locks[workflow_id] = lock
        return lock

    def _index(self, session: WorkflowSession) -> None:
        self.sessions[session.workflow_id] = session
        self._by_chat.setdefault(session.chat_id, [])
        if session.workflow_id not in self._by_chat[session.chat_id]:
            self._by_chat[session.chat_id].append(session.workflow_id)

    # --- create ----------------------------------------------------------- #

    async def create(
        self,
        chat_id: str,
        goal: str,
        script: str,
        args: Any = None,
        *,
        auto_start: bool = True,
        created_at: str,
        workflow_id: str | None = None,
    ) -> dict:
        """Validate, persist, and (optionally) launch a new workflow.

        Returns ``{"error", "line"}`` on validation failure (the Leader
        rewrites). On success returns ``{"workflow_id", "phases", "status"}``.
        ``created_at`` is injected by the caller for determinism; ``workflow_id``
        may be injected too, else a ``wf-{n}`` id is generated.
        """
        validation = validate_script(script)
        if not validation.ok:
            return {"error": validation.error, "line": validation.line}

        # §A.7 orphan guard (C1 handoff): a ``node(slot=<non-literal>)`` is not
        # statically knowable, so it can't be reconciled against the blueprint
        # and must not silently degrade to slot=None at runtime. Reject before
        # any persistence. Checked ahead of blueprint validation so the more
        # specific "slot must be literal" error wins over a generic undeclared.
        nonliteral_lines = _nonliteral_slot_lines(script)
        if nonliteral_lines:
            return {
                "error": (
                    "node(slot=...) must be a literal string (or None): a "
                    "non-literal slot is not statically knowable and would "
                    "orphan the node from its blueprint slot"
                ),
                "line": nonliteral_lines[0],
            }

        # §A.8 blueprint validation, BEFORE any persistence so a rejected
        # workflow leaves no journal/meta (not discoverable by workflow_list).
        blueprint = extract_blueprint(script)
        referenced_slots = _referenced_slots(script)
        bp_ok, bp_error, _ = validate_blueprint(blueprint, referenced_slots)
        if not bp_ok:
            # Tie the failure to a script line when it names a referenced slot.
            line = None
            for slot_id in sorted(referenced_slots):
                if slot_id in bp_error:
                    line = _slot_ref_line(script, slot_id)
                    if line is not None:
                        break
            return {"error": bp_error, "line": line}
        # preview is an explicit state (§A.8): declared blueprint => "full";
        # a legacy script with no blueprint (and—per rule ④—no slot=) => "none".
        preview = "full" if blueprint else "none"

        phases = extract_phases(script)
        if workflow_id is None:
            workflow_id = self._next_id()

        self.storage.ensure_workflow(workflow_id)
        status = "pending"
        self.storage.write_meta(
            WorkflowMeta(
                workflow_id=workflow_id,
                chat_id=chat_id,
                goal=goal,
                created_at=created_at,
                status=status,
                preview=preview,
                blueprint=blueprint,
            )
        )
        self.storage.write_state(
            WorkflowState(workflow_id=workflow_id, status=status)
        )
        self.storage.write_script(workflow_id, script)
        self.storage.context_dir(workflow_id).mkdir(parents=True, exist_ok=True)
        inputs_path = self.storage.context_dir(workflow_id) / "inputs.json"
        inputs_path.write_text(
            json.dumps(args if args is not None else {}, default=str),
            encoding="utf-8",
        )

        journal = Journal(self.storage.workflow_dir(workflow_id) / "journal.jsonl")
        session = WorkflowSession(
            workflow_id=workflow_id,
            chat_id=chat_id,
            script=script,
            args=args,
            journal=journal,
            status=status,
        )
        self._index(session)

        if auto_start:
            self._launch(session, goal=goal, phases=phases)
            status = session.status

        return {"workflow_id": workflow_id, "phases": phases, "status": status}

    # --- run -------------------------------------------------------------- #

    def _launch(self, session: WorkflowSession, *, goal: str, phases: list) -> None:
        """Start ``_run`` as a background task and store it on the session.

        A done-callback is attached so the engine's OWN exceptions (publisher
        errors, disk I/O in ``_persist_status`` / ``write_result``) cannot vanish
        as an unretrieved-task-exception at GC and leave the session stuck at
        "running". ``run_script`` already absorbs *script* exceptions; this
        guards the work AROUND it.
        """
        session.status = "running"
        task = asyncio.ensure_future(self._run(session, goal=goal, phases=phases))
        task.add_done_callback(lambda t: self._on_task_done(session, t))
        session.task = task

    def _on_task_done(self, session: WorkflowSession, task: asyncio.Task) -> None:
        """Surface an unhandled ``_run`` exception and fail the session.

        Cancellation is expected (pause/cancel/timeout) and ignored here. Any
        other exception means ``_run`` itself broke (not the script); we log it
        and best-effort mark the workflow "failed" so it is never stuck running.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.exception(
            "workflow %s background task crashed", session.workflow_id,
            exc_info=exc,
        )
        session.status = "failed"
        try:
            self._mark(session.workflow_id, "failed")
        except Exception:  # noqa: BLE001 — best-effort; never raise from a callback
            logger.exception(
                "failed to persist crash status for %s", session.workflow_id
            )

    async def _run(
        self, session: WorkflowSession, *, goal: str, phases: list
    ) -> None:
        """Background coroutine: execute the script and persist the outcome."""
        wf_id = session.workflow_id
        chat_id = session.chat_id

        # Build a FRESH journal from disk for this run: persisted entries are
        # reloaded (so cache hits work), but the per-run state (_first_miss,
        # _recorded_this_run) starts clean. The same disk path is reused, so a
        # resume re-reads the prior run's recorded entries as cache candidates.
        session.journal = Journal(
            self.storage.workflow_dir(wf_id) / "journal.jsonl"
        )

        ctx = NodeRunContext(
            workflow_id=wf_id,
            storage=self.storage,
            chat_workdir=str(self.base_dir),
            execution_context_id=f"wf-{wf_id}",
        )
        # Seed the §A.2/§A.6 reconcile counters from persisted state so a resume
        # continues the same cascade epoch and per-node attempt counts.
        try:
            seed_state = self.storage.read_state(wf_id)
            seed_epoch = seed_state.cascade_epoch
            seed_attempts = dict(seed_state.attempt_counts)
        except (OSError, ValueError):
            seed_epoch, seed_attempts = 0, {}
        # §A.7: read the declaration preview mode. ``preview == "none"`` flips
        # the no-preview boundary switch so node() cleanses every slot_id to None
        # at the event/journal boundary, regardless of what the script wrote.
        # On a read failure we default to the CONSERVATIVE "none" (slot_enabled
        # =False): §A.7 prefers over-cleansing to ever leaking an orphan slot_id,
        # so an unreadable/missing preview must NOT fall back to pass-through.
        try:
            preview = self.storage.read_meta(wf_id).preview
        except (OSError, ValueError):
            preview = "none"
        slot_enabled = preview != "none"
        api = make_api(
            session.journal,
            self.runner,
            self.publisher,
            chat_id,
            ctx,
            concurrency=self.concurrency,
            cascade_epoch=seed_epoch,
            attempt_counts=seed_attempts,
            slot_enabled=slot_enabled,
        )
        session.run_state = api["run_state"]
        api["node"] = self._guard_node(api["node"])

        await self.publisher.publish(
            chat_id, ev.make_created(wf_id, goal, phases)
        )
        self._persist_status(session, "running")

        result = await run_script(
            session.script, api, session.args, timeout=self.timeout
        )

        # Map the script outcome to a persisted status (§A.4).
        #
        # State semantics:
        #   * "interrupted" = system/environment stopped it and it is RESUMABLE:
        #     overall-timeout, sandbox CancelledError caught by run_script, or
        #     crash-recovery (recover()). run_script returns cancelled=True for
        #     both timeout and cancellation, so a NATURAL run that hits these
        #     lands here as interrupted (UNSETTLED, not terminal).
        #   * "cancelled"   = a USER explicitly stopped it (control("cancel")).
        #     That status is written by control(), not here, and is TERMINAL.
        #   * "awaiting_intervention" = a node() failed (NodeError →
        #     failed_node_id set). PERSISTENT but NON-terminal: the workflow
        #     stays discoverable so the user can retry/skip/cancel the node.
        #   * "failed" = a NON-intervenable failure (script syntax/runtime error,
        #     max_nodes cap). No node to retry → TERMINAL.
        if result.ok:
            status = "completed"
        elif result.cancelled:
            status = "interrupted"
        elif result.failed_node_id is not None:
            status = "awaiting_intervention"
        else:
            status = "failed"

        session.status = status
        self._persist_status(session, status)
        self.storage.write_result(wf_id, result.result if result.ok else None)

        await self.publisher.publish(
            chat_id,
            ev.make_status(wf_id, status, self._progress(session)),
        )

        # Key-event callback (decision 14). Completion, terminal failure, and
        # awaiting_intervention all wake the Leader / notify the UI (the
        # InterventionBar needs to know a decision is pending). The engine must
        # never raise out of the callback.
        if status in ("completed", "failed", "awaiting_intervention"):
            if status == "completed":
                summary = "workflow completed"
            elif status == "awaiting_intervention":
                summary = self._failure_summary(result)
            else:
                summary = self._failure_summary(result)
            self._fire_key_event(wf_id, chat_id, status, summary)

    def _guard_node(self, node_fn: Callable) -> Callable:
        """Wrap the api's ``node`` with a ``max_nodes`` counting guard.

        The api's internal counter is not exposed, so the engine counts calls
        here. Once the (1-based) count exceeds ``max_nodes`` we raise
        :class:`WorkflowLimitError`, which ``run_script`` captures into a failed
        ``ScriptResult`` — bounding an otherwise unbounded fan-out.
        """
        count = 0

        def guarded(*a: Any, **kw: Any) -> Any:
            nonlocal count
            count += 1
            if count > self.max_nodes:
                raise WorkflowLimitError(
                    f"workflow exceeded max_nodes={self.max_nodes}"
                )
            return node_fn(*a, **kw)

        return guarded

    # --- persistence helpers ---------------------------------------------- #

    def _progress(self, session: WorkflowSession) -> dict:
        rs = session.run_state
        return dict(rs.progress) if rs is not None else {}

    def _current_phase(self, session: WorkflowSession) -> str:
        rs = session.run_state
        return rs.current_phase if rs is not None else ""

    def _persist_status(self, session: WorkflowSession, status: str) -> None:
        wf_id = session.workflow_id
        meta = self.storage.read_meta(wf_id)
        meta.status = status
        # §A.4: every status transition monotonically bumps revision (the C4 CAS
        # baseline). One read-modify-write per transition — no double-counting.
        meta.revision += 1
        self.storage.write_meta(meta)
        rs = session.run_state
        self.storage.write_state(
            WorkflowState(
                workflow_id=wf_id,
                status=status,
                current_phase=self._current_phase(session),
                progress=self._progress(session),
                cascade_epoch=rs.cascade_epoch if rs is not None else 0,
                attempt_counts=dict(rs.attempt_counts) if rs is not None else {},
            )
        )

    @staticmethod
    def _failure_summary(result: Any) -> str:
        return f"workflow failed: {result.error}" if result.error else "workflow failed"

    def _fire_key_event(
        self, workflow_id: str, chat_id: str, kind: str, summary: str
    ) -> None:
        if self._on_key_event is None:
            return
        try:
            self._on_key_event(workflow_id, chat_id, kind, summary)
        except Exception:  # noqa: BLE001 — callback must never crash the engine
            logger.exception("on_key_event callback raised for %s", workflow_id)

    # --- status ----------------------------------------------------------- #

    def get_blueprint(self, workflow_id: str, chat_id: str) -> dict:
        """Return the §A.8 declaration skeleton for a workflow (C7 endpoint).

        Ownership-checked: ``chat_id`` is the ``expected_chat_id`` scope gate, so
        a cross-chat read raises ``PermissionError`` (→ 403 at the endpoint) and
        an unknown ``workflow_id`` raises ``FileNotFoundError`` (→ 404). Returns
        the pure-literal ``blueprint`` slot list, the ``preview`` mode ("full" /
        "none"), the distinct ``phases`` (in first-seen order), the workflow
        ``goal`` and the CAS ``revision`` — everything the UI needs to pre-render
        the trace skeleton before any node event arrives. No node output is read.
        """
        meta = self._check_owner(workflow_id, chat_id)
        phases: list[str] = []
        for slot in meta.blueprint:
            ph = slot.get("phase") if isinstance(slot, dict) else None
            if ph and ph not in phases:
                phases.append(ph)
        return {
            "workflow_id": workflow_id,
            "goal": meta.goal,
            "preview": meta.preview,
            "blueprint": meta.blueprint,
            "phases": phases,
            "revision": meta.revision,
            "status": meta.status,
        }

    async def status(
        self, workflow_id: str | None = None, chat_id: str | None = None
    ) -> dict:
        """List workflows for ``chat_id``, or a trace summary for one workflow.

        With no ``workflow_id``: a one-line overview per workflow owned by
        ``chat_id``. With a ``workflow_id``: an ownership-checked trace summary
        (status, phase, progress, per-node {node_id, label, status}, error
        summary, artifact dir) — node output contents are NEVER inlined.
        """
        if workflow_id is None:
            return self.list_workflows(chat_id)

        meta = self._check_owner(workflow_id, chat_id)
        state = self.storage.read_state(workflow_id)
        journal = self._journal_for(workflow_id)
        nodes = []
        errors = []
        for nid in sorted(journal.entries):
            entry = journal.entries[nid]
            nodes.append(
                {
                    "node_id": nid,
                    "label": entry.label,
                    "status": entry.status,
                    # §A.2: per-node reconcile fields. ``slot_id`` is None for an
                    # undeclared node; ``attempt`` is the retry count of this
                    # node's current (cached) entry.
                    "slot_id": entry.slot_id,
                    "attempt": entry.attempt,
                }
            )
            if entry.status == "failed":
                errors.append({"node_id": nid, "label": entry.label})
        return {
            "workflow_id": workflow_id,
            "status": meta.status,
            "phase": state.current_phase,
            "progress": state.progress,
            "nodes": nodes,
            "errors": errors,
            "artifact_dir": str(self.storage.workflow_dir(workflow_id)),
            # §A.6 reconcile: current cascade epoch + the durable invalidation
            # history (so a reconnecting UI can purge superseded downstream
            # nodes from its in-memory trace).
            "cascade_epoch": state.cascade_epoch,
            "invalidations": self.storage.read_invalidations(workflow_id),
        }

    def list_workflows(self, chat_id: str | None) -> dict:
        """List the *unsettled* workflows scoped to ``chat_id`` (§A.3).

        Scope + status contract:
          * **Scope** — only workflows whose persisted ``meta.chat_id`` equals
            the ``chat_id`` selector are returned. This blocks cross-chat
            listing: the same owner's workflows under a *different* chat are
            never surfaced here.
          * **Unsettled only** — only ``UNSETTLED_STATUSES`` (running, paused,
            interrupted, awaiting_intervention) are returned. Settled workflows
            (completed/failed/cancelled) are omitted — they are not live or
            actionable, so the multi-workflow UI does not show them.

        Discovery is via ``scan_workflows`` over the on-disk ``meta.json`` files,
        NOT the in-memory ``_by_chat`` map. A fresh engine process (empty memory)
        pointed at the same ``base_dir``, or a workflow whose ``workflow.created``
        event was lost, is still discoverable — the persisted meta is the
        authority.

        Each item carries ``chat_id`` (so the UI can re-verify scope), the
        live ``status``, ``goal``, ``progress`` (from state), plus the §A.8
        ``preview`` mode and the §A.4 ``revision`` the UI needs for the trace
        skeleton and CAS baseline.

        Authorization note (C5 vs C7): the engine layer enforces *scope* by
        ``meta.chat_id`` filtering. The "is the calling session actually a
        member of this ``chat_id``" check (→ 403) is enforced at the C7 endpoint
        layer, which derives the real chat_id from the session/NATS context and
        compares it to this selector before calling here. ``chat_id`` here is a
        scope selector, not a credential.
        """
        out = []
        for meta in scan_workflows(self.base_dir):
            if chat_id is not None and meta.chat_id != chat_id:
                continue
            if meta.status not in UNSETTLED_STATUSES:
                continue
            try:
                state = self.storage.read_state(meta.workflow_id)
                progress = state.progress
            except (OSError, ValueError):
                progress = {}
            out.append(
                {
                    "workflow_id": meta.workflow_id,
                    # §A.3: every item carries chat_id so the UI can re-verify
                    # the scope it asked for.
                    "chat_id": meta.chat_id,
                    "status": meta.status,
                    "goal": meta.goal,
                    "progress": progress,
                    # §A.8 declaration preview mode; §A.4 CAS baseline.
                    "preview": meta.preview,
                    "revision": meta.revision,
                }
            )
        return {"workflows": out}

    # --- get_output ------------------------------------------------------- #

    async def get_output(
        self,
        workflow_id: str,
        chat_id: str,
        node_id: int | None = None,
        summary_only: bool = True,
    ) -> dict:
        """Return the final result, or one node's output (path + preview).

        Ownership-checked. When ``summary_only`` (default) only a path and a
        truncated preview are returned — full content is never dumped.
        """
        self._check_owner(workflow_id, chat_id)

        if node_id is None:
            path = self.storage.result_path(workflow_id)
            return self._file_summary(path, summary_only)

        path = self.storage.node_context_path(workflow_id, node_id)
        return self._file_summary(path, summary_only, node_id=node_id)

    def _file_summary(
        self, path: Path, summary_only: bool, *, node_id: int | None = None
    ) -> dict:
        out: dict[str, Any] = {"path": str(path)}
        if node_id is not None:
            out["node_id"] = node_id
        if not path.is_file():
            out["preview"] = None
            out["exists"] = False
            return out
        out["exists"] = True
        text = path.read_text(encoding="utf-8")
        if summary_only:
            out["preview"] = text[:_PREVIEW_CHARS]
            out["truncated"] = len(text) > _PREVIEW_CHARS
        else:
            out["content"] = text
        return out

    # --- resume ----------------------------------------------------------- #

    async def resume(
        self,
        workflow_id: str,
        chat_id: str,
        new_script: str | None = None,
    ) -> dict:
        """Re-run a workflow with the SAME journal (prefix-cache reuse).

        ``meta.created_at`` is intentionally unchanged on a re-run (resume is the
        same workflow, not a new one), so no timestamp is taken here.

        Ownership-checked. An optional ``new_script`` (validated) replaces the
        stored script. Returns resume stats: ``cached_nodes`` (prior-run entries
        reused this run) and ``will_rerun`` (entries re-recorded this run).

        Counting approach (honest approximation): before the re-run we snapshot
        the prior journal's node_ids; ``journal.recorded_node_ids()`` after the
        run tells us exactly which ids were re-executed. ``cached_nodes`` = prior
        ids NOT re-recorded; ``will_rerun`` = the ids re-recorded (drawn from the
        prior set).
        """
        meta = self._check_owner(workflow_id, chat_id)

        if new_script is not None:
            validation = validate_script(new_script)
            if not validation.ok:
                return {"error": validation.error, "line": validation.line}
            self.storage.write_script(workflow_id, new_script)
            script = new_script
        else:
            script = self.storage.read_script(workflow_id)

        phases = extract_phases(script)
        # Snapshot the prior journal's recorded node_ids BEFORE the re-run.
        prior_ids = set(self._journal_for(workflow_id).entries)

        session = self.sessions.get(workflow_id)
        if session is None:
            session = WorkflowSession(
                workflow_id=workflow_id,
                chat_id=chat_id,
                script=script,
                args=self._read_args(workflow_id),
                journal=self._journal_for(workflow_id),
            )
            self._index(session)
        else:
            session.script = script

        self._launch(session, goal=meta.goal, phases=phases)
        await self._await_task(session)

        # ``_run`` rebuilt session.journal for this run; its recorded-this-run
        # set tells us exactly which ids were re-executed.
        recorded = set(session.journal.recorded_node_ids())
        rerun = sorted(prior_ids & recorded)
        cached = sorted(prior_ids - recorded)
        out = {
            "resumed_from": workflow_id,
            "cached_nodes": len(cached),
            "will_rerun": rerun,
        }
        await self.publisher.publish(
            chat_id, ev.make_resumed(workflow_id, len(cached), rerun)
        )
        return out

    # --- control ---------------------------------------------------------- #

    async def control(
        self,
        workflow_id: str,
        chat_id: str,
        action: str,
        node_id: int | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        """Pause / resume / cancel / skip_node / retry_node a workflow (§A.3).

        Serialized, ownership-checked, and revision-CAS guarded. The whole body
        runs under a per-workflow lock so concurrent UI/Leader control() calls on
        the same workflow never interleave.

        Ordering inside the lock (auth BEFORE CAS so a stale revision never leaks
        from an unauthorized caller):

          1. ``_check_owner`` — ``chat_id`` is the ``expected_chat_id`` scope
             gate; a mismatch raises ``PermissionError`` (never returns a dict).
          2. CAS on ``meta.revision``. ``expected_revision`` semantics:
             * an int that does NOT equal the current authoritative revision →
               the action is NOT executed; returns
               ``{accepted: False, status, revision}`` echoing the current
               authoritative state (no raise — the caller refreshes and retries).
             * an int that equals the current revision → execute.
             * ``None`` (the Leader path) → "accept current": execute against
               whatever the current revision is. The Leader drives in natural
               language and does not track revisions, so it is the last writer;
               it is still serialized by the lock, just not CAS-rejected.
          3. Execute the action. ``skip_node`` / ``retry_node`` mutate the
             journal then re-run; the journal's prefix cache makes the rest a
             no-op.

        Returns the unified envelope ``{workflow_id, accepted, status, revision}``
        where ``status``/``revision`` are the POST-execution authoritative values
        (so the next UI CAS uses the fresh revision). Resume-like actions merge
        their stats (``cached_nodes`` / ``will_rerun`` / ``resumed_from``) as
        extra fields without displacing the envelope keys.

        NOTE on reentrancy: the lock is held ONLY here; ``resume()`` and
        ``_cascade_invalidate()`` called below do NOT acquire it, so the
        non-reentrant ``asyncio.Lock`` cannot deadlock.
        """
        async with self._lock_for(workflow_id):
            # 1. auth (scope) — raises PermissionError BEFORE any CAS/revision
            #    read, so an unauthorized caller learns nothing about revision.
            meta = self._check_owner(workflow_id, chat_id)

            # 2. CAS on revision (only when an explicit expectation is given).
            current_revision = meta.revision
            if (
                expected_revision is not None
                and expected_revision != current_revision
            ):
                # Stale → do NOT execute; echo the authoritative state.
                return {
                    "workflow_id": workflow_id,
                    "accepted": False,
                    "status": meta.status,
                    "revision": current_revision,
                }

            # 3. execute the action; ``extra`` collects resume-like stats.
            extra = await self._dispatch_control(
                workflow_id, chat_id, action, node_id
            )
            if "error" in extra:
                # Validation errors (unknown action / missing node_id) are
                # surfaced as-is; they did not transition state.
                return extra

            # Re-read meta for the POST-execution authoritative revision/status
            # so the UI's next CAS uses the fresh value.
            post = self._read_meta(workflow_id)
            envelope = {
                "workflow_id": workflow_id,
                "accepted": True,
                "status": post.status,
                "revision": post.revision,
            }
            # Merge stats without clobbering the envelope keys.
            for k, v in extra.items():
                envelope.setdefault(k, v)
            return envelope

    async def _dispatch_control(
        self,
        workflow_id: str,
        chat_id: str,
        action: str,
        node_id: int | None,
    ) -> dict:
        """Execute a single control action (called INSIDE the control lock).

        Returns the action's native result dict (resume-like stats, a terminal
        ``{status}``, or an ``{error}`` for an invalid action). The caller wraps
        it in the unified envelope. MUST NOT acquire the control lock (it is
        already held by ``control``); ``resume`` / ``_cascade_invalidate`` it
        calls are lock-free by design.
        """
        if action == "pause":
            await self._cancel_task(workflow_id)
            # If the run already settled terminally (completed/failed/cancelled/
            # interrupted) before/while we cancelled, do NOT clobber it.
            terminal = self._terminal_status(workflow_id)
            if terminal is not None:
                return {"status": terminal}
            self._mark(workflow_id, "paused")
            return {"status": "paused"}

        if action == "cancel":
            await self._cancel_task(workflow_id)
            # Race guard (I-2): a cancel arriving AFTER natural completion must
            # not flip a terminal status. run_script catches CancelledError and
            # returns cancelled=True, so a truly-cancelled run lands as
            # "interrupted" via _run; we promote that (and only that) to the
            # user-terminal "cancelled". An already-completed/failed run is left
            # untouched.
            terminal = self._terminal_status(workflow_id)
            if terminal in ("completed", "failed", "cancelled"):
                return {"status": terminal}
            self._mark(workflow_id, "cancelled")
            return {"status": "cancelled"}

        if action == "resume":
            return await self.resume(workflow_id, chat_id)

        if action == "skip_node":
            if node_id is None:
                return {"error": "skip_node requires node_id"}
            self._journal_for(workflow_id).skip(int(node_id))
            return await self.resume(workflow_id, chat_id)

        if action == "retry_node":
            if node_id is None:
                return {"error": "retry_node requires node_id"}
            await self._cascade_invalidate(workflow_id, chat_id, int(node_id))
            self._journal_for(workflow_id).invalidate(int(node_id))
            return await self.resume(workflow_id, chat_id)

        return {"error": f"unknown action: {action!r}"}

    async def _cascade_invalidate(
        self, workflow_id: str, chat_id: str, node_id: int
    ) -> None:
        """Bump the cascade epoch, persist it, and emit ``slots_invalidated``.

        Runs BEFORE ``journal.invalidate`` (which DELETES the downstream entries)
        so we can read the doomed node_ids + their slot_ids first. Effects (§A.6):

        * ``WorkflowState.cascade_epoch`` += 1 and each invalidated node's
          ``attempt_counts`` += 1, persisted to ``state.json`` BEFORE the delete
          so a crash between here and resume still reflects the new epoch.
        * a ``{cascade_epoch, slot_ids, node_ids}`` record appended to the
          ``invalidations.jsonl`` sidecar (durable; replayable on reconnect).
        * a ``workflow.slots_invalidated`` NATS event published.

        The journal's delete semantics already guarantee the *current* state has
        no superseded downstream nodes; this gives a reconnecting UI the history
        it needs to purge them from its in-memory trace.
        """
        journal = self._journal_for(workflow_id)
        doomed_ids = sorted(k for k in journal.entries if k >= node_id)
        slot_ids = sorted(
            {
                journal.entries[k].slot_id
                for k in doomed_ids
                if journal.entries[k].slot_id
            }
        )

        try:
            state = self.storage.read_state(workflow_id)
        except (OSError, ValueError):
            state = WorkflowState(workflow_id=workflow_id, status="interrupted")
        new_epoch = state.cascade_epoch + 1
        state.cascade_epoch = new_epoch
        # Bump the retry counter for every node about to be re-run, so the next
        # run's events carry attempt >= 1 for them (the journal entry is deleted,
        # so the count must live in state).
        for k in doomed_ids:
            key = str(k)
            state.attempt_counts[key] = int(state.attempt_counts.get(key, 0)) + 1
        self.storage.write_state(state)

        record = {
            "cascade_epoch": new_epoch,
            "slot_ids": slot_ids,
            "node_ids": doomed_ids,
        }
        self.storage.append_invalidation(workflow_id, record)
        await self.publisher.publish(
            chat_id,
            ev.make_slots_invalidated(
                workflow_id, new_epoch, slot_ids, doomed_ids
            ),
        )

    async def _cancel_task(self, workflow_id: str) -> None:
        session = self.sessions.get(workflow_id)
        if session is None or session.task is None:
            return
        task = session.task
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        session.task = None

    def _terminal_status(self, workflow_id: str) -> str | None:
        """Return the persisted status iff it is terminal, else ``None``.

        Reads ``meta.json`` (the persisted authority that ``_run`` updates on
        settle) so the control() race guard sees whatever the background task
        committed before/while the cancel resolved.
        """
        try:
            status = self.storage.read_meta(workflow_id).status
        except (OSError, ValueError):
            return None
        return status if status in TERMINAL_STATUSES else None

    def _mark(self, workflow_id: str, status: str) -> None:
        session = self.sessions.get(workflow_id)
        if session is not None:
            session.status = status
        meta = self.storage.read_meta(workflow_id)
        meta.status = status
        meta.revision += 1  # §A.4 monotonic transition counter.
        self.storage.write_meta(meta)
        try:
            state = self.storage.read_state(workflow_id)
        except (OSError, ValueError):
            state = WorkflowState(workflow_id=workflow_id, status=status)
        state.status = status
        self.storage.write_state(state)

    # --- recovery --------------------------------------------------------- #

    def recover(self) -> list:
        """Restart recovery (decision 14.3): mark interrupted, do NOT re-run.

        Scans persisted metas; any workflow whose status was ``running`` (i.e.
        a live task that the crash/restart killed) is re-marked ``interrupted``
        in meta + state. Returns the list of interrupted workflow_ids. The room
        decides whether to resume them; the engine never auto-reruns.
        """
        interrupted: list[str] = []
        for meta in scan_workflows(self.base_dir):
            if meta.status != "running":
                continue
            if meta.workflow_id in self.sessions:
                continue  # a live session in THIS process — not a crash
            meta.status = "interrupted"
            meta.revision += 1  # §A.4: recover() is a running→interrupted transition.
            self.storage.write_meta(meta)
            try:
                state = self.storage.read_state(meta.workflow_id)
                state.status = "interrupted"
            except (OSError, ValueError):
                state = WorkflowState(
                    workflow_id=meta.workflow_id, status="interrupted"
                )
            self.storage.write_state(state)
            interrupted.append(meta.workflow_id)
        return interrupted

    # --- misc helpers ----------------------------------------------------- #

    def _journal_for(self, workflow_id: str) -> Journal:
        """Return the live session journal, or load it fresh from disk."""
        session = self.sessions.get(workflow_id)
        if session is not None:
            return session.journal
        return Journal(self.storage.workflow_dir(workflow_id) / "journal.jsonl")

    def _read_args(self, workflow_id: str) -> Any:
        path = self.storage.context_dir(workflow_id) / "inputs.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    async def _await_task(self, session: WorkflowSession) -> None:
        if session.task is not None:
            try:
                await session.task
            except asyncio.CancelledError:
                pass
