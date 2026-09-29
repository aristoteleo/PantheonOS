"""Patching a large file must not invent text, and must not half-succeed.

Both behaviours were found by running an evolutionary search over a 924-line C++ file. Six of
thirty-five mutations committed something that would not compile, and the wreckage was not the kind
a model produces:

    #include <5vector>                  a digit spliced into an identifier
    #include 24<set>                    a number welded into an include directive
    const double 0.04 = 0.1;            an identifier replaced by a numeric literal
    #include std::<iostream>

diff-match-patch matches CHARACTERS and was configured to search a thousand characters out on a
0.5 similarity threshold. On a file full of near-identical loops and bounds checks it finds a
"close enough" anchor in the wrong place and splices mid-word -- then the tool reports success,
because success meant "at least one hunk applied", so the caller's picture of the file is now
wrong and its next patch is built against a file that does not exist.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pantheon.toolsets.file.apply_patch import (apply_exact, apply_update_operation, split_hunks)

BIG = "\n".join(
    ["#include <iostream>", "#include <vector>", ""]
    + [f"void step_{i}() {{\n    for (int j = 0; j < 100; ++j) total += j;\n}}" for i in range(40)]
    + ["const double MARGIN = 0.1;", ""]
)


def write(tmp: Path, text: str) -> Path:
    p = tmp / "solution.cpp"
    p.write_text(text)
    return p


def test_a_patch_that_matches_nothing_leaves_the_file_alone(tmp_path):
    target = write(tmp_path, BIG)
    patch = ("--- a/solution.cpp\n+++ b/solution.cpp\n@@\n"
             " // === TUNING ===\n"          # a context line that is not in the file
             "-const double MARGIN = 0.1;\n"
             "+const double MARGIN = 0.05;\n")
    res = apply_update_operation(target, "solution.cpp", patch, "unified", 0.0)
    assert res["success"] is False
    assert target.read_text() == BIG, "a failed patch must not modify anything"


def test_partial_application_writes_nothing(tmp_path):
    """One hunk of two matching used to be a success, with the other silently dropped."""
    target = write(tmp_path, BIG)
    patch = ("--- a/solution.cpp\n+++ b/solution.cpp\n"
             "@@\n-const double MARGIN = 0.1;\n+const double MARGIN = 0.05;\n"
             "@@\n-int this_line_does_not_exist = 3;\n+int nor_this = 4;\n")
    res = apply_update_operation(target, "solution.cpp", patch, "unified", 0.0)
    assert res["success"] is False
    assert res["hunks_applied"] < res["hunks_total"]
    assert "nothing was written" in res["error"]
    assert target.read_text() == BIG, (
        "a half-applied patch leaves the caller believing in a file that does not exist, and its "
        "next patch is built against that belief")


def test_an_exact_hunk_applies_wherever_it_sits(tmp_path):
    """Exact must mean exact text, not exact text at the offset the patch was built at.

    diff-match-patch scores `errors/length + distance/Match_Distance`, so with a threshold of 0 it
    rejects character-for-character identical text merely for being further into the file. Every
    patch built from its own old-text hits this, which is why exact matching does not go through
    that scorer.
    """
    target = write(tmp_path, BIG)
    patch = ("--- a/solution.cpp\n+++ b/solution.cpp\n@@\n"
             "-const double MARGIN = 0.1;\n+const double MARGIN = 0.05;\n")
    res = apply_update_operation(target, "solution.cpp", patch, "unified", 0.0)
    assert res["success"] is True, res.get("error")
    assert "MARGIN = 0.05" in target.read_text()


def test_an_ambiguous_hunk_is_refused_rather_than_guessed(tmp_path):
    """The property fuzzy matching lacks. Text that occurs twice does not say which one is meant,
    and picking one is how a hunk lands somewhere it does not belong."""
    text = "int a = 1;\n    total += 1;\nint b = 2;\n    total += 1;\n"
    write(tmp_path, text)

    unique = ("--- a/solution.cpp\n+++ b/solution.cpp\n@@\n"
              "-int a = 1;\n+int a = 7;\n")
    out, applied, _, _ = apply_exact(text, split_hunks(unique, "unified"))
    assert applied == 1 and "int a = 7;" in out          # this one names exactly one place

    ambiguous = ("--- a/solution.cpp\n+++ b/solution.cpp\n@@\n"
                 "-    total += 1;\n+    total += 2;\n")
    out2, applied2, _, reason2 = apply_exact(text, split_hunks(ambiguous, "unified"))
    assert applied2 == 0, "the same line twice does not say which one is meant"
    assert "2 matches" in reason2
    assert out2 == text


def test_fuzzy_is_still_available_when_asked_for(tmp_path):
    """The knob stays. A caller who knows the only difference is whitespace can still say so."""
    target = write(tmp_path, "def process():  \n    value = 10  \n    return value\n")
    patch = ("--- a/solution.cpp\n+++ b/solution.cpp\n@@\n"
             " def process():\n-    value = 10\n+    value = 20\n     return value\n")
    res = apply_update_operation(target, "solution.cpp", patch, "unified", 0.7)
    assert res["success"] is True
    assert "value = 20" in target.read_text()


@pytest.mark.parametrize("marker_style", ["-    pass", "- pass"])
def test_both_marker_conventions_are_understood(tmp_path, marker_style):
    """`-    pass` means four spaces of real indentation; `- x = 1` means a separator then the
    content. Both conventions appear in real patches, and reading the format two ways is safe in a
    way fuzzy matching is not: neither reading can place a hunk somewhere else, because both still
    require a unique occurrence."""
    target = write(tmp_path, "def foo():\n    pass\n")
    patch = f"*** Begin Patch\n*** Update File: solution.cpp\n@@ @@\n{marker_style}\n+    return 42\n*** End Patch\n"
    from pantheon.toolsets.file.apply_patch import parse_multi_file_patch

    ops = parse_multi_file_patch(patch, "v4a", None)
    res = apply_update_operation(target, "solution.cpp", ops[0]["patch"], "v4a", 0.0)
    assert res["success"] is True, res.get("error")
    assert "return 42" in target.read_text()


def test_a_trailing_newline_in_the_patch_is_not_a_blank_context_line(tmp_path):
    """`patch.split("\\n")` on a string ending in a newline yields a trailing empty element, which
    the shared line parser read as an empty CONTEXT line and appended to both sides -- so the
    hunk's old-text carried a newline the file does not have. Fuzzy matching absorbed it."""
    hunks = split_hunks("@@\n-    pass\n+    return 42\n", "v4a")
    assert hunks == [("    pass\n", "    return 42\n")]
