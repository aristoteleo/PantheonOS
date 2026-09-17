package nodefiles

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSnapshotRandomReadsRejectChangedSource(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "deck.pptx")
	data := []byte("unchanged document bytes")
	os.WriteFile(path, data, 0600)
	app, _ := New([]string{dir}, "node")
	defer app.Close()
	opened := transferCall(t, app, "open_file_for_read", map[string]any{"file_path": path, "snapshot": true})
	if opened["sha256"] != digest(data) {
		t.Fatal("incorrect source fingerprint")
	}
	args := map[string]any{"handle_id": opened["handle_id"], "offset": 4, "size": 7}
	read := transferCall(t, app, "read_chunk_at", args)
	if read["data"] != base64.StdEncoding.EncodeToString(data[4:11]) {
		t.Fatal("incorrect random access read")
	}
	os.WriteFile(path, []byte("different document bytes"), 0600)
	future := time.Now().Add(time.Second)
	os.Chtimes(path, future, future)
	if _, err := app.transfer("read_chunk_at", args); err == nil {
		t.Fatal("mixed source versions accepted")
	}
	transferCall(t, app, "close_file", map[string]any{"handle_id": opened["handle_id"]})
}
