"""JSON-Schema → Python typing/pydantic conversion for schema nodes.

Why this exists
---------------
The workflow ``node(schema=...)`` contract (see ``SCRIPT_GUIDE.md`` §6) is that
``schema`` is a **JSON-Schema dict** and a schema node returns a **validated
dict** (or list/scalar for a non-object top-level schema). But
``pantheon.agent.Agent.run(response_format=...)`` does NOT accept a JSON-Schema
dict — it does ``create_model("Response", result=(response_format, ...))``, so
``response_format`` must be a valid *pydantic field type* (a ``BaseModel``
subclass, ``list[str]``, ``int``, …). Passing the raw dict raises
``SchemaError: Unknown schema type: "object"``.

This module bridges the two: :func:`schema_to_field_type` turns the documented
JSON-Schema subset into a field type suitable for ``response_format``, and
:func:`to_plain` normalizes the value ``Agent`` returns (a pydantic model
instance for object schemas) back into the plain dict/list/scalar the workflow
contract promises.

Supported JSON-Schema subset (the structured-output subset; matches what
OpenAI-compatible providers accept):
  * ``type``: object / array / string / integer / number / boolean / null
  * object: ``properties`` (recursively typed), ``required`` (others Optional)
  * array: ``items`` (recursively typed; missing items -> ``list``)
  * ``enum`` (on a scalar) -> ``Literal[...]``
  * a top-level non-object schema (array/scalar) is returned as that bare type,
    so ``{"type":"array","items":{"type":"string"}}`` -> ``list[str]`` and the
    node result is a plain list.

Anything outside this subset degrades gracefully to a permissive type
(``Any`` / ``dict`` / ``list``) rather than raising — a node should still run
even if the Leader writes a schema feature we don't model, and the agent's JSON
is still validated as *valid JSON of that shape* by the provider.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, create_model

__all__ = ["schema_to_field_type", "to_plain"]

_SCALAR_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "null": type(None),
}

#: Bounds recursion on a pathological/cyclic schema (JSON-Schema can ``$ref``;
#: we don't follow refs, but a deeply nested literal schema is still bounded).
_MAX_DEPTH = 8

#: Counter making each synthesized object model name unique (pydantic caches
#: models by qualified name; identical names with different fields can collide).
_model_seq = 0


def _next_model_name() -> str:
    global _model_seq
    _model_seq += 1
    return f"WFSchema{_model_seq}"


def schema_to_field_type(schema: dict | None, _depth: int = 0) -> Any:
    """Convert a JSON-Schema dict into a pydantic-compatible field type.

    Returns ``Any`` for ``None``/unmodelable input. Object schemas become a
    fresh ``BaseModel`` subclass; arrays become ``list[item_type]``; scalars
    become their Python type (or a ``Literal`` when an ``enum`` is present).
    """
    if schema is None or not isinstance(schema, dict) or _depth > _MAX_DEPTH:
        return Any

    # enum (independent of declared type) -> Literal of the allowed values.
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        try:
            return Literal[tuple(enum)]  # type: ignore[valid-type]
        except TypeError:
            return Any

    stype = schema.get("type")

    # A union type list (e.g. ["string","null"]) -> Optional/Any fallback.
    if isinstance(stype, list):
        non_null = [t for t in stype if t != "null"]
        inner = _SCALAR_TYPES.get(non_null[0], Any) if len(non_null) == 1 else Any
        return Optional[inner] if "null" in stype else inner

    if stype == "object" or (stype is None and "properties" in schema):
        return _object_model(schema, _depth)

    if stype == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            item_type = schema_to_field_type(items, _depth + 1)
            return list[item_type]  # type: ignore[valid-type]
        return list

    if isinstance(stype, str) and stype in _SCALAR_TYPES:
        return _SCALAR_TYPES[stype]

    # Unmodelable / unspecified -> permissive.
    return Any


def _object_model(schema: dict, depth: int) -> Any:
    """Build a ``BaseModel`` subclass from an object schema's properties.

    An object schema with NO declared ``properties`` accepts an arbitrary
    object, so it maps to a permissive ``dict`` (NOT an empty model — an empty
    model would silently drop every key on ``model_dump``).
    """
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return dict

    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for name, prop_schema in props.items():
        if not isinstance(name, str):
            continue
        ftype = schema_to_field_type(
            prop_schema if isinstance(prop_schema, dict) else None, depth + 1
        )
        if name in required:
            fields[name] = (ftype, ...)
        else:
            fields[name] = (Optional[ftype], None)
    return create_model(_next_model_name(), **fields)


def to_plain(value: Any) -> Any:
    """Normalize an ``Agent``-returned value into plain dict/list/scalar.

    ``Agent.run`` returns ``parsed.result``; for an object schema that is a
    pydantic model instance. Convert it (recursively) to a plain dict so the
    node's context envelope and the value handed back to the script match the
    documented "validated dict" contract. Lists are normalized element-wise;
    plain scalars pass through unchanged.
    """
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value
