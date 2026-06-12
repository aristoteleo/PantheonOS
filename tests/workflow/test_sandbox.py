"""Security-critical tests for the restricted script sandbox.

Covers normal execution, args injection, denylist enforcement (static +
runtime), dunder-escape blocking, syntax errors, timeout/cancellation,
exception capture, and absence of determinism-breaking names.
"""

import asyncio

import pytest

from pantheon.workflow.sandbox import (
    ScriptResult,
    ValidationResult,
    run_script,
    validate_script,
)


# --------------------------------------------------------------------------
# Test API stubs (stand-ins for the real orchestration API injected later)
# --------------------------------------------------------------------------


def make_api():
    async def node(value):
        return value * 2

    async def hang(*args, **kwargs):
        # Never resolves on its own; only cancellation stops it.
        await asyncio.Event().wait()

    def log(*args, **kwargs):
        pass

    return {"node": node, "hang": hang, "log": log}


# --------------------------------------------------------------------------
# 1. Normal execution
# --------------------------------------------------------------------------


async def test_normal_script_returns_value():
    src = "x = sum(range(5))\nreturn x"
    res = await run_script(src, make_api(), args=None)
    assert isinstance(res, ScriptResult)
    assert res.ok is True
    assert res.result == 10
    assert res.error is None
    assert res.cancelled is False


async def test_await_injected_node():
    src = "v = await node(21)\nreturn v"
    res = await run_script(src, make_api(), args=None)
    assert res.ok is True
    assert res.result == 42


async def test_control_flow_and_comprehensions():
    src = (
        "out = []\n"
        "for i in range(3):\n"
        "    out.append(await node(i))\n"
        "doubled = [y + 1 for y in out]\n"
        "return doubled"
    )
    res = await run_script(src, make_api(), args=None)
    assert res.ok is True
    assert res.result == [1, 3, 5]


async def test_multiline_string_literal_not_corrupted():
    # Regression: textual indentation would prepend 4 spaces to the
    # continuation line inside the triple-quoted literal. AST wrapping must
    # preserve the string content byte-for-byte.
    src = 'x = """alpha\nbeta"""\nreturn x'
    res = await run_script(src, make_api(), args=None)
    assert res.ok is True
    assert res.result == "alpha\nbeta"


async def test_no_return_yields_none():
    res = await run_script("y = sum(range(3))", make_api(), args=None)
    assert res.ok is True
    assert res.result is None


def test_validate_rejects_empty_and_comment_only():
    for src in ["# just a comment", "   \n  # c\n", "pass", '"""only a docstring"""']:
        res = validate_script(src)
        assert res.ok is False
        assert res.error is not None


# --------------------------------------------------------------------------
# 2. args available
# --------------------------------------------------------------------------


async def test_args_available():
    src = "return args['foo']"
    res = await run_script(src, make_api(), args={"foo": 99})
    assert res.ok is True
    assert res.result == 99


# --------------------------------------------------------------------------
# 3. Disallowed names blocked (static + runtime)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "return open('/etc/passwd')",
        "import os\nreturn 1",
        "from os import system\nreturn 1",
        "return __import__('os')",
        "return eval('1')",
        "return exec('x=1')",
        "return compile('1', '<s>', 'eval')",
        "import sys\nreturn sys.argv",
    ],
)
def test_validate_rejects_disallowed(src):
    res = validate_script(src)
    assert isinstance(res, ValidationResult)
    assert res.ok is False
    assert res.error is not None
    assert res.line is not None


async def test_runtime_nameerror_for_blocked_name():
    # `os` is not import-guarded as a Name here (no import statement), but it
    # also is not in globals -> NameError at runtime, captured as error.
    # validate_script should also reject it statically.
    src = "return os.getcwd()"
    vres = validate_script(src)
    assert vres.ok is False

    # Prove runtime defense independently with a name that passes static check
    # but is absent from the restricted globals.
    safe_but_absent = "return some_undefined_helper(3)"
    res = await run_script(safe_but_absent, make_api(), args=None)
    assert res.ok is False
    assert res.cancelled is False
    assert "NameError" in res.error or "some_undefined_helper" in res.error


# --------------------------------------------------------------------------
# 4. Dunder-escape blocked
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "return ().__class__.__bases__",
        "return ''.__class__.__mro__",
        "return (1).__class__.__subclasses__()",
        "return type(1).__dict__",
    ],
)
def test_validate_rejects_dunder_attribute(src):
    res = validate_script(src)
    assert res.ok is False
    assert res.line is not None


# --------------------------------------------------------------------------
# 5. Syntax error -> line number
# --------------------------------------------------------------------------


async def test_syntax_error_line():
    src = "x = 1\ny = (\nreturn y"
    res = validate_script(src)
    assert res.ok is False
    assert res.line is not None

    # run_script short-circuits to error on invalid scripts.
    rr = await run_script(src, make_api(), args=None)
    assert rr.ok is False


# --------------------------------------------------------------------------
# 6. Timeout / cancellation
# --------------------------------------------------------------------------


async def test_timeout_cancels():
    src = "await hang()\nreturn 1"
    res = await run_script(src, make_api(), args=None, timeout=0.3)
    assert res.ok is False
    assert res.cancelled is True
    assert res.error == "timeout"


# --------------------------------------------------------------------------
# 7. Exception capture
# --------------------------------------------------------------------------


async def test_exception_captured():
    src = "raise ValueError('boom')"
    res = await run_script(src, make_api(), args=None)
    assert res.ok is False
    assert res.cancelled is False
    assert "boom" in res.error


# --------------------------------------------------------------------------
# 8. Determinism names absent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "import time\nreturn time.time()",
        "import random\nreturn random.random()",
        "import datetime\nreturn datetime.datetime.now()",
    ],
)
def test_determinism_names_rejected_static(src):
    res = validate_script(src)
    assert res.ok is False


async def test_determinism_names_nameerror_runtime():
    # A non-denylisted but absent helper passes static checks yet has no
    # binding in restricted globals -> NameError at runtime.
    src = "return clock_now()"
    res = await run_script(src, make_api(), args=None)
    assert res.ok is False
    assert "NameError" in res.error or "clock_now" in res.error
