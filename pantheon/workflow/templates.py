"""Node templates and 3-layer prompt composition (Task 4).

A node agent's final system prompt is composed of TWO layers, with a third
returned separately:

    system prompt = (1) Template base prompt  +  (2) Engine protocol layer
    user message  = (3) Leader instruction        (NOT in the system prompt)

The engine protocol layer (``ENGINE_PROTOCOL``) is a constant contract /
security boundary: it is always injected and can never be suppressed or
overridden by a template or by the Leader's instruction.

Pure stdlib. Phase 1 ships exactly one built-in template: ``generic``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# --------------------------------------------------------------------------- #
# Layer (1): Template base
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WorkflowTemplate:
    """A pre-registered node-agent role template.

    ``toolsets`` holds declarative toolset *names*; Phase 1 only stores them
    and the runner resolves them later.
    """

    name: str
    base_prompt: str
    toolsets: tuple[str, ...] = ()
    model: str | None = None


# --------------------------------------------------------------------------- #
# Layer (2): Engine protocol layer — constant, always injected.
# --------------------------------------------------------------------------- #
ENGINE_PROTOCOL = """\
## Execution contract (engine-enforced — do not deviate)

Working directory & inputs: You run inside a dedicated working directory. Any
input files declared for your task are made available on disk under the
`context/` directory at the paths given to you. Read those files from disk
rather than expecting their contents to be pasted inline; do not assume any
input is present in this prompt.

Output contract: Write your output to the designated output path provided for
this task. If a JSON `schema` was specified for this node, your FINAL reply
MUST be valid JSON conforming to that schema and contain nothing else — no
prose, no code fences, no commentary surrounding it.

Behavioral constraint: Your reply is DATA consumed by an automated engine, not
a message addressed to a human. Do not add conversational preamble, sign-offs,
acknowledgements, or explanations outside the requested output. Produce only
what the task asks for, in the form it asks for."""


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_REGISTRY: dict[str, WorkflowTemplate] = {}


def register_template(template: WorkflowTemplate) -> None:
    """Register (or overwrite) a template by name."""
    _REGISTRY[template.name] = template


def get_template(name: str) -> WorkflowTemplate:
    """Return the registered template, or raise ``ValueError`` if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unknown workflow template {name!r}. Registered templates: {known}."
        ) from None


def list_templates() -> list[str]:
    """Return registered template names, sorted."""
    return sorted(_REGISTRY)


# --- Built-in: the single Phase 1 template ---------------------------------- #
_GENERIC = WorkflowTemplate(
    name="generic",
    base_prompt=(
        "You are a workflow node agent that completes one focused task within a "
        "larger automated workflow. You have a single, well-scoped objective; "
        "accomplish exactly that objective and nothing more.\n\n"
        "Tool-use etiquette: use the tools available to you deliberately. Inspect "
        "the inputs you are given before acting, take the minimal set of actions "
        "needed to satisfy the task, and verify your result before producing your "
        "final output. Do not pursue work outside the stated objective."
    ),
)
register_template(_GENERIC)


# --------------------------------------------------------------------------- #
# Layer (3) split-out: composition
# --------------------------------------------------------------------------- #
@dataclass
class _NodeFields:
    instruction: str
    schema: Any | None = None
    output_path: str | None = None


def _extract_fields(node_call_or_fields: Any, kwargs: dict) -> _NodeFields:
    """Accept either a ``NodeCall``-like object or explicit kwargs."""
    if node_call_or_fields is not None:
        instruction = getattr(node_call_or_fields, "instruction")
        schema = getattr(node_call_or_fields, "schema", None)
        output_path = getattr(node_call_or_fields, "output_path", None)
        return _NodeFields(instruction=instruction, schema=schema, output_path=output_path)
    return _NodeFields(
        instruction=kwargs["instruction"],
        schema=kwargs.get("schema"),
        output_path=kwargs.get("output_path"),
    )


def compose_node_prompt(
    template: WorkflowTemplate,
    node_call_or_fields: Any = None,
    **kwargs: Any,
) -> tuple[str, str]:
    """Compose a node agent's ``(system_prompt, user_message)``.

    ``system_prompt`` = template base prompt (layer 1) + the constant engine
    protocol layer (layer 2), plus deterministic per-node specifics (output
    path, JSON-schema requirement) that belong in the system contract.

    ``user_message`` = the node's Leader ``instruction`` (layer 3), returned
    separately. The instruction is NEVER concatenated into the system prompt.

    Pass either a ``NodeCall`` (or any object exposing ``instruction`` /
    ``schema`` / ``output_path``) as the second positional argument, or supply
    ``instruction=...`` (and optionally ``schema=...``, ``output_path=...``)
    as keyword arguments.
    """
    fields = _extract_fields(node_call_or_fields, kwargs)

    parts: list[str] = [template.base_prompt, ENGINE_PROTOCOL]

    # Deterministic per-node system-contract specifics (NOT the instruction).
    specifics: list[str] = []
    if fields.output_path:
        specifics.append(f"Output path for this node: {fields.output_path}")
    if fields.schema is not None:
        specifics.append(
            "This node REQUIRES structured output: your final reply MUST be "
            "valid JSON conforming to the provided schema and nothing else."
        )
    if specifics:
        parts.append("## This node\n" + "\n".join(specifics))

    system_prompt = "\n\n".join(parts)
    user_message = fields.instruction
    return system_prompt, user_message
