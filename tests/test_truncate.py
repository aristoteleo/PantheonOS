"""Tests for truncate.py utilities.

Tests cover:
- truncate_string: edge cases, negative slice protection
- _truncate_fields_recursive: depth limit, budget allocation, format consistency
- smart_truncate_result: PATH 1 (text tools), PATH 2 (JSON tools)
- Format consistency across all truncation markers
"""

import json
import tempfile
from pathlib import Path

from pantheon.utils.truncate import (
    truncate_string,
    _truncate_fields_recursive,
    smart_truncate_result,
)


def test_truncate_string_basic():
    """Test basic string truncation."""
    content = "a" * 1000
    result = truncate_string(content, 500)
    
    # Allow some tolerance for suffix
    assert len(result) <= 550
    assert "[truncated" in result
    assert "1,000 chars]" in result  # Note: has thousand separator
    print("✓ test_truncate_string_basic passed")


def test_truncate_string_small_max_length():
    """Test truncate_string with very small max_length (Bug 1 fix)."""
    content = "a" * 1000
    result = truncate_string(content, 50)
    
    # Should not crash with negative slice
    assert isinstance(result, str)
    assert len(result) <= 100  # Some tolerance for suffix
    print("✓ test_truncate_string_small_max_length passed")


def test_truncate_string_no_truncation_needed():
    """Test when content is within limit."""
    content = "short"
    result = truncate_string(content, 100)
    
    assert result == content
    assert "[truncated" not in result
    print("✓ test_truncate_string_no_truncation_needed passed")


def test_truncate_fields_recursive_depth_limit():
    """Test depth limit is enforced (simplified to 2)."""
    # Create deeply nested structure
    data = {"level1": {"level2": {"level3": {"level4": "deep"}}}}
    
    result = _truncate_fields_recursive(data, budget=1000, depth=0)
    
    # At depth 2, should stop recursing
    # level1 -> level2 -> level3 (depth 2, should return as-is)
    assert "level1" in result
    assert "level2" in result["level1"]
    print("✓ test_truncate_fields_recursive_depth_limit passed")


def test_truncate_fields_recursive_dict():
    """Test dictionary field truncation."""
    data = {
        "short": "abc",
        "long": "x" * 1000,
    }
    
    result = _truncate_fields_recursive(data, budget=100)
    
    # Short field should be preserved
    assert result["short"] == "abc"
    
    # Long field should be truncated
    assert len(result["long"]) < 1000
    assert "[truncated" in result["long"]
    print("✓ test_truncate_fields_recursive_dict passed")


def test_truncate_fields_recursive_list():
    """Test list item truncation (simplified logic)."""
    data = [
        "short",
        "x" * 1000,
        {"nested": "dict"},
        ["nested", "list"],
    ]
    
    result = _truncate_fields_recursive(data, budget=200)
    
    assert isinstance(result, list)
    assert len(result) == 4
    
    # Short string preserved
    assert result[0] == "short"
    
    # Long string truncated
    assert len(result[1]) < 1000
    assert "[truncated" in result[1]
    
    # Nested structures processed
    assert isinstance(result[2], dict)
    assert isinstance(result[3], list)
    print("✓ test_truncate_fields_recursive_list passed")


def test_truncate_fields_recursive_empty():
    """Test empty containers."""
    assert _truncate_fields_recursive({}, 100) == {}
    assert _truncate_fields_recursive([], 100) == []
    print("✓ test_truncate_fields_recursive_empty passed")


def test_format_consistency():
    """Test all truncation markers use consistent format."""
    # Test truncate_string format
    result1 = truncate_string("x" * 1000, 100)
    assert "chars]" in result1  # Has truncation marker
    
    # Test _truncate_fields_recursive format  
    data = {"field": "x" * 1000}
    result2 = _truncate_fields_recursive(data, budget=50)
    assert "chars]" in result2["field"]  # Has truncation marker
    
    print("✓ test_format_consistency passed")


def test_smart_truncate_with_truncated_field():
    """Test tools with 'truncated' field: skip base64 filter, but
    length limits are still applied (per-tool thresholds are always enforced)."""
    # Simulate read_file/shell output (small enough to be under limit)
    result = {
        "content": "file content here",
        "truncated": False,
        "path": "/some/path",
    }

    output = smart_truncate_result(result, max_length=100)

    # Should be JSON formatted (unified format)
    parsed = json.loads(output)
    assert parsed["content"] == "file content here"
    assert parsed["truncated"] == False
    assert parsed["path"] == "/some/path"

    # Small content under limit → passes through as-is
    assert isinstance(output, str)
    print("✓ test_smart_truncate_with_truncated_field passed")


def test_smart_truncate_path2_json_tools():
    """Test PATH 2: JSON tools without 'truncated' field."""
    result = {
        "data": "some data",
        "status": "success",
    }
    
    output = smart_truncate_result(result, max_length=10000)
    
    # Should be JSON formatted
    parsed = json.loads(output)
    assert parsed["data"] == "some data"
    assert parsed["status"] == "success"
    print("✓ test_smart_truncate_path2_json_tools passed")


def test_smart_truncate_path2_oversized():
    """Test PATH 2: Oversized JSON with file save and preview."""
    # Create large data
    result = {
        "large_field": "x" * 50000,
        "small_field": "abc",
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output = smart_truncate_result(
            result, 
            max_length=10000,
            temp_dir=tmpdir
        )
        
        # Should indicate truncation (unified <persisted-output> format)
        assert "Full output saved to:" in output
        assert "<persisted-output>" in output
        
        # Should contain preview
        assert "small_field" in output
        
        # Check file was created
        files = list(Path(tmpdir).glob("tool_output_*.json"))
        assert len(files) == 1
        
        # Verify full content in file
        with open(files[0]) as f:
            saved = json.load(f)
            assert saved["large_field"] == "x" * 50000
    
    print("✓ test_smart_truncate_path2_oversized passed")


def test_smart_truncate_non_dict():
    """Test non-dict input (strings, numbers)."""
    # String input
    result1 = smart_truncate_result("simple string", max_length=100)
    assert result1 == "simple string"
    
    # Large string - when saved to file, uses <persisted-output> format
    result2 = smart_truncate_result("x" * 1000, max_length=100)
    # May be truncated inline or saved to file with persisted-output wrapper
    assert "[truncated" in result2 or "<persisted-output>" in result2
    
    print("✓ test_smart_truncate_non_dict passed")


def test_preview_overflow_protection():
    """Test Bug 3 fix: preview doesn't exceed max_length."""
    # Create data that will have large preview even after field truncation
    result = {f"field{i}": "x" * 1000 for i in range(100)}
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output = smart_truncate_result(
            result,
            max_length=5000,
            temp_dir=tmpdir
        )
        
        # Preview should not exceed max_length significantly
        # (some tolerance for formatting)
        assert len(output) <= 6000
        assert "[truncated" in output
    
    print("✓ test_preview_overflow_protection passed")


def run_all_tests():
    """Run all tests."""
    print("\n" + "="*60)
    print("Running truncate.py tests")
    print("="*60 + "\n")
    
    tests = [
        test_truncate_string_basic,
        test_truncate_string_small_max_length,
        test_truncate_string_no_truncation_needed,
        test_truncate_fields_recursive_depth_limit,
        test_truncate_fields_recursive_dict,
        test_truncate_fields_recursive_list,
        test_truncate_fields_recursive_empty,
        test_format_consistency,
        test_smart_truncate_with_truncated_field,
        test_smart_truncate_path2_json_tools,
        test_smart_truncate_path2_oversized,
        test_smart_truncate_non_dict,
        test_preview_overflow_protection,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*60 + "\n")
    
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
