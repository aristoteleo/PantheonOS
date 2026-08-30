package fmapp

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func write(t *testing.T, root, rel, content string) {
	t.Helper()
	p := filepath.Join(root, rel)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func ok(t *testing.T, res map[string]any) map[string]any {
	t.Helper()
	if s, _ := res["success"].(bool); !s {
		t.Fatalf("expected success: %v", res)
	}
	return res
}

func TestReadFileModes(t *testing.T) {
	root := t.TempDir()
	app := NewApp(root)
	write(t, root, "a.txt", "l1\nl2\nl3\nl4\n")

	res := ok(t, app.readFile("a.txt", 0, 0, 0, ""))
	if res["content"] != "l1\nl2\nl3\nl4\n" || res["total_lines"] != 4 || res["truncated"] != false {
		t.Fatalf("full read: %v", res)
	}
	res = ok(t, app.readFile("a.txt", 2, 3, 0, ""))
	if res["content"] != "l2\nl3\n" {
		t.Fatalf("range read: %v", res)
	}
	if res := app.readFile("a.txt", 9, 9, 0, ""); res["success"].(bool) {
		t.Fatalf("oob start_line should fail: %v", res)
	}
	if res := app.readFile("missing.txt", 0, 0, 0, ""); res["error"] != "File does not exist" {
		t.Fatalf("missing: %v", res)
	}
	if res := app.readFile("a.txt", 0, 0, 0, "MyClass"); res["error"] != "Code navigation requires tree-sitter" {
		t.Fatalf("symbol mode should refuse: %v", res)
	}
	// char-limit truncation
	res = ok(t, app.readFile("a.txt", 0, 0, 5, ""))
	if res["truncated"] != true || res["content"] != "l1\nl2" {
		t.Fatalf("max_chars: %v", res)
	}
}

func TestWriteUpdateFile(t *testing.T) {
	root := t.TempDir()
	app := NewApp(root)

	ok(t, app.writeFile("sub/new.txt", "hello\n", true, false))
	if res := app.writeFile("sub/new.txt", "x", false, false); res["reason"] != "overwrite_disabled" {
		t.Fatalf("overwrite=false: %v", res)
	}
	res := ok(t, app.writeFile("sub/new.txt", "more\n", true, true))
	if res["appended_chars"] != 5 {
		t.Fatalf("append: %v", res)
	}
	if res := app.writeFile("nope.txt", "x", true, true); res["reason"] != "file_not_found" {
		t.Fatalf("append missing: %v", res)
	}

	res = ok(t, app.updateFile("sub/new.txt", "hello", "goodbye", false, 0, 0))
	if res["replacements"] != 1 {
		t.Fatalf("update: %v", res)
	}
	write(t, root, "multi.txt", "x\nx\n")
	if res := app.updateFile("multi.txt", "x", "y", false, 0, 0); res["success"].(bool) {
		t.Fatalf("ambiguous update must fail: %v", res)
	}
	res = ok(t, app.updateFile("multi.txt", "x", "y", true, 0, 0))
	if res["replacements"] != 2 {
		t.Fatalf("replace_all: %v", res)
	}
	write(t, root, "range.txt", "a\nb\na\n")
	res = ok(t, app.updateFile("range.txt", "a", "z", false, 3, 3))
	data, _ := os.ReadFile(filepath.Join(root, "range.txt"))
	if string(data) != "a\nb\nz\n" {
		t.Fatalf("line-ranged update: %q (%v)", data, res)
	}
}

func TestApplyPatchUnifiedAndV4A(t *testing.T) {
	root := t.TempDir()
	app := NewApp(root)
	write(t, root, "hello.py", "def hello():\n    return \"Hello\"\n")

	unified := `--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,2 @@
 def hello():
-    return "Hello"
+    return "Hello, World!"
`
	res := ok(t, app.applyPatch(unified, "", 0.5))
	data, _ := os.ReadFile(filepath.Join(root, "hello.py"))
	if !strings.Contains(string(data), "Hello, World!") {
		t.Fatalf("unified patch not applied: %q (%v)", data, res)
	}

	v4a := `*** Begin Patch
*** Update File: hello.py
- def hello():
+ def hi():

*** Create File: fresh.py
+VALUE = 1

*** Delete File: hello_old.py
*** End Patch`
	write(t, root, "hello_old.py", "legacy\n")
	res = ok(t, app.applyPatch(v4a, "", 0.5))
	sum := res["summary"].(map[string]any)
	if sum["modified"] != 1 || sum["created"] != 1 || sum["deleted"] != 1 {
		t.Fatalf("v4a summary: %v", res)
	}
	if _, err := os.Stat(filepath.Join(root, "hello_old.py")); err == nil {
		t.Fatal("delete didn't happen")
	}
	fresh, _ := os.ReadFile(filepath.Join(root, "fresh.py"))
	if string(fresh) != "VALUE = 1\n" {
		t.Fatalf("create content: %q", fresh)
	}

	if res := app.applyPatch("not a patch at all", "", 0.5); res["success"].(bool) {
		t.Fatalf("garbage should not succeed: %v", res)
	}
}

func TestGlobAndGrep(t *testing.T) {
	root := t.TempDir()
	app := NewApp(root)
	write(t, root, "src/a.py", "import os\n# TODO fix\n")
	write(t, root, "src/deep/b.py", "print('b')\n")
	write(t, root, "readme.md", "# readme\n")
	write(t, root, ".venv/lib.py", "ignored\n")

	res := ok(t, app.glob("**/*.py", "", true, "", nil, 0))
	paths := []string{}
	for _, f := range res["files"].([]any) {
		paths = append(paths, f.(map[string]any)["path"].(string))
	}
	joined := strings.Join(paths, ",")
	if !strings.Contains(joined, "src/a.py") || !strings.Contains(joined, "src/deep/b.py") {
		t.Fatalf("glob missed files: %v", paths)
	}
	if strings.Contains(joined, ".venv") {
		t.Fatalf("gitignore not respected: %v", paths)
	}

	res = ok(t, app.grep("TODO", "", "", 1, false, true))
	matches := res["matches"].([]any)
	if len(matches) != 1 {
		t.Fatalf("grep matches: %v", res)
	}
	m := matches[0].(map[string]any)
	if m["file"] != "src/a.py" || m["line_number"] != 2 {
		t.Fatalf("grep match detail: %v", m)
	}

	if res := app.grep("x", "/", "", 0, false, true); res["success"].(bool) {
		t.Fatalf("grep at / must refuse: %v", res)
	}
}

func TestDirManagement(t *testing.T) {
	root := t.TempDir()
	app := NewApp(root)

	ok(t, app.managePath("create_dir", "x/y", "", false))
	write(t, root, "x/y/f.txt", "data\n")
	ok(t, app.managePath("move", "x/y/f.txt", "z/g.txt", false))
	if _, err := os.Stat(filepath.Join(root, "z/g.txt")); err != nil {
		t.Fatal("move failed")
	}
	if res := app.managePath("delete", "x/y", "", false); !res["success"].(bool) {
		t.Fatalf("empty dir delete: %v", res)
	}
	write(t, root, "tree/a/b.txt", "1\n")
	if res := app.managePath("delete", "tree", "", false); res["success"].(bool) {
		t.Fatal("non-recursive delete of non-empty dir should fail")
	}
	ok(t, app.managePath("delete", "tree", "", true))
	if res := app.managePath("chmod", "x", "", false); res["success"].(bool) {
		t.Fatalf("invalid op should fail: %v", res)
	}

	res := ok(t, app.listFiles("", true, 5))
	if res["tree"] == nil {
		t.Fatalf("tree listing: %v", res)
	}
	ok(t, app.getCwd())
	ok(t, app.getHomeDir())
}
