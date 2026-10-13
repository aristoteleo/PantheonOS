package lifecycle

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStartCollectsSupersededInstallations(t *testing.T) {
	m, _, first := setup(t)
	m.collectQuiet = time.Nanosecond
	digests := []string{first}
	for _, version := range []string{"2", "3"} {
		def := definition()
		def.Version = version
		b, digest := bundle(t, def, nil)
		if _, err := m.Stage(digest, 0, b); err != nil {
			t.Fatal(err)
		}
		digests = append(digests, digest)
	}
	run := func(id, action, digest string, generation uint64, source *DataSource) {
		t.Helper()
		if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "model-test", Generation: generation, DataSource: source}); err != nil {
			t.Fatal(err)
		}
		if op := wait(t, m, id); op.State != "succeeded" {
			t.Fatalf("%s: %+v", id, op)
		}
	}
	// The first revision's environment is its own; the cache holds it under the node.
	env := filepath.Join(m.root, "python-environments", "k1")
	if err := os.MkdirAll(filepath.Join(env, "bin"), 0700); err != nil {
		t.Fatal(err)
	}
	run("start-1", "start", digests[0], 0, nil)
	binding := `{"schema":1,"key":"k1","python":"` + filepath.Join(env, "bin", "python") + `"}`
	if err := os.WriteFile(filepath.Join(m.paths(digests[0], "model-test").Install, "python-environment.json"), []byte(binding), 0600); err != nil {
		t.Fatal(err)
	}
	for i := 1; i < len(digests); i++ {
		run("stop-"+digests[i-1][:4], "stop", digests[i-1], 1, nil)
		run("install-"+digests[i][:4], "install", digests[i], 0, nil)
		run("copy-"+digests[i][:4], "clone_data", digests[i], 0, &DataSource{Digest: digests[i-1], Generation: 2})
		run("start-"+digests[i][:4], "start", digests[i], 0, nil)
	}
	deadline := time.Now().Add(5 * time.Second)
	for m.Snapshot().Installations[digests[0]].State != "absent" {
		if time.Now().After(deadline) {
			t.Fatal("superseded installation was not collected", m.Snapshot().Installations[digests[0]].State)
		}
		time.Sleep(10 * time.Millisecond)
	}
	gone := m.paths(digests[0], "model-test")
	for _, path := range []string{gone.Install, gone.Package, env} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatal("not removed:", path, err)
		}
	}
	if _, err := os.Stat(gone.Data); err != nil {
		t.Fatal("App data must be kept", err)
	}
	// The previous revision stays installed: the data source and rollback target.
	if state := m.Snapshot().Installations[digests[1]].State; state != "installed" {
		t.Fatal("previous revision collected", state)
	}
	// A collected revision installs again from its retained artifact.
	run("reinstall", "install", digests[0], 0, nil)
}

// A data copy that fails for want of disk space collects superseded
// installations at once instead of waiting out the quiet period: no start
// succeeds on a full disk, so waiting for one would repeat the failure forever.
func TestDiskPressureCollectsWithoutWaiting(t *testing.T) {
	m, _, first := setup(t)
	m.collectQuiet = time.Hour
	digests := []string{first}
	for _, version := range []string{"2", "3"} {
		def := definition()
		def.Version = version
		b, digest := bundle(t, def, nil)
		if _, err := m.Stage(digest, 0, b); err != nil {
			t.Fatal(err)
		}
		digests = append(digests, digest)
	}
	run := func(id, action, digest string, generation uint64, source *DataSource) Operation {
		t.Helper()
		if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "model-test", Generation: generation, DataSource: source}); err != nil {
			t.Fatal(err)
		}
		return wait(t, m, id)
	}
	must := func(op Operation) {
		t.Helper()
		if op.State != "succeeded" {
			t.Fatalf("%+v", op)
		}
	}
	must(run("start-1", "start", digests[0], 0, nil))
	must(run("stop-1", "stop", digests[0], 1, nil))
	must(run("install-2", "install", digests[1], 0, nil))
	must(run("copy-2", "clone_data", digests[1], 0, &DataSource{Digest: digests[0], Generation: 2}))
	must(run("start-2", "start", digests[1], 0, nil))
	must(run("stop-2", "stop", digests[1], 1, nil))
	must(run("install-3", "install", digests[2], 0, nil))
	// Within the quiet period nothing was collected.
	if state := m.Snapshot().Installations[digests[0]].State; state != "installed" {
		t.Fatal("collected too early", state)
	}
	// The disk is now too full for the next copy.
	m.resourcePolicy.StateCopy = &StateCopyPolicy{MaxBytes: maxCloneBytes, MaxEntries: 1000000, ReserveBytes: 1 << 50}
	if op := run("copy-3", "clone_data", digests[2], 0, &DataSource{Digest: digests[1], Generation: 2}); op.State != "failed" || !strings.Contains(op.Error, "insufficient disk space") {
		t.Fatalf("copy should fail for space: %+v", op)
	}
	deadline := time.Now().Add(5 * time.Second)
	for m.Snapshot().Installations[digests[0]].State != "absent" {
		if time.Now().After(deadline) {
			t.Fatal("superseded installation was not collected under disk pressure", m.Snapshot().Installations[digests[0]].State)
		}
		time.Sleep(10 * time.Millisecond)
	}
	// The newest stopped revision (the data source) and the new one stay.
	for _, d := range digests[1:] {
		if state := m.Snapshot().Installations[d].State; state != "installed" {
			t.Fatal("kept revision collected", d[:8], state)
		}
	}
}
