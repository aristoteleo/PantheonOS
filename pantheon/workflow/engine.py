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
return value, so the engine writes ``{workflow_dir}/result.json`` as
``{"result": <value>}`` (json ``default=str``). ``get_output`` reads it back.
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
]

#: Defaults for the resource caps (decision §4.1).
DEFAULT_MAX_NODES = 200
DEFAULT_TIMEOUT = 1800.0  # seconds (overall script wall-clock)
DEFAULT_CONCURRENCY = 8

#: How many characters of a result/preview to surface when ``summary_only``.
_PREVIEW_CHARS = 500


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
        get_output, resume, control). Raises ``PermissionError`` on mismatch.
        """
        meta = self._read_meta(workflow_id)
        if meta.chat_id != chat_id:
            raise PermissionError(
                f"workflow {workflow_id!r} is not owned by chat {chat_id!r}"
            )
        return meta

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
        base_dir: Path | None = None,  # accepted for API symmetry; unused
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
        """Start ``_run`` as a background task and store it on the session."""
        session.status = "running"
        session.task = asyncio.ensure_future(
            self._run(session, goal=goal, phases=phases)
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
        api = make_api(
            session.journal,
            self.runner,
            self.publisher,
            chat_id,
            ctx,
            concurrency=self.concurrency,
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

        # Map the script outcome to a terminal status.
        if result.ok:
            status = "completed"
        elif result.cancelled:
            status = "interrupted"
        else:
            status = "failed"

        session.status = status
        self._persist_status(session, status)
        self._write_result(wf_id, result.result if result.ok else None)

        await self.publisher.publish(
            chat_id,
            ev.make_status(wf_id, status, self._progress(session)),
        )

        # Key-event callback (decision 14). Phase-1: completion + failure are
        # the trigger kinds. The engine must never raise out of the callback.
        if status in ("completed", "failed"):
            summary = (
                "workflow completed"
                if status == "completed"
                else self._failure_summary(result)
            )
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
        self.storage.write_meta(meta)
        self.storage.write_state(
            WorkflowState(
                workflow_id=wf_id,
                status=status,
                current_phase=self._current_phase(session),
                progress=self._progress(session),
            )
        )

    def _write_result(self, workflow_id: str, value: Any) -> None:
        path = self.storage.workflow_dir(workflow_id) / "result.json"
        try:
            text = json.dumps({"result": value}, default=str)
        except (TypeError, ValueError):
            text = json.dumps({"result": str(value)})
        path.write_text(text, encoding="utf-8")

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
            return self._list_for_chat(chat_id)

        meta = self._check_owner(workflow_id, chat_id)
        state = self.storage.read_state(workflow_id)
        journal = self._journal_for(workflow_id)
        nodes = []
        errors = []
        for nid in sorted(journal.entries):
            entry = journal.entries[nid]
            nodes.append(
                {"node_id": nid, "label": entry.label, "status": entry.status}
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
        }

    def _list_for_chat(self, chat_id: str | None) -> dict:
        out = []
        for meta in scan_workflows(self.base_dir):
            if chat_id is not None and meta.chat_id != chat_id:
                continue
            try:
                state = self.storage.read_state(meta.workflow_id)
                progress = state.progress
            except (OSError, ValueError):
                progress = {}
            out.append(
                {
                    "workflow_id": meta.workflow_id,
                    "status": meta.status,
                    "goal": meta.goal,
                    "progress": progress,
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
            path = self.storage.workflow_dir(workflow_id) / "result.json"
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
        *,
        created_at: str | None = None,
    ) -> dict:
        """Re-run a workflow with the SAME journal (prefix-cache reuse).

        Ownership-checked. An optional ``new_script`` (validated) replaces the
        stored script. Returns resume stats: ``cached_nodes`` (prior-run entries
        reused this run) and ``will_rerun`` (entries re-recorded this run).

        Counting approach (honest approximation, no cross-module hook): before
        the re-run we snapshot the prior journal's node_ids; the journal's
        ``_recorded_this_run`` set after the run tells us exactly which ids were
        re-executed. ``cached_nodes`` = prior ids NOT re-recorded; ``will_rerun``
        = the ids that were re-recorded (drawn from the prior set).
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

        # ``_run`` rebuilt session.journal for this run; its _recorded_this_run
        # tells us exactly which ids were re-executed.
        recorded = set(session.journal._recorded_this_run)
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
    ) -> dict:
        """Pause / resume / cancel / skip_node / retry_node a workflow.

        Ownership-checked. ``skip_node`` and ``retry_node`` mutate the journal
        (skip / invalidate) then re-run; the journal's prefix cache makes the
        rest of the work a no-op.
        """
        self._check_owner(workflow_id, chat_id)

        if action == "pause":
            await self._cancel_task(workflow_id)
            self._mark(workflow_id, "paused")
            return {"workflow_id": workflow_id, "status": "paused"}

        if action == "cancel":
            await self._cancel_task(workflow_id)
            self._mark(workflow_id, "cancelled")
            return {"workflow_id": workflow_id, "status": "cancelled"}

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
            self._journal_for(workflow_id).invalidate(int(node_id))
            return await self.resume(workflow_id, chat_id)

        return {"error": f"unknown action: {action!r}"}

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

    def _mark(self, workflow_id: str, status: str) -> None:
        session = self.sessions.get(workflow_id)
        if session is not None:
            session.status = status
        meta = self.storage.read_meta(workflow_id)
        meta.status = status
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
