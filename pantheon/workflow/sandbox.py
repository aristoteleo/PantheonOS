"""Restricted script sandbox for the Pantheon dynamic-workflow Leader.

The Leader (an aligned LLM) emits a restricted Python orchestration script.
This module validates and executes that script with a deliberately minimal,
locked-down global namespace plus an injected async orchestration API
(``node``, ``parallel``, ``pipeline``, ``phase``, ``log`` — supplied by later
tasks) and an ``args`` value.

Execution model
---------------
We use the **async-wrapper** model. The user source becomes the *body* of a
generated ``async def __workflow_main__():`` coroutine. The source is indented
under that ``def``, compiled, and ``exec``'d into the restricted globals to
define the coroutine, which is then awaited. This is the cleanest way to allow
the script to ``await node(...)`` and to ``return`` a final value (top-level
``return`` is illegal in a module, but legal in our wrapper function).

Security boundary (decision §4.1)
---------------------------------
The threat model treats the script *author* (the Leader) as aligned, but
prompt-injection could induce a boundary-violating script. The sandbox enforces
hard boundaries against accidents and injected violations:

What we BLOCK:
  * Imports (``import`` / ``from ... import``) — statically rejected.
  * A denylist of dangerous identifiers used as names or attributes
    (``eval``, ``exec``, ``open``, ``os``, ``sys``, reflection dunders, ...).
  * Dunder *attribute* access generally (``().__class__`` etc.) — the classic
    object-graph escape vector — rejected statically.
  * Direct IO: ``open`` is neither in globals nor allowed by the denylist;
    all file/network access must go through the injected API.
  * Non-determinism: ``time`` / ``random`` / ``datetime`` are absent from
    globals (and denylisted as bare names), enabling journal/resume.
  * Resource exhaustion (wall-clock): ``run_script`` owns the overall
    ``timeout`` + cancellation. Any name not present in the restricted
    globals raises ``NameError`` because ``__builtins__`` is replaced by a
    curated safe-builtins dict.

Division of responsibility:
  * The sandbox owns: static validation, restricted globals, overall timeout,
    cancellation, and exception capture.
  * The API layer (a later task) owns: per-API caps such as ``max_nodes`` and
    ``concurrency``. Those are NOT enforced here.

Residual risk (explicitly accepted):
  Replacing ``__builtins__`` with a restricted dict is the standard CPython
  mechanism, but it is NOT a hardened sandbox. A determined adversary with
  arbitrary code execution can, in principle, traverse the object graph to
  reach disallowed capabilities. The static denylist closes the well-known
  dunder-escape doors, but in-process ``exec`` cannot be a complete defense.
  This residual is accepted for Phase 1 and is handled by the isolation
  runners (separate process / container) planned for Phase 3/4. Our job here
  is to block accidents, direct IO, disallowed names, and runaway scripts.
"""

from __future__ import annotations

import ast
import asyncio
import textwrap
import traceback
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ValidationResult",
    "ScriptResult",
    "validate_script",
    "run_script",
    "SAFE_BUILTINS",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of static validation (no execution)."""

    ok: bool
    error: str | None = None
    line: int | None = None


@dataclass
class ScriptResult:
    """Outcome of executing a validated script."""

    ok: bool
    result: Any = None
    error: str | None = None
    cancelled: bool = False


# ---------------------------------------------------------------------------
# Safe builtins whitelist
# ---------------------------------------------------------------------------
#
# Only pure, side-effect-free helpers and the exception types a script may
# legitimately raise/catch. Notably ABSENT: open, __import__, eval, exec,
# compile, globals, locals, vars, getattr, setattr, delattr, input, exit,
# quit, help, dir, type (type() is a reflection vector toward __subclasses__),
# and anything touching the import system or filesystem.
#
# ``print`` is intentionally NOT a passthrough to stdout; it is a no-op so a
# script that prints does not crash but also does not perform IO. (Logging is
# done via the injected ``log`` API.)

def _noop_print(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - trivial
    return None


_SAFE_BUILTIN_NAMES = (
    # iteration / sequence helpers
    "len", "range", "enumerate", "zip", "map", "filter",
    "sorted", "reversed", "min", "max", "sum",
    # numeric helpers
    "abs", "round", "pow", "divmod",
    # logic helpers
    "all", "any",
    # constructors / coercion
    "list", "dict", "set", "frozenset", "tuple",
    "str", "int", "float", "bool", "bytes", "complex",
    # predicates
    "isinstance", "issubclass",
    # exceptions the script may raise/catch
    "Exception", "ValueError", "KeyError", "IndexError", "TypeError",
    "RuntimeError", "StopIteration", "StopAsyncIteration", "ArithmeticError",
    "ZeroDivisionError", "AttributeError", "NotImplementedError",
    "AssertionError", "LookupError", "OverflowError",
)


def _build_safe_builtins() -> dict[str, Any]:
    import builtins as _b

    safe: dict[str, Any] = {}
    for name in _SAFE_BUILTIN_NAMES:
        obj = getattr(_b, name, None)
        if obj is not None:
            safe[name] = obj
    # Constants / overrides
    safe["True"] = True
    safe["False"] = False
    safe["None"] = None
    safe["print"] = _noop_print
    return safe


SAFE_BUILTINS: dict[str, Any] = _build_safe_builtins()


# ---------------------------------------------------------------------------
# Denylist (static AST checks)
# ---------------------------------------------------------------------------
#
# Bare Names that must never appear, and attribute names that signal a
# reflection / escape attempt. Dunder attributes are rejected wholesale (see
# _is_dunder), so this set mainly catches non-dunder dangerous attributes.

_DENY_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "open",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "__builtins__", "input", "exit", "quit", "help",
    # determinism breakers
    "time", "random", "datetime",
    # reflection dunders (also caught as attributes)
    "__class__", "__bases__", "__subclasses__", "__globals__",
    "__mro__", "__dict__", "__code__", "__closure__", "__init__",
    "__getattribute__", "__base__", "__builtins__",
})

# Non-dunder attribute names that are still dangerous reflection vectors.
_DENY_ATTRS = frozenset({
    "system", "popen", "spawn", "fork",
})


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


class _Visitor(ast.NodeVisitor):
    """Walks the AST and records the first violation found."""

    def __init__(self) -> None:
        self.error: str | None = None
        self.line: int | None = None

    def _flag(self, msg: str, node: ast.AST) -> None:
        if self.error is None:
            self.error = msg
            self.line = getattr(node, "lineno", None)

    def visit_Import(self, node: ast.Import) -> None:
        self._flag("imports are not allowed", node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._flag("imports are not allowed", node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DENY_NAMES:
            self._flag(f"disallowed name: {node.id!r}", node)
        elif _is_dunder(node.id):
            self._flag(f"disallowed dunder name: {node.id!r}", node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if _is_dunder(attr):
            self._flag(f"disallowed dunder attribute: {attr!r}", node)
        elif attr in _DENY_NAMES or attr in _DENY_ATTRS:
            self._flag(f"disallowed attribute: {attr!r}", node)
        self.generic_visit(node)


def validate_script(source: str) -> ValidationResult:
    """Statically validate a script without executing it.

    Returns ``ok=False`` with a line number for syntax errors and for any
    statically-detectable boundary violation (imports, denylisted names,
    dunder attribute access).
    """
    if not source or not source.strip():
        return ValidationResult(ok=False, error="empty script", line=None)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ValidationResult(ok=False, error=f"syntax error: {exc.msg}", line=exc.lineno)

    visitor = _Visitor()
    visitor.visit(tree)
    if visitor.error is not None:
        return ValidationResult(ok=False, error=visitor.error, line=visitor.line)

    return ValidationResult(ok=True, error=None, line=None)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

_WRAPPER_NAME = "__workflow_main__"


def _compile_wrapper(source: str) -> Any:
    """Wrap user source as the body of an async coroutine and compile it."""
    indented = textwrap.indent(source, "    ")
    wrapped = f"async def {_WRAPPER_NAME}():\n{indented}\n"
    return compile(wrapped, filename="<workflow-script>", mode="exec")


async def run_script(
    source: str,
    api: dict,
    args: Any,
    *,
    timeout: float | None = None,
) -> ScriptResult:
    """Validate, then execute a restricted orchestration script.

    The script body may ``await`` injected API coroutines, use normal control
    flow / comprehensions / inner functions, and ``return`` a final value
    (surfaced as ``ScriptResult.result``).

    The sandbox owns the overall ``timeout`` (wall-clock) and cancellation.
    On timeout the script task is cancelled and ``cancelled=True`` is returned.
    Any exception raised inside the script is captured, never propagated.
    """
    # Defense in depth: re-validate before executing.
    validation = validate_script(source)
    if not validation.ok:
        return ScriptResult(
            ok=False,
            result=None,
            error=f"validation failed: {validation.error}"
            + (f" (line {validation.line})" if validation.line else ""),
            cancelled=False,
        )

    try:
        code = _compile_wrapper(source)
    except SyntaxError as exc:  # pragma: no cover - validate catches these
        return ScriptResult(ok=False, error=f"syntax error: {exc.msg}", cancelled=False)

    # Restricted globals: locked __builtins__ + injected API + args.
    # A name absent here raises NameError (the core invariant).
    restricted_globals: dict[str, Any] = {
        "__builtins__": dict(SAFE_BUILTINS),
        **api,
        "args": args,
    }
    # Guard: ensure the injected API cannot shadow __builtins__ and re-open
    # the builtins door (set it last, after the **api spread).
    restricted_globals["__builtins__"] = dict(SAFE_BUILTINS)

    try:
        exec(code, restricted_globals)  # noqa: S102 - intentional, sandboxed
    except Exception as exc:  # pragma: no cover - compile-time defined coroutine
        return ScriptResult(ok=False, error=_fmt_exc(exc), cancelled=False)

    main = restricted_globals.get(_WRAPPER_NAME)
    if main is None:  # pragma: no cover - wrapper always defines it
        return ScriptResult(ok=False, error="failed to define workflow entrypoint", cancelled=False)

    task = asyncio.ensure_future(main())
    try:
        if timeout is not None:
            result = await asyncio.wait_for(task, timeout=timeout)
        else:
            result = await task
    except asyncio.TimeoutError:
        # wait_for already cancelled the task; ensure it's settled.
        await _drain_cancelled(task)
        return ScriptResult(ok=False, result=None, error="timeout", cancelled=True)
    except asyncio.CancelledError:
        await _drain_cancelled(task)
        return ScriptResult(ok=False, result=None, error="cancelled", cancelled=True)
    except Exception as exc:
        return ScriptResult(ok=False, result=None, error=_fmt_exc(exc), cancelled=False)

    return ScriptResult(ok=True, result=result, error=None, cancelled=False)


async def _drain_cancelled(task: "asyncio.Future") -> None:
    """Best-effort: await a cancelled task to suppress pending warnings."""
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def _fmt_exc(exc: BaseException) -> str:
    """Compact traceback summary suitable for ScriptResult.error."""
    tb = traceback.format_exception_only(type(exc), exc)
    return "".join(tb).strip()
