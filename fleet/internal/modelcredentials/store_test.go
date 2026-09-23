package modelcredentials

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

func TestCredentialScopeRotationAndRemoval(t *testing.T) {
	root := filepath.Join(t.TempDir(), "store")
	ref := "node-secret://provider"
	if err := Put(root, ref, "https://api.example/v1/", "first-key", false); err != nil {
		t.Fatal(err)
	}
	key, err := Read(root, ref, "https://api.example/v1")
	if err != nil || key != "first-key" {
		t.Fatal("credential roundtrip failed", err)
	}
	for _, endpoint := range []string{"https://other.example/v1", "https://api.example/other", "http://api.example/v1"} {
		if key, err := Read(root, ref, endpoint); err == nil || key != "" {
			t.Fatal("credential escaped its endpoint")
		}
	}
	if err := Put(root, ref, "https://api.example/v1", "second-key", false); err == nil {
		t.Fatal("implicit replacement")
	}
	if err := Put(root, ref, "https://api.example/v1", "second-key", true); err != nil {
		t.Fatal(err)
	}
	key, err = Read(root, ref, "https://api.example/v1")
	if err != nil || key != "second-key" {
		t.Fatal("rotation was not read on next use", err)
	}
	refs, err := List(root)
	if err != nil || len(refs) != 1 || refs[0] != ref {
		t.Fatal(refs, err)
	}
	if _, err := Read(filepath.Join(t.TempDir(), "another-fleet"), ref, "https://api.example/v1"); err == nil {
		t.Fatal("cross-Fleet read")
	}
	if err := Delete(root, ref); err != nil {
		t.Fatal(err)
	}
	if _, err := Read(root, ref, "https://api.example/v1"); err == nil {
		t.Fatal("deleted credential remains readable")
	}
}
func TestCredentialRejectsUnsafeInput(t *testing.T) {
	for _, ref := range []string{"key", "node-secret://../key", "node-secret://key?other", "node-secret://KEY", "node-secret://", "node-secret://key/name"} {
		if err := Put(t.TempDir(), ref, "https://api.example/v1", "key", false); err == nil {
			t.Fatalf("accepted ref %q", ref)
		}
	}
	for _, endpoint := range []string{"http://remote.example/v1", "https://user:pass@api.example/v1", "https://api.example/v1?token=x", "https://api.example/v1#", "https://api.example\n/", "file:///tmp/key"} {
		if err := Put(t.TempDir(), "node-secret://provider", endpoint, "key", false); err == nil {
			t.Fatalf("accepted endpoint %q", endpoint)
		}
	}
	for _, key := range []string{"", "line\nline", "bad\rkey", "key value", "é", strings.Repeat("k", 8193)} {
		if err := Put(t.TempDir(), "node-secret://provider", "https://api.example", key, false); err == nil {
			t.Fatal("accepted malformed key")
		}
	}
}
func TestCredentialRejectsSymlinksAndUnsafeFiles(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX mode and symlink checks; DPAPI is checked separately on Windows")
	}
	parent := t.TempDir()
	root := filepath.Join(parent, "store")
	ref := "node-secret://provider"
	if err := Put(root, ref, "https://api.example/v1", "key", false); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, filename("provider"))
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := Read(root, ref, "https://api.example/v1"); err == nil {
		t.Fatal("read publicly accessible key")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(parent, "link")
	if err := os.Symlink(root, link); err != nil {
		t.Fatal(err)
	}
	if _, err := Read(link, ref, "https://api.example/v1"); err == nil {
		t.Fatal("read symlinked root")
	}
	if err := os.Rename(path, filepath.Join(root, "real")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("real", path); err != nil {
		t.Fatal(err)
	}
	if _, err := Read(root, ref, "https://api.example/v1"); err == nil {
		t.Fatal("read symlinked credential")
	}
	if err := os.Chmod(root, 0755); err != nil {
		t.Fatal(err)
	}
	if err := Put(root, ref, "https://api.example/v1", "key", true); err == nil {
		t.Fatal("write to nonprivate root")
	}
}
func TestCredentialCorruptionDoesNotExposeKey(t *testing.T) {
	root := filepath.Join(t.TempDir(), "store")
	if err := Put(root, "node-secret://provider", "https://api.example/v1", "valid-key", false); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, filename("provider"))
	secret := "untrusted-key-do-not-log"
	for _, data := range []string{secret, strings.Repeat(secret, 4096), `{"protocol":1,"protection":"wrong","payload":"AA=="}`} {
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		value, err := Read(root, "node-secret://provider", "https://api.example/v1")
		if value != "" || err == nil || strings.Contains(err.Error(), secret) {
			t.Fatal("unsafe corruption response")
		}
	}
}

func TestCredentialRotationIsAtomicForConcurrentReaders(t *testing.T) {
	root := filepath.Join(t.TempDir(), "store")
	ref := "node-secret://provider"
	endpoint := "https://api.example/v1"
	if err := Put(root, ref, endpoint, "old-key", false); err != nil {
		t.Fatal(err)
	}
	var workers sync.WaitGroup
	for range 3 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for range 30 {
				key, err := Read(root, ref, endpoint)
				if err != nil || (key != "old-key" && key != "new-key") {
					t.Error("reader saw a partial credential", err)
					return
				}
			}
		}()
	}
	for range 10 {
		if err := Put(root, ref, endpoint, "new-key", true); err != nil {
			t.Error(err)
		}
	}
	workers.Wait()
}
