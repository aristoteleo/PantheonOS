package lifecycle

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func TestStateMovesToAnotherNodeThroughExportAndImport(t *testing.T) {
	source, _, digest := setup(t)
	run := func(m *Manager, id, action string, generation uint64, ds *DataSource, ok bool) {
		t.Helper()
		if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "app", Generation: generation, DataSource: ds}); err != nil {
			t.Fatal(err)
		}
		if op := wait(t, m, id); (op.State == "succeeded") != ok {
			t.Fatalf("%s: %+v", id, op)
		}
	}
	run(source, "start", "start", 0, nil, true)
	data := source.paths(digest, "app").Data
	os.MkdirAll(filepath.Join(data, "chats"), 0o700)
	os.WriteFile(filepath.Join(data, "chats", "one.jsonl"), []byte("hello"), 0o600)
	if _, err := source.ExportData(context.Background(), digest, "app", 1, 0); err == nil {
		t.Fatal("a running instance is not exported")
	}
	run(source, "stop", "stop", 1, nil, true)

	target, err := Open(t.TempDir(), "owner", "node-b", proto.Capability{}, &fakeDriver{alive: map[string]bool{}})
	if err != nil {
		t.Fatal(err)
	}
	defer target.Close()
	b, _ := bundle(t, definition(), nil)
	target.Stage(digest, 0, b)
	run(target, "install", "install", 0, nil, true)
	var sum string
	for offset := int64(0); ; {
		chunk, err := source.ExportData(context.Background(), digest, "app", 2, offset)
		if err != nil {
			t.Fatal(err)
		}
		sum = chunk.SHA256
		next, err := target.ImportStage(sum, offset, chunk.Data)
		if err != nil {
			t.Fatal(err)
		}
		if offset = next; offset >= chunk.Size {
			break
		}
	}
	ds := &DataSource{Digest: digest, Generation: 2, Archive: sum}
	run(target, "import", "import_data", 0, ds, true)
	raw, err := os.ReadFile(filepath.Join(target.paths(digest, "app").Data, "chats", "one.jsonl"))
	if err != nil || string(raw) != "hello" {
		t.Fatalf("state moved: %q %v", raw, err)
	}
	// A lost acknowledgement retries to the same result; a used state is never overwritten.
	run(target, "import-again", "import_data", 0, ds, true)
	run(target, "start-b", "start", 0, nil, true)
	run(target, "import-over", "import_data", 0, ds, false)
	if _, err := target.Submit(Request{Protocol: 1, OperationID: "bad", Action: "import_data", Digest: digest, Scope: "app",
		DataSource: &DataSource{Digest: digest, Generation: 2}}); err == nil {
		t.Fatal("import needs its staged archive")
	}
}
