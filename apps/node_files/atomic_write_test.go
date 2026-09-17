package nodefiles

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func digest(data []byte) string { h := sha256.Sum256(data); return hex.EncodeToString(h[:]) }
func transferCall(t *testing.T, a *App, method string, args map[string]any) map[string]any {
	t.Helper()
	return call(t, a, "file_transfer", map[string]any{"method": method, "args": args})
}
func TestAtomicSaveVerifiedReplacement(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "deck.pptx")
	old, next := []byte("old file"), []byte("new document")
	os.WriteFile(path, old, 0600)
	a, _ := New([]string{root}, "node")
	defer a.Close()
	args := map[string]any{"file_path": path, "expected_sha256": digest(old), "sha256": digest(next), "total_size": len(next)}
	opened := transferCall(t, a, "begin_atomic_write", args)
	id := opened["handle_id"]
	for _, pair := range [][2]int{{4, len(next)}, {0, 4}} {
		transferCall(t, a, "write_chunk_at", map[string]any{"handle_id": id, "offset": pair[0], "data": base64.StdEncoding.EncodeToString(next[pair[0]:pair[1]])})
	}
	before, _ := os.ReadFile(path)
	if string(before) != string(old) {
		t.Fatal("replaced before commit")
	}
	saved := transferCall(t, a, "commit_atomic_write", map[string]any{"handle_id": id})
	after, _ := os.ReadFile(path)
	if string(after) != string(next) || saved["sha256"] != digest(next) {
		t.Fatal("wrong saved bytes", saved)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal("permissions changed")
	}
	retry := transferCall(t, a, "begin_atomic_write", args)
	if retry["already_written"] != true {
		t.Fatal("lost acknowledgement not idempotent")
	}
	temps, _ := filepath.Glob(filepath.Join(root, ".pantheon-save-*"))
	if len(temps) != 0 {
		t.Fatal("temporary leaked", temps)
	}
}
func TestAtomicSaveFailuresPreserveOriginal(t *testing.T) {
	for _, mode := range []string{"wrong initial hash", "external edit", "missing original", "corrupt upload", "incomplete upload", "cancel", "expire", "symlink"} {
		t.Run(mode, func(t *testing.T) {
			root := t.TempDir()
			path := filepath.Join(root, "deck.pptx")
			old, next := []byte("old"), []byte("new version")
			os.WriteFile(path, old, 0600)
			a, _ := New([]string{root}, "node")
			defer a.Close()
			expected := digest(old)
			if mode == "wrong initial hash" {
				expected = digest([]byte("different"))
			}
			if mode == "missing original" {
				os.Remove(path)
			}
			if mode == "symlink" {
				os.Rename(path, path+"-real")
				os.Symlink(path+"-real", path)
			}
			opened, err := a.transfer("begin_atomic_write", map[string]any{"file_path": path, "expected_sha256": expected, "sha256": digest(next), "total_size": len(next)})
			if mode == "wrong initial hash" || mode == "missing original" || mode == "symlink" {
				if err == nil {
					t.Fatal("unsafe begin accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			id := opened.(map[string]any)["handle_id"].(string)
			payload := append([]byte(nil), next...)
			if mode == "corrupt upload" {
				payload[0] = 'x'
			}
			if mode == "incomplete upload" {
				payload = payload[:3]
			}
			transferCall(t, a, "write_chunk_at", map[string]any{"handle_id": id, "offset": 0, "data": base64.StdEncoding.EncodeToString(payload)})
			if mode == "external edit" {
				old = []byte("changed elsewhere")
				os.WriteFile(path, old, 0600)
			}
			switch mode {
			case "cancel":
				transferCall(t, a, "close_file", map[string]any{"handle_id": id})
			case "expire":
				a.handles[id].touched = time.Now().Add(-2 * handleTTL)
				transferCall(t, a, "capabilities", map[string]any{})
			default:
				if _, err = a.transfer("commit_atomic_write", map[string]any{"handle_id": id}); err == nil {
					t.Fatal("unsafe commit accepted")
				}
			}
			actual, _ := os.ReadFile(path)
			if string(actual) != string(old) {
				t.Fatal("original damaged")
			}
			temps, _ := filepath.Glob(filepath.Join(root, ".pantheon-save-*"))
			if len(temps) != 0 {
				t.Fatal("temporary leaked")
			}
		})
	}
}
func TestAtomicNewFileAndBounds(t *testing.T) {
	root := t.TempDir()
	a, _ := New([]string{root}, "node")
	defer a.Close()
	path := filepath.Join(root, "new.docx")
	content := []byte("new")
	args := map[string]any{"file_path": path, "expected_sha256": "", "sha256": digest(content), "total_size": len(content)}
	opened := transferCall(t, a, "begin_atomic_write", args)
	id := opened["handle_id"]
	for _, offset := range []int{-1, 4} {
		if _, err := a.transfer("write_chunk_at", map[string]any{"handle_id": id, "offset": offset, "data": "eA=="}); err == nil {
			t.Fatal("invalid range accepted")
		}
	}
	transferCall(t, a, "write_chunk_at", map[string]any{"handle_id": id, "offset": 0, "data": base64.StdEncoding.EncodeToString(content)})
	transferCall(t, a, "commit_atomic_write", map[string]any{"handle_id": id})
	actual, _ := os.ReadFile(path)
	if string(actual) != string(content) {
		t.Fatal("new file missing")
	}
	args["file_path"] = filepath.Join(t.TempDir(), "outside")
	if _, err := a.transfer("begin_atomic_write", args); err == nil {
		t.Fatal("escaped shared roots")
	}
}
