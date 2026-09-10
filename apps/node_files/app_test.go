package nodefiles

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func call(t *testing.T, a *App, m string, p map[string]any) map[string]any {
	t.Helper()
	v, err := a.Call(m, p)
	if err != nil {
		t.Fatalf("%s: %v", m, err)
	}
	return v.(map[string]any)
}
func TestSharedFilesAndTransfers(t *testing.T) {
	root := t.TempDir()
	a, err := New([]string{root}, "node-A")
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	path := filepath.Join(root, "folder", "a.txt")
	call(t, a, "write_file", map[string]any{"file_path": path, "content": "hello\nworld"})
	got := call(t, a, "list_files", map[string]any{})
	entries := got["files"].([]map[string]any)
	if len(entries) != 1 || entries[0]["path"] != filepath.ToSlash(a.roots[0].path) {
		t.Fatal(got)
	}
	got = call(t, a, "stat_path", map[string]any{"file_path": path})
	if got["node_id"] != "node-A" || got["exists"] != true {
		t.Fatal(got)
	}
	got = call(t, a, "read_file", map[string]any{"file_path": path, "start_line": 2})
	if got["content"] != "world" {
		t.Fatal(got)
	}
	got = call(t, a, "file_transfer", map[string]any{"method": "open_file_for_read", "args": map[string]any{"file_path": path}})
	id := got["handle_id"]
	got = call(t, a, "file_transfer", map[string]any{"method": "read_chunk_at", "args": map[string]any{"handle_id": id, "offset": 6, "size": 3}})
	if got["data"] != base64.StdEncoding.EncodeToString([]byte("wor")) {
		t.Fatal(got)
	}
	if _, err = a.Call("file_transfer", map[string]any{"method": "write_chunk", "args": map[string]any{"handle_id": id, "data": "eA=="}}); err == nil {
		t.Fatal("wrote into read handle")
	}
	call(t, a, "file_transfer", map[string]any{"method": "close_file", "args": map[string]any{"handle_id": id}})
	if _, err = a.Call("file_transfer", map[string]any{"method": "read_chunk_at", "args": map[string]any{"handle_id": id, "size": 1}}); err == nil {
		t.Fatal("read closed handle")
	}
	moved := filepath.Join(root, "moved.txt")
	call(t, a, "move_file", map[string]any{"old_path": path, "new_path": moved})
	call(t, a, "delete_path", map[string]any{"path": moved})
	if call(t, a, "stat_path", map[string]any{"file_path": moved})["exists"] != false {
		t.Fatal("still exists")
	}
}
func TestRejectEscapesAndProtectRoots(t *testing.T) {
	parent := t.TempDir()
	root := filepath.Join(parent, "shared")
	outside := filepath.Join(parent, "private")
	os.Mkdir(root, 0755)
	os.Mkdir(outside, 0755)
	os.WriteFile(filepath.Join(outside, "secret"), []byte("secret"), 0600)
	a, err := New([]string{root}, "node")
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if err = os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Skip("symlinks unavailable", err)
	}
	for _, path := range []string{outside, filepath.Join(root, "escape", "secret"), root + "/../private/secret"} {
		for _, method := range []string{"read_file", "write_file", "stat_path"} {
			if _, err = a.Call(method, map[string]any{"file_path": path, "content": "bad"}); err == nil {
				t.Fatalf("%s escaped: %s", method, path)
			}
		}
	}
	if _, err = a.Call("delete_path", map[string]any{"path": root, "recursive": true}); err == nil {
		t.Fatal("deleted root")
	}
	if _, err = a.Call("move_file", map[string]any{"old_path": root, "new_path": root + "-moved"}); err == nil {
		t.Fatal("renamed root")
	}
	data, _ := os.ReadFile(filepath.Join(outside, "secret"))
	if string(data) != "secret" {
		t.Fatal("changed unshared file")
	}
	// An external symlink is not exposed as a navigable entry.
	if len(call(t, a, "list_files", map[string]any{"sub_dir": root})["files"].([]map[string]any)) != 0 {
		t.Fatal("external link leaked")
	}
}
func TestBinaryUploadAndBoundedHandles(t *testing.T) {
	root := t.TempDir()
	a, _ := New([]string{root}, "node")
	defer a.Close()
	path := filepath.Join(root, "image.bin")
	open := call(t, a, "file_transfer", map[string]any{"method": "open_file_for_write", "args": map[string]any{"file_path": path}})
	id := open["handle_id"]
	payload := []byte{0, 255, 254, 1}
	call(t, a, "file_transfer", map[string]any{"method": "write_chunk", "args": map[string]any{"handle_id": id, "data": base64.StdEncoding.EncodeToString(payload)}})
	if _, err := a.Call("file_transfer", map[string]any{"method": "write_chunk", "args": map[string]any{"handle_id": id, "data": strings.Repeat("A", MaxChunk*2)}}); err == nil {
		t.Fatal("unbounded chunk")
	}
	call(t, a, "file_transfer", map[string]any{"method": "close_file", "args": map[string]any{"handle_id": id}})
	data, _ := os.ReadFile(path)
	if string(data) != string(payload) {
		t.Fatal(data)
	}
	if _, err := a.Call("read_file", map[string]any{"file_path": path}); err == nil {
		t.Fatal("binary read as text")
	}
	if _, err := Tools(a); err != nil {
		t.Fatal(err)
	}
}
func TestNoImplicitShares(t *testing.T) {
	if _, err := New(nil, "node"); err == nil {
		t.Fatal("empty shares accepted")
	}
	if _, err := NormalizeRoots([]string{"relative/path"}); err == nil {
		t.Fatal("relative share accepted")
	}
}
