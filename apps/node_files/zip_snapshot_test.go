package nodefiles

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSnapshotZipHashes(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "deck.pptx")
	f, _ := os.Create(path)
	z := zip.NewWriter(f)
	payload := strings.Repeat("image content", 10000)
	for _, method := range []uint16{zip.Store, zip.Deflate} {
		name := "raw"
		if method == zip.Deflate {
			name = "compressed"
		}
		w, _ := z.CreateHeader(&zip.FileHeader{Name: name, Method: method})
		w.Write([]byte(payload))
	}
	z.Close()
	f.Close()
	a, err := New([]string{root}, "test")
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	invoke := func(method string, args map[string]any) (any, error) {
		return a.Call("file_transfer", map[string]any{"method": method, "args": args})
	}
	opened := call(t, a, "file_transfer", map[string]any{"method": "open_file_for_read", "args": map[string]any{"file_path": path, "snapshot": true}})
	id := opened["handle_id"]
	got, err := invoke("hash_zip_entries", map[string]any{"handle_id": id, "names": []any{"raw", "compressed"}})
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte(payload))
	for _, entry := range got.(map[string]any)["entries"].([]map[string]any) {
		if entry["sha256"] != hex.EncodeToString(digest[:]) || entry["size"] != int64(len(payload)) {
			t.Fatal(entry)
		}
	}
	for _, names := range [][]any{{"missing"}, {"raw", "raw"}, {12}, make([]any, 65)} {
		if _, err := invoke("hash_zip_entries", map[string]any{"handle_id": id, "names": names}); err == nil {
			t.Fatal("accepted invalid entries")
		}
	}
	plain := call(t, a, "file_transfer", map[string]any{"method": "open_file_for_read", "args": map[string]any{"file_path": path}})
	if _, err := invoke("hash_zip_entries", map[string]any{"handle_id": plain["handle_id"], "names": []any{"raw"}}); err == nil {
		t.Fatal("accepted unverified handle")
	}
	os.WriteFile(path, []byte("changed"), 0600)
	if _, err := invoke("hash_zip_entries", map[string]any{"handle_id": id, "names": []any{"raw"}}); err == nil {
		t.Fatal("accepted changed snapshot")
	}
}
