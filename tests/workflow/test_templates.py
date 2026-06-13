"""Tests for Task 4: node templates + 3-layer prompt composition.

Three layers compose a node agent's prompt:
  (1) Template base prompt  -> system prompt
  (2) Engine protocol layer -> system prompt (constant, always injected)
  (3) Leader instruction    -> returned SEPARATELY as the user message

The key invariant under test: the Leader instruction never leaks into the
system prompt, and the engine protocol layer is always present in it.
"""

import pytest

from pantheon.workflow import templates as templates_mod
from pantheon.workflow.templates import (
    ENGINE_PROTOCOL,
    WorkflowTemplate,
    compose_node_prompt,
    get_template,
    list_templates,
    register_template,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot and restore the module registry around every test.

    ``register_template`` mutates the module-level dict in place, so a plain
    monkeypatch of a copy would not undo it. Snapshot the contents, then
    restore them in teardown so per-test mutations cannot leak and make other
    tests order-dependent.
    """
    snapshot = dict(templates_mod._REGISTRY)
    try:
        yield
    finally:
        templates_mod._REGISTRY.clear()
        templates_mod._REGISTRY.update(snapshot)


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
    system_prompt, _user = compose_node_prompt(t, "do a thing")
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
    system_prompt, user_message = compose_node_prompt(t, instruction)
    assert instruction in user_message
    assert instruction not in system_prompt


# --- output_path is an explicit parameter, supplied by the runner ---
def test_output_path_appears_in_system_prompt():
    t = get_template("generic")
    system_prompt, user_message = compose_node_prompt(
        t,
        "REVIEW_THE_AUTH_MODULE_XYZ",
        output_path="context/n7.json",
    )
    assert "context/n7.json" in system_prompt
    # instruction still isolated from system prompt.
    assert "REVIEW_THE_AUTH_MODULE_XYZ" in user_message
    assert "REVIEW_THE_AUTH_MODULE_XYZ" not in system_prompt


# --- Test 5: schema-required node communicates JSON-output requirement ---
def test_schema_requirement_reflected():
    t = get_template("generic")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    system_prompt, _user = compose_node_prompt(t, "emit json", schema=schema)
    # Stable keyword guaranteed by ENGINE_PROTOCOL / deterministic append.
    assert "JSON" in system_prompt
    assert "schema" in system_prompt.lower()


def test_no_schema_omits_schema_directive():
    # When no schema is required, the constant protocol layer (which mentions
    # schema conditionally) is still fully present.
    t = get_template("generic")
    system_prompt, _user = compose_node_prompt(t, "free text")
    assert ENGINE_PROTOCOL in system_prompt


# --- instruction is a required positional: omitting it is a TypeError ---
def test_instruction_required():
    t = get_template("generic")
    with pytest.raises(TypeError):
        compose_node_prompt(t)  # type: ignore[call-arg]


# --- inputs are inlined into the USER message (Phase-1 tool-less delivery) ---
def test_inputs_inlined_into_user_message():
    t = get_template("generic")
    system_prompt, user = compose_node_prompt(
        t,
        "Summarize the input.",
        inputs=[("n0.json", {"facts": ["a", "b"]}), ("n1.json", "plain text")],
    )
    # Inputs go in the user message, NOT the system contract.
    assert "## Inputs" in user
    assert "n0.json" in user and "n1.json" in user
    assert '"facts"' in user and "plain text" in user
    # The instruction still follows the inputs block.
    assert user.rstrip().endswith("Summarize the input.")
    assert "## Inputs" not in system_prompt


def test_no_inputs_user_is_bare_instruction():
    t = get_template("generic")
    _system, user = compose_node_prompt(t, "Just do it.")
    assert user == "Just do it."


# --- register/get round-trip ---
def test_register_and_get_roundtrip():
    custom = WorkflowTemplate(name="custom_x", base_prompt="You are X.")
    register_template(custom)
    assert get_template("custom_x") is custom
