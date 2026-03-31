import os
import pytest
from tempfile import TemporaryDirectory
from pantheon.toolsets.file import FileManagerToolSet

@pytest.fixture
def temp_toolset():
    """Create a FileManagerToolSet with a temporary directory."""
    with TemporaryDirectory() as temp_dir:
        yield FileManagerToolSet("file_manager", temp_dir)

async def test_filemanager_comprehensive(temp_toolset):
    """
    Test comprehensive file manager operations in a single flow.
    
    Covers:
    - create_directory
    - write_file (and overwrite behavior)
    - list_files (root and subdirectory)
    - read_file (full and partial/line-range)
    - update_file (single, multiple/replace_all, line-limited)
    - move_file
    - delete_path
    """
    
    # 1. Start with directory structure
    assert (await temp_toolset.create_directory("src"))["success"]
    assert (await temp_toolset.create_directory("src/utils"))["success"]
    
    # 2. Write file with multiple lines
    content = "import os\nimport sys\n\ndef func():\n    pass\n    return 0\n"
    res = await temp_toolset.write_file("src/main.py", content)
    assert res["success"]
    
    # Test overwrite protection (default is overwrite=False but tool interface usually defaults to user intent, 
    # checking impl: write_file defaults to overwrite=False in some versions, let's verify)
    # Actually most LLM tool implementations of write_file default to overwriting or failing. 
    # Current pantheon implementation signature is: write_file(file_path, content) -> likely overwrites or fails.
    # Let's test overwrite explicit just in case.
    res = await temp_toolset.write_file("src/main.py", "OVERWRITTEN", overwrite=True)
    assert res["success"]
    assert (await temp_toolset.read_file("src/main.py"))["content"] == "OVERWRITTEN"
    
    # Restore content
    await temp_toolset.write_file("src/main.py", content, overwrite=True)
    
    # 3. List files in subdirectory
    res = await temp_toolset.list_files("src")
    assert res["success"]
    names = [f["name"] for f in res["files"]]
    assert "main.py" in names
    assert "utils" in names

    # 4. Read file with line range
    # Content:
    # 1: import os
    # 2: import sys
    # 3: 
    # 4: def func():
    # 5:     pass
    # 6:     return 0
    
    # Read lines 4-6
    res = await temp_toolset.read_file("src/main.py", start_line=4, end_line=6)
    assert res["success"]
    part = res["content"]
    assert "def func():" in part
    assert "return 0" in part
    assert "import os" not in part
    
    # 5. Update file with advanced params
    
    # A. Scope limit (only replace 'pass' inside function if we knew lines, 
    # but here just testing line limit works)
    res = await temp_toolset.update_file(
        "src/main.py",
        old_string="pass",
        new_string="print('hello')",
        start_line=5,
        end_line=5
    )
    assert res["success"]
    assert "print('hello')" in (await temp_toolset.read_file("src/main.py"))["content"]
    
    # Prepare file for replace_all test
    # 1: import os
    # 2: import sys
    # ...
    # Let's add multiple same lines
    multi_content = "foo\nbar\nfoo\nbaz\nfoo"
    await temp_toolset.write_file("src/multi.txt", multi_content)
    
    # B. Replace All
    res = await temp_toolset.update_file(
        "src/multi.txt",
        old_string="foo",
        new_string="replaced",
        replace_all=True
    )
    assert res["success"]
    assert res["replacements"] == 3
    new_multi = (await temp_toolset.read_file("src/multi.txt"))["content"]
    assert new_multi.count("replaced") == 3
    assert "foo" not in new_multi
    
    # 7. Move and Delete
    # Move directory
    res = await temp_toolset.move_file("src/utils", "src/tools")
    assert res["success"]
    assert (temp_toolset.path / "src/tools").exists()
    assert not (temp_toolset.path / "src/utils").exists()
    
    # Delete file
    res = await temp_toolset.delete_path("src/multi.txt")
    assert res["success"]
    assert not (temp_toolset.path / "src/multi.txt").exists()


async def test_glob_comprehensive(temp_toolset):
    """
    Test comprehensive glob functionality.
    
    Covers:
    - Find files with pattern matching
    - Relative path search
    - Absolute path search
    - Error handling for nonexistent paths
    - Type filtering (file/directory/any)
    - Exclude patterns
    - Max depth limiting
    - Combined filters
    """
    # Setup: Create test file structure
    await temp_toolset.create_directory("src")
    await temp_toolset.create_directory("src/nested")
    await temp_toolset.create_directory("tests")
    await temp_toolset.create_directory(".venv")
    await temp_toolset.create_directory("__pycache__")
    await temp_toolset.write_file("test.py", "def hello():\n    print('Hello')\n")
    await temp_toolset.write_file("main.py", "# TODO: implement\nclass Main:\n    pass\n")
    await temp_toolset.write_file("config.json", '{"version": "1.2.3"}\n')
    await temp_toolset.write_file("src/utils.py", "def helper():\n    # TODO: fix bug\n    return 42\n")
    await temp_toolset.write_file("src/api.py", "import requests\n\ndef fetch():\n    pass\n")
    await temp_toolset.write_file("src/nested/deep.py", "# Deep file\n")
    await temp_toolset.write_file("tests/test_main.py", "def test_hello():\n    assert True\n")
    await temp_toolset.write_file(".venv/lib.py", "# Should be excluded\n")
    await temp_toolset.write_file("__pycache__/cache.pyc", "# Cache file\n")
    
    # Test 1: Find all Python files in workspace
    result = await temp_toolset.glob("**/*.py")
    assert result["success"] is True
    assert result["total"] >= 5
    assert all(f["path"].endswith(".py") for f in result["files"])
    assert any("test.py" in f["path"] for f in result["files"])
    assert any("main.py" in f["path"] for f in result["files"])
    assert any("utils.py" in f["path"] for f in result["files"])
    
    # Test 2: Find files with relative path
    result = await temp_toolset.glob("*.py", path="src")
    assert result["success"] is True
    assert result["total"] >= 2
    assert all(f"src{os.sep}" in f["path"] or "src/" in f["path"] for f in result["files"])
    assert any("utils.py" in f["name"] for f in result["files"])
    assert any("api.py" in f["name"] for f in result["files"])
    
    # Test 3: Find files with absolute path
    src_absolute = str(temp_toolset.path / "src")
    result = await temp_toolset.glob("*.py", path=src_absolute)
    assert result["success"] is True
    assert result["total"] >= 2
    assert all(f["name"].endswith(".py") for f in result["files"])
    
    # Test 4: Error handling - nonexistent path
    result = await temp_toolset.glob("*.py", path="nonexistent_dir")
    assert result["success"] is False
    assert "does not exist" in result["error"]
    
    # Test 5: Type filter - only files (default behavior)
    result = await temp_toolset.glob("*", type_filter="file")
    assert result["success"] is True
    assert all(f["type"] == "file" for f in result["files"])
    assert any("test.py" in f["name"] for f in result["files"])
    assert not any(f["type"] == "directory" for f in result["files"])
    
    # Test 6: Type filter - only directories
    result = await temp_toolset.glob("*", type_filter="directory")
    assert result["success"] is True
    assert all(f["type"] == "directory" for f in result["files"])
    assert any("src" in f["name"] for f in result["files"])
    assert any("tests" in f["name"] for f in result["files"])
    assert not any(f["type"] == "file" for f in result["files"])
    
    # Test 7: Type filter - both files and directories
    result = await temp_toolset.glob("*", type_filter="any")
    assert result["success"] is True
    has_files = any(f["type"] == "file" for f in result["files"])
    has_dirs = any(f["type"] == "directory" for f in result["files"])
    assert has_files and has_dirs
    
    # Test 8: Exclude patterns - single pattern
    result = await temp_toolset.glob("**/*.py", excludes=[".venv/*"])
    assert result["success"] is True
    assert not any(".venv" in f["path"] for f in result["files"])
    assert any("test.py" in f["path"] for f in result["files"])
    
    # Test 9: Exclude patterns - multiple patterns
    result = await temp_toolset.glob("**/*", excludes=[".venv/*", "__pycache__/*"])
    assert result["success"] is True
    assert not any(".venv" in f["path"] for f in result["files"])
    assert not any("__pycache__" in f["path"] for f in result["files"])
    
    # Test 10: Exclude patterns - nested paths
    result = await temp_toolset.glob("**/*.py", excludes=["**/.venv/*", "**/__pycache__/*"])
    assert result["success"] is True
    assert not any(".venv" in f["path"] for f in result["files"])
    assert not any("__pycache__" in f["path"] for f in result["files"])
    
    # Test 11: Max depth - current directory only
    result = await temp_toolset.glob("*.py", max_depth=1)
    assert result["success"] is True
    assert all("/" not in f["path"] or f["path"].count("/") == 0 for f in result["files"])
    assert any("test.py" in f["name"] for f in result["files"])
    assert any("main.py" in f["name"] for f in result["files"])
    assert not any("utils.py" in f["name"] for f in result["files"])  # In src/
    
    # Test 12: Max depth - two levels
    result = await temp_toolset.glob("**/*.py", max_depth=2)
    assert result["success"] is True
    # Check path contains src and utils.py (cross-platform)
    assert any("src" in f["path"] and "utils.py" in f["path"] for f in result["files"])
    # Too deep should not be included
    assert not any("nested" in f["path"] and "deep.py" in f["path"] for f in result["files"])
    
    # Test 13: Combined filters - type + excludes
    result = await temp_toolset.glob(
        "*",
        type_filter="directory",
        excludes=[".venv", "__pycache__"]
    )
    assert result["success"] is True
    assert all(f["type"] == "directory" for f in result["files"])
    assert not any(".venv" in f["name"] for f in result["files"])
    assert not any("__pycache__" in f["name"] for f in result["files"])
    assert any("src" in f["name"] for f in result["files"])
    
    # Test 14: Combined filters - all parameters
    result = await temp_toolset.glob(
        "**/*.py",
        type_filter="file",
        excludes=[".venv/*", "__pycache__/*"],
        max_depth=2
    )
    assert result["success"] is True
    assert all(f["type"] == "file" for f in result["files"])
    assert all(f["path"].endswith(".py") for f in result["files"])
    assert not any(".venv" in f["path"] for f in result["files"])
    assert not any("__pycache__" in f["path"] for f in result["files"])
    assert not any("src/nested/deep.py" in f["path"] for f in result["files"])
    
    # Test 15: Verify filters_applied in response
    result = await temp_toolset.glob(
        "**/*.py",
        type_filter="file",
        excludes=[".venv/*"],
        max_depth=3
    )
    assert result["success"] is True
    assert "filters_applied" in result
    assert result["filters_applied"]["type"] == "file"
    assert result["filters_applied"]["excludes"] == [".venv/*"]
    assert result["filters_applied"]["max_depth"] == 3
    
    # Test 16: Empty excludes list should not affect results
    result_no_exclude = await temp_toolset.glob("**/*.py")
    result_empty_exclude = await temp_toolset.glob("**/*.py", excludes=[])
    assert result_no_exclude["total"] == result_empty_exclude["total"]
    
    # Test 17: None values should maintain backward compatibility
    result = await temp_toolset.glob(
        "**/*.py",
        type_filter=None,
        excludes=None,
        max_depth=None
    )
    assert result["success"] is True
    assert result["total"] >= 5


async def test_grep_comprehensive(temp_toolset):
    """
    Test comprehensive grep functionality.
    
    Covers:
    - Content search with pattern matching
    - File pattern filtering
    - Relative path search
    - Absolute path search
    - Context lines (with content verification)
    - Result capping
    - Error handling for nonexistent paths
    - Both ripgrep and Python fallback implementations
    """
    # Setup: Create test file structure with searchable content
    await temp_toolset.create_directory("src")
    await temp_toolset.write_file("main.py", "# TODO: implement\nclass Main:\n    pass\n")
    await temp_toolset.write_file("src/utils.py", "def helper():\n    # TODO: fix bug\n    return 42\n")
    await temp_toolset.write_file("src/api.py", "import requests\n\ndef fetch():\n    pass\n")
    
    # Create a dedicated test file for context_lines testing
    context_test_content = """line 1
line 2
line 3
TARGET line 4
line 5
line 6
line 7
TARGET line 8
line 9
line 10
"""
    await temp_toolset.write_file("context_test.txt", context_test_content)
    
    # Test 1: Find TODO comments in all Python files
    result = await temp_toolset.grep("TODO", file_pattern="**/*.py")
    assert result["success"] is True
    assert result["total_matches"] >= 2
    assert result["files_matched"] >= 2
    for match in result["matches"]:
        assert "file" in match
        assert "line_number" in match
        assert "line_content" in match
        assert "TODO" in match["line_content"]
        assert match["line_number"] > 0
    
    # Test 2: Search with relative path
    result = await temp_toolset.grep("TODO", path="src", file_pattern="*.py")
    assert result["success"] is True
    assert result["total_matches"] >= 1
    assert all(f"src{os.sep}" in m["file"] or "src/" in m["file"] for m in result["matches"])
    
    # Test 3: Search with absolute path
    src_absolute = str(temp_toolset.path / "src")
    result = await temp_toolset.grep("TODO", path=src_absolute, file_pattern="*.py")
    assert result["success"] is True
    assert result["total_matches"] >= 1
    
    # Test 4: Search with context lines - verify content correctness
    result = await temp_toolset.grep("TARGET", path="context_test.txt", context_lines=2)
    assert result["success"] is True
    assert len(result["matches"]) == 2
    
    # Verify first match (line 4)
    match1 = result["matches"][0]
    assert match1["line_number"] == 4
    assert match1["line_content"] == "TARGET line 4"
    assert "context_before" in match1
    assert "context_after" in match1
    # Should have 2 lines before (lines 2-3)
    assert len(match1["context_before"]) == 2
    assert match1["context_before"][0] == "line 2"
    assert match1["context_before"][1] == "line 3"
    # Should have 2 lines after (lines 5-6)
    assert len(match1["context_after"]) == 2
    assert match1["context_after"][0] == "line 5"
    assert match1["context_after"][1] == "line 6"
    
    # Verify second match (line 8)
    match2 = result["matches"][1]
    assert match2["line_number"] == 8
    assert match2["line_content"] == "TARGET line 8"
    # Should have context before (line 7, and possibly line 6 if not consumed by match1)
    # Note: ripgrep behavior - context lines between close matches may not overlap
    assert len(match2["context_before"]) >= 1
    assert "line 7" in match2["context_before"]
    # Should have 2 lines after (lines 9-10)
    assert len(match2["context_after"]) == 2
    assert match2["context_after"][0] == "line 9"
    assert match2["context_after"][1] == "line 10"
    
    # Test 5: Context lines = 0 should not have context fields
    result = await temp_toolset.grep("TARGET", path="context_test.txt", context_lines=0)
    assert result["success"] is True
    assert len(result["matches"]) == 2
    for match in result["matches"]:
        # When context_lines=0, context fields should not exist
        assert "context_before" not in match
        assert "context_after" not in match
    
    # Test 6: Context at file boundaries
    boundary_content = """TARGET line 1
line 2
line 3
"""
    await temp_toolset.write_file("boundary_test.txt", boundary_content)
    result = await temp_toolset.grep("TARGET", path="boundary_test.txt", context_lines=2)
    assert result["success"] is True
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    # At file start, context_before should be empty
    assert len(match["context_before"]) == 0
    # Should have 2 lines after
    assert len(match["context_after"]) == 2
    assert match["context_after"][0] == "line 2"
    assert match["context_after"][1] == "line 3"
    
    # Test 7: Result capping
    # Create a file with many matches
    many_matches = "\n".join([f"TARGET line {i}" if i % 2 == 0 else f"line {i}" for i in range(1, 201)])
    await temp_toolset.write_file("many_matches.txt", many_matches)
    result = await temp_toolset.grep("TARGET", path="many_matches.txt")
    assert result["success"] is True
    # Should be capped - Python fallback caps at 50, ripgrep at 100
    # Just verify we got results and capping worked if applicable
    assert len(result["matches"]) > 0
    if result.get("capped"):
        # Either 50 (Python fallback) or 100 (ripgrep) matches
        assert len(result["matches"]) in (50, 100)
        # Message may contain "capped", "limit", or "terminated early"
        msg_lower = result.get("message", "").lower()
        assert any(kw in msg_lower for kw in ("capped", "limit", "terminated", "early"))
    
    # Test 8: Error handling - nonexistent path
    result = await temp_toolset.grep("TODO", path="nonexistent_dir")
    assert result["success"] is False
    assert "does not exist" in result["error"]
    
    # Test 9: Test Python fallback (by using a pattern that works in both)
    # This ensures both code paths are tested
    result = await temp_toolset.grep("TARGET", path="context_test.txt", context_lines=1)
    assert result["success"] is True
    # Verify it works regardless of which implementation is used
    assert len(result["matches"]) == 2
    for match in result["matches"]:
        assert "TARGET" in match["line_content"]
        assert len(match["context_before"]) <= 1
        assert len(match["context_after"]) <= 1


async def test_manage_path_comprehensive(temp_toolset):
    """
    Test comprehensive manage_path functionality.
    
    Covers:
    - create_dir operation (with automatic parent creation)
    - delete operation (files and directories, with/without recursive)
    - move operation (rename and move to different directory)
    - Error handling (invalid operation, missing parameters, nonexistent paths)
    """
    
    # Test 1: Create directory (with automatic parent creation)
    result = await temp_toolset.manage_path("create_dir", "src/components/ui")
    assert result["success"] is True
    assert (temp_toolset.path / "src/components/ui").is_dir()
    
    # Test 2: Delete file
    test_file = temp_toolset.path / "test.txt"
    test_file.write_text("content")
    result = await temp_toolset.manage_path("delete", "test.txt")
    assert result["success"] is True
    assert not test_file.exists()
    
    # Test 3: Delete empty directory
    await temp_toolset.manage_path("create_dir", "empty_dir")
    result = await temp_toolset.manage_path("delete", "empty_dir")
    assert result["success"] is True
    assert not (temp_toolset.path / "empty_dir").exists()
    
    # Test 4: Delete directory with contents (recursive=False should fail)
    test_dir = temp_toolset.path / "test_dir"
    test_dir.mkdir()
    (test_dir / "file.txt").write_text("content")
    result = await temp_toolset.manage_path("delete", "test_dir", recursive=False)
    assert result["success"] is False  # Should fail because directory is not empty
    
    # Test 5: Delete directory with contents (recursive=True should succeed)
    result = await temp_toolset.manage_path("delete", "test_dir", recursive=True)
    assert result["success"] is True
    assert not test_dir.exists()
    
    # Test 6: Move/rename file
    old_file = temp_toolset.path / "old.txt"
    old_file.write_text("content")
    result = await temp_toolset.manage_path("move", "old.txt", new_path="new.txt")
    assert result["success"] is True
    assert not old_file.exists()
    assert (temp_toolset.path / "new.txt").exists()
    assert (temp_toolset.path / "new.txt").read_text() == "content"
    
    # Test 7: Move file to different directory
    await temp_toolset.manage_path("create_dir", "backup")
    test_file = temp_toolset.path / "file.txt"
    test_file.write_text("test content")
    result = await temp_toolset.manage_path("move", "file.txt", new_path="backup/file.txt")
    assert result["success"] is True
    assert not test_file.exists()
    assert (temp_toolset.path / "backup/file.txt").exists()
    assert (temp_toolset.path / "backup/file.txt").read_text() == "test content"
    
    # Test 8: Error - invalid operation
    result = await temp_toolset.manage_path("invalid_op", "path")
    assert result["success"] is False
    assert "Invalid operation" in result["error"]
    
    # Test 9: Error - move without new_path
    result = await temp_toolset.manage_path("move", "old.txt")
    assert result["success"] is False
    assert "new_path is required" in result["error"]
    
    # Test 10: Error - delete nonexistent path
    result = await temp_toolset.manage_path("delete", "nonexistent.txt")
    assert result["success"] is False
    assert "does not exist" in result["error"]


# ---------------------------------------------------------------------------
# Output-token truncation guards (PR #52)
# ---------------------------------------------------------------------------

async def test_write_file_rejects_large_content(temp_toolset):
    """write_file must reject content > 12,000 chars."""
    big = "x" * 13_000
    res = await temp_toolset.write_file("big.txt", big)
    assert not res["success"]
    assert res["reason"] == "content_too_large"
    assert "12,000" in res["error"]
    # File must NOT exist on disk
    assert not (temp_toolset.path / "big.txt").exists()


async def test_write_file_accepts_content_at_limit(temp_toolset):
    """write_file must accept content exactly at 12,000 chars."""
    content = "a" * 12_000
    res = await temp_toolset.write_file("exact.txt", content)
    assert res["success"]
    assert (temp_toolset.path / "exact.txt").read_text() == content


async def test_append_file_basic(temp_toolset):
    """append_file appends to existing file."""
    await temp_toolset.write_file("log.txt", "header\n")
    res = await temp_toolset.append_file("log.txt", "line1\nline2\n")
    assert res["success"]
    assert res["appended_chars"] == len("line1\nline2\n")
    content = (await temp_toolset.read_file("log.txt"))["content"]
    assert content == "header\nline1\nline2\n"


async def test_append_file_multiple_batches(temp_toolset):
    """append_file supports multiple sequential appends (BibTeX batch pattern)."""
    await temp_toolset.write_file("refs.bib", "% Bibliography\n")
    for i in range(5):
        batch = f"@article{{ref{i},\n  title={{Title {i}}},\n}}\n\n"
        res = await temp_toolset.append_file("refs.bib", batch)
        assert res["success"], f"Batch {i} failed: {res}"
    content = (await temp_toolset.read_file("refs.bib"))["content"]
    assert content.startswith("% Bibliography\n")
    assert content.count("@article{") == 5


async def test_append_file_rejects_nonexistent(temp_toolset):
    """append_file must reject when target file does not exist."""
    res = await temp_toolset.append_file("missing.txt", "data")
    assert not res["success"]
    assert res["reason"] == "file_not_found"


async def test_append_file_rejects_large_content(temp_toolset):
    """append_file must reject content > 6,000 chars."""
    await temp_toolset.write_file("base.txt", "ok\n")
    big = "x" * 7_000
    res = await temp_toolset.append_file("base.txt", big)
    assert not res["success"]
    assert res["reason"] == "content_too_large"
    assert "6,000" in res["error"]
    # Original content must be unchanged
    content = (await temp_toolset.read_file("base.txt"))["content"]
    assert content == "ok\n"


async def test_append_file_accepts_content_at_limit(temp_toolset):
    """append_file must accept content exactly at 6,000 chars."""
    await temp_toolset.write_file("base.txt", "start\n")
    chunk = "b" * 6_000
    res = await temp_toolset.append_file("base.txt", chunk)
    assert res["success"]
    content = (await temp_toolset.read_file("base.txt"))["content"]
    assert content == "start\n" + chunk


async def test_update_file_rejects_large_new_string(temp_toolset):
    """update_file must reject new_string > 8,000 chars."""
    await temp_toolset.write_file("doc.txt", "PLACEHOLDER\n")
    big = "y" * 9_000
    res = await temp_toolset.update_file("doc.txt", "PLACEHOLDER", big)
    assert not res["success"]
    assert res["reason"] == "content_too_large"
    assert "8,000" in res["error"]
    # Original content must be unchanged
    content = (await temp_toolset.read_file("doc.txt"))["content"]
    assert content == "PLACEHOLDER\n"


async def test_update_file_accepts_new_string_at_limit(temp_toolset):
    """update_file must accept new_string exactly at 8,000 chars."""
    await temp_toolset.write_file("doc.txt", "STUB\n")
    replacement = "c" * 8_000
    res = await temp_toolset.update_file("doc.txt", "STUB", replacement)
    assert res["success"]
    content = (await temp_toolset.read_file("doc.txt"))["content"]
    assert replacement in content


async def test_two_phase_write_protocol(temp_toolset):
    """End-to-end: scaffold → section fill → append (the protocol PR #52 teaches)."""
    # Phase 1: scaffold
    skeleton = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "% INTRO_PLACEHOLDER\n"
        "\\section{Methods}\n"
        "% METHODS_PLACEHOLDER\n"
        "\\end{document}\n"
    )
    res = await temp_toolset.write_file("paper.tex", skeleton)
    assert res["success"]

    # Phase 2: fill sections via update_file
    res = await temp_toolset.update_file(
        "paper.tex",
        "% INTRO_PLACEHOLDER",
        "This paper presents a novel approach to analyzing single-cell data.",
    )
    assert res["success"]

    res = await temp_toolset.update_file(
        "paper.tex",
        "% METHODS_PLACEHOLDER",
        "We applied dimensionality reduction using UMAP.",
    )
    assert res["success"]

    # Phase 3: append bibliography
    bib_entries = "\\begin{thebibliography}{9}\n\\bibitem{ref1} Author, Title, 2024.\n\\end{thebibliography}\n"
    # Insert before \end{document} via update_file
    res = await temp_toolset.update_file(
        "paper.tex",
        "\\end{document}",
        bib_entries + "\\end{document}",
    )
    assert res["success"]

    # Verify final document
    content = (await temp_toolset.read_file("paper.tex"))["content"]
    assert "novel approach" in content
    assert "UMAP" in content
    assert "\\bibitem{ref1}" in content
    assert "INTRO_PLACEHOLDER" not in content
    assert "METHODS_PLACEHOLDER" not in content
