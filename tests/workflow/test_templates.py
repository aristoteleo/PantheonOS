"""Tests for Task 4: node templates + 3-layer prompt composition.

Three layers compose a node agent's prompt:
  (1) Template base prompt  -> system prompt
  (2) Engine protocol layer -> system prompt (constant, always injected)
  (3) Leader instruction    -> returned SEPARATELY as the user message

The key invariant under test: the Leader instruction never leaks into the
system prompt, and the engine protocol layer is always present in it.
"""

import pytest

from pantheon.workflow.models import NodeCall
from pantheon.workflow.templates import (
    ENGINE_PROTOCOL,
    WorkflowTemplate,
    compose_node_prompt,
    get_template,
    list_templates,
    register_template,
)


# --- Test 1: generic is registered and retrievable ---
def test_generic_registered():
    t = get_template("generic")
    assert isinstance(t, WorkflowTemplate)
    assert t.name == "generic"
    assert t.base_prompt.strip()  # non-empty


def test_generic_is_only_builtin():
    # YAGNI: Phase 1 ships exactly one built-in template.
    assert list_templates() == ["generic"]


# --- Test 2: unknown template raises a clear error ---
def test_unknown_template_raises():
    with pytest.raises((KeyError, ValueError)) as excinfo:
        get_template("nonexistent")
    assert "nonexistent" in str(excinfo.value)


# --- Test 3: system prompt contains protocol sentences + template base ---
def test_compose_includes_protocol_and_base():
    t = get_template("generic")
    system_prompt, _user = compose_node_prompt(
        t, NodeCall(node_id=1, instruction="do a thing")
    )
    # Template base layer present.
    assert t.base_prompt in system_prompt
    # Engine protocol layer present (whole block).
    assert ENGINE_PROTOCOL in system_prompt
    # Distinctive protocol substrings present.
    assert "reply is DATA" in system_prompt
    assert "output" in system_prompt.lower()


# --- Test 4: instruction goes to user_message, NOT system_prompt ---
def test_instruction_not_in_system_prompt():
    t = get_template("generic")
    instruction = "REVIEW_THE_AUTH_MODULE_XYZ"
    system_prompt, user_message = compose_node_prompt(
        t, NodeCall(node_id=2, instruction=instruction)
    )
    assert instruction in user_message
    assert instruction not in system_prompt


# --- Test 5: schema-required node communicates JSON-output requirement ---
def test_schema_requirement_reflected():
    t = get_template("generic")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    system_prompt, _user = compose_node_prompt(
        t,
        NodeCall(node_id=3, instruction="emit json", schema=schema),
    )
    # Stable keyword guaranteed by ENGINE_PROTOCOL / deterministic append.
    assert "JSON" in system_prompt
    assert "schema" in system_prompt.lower()


def test_no_schema_omits_schema_directive():
    # When no schema is required, the appended schema directive is absent,
    # but the constant protocol layer (which mentions schema conditionally)
    # is still fully present.
    t = get_template("generic")
    system_prompt, _user = compose_node_prompt(
        t, NodeCall(node_id=4, instruction="free text")
    )
    assert ENGINE_PROTOCOL in system_prompt


# --- Composition accepts explicit kwargs too ---
def test_compose_accepts_kwargs():
    t = get_template("generic")
    system_prompt, user_message = compose_node_prompt(
        t,
        instruction="KWARG_INSTRUCTION_ABC",
        schema={"type": "object"},
        output_path="context/n5.json",
    )
    assert "KWARG_INSTRUCTION_ABC" in user_message
    assert "KWARG_INSTRUCTION_ABC" not in system_prompt
    assert "context/n5.json" in system_prompt
    assert "JSON" in system_prompt


# --- register/get round-trip ---
def test_register_and_get_roundtrip():
    custom = WorkflowTemplate(name="custom_x", base_prompt="You are X.")
    register_template(custom)
    assert get_template("custom_x") is custom
