import asyncio
import tempfile
import os
from pathlib import Path
from pantheon.apps.builtin.file.file_manager import FileManagerToolSet


async def test_apply_patch():
    with tempfile.TemporaryDirectory() as tmpdir:
        fm = FileManagerToolSet("test", path=tmpdir)

        print("=" * 60)
        print("Testing Multi-file Apply Patch")
        print("=" * 60)

        # Test 1: Single file unified diff (with file_path)
        print("\n📝 Test 1: Single file unified diff with file_path")
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write('def hello():\n    return "Hello"\n')

        unified_patch = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    return "Hello"
+    return "Hello, World!"
"""
        result = await fm.apply_patch(unified_patch, file_path="test.py")
        print(f"  Result: {result}")
        with open(test_file, "r") as f:
            content = f.read()
        assert result["success"] is True
        assert result["summary"]["modified"] == 1
        assert "Hello, World!" in content
        print("  ✅ PASSED!")

        # Test 2: Multi-file unified diff (auto-detect files)
        print("\n📝 Test 2: Multi-file unified diff")
        file1 = os.path.join(tmpdir, "file1.py")
        file2 = os.path.join(tmpdir, "file2.py")
        with open(file1, "w") as f:
            f.write("# File 1\nold_value = 1\n")
        with open(file2, "w") as f:
            f.write("# File 2\nold_value = 2\n")

        multi_patch = """--- a/file1.py
+++ b/file1.py
@@ -1,2 +1,2 @@
 # File 1
-old_value = 1
+new_value = 100

--- a/file2.py
+++ b/file2.py
@@ -1,2 +1,2 @@
 # File 2
-old_value = 2
+new_value = 200
"""
        result = await fm.apply_patch(multi_patch)
        print(f"  Result: {result}")
        with open(file1, "r") as f:
            c1 = f.read()
        with open(file2, "r") as f:
            c2 = f.read()
        assert result["success"] is True
        assert result["summary"]["total_files"] == 2
        assert result["summary"]["modified"] == 2
        assert "new_value = 100" in c1 and "new_value = 200" in c2
        print("  ✅ PASSED!")

        # Test 3: V4A/Codex format
        print("\n📝 Test 3: V4A/Codex format")
        file3 = os.path.join(tmpdir, "codex.py")
        with open(file3, "w") as f:
            f.write("def foo():\n    pass\n")

        v4a_patch = """*** Begin Patch
*** Update File: codex.py
@@ function foo @@
- pass
+ return 42
*** End Patch
"""
        result = await fm.apply_patch(v4a_patch)
        print(f"  Result: {result}")
        with open(file3, "r") as f:
            c3 = f.read()
        assert result["success"] is True
        assert result["summary"]["modified"] == 1
        assert "return 42" in c3
        print("  ✅ PASSED!")

        # Test 4: V4A Create File
        print("\n📝 Test 4: V4A Create File")
        new_file_path = os.path.join(tmpdir, "new_file.py")
        v4a_create = """*** Begin Patch
*** Create File: new_file.py
+ # New file created by patch
+ def new_function():
+     return "created"
*** End Patch
"""
        result = await fm.apply_patch(v4a_create)
        print(f"  Result: {result}")
        assert result["success"] is True
        assert result["summary"]["created"] == 1
        assert os.path.exists(new_file_path)
        with open(new_file_path, "r") as f:
            content = f.read()
        assert "new_function" in content
        print("  ✅ PASSED!")

        # Test 5: V4A Delete File
        print("\n📝 Test 5: V4A Delete File")
        to_delete = os.path.join(tmpdir, "to_delete.py")
        with open(to_delete, "w") as f:
            f.write("# This will be deleted\n")

        v4a_delete = """*** Begin Patch
*** Delete File: to_delete.py
*** End Patch
"""
        result = await fm.apply_patch(v4a_delete)
        print(f"  Result: {result}")
        assert result["success"] is True
        assert result["summary"]["deleted"] == 1
        assert not os.path.exists(to_delete)
        print("  ✅ PASSED!")

        # Test 6: Multi-operation V4A patch
        print("\n📝 Test 6: Multi-operation V4A patch (update + create)")
        update_file = os.path.join(tmpdir, "update_me.py")
        with open(update_file, "w") as f:
            f.write("x = 1\n")

        multi_v4a = """*** Begin Patch
*** Update File: update_me.py
@@ @@
- x = 1
+ x = 999

*** Create File: brand_new.py
+ # Brand new file
+ y = 42
*** End Patch
"""
        result = await fm.apply_patch(multi_v4a)
        print(f"  Result: {result}")

        with open(update_file, "r") as f:
            updated = f.read()
        brand_new = os.path.join(tmpdir, "brand_new.py")
        
        assert result["success"] is True
        assert result["summary"]["total_files"] == 2
        assert result["summary"]["modified"] == 1
        assert result["summary"]["created"] == 1
        assert "x = 999" in updated
        assert os.path.exists(brand_new)
        with open(brand_new, "r") as f:
            assert "y = 42" in f.read()
        print("  ✅ PASSED!")

        print("\n" + "=" * 60)
        print("Testing Error Handling & Edge Cases")
        print("=" * 60)

        # Test 7: Error - File not exists
        print("\n📝 Test 7: Error - File does not exist")
        patch_nonexist = """--- a/nonexistent.py
+++ b/nonexistent.py
@@ -1,2 +1,2 @@
 def foo():
-    old line
+    new line
"""
        result = await fm.apply_patch(patch_nonexist)
        print(f"  Result: {result}")
        assert result["success"] is False
        assert result["summary"]["failed"] == 1
        assert len(result["failed_files"]) == 1
        assert "does not exist" in result["files"][0]["error"].lower()
        print("  ✅ PASSED!")

        # Test 8: Error - Content mismatch (no fuzzy matching)
        print("\n📝 Test 8: Error - Content mismatch with exact matching")
        mismatch_file = os.path.join(tmpdir, "mismatch.py")
        with open(mismatch_file, "w") as f:
            f.write("def bar():\n    actual_content = 'different'\n")

        mismatch_patch = """--- a/mismatch.py
+++ b/mismatch.py
@@ -1,2 +1,2 @@
 def bar():
-    expected_content = 'original'
+    new_content = 'updated'
"""
        result = await fm.apply_patch(mismatch_patch, fuzzy_threshold=0.0)
        print(f"  Result: {result}")
        assert result["success"] is False
        assert result["files"][0]["hunks_applied"] == 0
        assert "No hunks applied" in result["files"][0]["error"]
        print("  ✅ PASSED!")

        # Test 9: Fuzzy matching - whitespace tolerance
        print("\n📝 Test 9: Fuzzy matching with whitespace differences")
        fuzzy_file = os.path.join(tmpdir, "fuzzy.py")
        # Original has extra spaces
        with open(fuzzy_file, "w") as f:
            f.write("def process():  \n    value = 10  \n    return value\n")

        # Patch expects no trailing spaces
        fuzzy_patch = """--- a/fuzzy.py
+++ b/fuzzy.py
@@ -1,3 +1,3 @@
 def process():
-    value = 10
+    value = 20
     return value
"""
        # Should work with fuzzy matching
        result = await fm.apply_patch(fuzzy_patch, fuzzy_threshold=0.7)
        print(f"  Result: {result}")
        assert result["success"] is True
        assert result["files"][0]["hunks_applied"] >= 1
        with open(fuzzy_file, "r") as f:
            content = f.read()
        assert "value = 20" in content
        print("  ✅ PASSED!")

        # Test 10: Partial failure - multi-file scenario
        print("\n📝 Test 10: Partial failure in multi-file patch")
        ok_file = os.path.join(tmpdir, "ok.py")
        with open(ok_file, "w") as f:
            f.write("x = 1\n")

        partial_patch = """--- a/ok.py
+++ b/ok.py
@@ -1 +1 @@
-x = 1
+x = 100

--- a/missing.py
+++ b/missing.py
@@ -1 +1 @@
-old
+new
"""
        result = await fm.apply_patch(partial_patch)
        print(f"  Result: {result}")
        assert result["success"] is False  # Overall failure
        assert len(result["files"]) == 2  # Two operations were attempted
        assert result["summary"]["modified"] == 1  # One succeeded
        assert result["summary"]["failed"] == 1
        assert len(result["failed_files"]) == 1
        assert "missing.py" in result["failed_files"][0]
        # Verify successful file was updated
        with open(ok_file, "r") as f:
            assert "x = 100" in f.read()
        print("  ✅ PASSED!")

        # Test 11: Return value validation - hunks and exact_match
        print("\n📝 Test 11: Return value fields validation")
        validate_file = os.path.join(tmpdir, "validate.py")
        with open(validate_file, "w") as f:
            f.write("a = 1\nb = 2\n")

        validate_patch = """--- a/validate.py
+++ b/validate.py
@@ -1,2 +1,2 @@
-a = 1
+a = 10
 b = 2
"""
        result = await fm.apply_patch(validate_patch)
        print(f"  Result: {result}")
        assert result["success"] is True
        file_result = result["files"][0]
        assert "hunks_applied" in file_result
        assert "hunks_total" in file_result
        assert "exact_match" in file_result
        assert file_result["hunks_applied"] == file_result["hunks_total"]
        assert file_result["exact_match"] is True
        print("  ✅ PASSED!")

        # Test 12: Unified diff - create file via /dev/null
        print("\n📝 Test 12: Unified diff - create file (--- /dev/null)")
        create_via_null = """--- /dev/null
+++ b/created_via_null.py
@@ -0,0 +1,3 @@
+# Created via unified diff
+def created():
+    pass
"""
        result = await fm.apply_patch(create_via_null)
        print(f"  Result: {result}")
        assert result["success"] is True
        assert result["summary"]["created"] == 1
        created_path = os.path.join(tmpdir, "created_via_null.py")
        assert os.path.exists(created_path)
        with open(created_path, "r") as f:
            content = f.read()
        assert "def created():" in content
        print("  ✅ PASSED!")

        # Test 13: Multiple hunks in single file
        print("\n📝 Test 13: Multiple hunks in single file")
        multi_hunk_file = os.path.join(tmpdir, "multi_hunk.py")
        with open(multi_hunk_file, "w") as f:
            f.write("# Header\nimport os\n\ndef func1():\n    pass\n\ndef func2():\n    pass\n")

        multi_hunk_patch = """--- a/multi_hunk.py
+++ b/multi_hunk.py
@@ -1,2 +1,2 @@
 # Header
-import os
+import sys

@@ -4,3 +4,3 @@
 def func1():
-    pass
+    return 1
"""
        result = await fm.apply_patch(multi_hunk_patch)
        print(f"  Result: {result}")
        assert result["success"] is True
        assert result["files"][0]["hunks_total"] == 2
        assert result["files"][0]["hunks_applied"] == 2
        with open(multi_hunk_file, "r") as f:
            content = f.read()
        assert "import sys" in content
        assert "return 1" in content
        print("  ✅ PASSED!")

        # Test 14: Empty/invalid patch
        print("\n📝 Test 14: Error - Invalid/empty patch")
        result = await fm.apply_patch("")
        print(f"  Result: {result}")
        assert result["success"] is False
        assert "error" in result
        print("  ✅ PASSED!")

        # Test 15: V4A - Mixed operations (update + create + delete)
        print("\n📝 Test 15: V4A - All operations in one patch")
        v4a_update_target = os.path.join(tmpdir, "v4a_update.py")
        v4a_delete_target = os.path.join(tmpdir, "v4a_delete.py")
        with open(v4a_update_target, "w") as f:
            f.write("old = 1\n")
        with open(v4a_delete_target, "w") as f:
            f.write("to_be_deleted\n")

        v4a_all_ops = """*** Begin Patch
*** Update File: v4a_update.py
- old = 1
+ new = 2

*** Create File: v4a_create.py
+ created = True

*** Delete File: v4a_delete.py
*** End Patch
"""
        result = await fm.apply_patch(v4a_all_ops)
        print(f"  Result: {result}")
        assert result["success"] is True
        assert result["summary"]["total_files"] == 3
        assert result["summary"]["modified"] == 1
        assert result["summary"]["created"] == 1
        assert result["summary"]["deleted"] == 1
        
        # Verify operations
        with open(v4a_update_target, "r") as f:
            assert "new = 2" in f.read()
        assert os.path.exists(os.path.join(tmpdir, "v4a_create.py"))
        assert not os.path.exists(v4a_delete_target)
        print("  ✅ PASSED!")

        print("\n" + "=" * 60)
        print("🎉 All 15 tests completed successfully!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_apply_patch())
