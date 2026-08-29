"""Reflect a ToolSet class into its headless tools face (§02 provides.tools).

Same-source with the runtime by construction: the @tool decorator already
parses every tool's signature at decoration time (`_tool_desc`, via funcdesc)
and marks visibility (`_exclude`) — exactly what `ToolSet.__init__` collects
and the worker registers. This module reads those markers off the CLASS, so
no instantiation (and none of a toolset's constructor side effects) is needed
to know its contract.
"""

from __future__ import annotations

from typing import Iterable

from pantheon.apps.schema import ToolParam, ToolSig


def _params_from_desc(desc: dict | None) -> list[ToolParam]:
    if not desc:
        return []
    out: list[ToolParam] = []
    for inp in desc.get("inputs", []):
        name = inp.get("name")
        if not name:
            continue
        type_ = inp.get("type")
        out.append(
            ToolParam(
                name=name,
                type=str(type_) if type_ is not None else None,
                description=inp.get("doc") or inp.get("description"),
                # funcdesc marks optionality via a recorded default
                required="default" not in inp,
                default=inp.get("default"),
            )
        )
    return out


def reflect_toolset_class(cls: type) -> list[ToolSig]:
    """The tools face of a ToolSet class, without instantiating it.

    Mirrors ToolSet.__init__'s collection filter (`_is_tool`), and carries
    `_exclude` through as `hidden` rather than dropping — hidden tools are
    still part of the bus contract (the frontend calls them), just not of the
    LLM's menu.
    """
    sigs: list[ToolSig] = []
    for name in dir(cls):
        if name.startswith("__"):
            continue
        fn = getattr(cls, name, None)
        if fn is None or not getattr(fn, "_is_tool", False):
            continue
        desc = getattr(fn, "_tool_desc", None) or {}
        sigs.append(
            ToolSig(
                name=name,
                description=(desc.get("doc") or (fn.__doc__ or "").strip().split("\n")[0] or None),
                params=_params_from_desc(desc),
                hidden=bool(getattr(fn, "_exclude", False)),
            )
        )
    sigs.sort(key=lambda s: s.name)
    return sigs


def reflect_toolset_instance(ts) -> list[ToolSig]:
    """The tools face as the RUNTIME sees it (from a live instance).

    Used by tests to pin reflect_toolset_class against what a worker would
    actually register — the two must agree or the manifest lies.
    """
    sigs: list[ToolSig] = []
    for name, (method, _kwargs) in ts.functions.items():
        desc = getattr(method, "_tool_desc", None) or {}
        sigs.append(
            ToolSig(
                name=name,
                description=(desc.get("doc") or (method.__doc__ or "").strip().split("\n")[0] or None),
                params=_params_from_desc(desc),
                hidden=bool(getattr(method, "_exclude", False)),
            )
        )
    sigs.sort(key=lambda s: s.name)
    return sigs


def signature_diff(a: Iterable[ToolSig], b: Iterable[ToolSig]) -> list[str]:
    """Human-readable differences between two tool faces (empty = identical).

    This is the primitive `app check-compat` (§06) builds on: removed tools,
    removed/now-required params, and type changes are the breaking cases.
    """
    am = {s.name: s for s in a}
    bm = {s.name: s for s in b}
    problems: list[str] = []
    for name in sorted(set(am) - set(bm)):
        problems.append(f"tool removed: {name}")
    for name in sorted(set(bm) - set(am)):
        problems.append(f"tool added: {name}")
    for name in sorted(set(am) & set(bm)):
        ap = {p.name: p for p in am[name].params}
        bp = {p.name: p for p in bm[name].params}
        for pn in sorted(set(ap) - set(bp)):
            problems.append(f"{name}: param removed: {pn}")
        for pn in sorted(set(bp) - set(ap)):
            marker = "required " if bp[pn].required else ""
            problems.append(f"{name}: {marker}param added: {pn}")
        for pn in sorted(set(ap) & set(bp)):
            if (ap[pn].type or None) != (bp[pn].type or None):
                problems.append(f"{name}: param {pn} type {ap[pn].type} -> {bp[pn].type}")
            if ap[pn].required != bp[pn].required:
                problems.append(f"{name}: param {pn} required {ap[pn].required} -> {bp[pn].required}")
    return problems
