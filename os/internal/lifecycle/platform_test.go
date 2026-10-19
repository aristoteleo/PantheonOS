package lifecycle

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPortableArtifactPaths(t *testing.T) {
	for _, p := range []string{"native/runtime.py", "office/checkpoint.db", "文件/file.txt"} {
		if !relative(p) {
			t.Fatalf("valid portable path rejected: %q", p)
		}
	}
	for _, p := range []string{"../file", "native/../file", "/file", "C:/file", "C:file", "file:stream", `native\file`, "native//file"} {
		if relative(p) {
			t.Fatalf("unsafe path accepted: %q", p)
		}
	}
}

func TestRootLockExclusiveAndReleased(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lock")
	first, err := lockRoot(path)
	if err != nil {
		t.Fatal(err)
	}
	if second, err := lockRoot(path); err == nil {
		second.Close()
		first.Close()
		t.Fatal("duplicate lifecycle owner allowed")
	}
	first.Close()
	third, err := lockRoot(path)
	if err != nil {
		t.Fatal(err)
	}
	third.Close()
}

func TestLedgerReplacement(t *testing.T) {
	dir := t.TempDir()
	for _, content := range []string{"first", "second"} {
		src, dst := filepath.Join(dir, "ledger.tmp"), filepath.Join(dir, "ledger.json")
		if err := os.WriteFile(src, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
		if err := commitLedger(src, dst, dir); err != nil {
			t.Fatal(err)
		}
		data, err := os.ReadFile(dst)
		if err != nil || string(data) != content {
			t.Fatalf("ledger update lost: %s %v", data, err)
		}
	}
}
