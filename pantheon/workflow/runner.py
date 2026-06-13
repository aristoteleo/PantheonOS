"""NodeRunner abstraction + InProcessRunner (Task 5).

A ``NodeRunner`` executes a single :class:`NodeCall` and returns a
:class:`NodeResult`. ``InProcessRunner`` builds a node agent from a template
(layer ①+② via ``compose_node_prompt``) and runs it in-process.

Design decisions encoded here:

- **Decision 11 (no bubble):** node agents are run WITHOUT
  ``process_step_message`` / ``process_chunk`` — their output is DATA written
  to the node's context file (and traced to node memory), not conversational
  messages that bubble up to the Leader. The API layer turns these results
  into NATS events; this runner never does.
- **Codex Finding 3 / node_id-only filenames:** the agent name and all
  filesystem paths derive from ``node_id`` only — never ``label`` (which is
  display-only and may contain ``/`` or ``..``).
- **Failed NodeResults never raise:** every failure surface (exception,
  timeout, schema-validation miss) is captured into a
  ``NodeResult(status="failed", error=...)``. The runner itself does not
  propagate exceptions; the engine/API layer decides what to do.

Schema-retry (spec §6 decision 1)
---------------------------------
Pantheon's ``agent.run(response_format=...)`` extracts the parsed structured
object from ``last_message["parsed"]`` (see ``agent.py`` ~line 2693). On
output that fails to parse/validate, two surfaces are possible and BOTH are
handled here:

1. the run *raises* (provider/validation error), or
2. the run *returns* with ``content is None`` (no parsed object).

For a schema node, either is treated as an invalid attempt and the node is
re-run ONCE with an error-feedback user message. After the retry still fails
we return ``status="failed"``. (DONE_WITH_CONCERNS note: the exact provider
failure surface for malformed structured output was not 100% pinned down from
a static read; catching both a raise and a ``None`` content is the
conservative, correct superset.)

Testability seam
----------------
``InProcessRunner.__init__`` accepts an ``agent_factory`` (defaults to
:class:`pantheon.agent.Agent`). Tests inject a fake factory whose ``run()``
returns a canned response, keeping unit tests fast and LLM-free.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pantheon.agent import Agent
from pantheon.internal.memory.memory import Memory

from .models import NodeCall, NodeResult
from .schema_convert import schema_to_field_type
from .storage import WorkflowStorage
from .templates import compose_node_prompt, get_template

logger = logging.getLogger(__name__)

#: How many times a schema node is retried after an invalid first attempt.
SCHEMA_RETRY_LIMIT = 1


@dataclass
class NodeRunContext:
    """Per-run context the API/engine layer passes into a :class:`NodeRunner`.

    Carries the workflow identity, storage (for path helpers), the chat
    working directory (exposed to the node agent's tools), and the
    ``execution_context_id`` used for event attribution.
    """

    workflow_id: str
    storage: WorkflowStorage
    chat_workdir: str
    execution_context_id: str | None = None


class NodeRunner(ABC):
    """Executes a single node and returns its result."""

    @abstractmethod
    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        ...


class InProcessRunner(NodeRunner):
    """Run nodes as in-process Pantheon agents built from a template."""

    def __init__(
        self,
        agent_factory: Callable[..., Agent] = Agent,
        *,
        schema_retry_limit: int = SCHEMA_RETRY_LIMIT,
    ) -> None:
        self._agent_factory = agent_factory
        self._schema_retry_limit = schema_retry_limit

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        node_id = node_call.node_id
        try:
            return await self._run_inner(node_call, ctx)
        except Exception as exc:  # noqa: BLE001 — runner never propagates.
            logger.exception("node %s failed", node_id)
            return NodeResult(
                node_id=node_id, status="failed", error=_summarize(exc)
            )

    # --- internals --------------------------------------------------------- #

    @staticmethod
    def _load_inputs(
        inputs: tuple[str, ...], ctx: NodeRunContext
    ) -> list[tuple[str, Any]]:
        """Load declared input files' contents for inlining into the prompt.

        Each entry of ``inputs`` is a relative segment under the workflow's
        ``context/`` dir (typically an upstream node's ``n{id}.json``). The
        ``{"kind","result"}`` envelope is unwrapped so the node sees the actual
        upstream value, not the wrapper. Path resolution is confined via
        ``safe_node_path``; a missing or unsafe input is skipped (it already
        hashed as "" for the cache key) rather than failing the node.

        Returns an ordered list of ``(name, value)`` preserving declaration
        order — order matters because the prompt presents them positionally.
        """
        from .storage import safe_node_path

        out: list[tuple[str, Any]] = []
        context_dir = ctx.storage.context_dir(ctx.workflow_id)
        for rel in inputs:
            name = str(rel)
            try:
                parts = [p for p in name.split("/") if p]
                path = safe_node_path(context_dir, *parts)
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # missing/unsafe -> skip (consistent with hash="")
            value = payload.get("result") if isinstance(payload, dict) else payload
            out.append((name, value))
        return out

    async def _run_inner(
        self, node_call: NodeCall, ctx: NodeRunContext
    ) -> NodeResult:
        node_id = node_call.node_id
        storage = ctx.storage
        wf_id = ctx.workflow_id

        template = get_template(node_call.template)

        # Phase 1: toolset names are declarative only; resolving them to
        # ToolSet instances is Phase 2. Warn and proceed tool-less for
        # forward-compat rather than hard-failing.
        if template.toolsets:
            logger.warning(
                "template %r declares toolsets %r but Phase 1 cannot resolve "
                "them; running node %s with no tools",
                template.name,
                template.toolsets,
                node_id,
            )

        # Paths derive from node_id ONLY (never label).
        context_path = storage.node_context_path(wf_id, node_id)
        memory_path = storage.node_memory_path(wf_id, node_id)
        result_ref = f"context/n{node_id}.json"

        # Phase 1 nodes run tool-less (toolset injection is Phase 2), so a node
        # cannot READ its declared input files itself. The engine therefore
        # INLINES the declared inputs' contents into the prompt — deterministic,
        # no tool round-trip. ``inputs=`` already drives cache invalidation via
        # content hashing; here it also delivers the actual data downstream.
        inputs_payload = self._load_inputs(node_call.inputs, ctx)

        system_prompt, user_message = compose_node_prompt(
            template,
            node_call.instruction,
            schema=node_call.schema,
            output_path=result_ref,
            inputs=inputs_payload,
        )

        model = node_call.model or template.model

        # NOTE: we deliberately do NOT pass ``response_format`` to the Agent.
        # The Agent's native structured-output path is unreliable across
        # OpenAI-compatible proxies (the Responses-API path serializes the
        # pydantic class raw; the chat-completions path double-wraps), so a
        # schema node instead asks for JSON *in the prompt* (compose_node_prompt
        # embeds the concrete schema) and we validate the returned TEXT here.
        # ``validator`` is the pydantic model built from the JSON-Schema dict;
        # it is the source of truth for accept/retry. ``None`` -> plain text node.
        validator = (
            schema_to_field_type(node_call.schema)
            if node_call.schema is not None
            else None
        )

        memory = Memory(
            name=f"wf-{wf_id}-n{node_id}",
            file_path=str(memory_path),
        )
        agent = self._agent_factory(
            name=f"wf-{wf_id}-n{node_id}",
            instructions=system_prompt,
            model=model,
            tools=None,
            response_format=None,
            memory=memory,
        )

        context_variables = {
            "workdir": ctx.chat_workdir,
            "execution_context_id": ctx.execution_context_id,
        }

        max_attempts = (
            1 + self._schema_retry_limit if node_call.schema is not None else 1
        )
        last_err: str | None = None

        for attempt in range(max_attempts):
            msg = user_message
            if attempt > 0:
                msg = (
                    f"{user_message}\n\n---\nYour previous output was invalid: "
                    f"{last_err}. Return ONLY valid JSON conforming to the "
                    "schema, with nothing else."
                )
            try:
                content = await self._invoke(
                    agent, msg, node_call, context_variables, ctx
                )
            except Exception as exc:  # noqa: BLE001
                if node_call.schema is not None and attempt < max_attempts - 1:
                    last_err = _summarize(exc)
                    continue
                raise

            # Schema node: the agent returned TEXT (we never used the Agent's
            # native structured output — see the note above). Parse + validate
            # it here against the pydantic model. On failure, retry once with
            # feedback, else fail the node.
            if node_call.schema is not None:
                parsed, err = _parse_and_validate(content, validator)
                if err is not None:
                    last_err = err
                    if attempt < max_attempts - 1:
                        continue
                    return NodeResult(
                        node_id=node_id, status="failed", error=last_err
                    )
                content = parsed  # plain dict/list/scalar per the contract

            # Success — persist and return. A persistence failure here is NOT
            # an agent failure: the run succeeded, we just couldn't record it.
            # Surface it with a distinguishable error so the journal/API layer
            # can tell "agent failed" from "we failed to record a success".
            try:
                self._write_context(context_path, content, node_call.schema)
            except Exception as exc:  # noqa: BLE001
                logger.exception("node %s persist failed", node_id)
                return NodeResult(
                    node_id=node_id,
                    status="failed",
                    error=f"persist failed: {_summarize(exc)}",
                )
            return NodeResult(
                node_id=node_id,
                status="completed",
                result=content,
                result_ref=result_ref,
                # TODO: token_cost is a best-effort stub — ResponseDetails
                # carries no usage field yet; wire real usage when available.
                token_cost=0,
            )

        # Exhausted retries (schema node only reaches here on repeated invalid).
        return NodeResult(
            node_id=node_id,
            status="failed",
            error=last_err or "schema validation failed",
        )

    async def _invoke(
        self,
        agent: Agent,
        msg: str,
        node_call: NodeCall,
        context_variables: dict,
        ctx: NodeRunContext,
    ) -> Any:
        """Run the agent once; honor an optional timeout. Returns ``.content``.

        We never pass ``response_format`` (the Agent's native structured-output
        path is unreliable on OpenAI-compatible proxies). Schema enforcement is
        prompt-driven and validated by the caller against the JSON-Schema model.

        Deliberately omits ``process_step_message`` / ``process_chunk``
        (decision 11): node output must NOT bubble to the Leader.
        """
        coro = agent.run(
            msg,
            response_format=None,
            memory=None,
            use_memory=False,
            update_memory=True,
            context_variables=context_variables,
            execution_context_id=ctx.execution_context_id,
            allow_transfer=False,
        )
        if node_call.timeout is not None:
            try:
                response = await asyncio.wait_for(coro, timeout=node_call.timeout)
            except (asyncio.TimeoutError, TimeoutError):
                raise TimeoutError(
                    f"timeout after {node_call.timeout}s"
                ) from None
        else:
            response = await coro
        return getattr(response, "content", None)

    @staticmethod
    def _write_context(path, content: Any, schema: dict | None) -> None:
        """Serialize the node result to its context file as JSON.

        Writes a SELF-DESCRIBING envelope so a reader of ``result_ref`` can
        interpret the file without out-of-band knowledge of the node's schema::

            {"kind": "schema" | "text", "result": <object-or-text>}

        ``kind="schema"`` carries the validated structured object; ``kind="text"``
        carries the raw text content. Falls back to a ``text`` envelope with the
        ``repr`` of the content if it is not JSON-serializable.
        """
        kind = "schema" if schema is not None else "text"
        payload = {"kind": kind, "result": content}
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except TypeError:
            text = json.dumps(
                {"kind": "text", "result": str(content)},
                ensure_ascii=False,
                indent=2,
            )
        path.write_text(text, encoding="utf-8")


def _summarize(exc: BaseException, limit: int = 500) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence (```json … ```), if present.

    Agents frequently wrap JSON in a fenced block despite being told not to;
    tolerate it so a well-formed object isn't rejected over formatting.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    # Drop the opening fence line (``` or ```json) and a trailing fence line.
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_and_validate(content: Any, validator: Any) -> tuple[Any, str | None]:
    """Parse ``content`` as JSON and validate it against ``validator``.

    ``validator`` is the pydantic field type built from the node's JSON-Schema
    (see :mod:`schema_convert`). The text is fence-stripped, JSON-parsed, then
    validated by wrapping the field type the way ``Agent`` would
    (``create_model("Response", result=(validator, ...))``) so a top-level
    array/scalar schema validates as cleanly as an object schema.

    Returns ``(plain_value, None)`` on success — ``plain_value`` is a plain
    dict/list/scalar — or ``(None, error_message)`` on any parse/validation
    failure, so the caller can retry once then fail the node.
    """
    from pydantic import BaseModel, ValidationError, create_model

    if content is None:
        return None, "node returned no output"
    text = _strip_code_fence(content) if isinstance(content, str) else content
    try:
        data = json.loads(text) if isinstance(text, str) else text
    except (ValueError, TypeError) as exc:
        return None, f"output was not valid JSON: {_summarize(exc)}"

    # Validate via a Response wrapper so list/scalar top-level schemas work too.
    wrapper = create_model("Response", result=(validator, ...))
    try:
        validated = wrapper.model_validate({"result": data})
    except ValidationError as exc:
        return None, f"output did not match the schema: {_summarize(exc)}"

    result = validated.result
    # Normalize a validated pydantic submodel back to a plain dict/list/scalar.
    if isinstance(result, BaseModel):
        return result.model_dump(), None
    if isinstance(result, (list, tuple)):
        return [r.model_dump() if isinstance(r, BaseModel) else r for r in result], None
    return result, None

