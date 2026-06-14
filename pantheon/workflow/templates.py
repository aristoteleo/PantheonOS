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

import json
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

_ANALYZER = WorkflowTemplate(
    name="analyzer",
    base_prompt=(
        "You are a workflow node agent specialising in analysis. Your task is to "
        "examine the inputs you are given — data, documents, code, or prior results "
        "— and produce structured findings or assessments. You have one focused "
        "analytical objective; address exactly that objective and nothing more.\n\n"
        "Be precise and evidence-grounded: every claim you make must be traceable "
        "to the inputs. When evidence is ambiguous or absent, say so explicitly "
        "rather than extrapolating. Do not fabricate details that are not present "
        "in what you were given. Produce your output in the form and structure the "
        "task requests."
    ),
)
register_template(_ANALYZER)

_CODER = WorkflowTemplate(
    name="coder",
    base_prompt=(
        "You are a workflow node agent specialising in writing and editing code. "
        "Your task is to produce or modify code to satisfy a focused, well-scoped "
        "specification; accomplish exactly that specification and nothing more.\n\n"
        "Follow the patterns, style, and conventions already present in the "
        "codebase or inputs you are given. Prefer minimal, correct changes over "
        "broad rewrites. Where the task asks you to verify or test your work, do "
        "so before producing your final output. Deliver only the code artifact the "
        "task asks for — no unrequested refactors, no speculative additions."
    ),
)
register_template(_CODER)

_RESEARCHER = WorkflowTemplate(
    name="researcher",
    base_prompt=(
        "You are a workflow node agent specialising in research and synthesis. "
        "Your task is to gather and synthesise information to answer a focused "
        "question; address exactly that question and nothing more.\n\n"
        "Clearly distinguish between what is directly known from your inputs or "
        "retrieved sources, what is reasonably inferred, and what remains "
        "uncertain. Where sources are available, attribute claims to them. Avoid "
        "overclaiming: do not assert facts that are not supported by the evidence "
        "you have access to. Present your findings in the form and level of detail "
        "the task requests."
    ),
)
register_template(_RESEARCHER)


# --------------------------------------------------------------------------- #
# Layer (3) split-out: composition
# --------------------------------------------------------------------------- #
def compose_node_prompt(
    template: WorkflowTemplate,
    instruction: str,
    *,
    schema: Any | None = None,
    output_path: str | None = None,
    inputs: list[tuple[str, Any]] | None = None,
) -> tuple[str, str]:
    """Compose a node agent's ``(system_prompt, user_message)``.

    ``system_prompt`` = template base prompt (layer 1) + the constant engine
    protocol layer (layer 2), plus deterministic per-node specifics (output
    path, JSON-schema requirement) that belong in the system contract.

    ``user_message`` = the node's Leader ``instruction`` (layer 3), returned
    separately, optionally preceded by an ``## Inputs`` block carrying the
    inlined contents of the node's declared input files. The instruction is
    NEVER concatenated into the system prompt.

    ``instruction`` is required. ``output_path`` is computed and supplied by the
    runner (it derives from ``node_id``; it is not a field on ``NodeCall``).
    ``schema``, when provided, makes the JSON-output requirement explicit in the
    system contract. ``inputs`` is an ordered ``(name, value)`` list the runner
    loads from the declared input files; Phase-1 nodes are tool-less, so their
    inputs are delivered inline here rather than read from disk by the agent.
    """
    parts: list[str] = [template.base_prompt, ENGINE_PROTOCOL]

    # Deterministic per-node system-contract specifics (NOT the instruction).
    specifics: list[str] = []
    if output_path:
        specifics.append(f"Output path for this node: {output_path}")
    if schema is not None:
        # The engine does NOT use the model's native structured-output mode;
        # the node must emit conforming JSON as its final reply, which the
        # engine then parses and validates. Embed the concrete schema so the
        # agent knows the exact shape to produce.
        try:
            schema_json = json.dumps(schema, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            schema_json = str(schema)
        specifics.append(
            "This node REQUIRES structured output. Your FINAL reply MUST be a "
            "single JSON value that validates against this JSON Schema, and "
            "MUST contain nothing else — no prose, no markdown code fences, no "
            "commentary:\n\n"
            f"{schema_json}"
        )
    if specifics:
        parts.append("## This node\n" + "\n\n".join(specifics))

    system_prompt = "\n\n".join(parts)

    # Layer 3 (user message): inlined inputs (if any) precede the instruction so
    # the node sees its upstream data without needing a file-reading tool.
    if inputs:
        blocks: list[str] = [
            "## Inputs\n"
            "The following input data was produced by upstream steps. Use it to "
            "complete your task; do not try to read these files from disk."
        ]
        for name, value in inputs:
            try:
                rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
            except (TypeError, ValueError):
                rendered = str(value)
            blocks.append(f"### {name}\n{rendered}")
        user_message = "\n\n".join(blocks) + "\n\n---\n\n" + instruction
    else:
        user_message = instruction

    return system_prompt, user_message
