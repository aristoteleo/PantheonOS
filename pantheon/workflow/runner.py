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

        system_prompt, user_message = compose_node_prompt(
            template,
            node_call.instruction,
            schema=node_call.schema,
            output_path=result_ref,
        )

        model = node_call.model or template.model

        memory = Memory(
            name=f"wf-{wf_id}-n{node_id}",
            file_path=str(memory_path),
        )
        agent = self._agent_factory(
            name=f"wf-{wf_id}-n{node_id}",
            instructions=system_prompt,
            model=model,
            tools=None,
            response_format=node_call.schema,
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

            if node_call.schema is not None and content is None:
                last_err = "output did not match the required schema"
                if attempt < max_attempts - 1:
                    continue
                return NodeResult(
                    node_id=node_id, status="failed", error=last_err
                )

            # Success — persist and return.
            self._write_context(context_path, content, node_call.schema)
            return NodeResult(
                node_id=node_id,
                status="completed",
                result=content,
                result_ref=result_ref,
                token_cost=0,  # best-effort: ResponseDetails carries no usage.
            )

        # Exhausted retries (schema node only reaches here on repeated None).
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

        Deliberately omits ``process_step_message`` / ``process_chunk``
        (decision 11): node output must NOT bubble to the Leader.
        """
        coro = agent.run(
            msg,
            response_format=node_call.schema,
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

        Schema nodes write the structured object directly; text nodes wrap the
        text as ``{"result": <text>}`` for a consistent JSON shape on disk.
        Falls back to ``{"result": <repr>}`` if the content is not
        JSON-serializable.
        """
        if schema is not None:
            payload = content
        else:
            payload = {"result": content}
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except TypeError:
            text = json.dumps({"result": str(content)}, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8")


def _summarize(exc: BaseException, limit: int = 500) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"
