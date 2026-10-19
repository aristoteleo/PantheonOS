package lifecycle

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStartCollectsSupersededInstallations(t *testing.T) {
	m, _, first := setup(t)
	m.collectQuiet = time.Nanosecond
	digests, bundles := []string{first}, map[string][]byte{}
	if b, digest := bundle(t, definition(), nil); digest == first {
		bundles[first] = b
	} else {
		t.Fatal("the first bundle is not reproducible")
	}
	for _, version := range []string{"2", "3", "4"} {
		def := definition()
		def.Version = version
		b, digest := bundle(t, def, nil)
		bundles[digest] = b
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
	// As the Controller does, an install stages its artifact first. With no
	// quiet period here, collection could take a just-staged artifact before
	// the install is submitted; in service the quiet period covers that gap
	// (and a lost artifact is staged again by the next install attempt).
	install := func(id, digest string) {
		t.Helper()
		m.serial.Lock()
		if _, err := m.Stage(digest, 0, bundles[digest]); err != nil {
			m.serial.Unlock()
			t.Fatal(err)
		}
		_, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: "install", Digest: digest, Scope: "model-test"})
		m.serial.Unlock()
		if err != nil {
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
		install("install-"+digests[i][:4], digests[i])
		run("copy-"+digests[i][:4], "clone_data", digests[i], 0, &DataSource{Digest: digests[i-1], Generation: 2})
		run("start-"+digests[i][:4], "start", digests[i], 0, nil)
	}
	eventually := func(what string, done func() bool) {
		t.Helper()
		for deadline := time.Now().Add(5 * time.Second); !done(); time.Sleep(10 * time.Millisecond) {
			if time.Now().After(deadline) {
				t.Fatal(what)
			}
		}
	}
	gone := func(path string) bool { _, err := os.Stat(path); return os.IsNotExist(err) }
	artifact := func(d string) string { return filepath.Join(m.root, "artifacts", d+".tar") }
	oldest, older := m.paths(digests[0], "model-test"), m.paths(digests[1], "model-test")
	eventually("superseded installations were not collected", func() bool {
		s := m.Snapshot()
		return s.Installations[digests[0]].State == "absent" && s.Installations[digests[1]].State == "absent"
	})
	for _, path := range []string{oldest.Install, oldest.Package, env} {
		if !gone(path) {
			t.Fatal("not removed:", path)
		}
	}
	// Beyond the newest keepData stopped revisions, data and the instance
	// record go; staged artifacts of revisions no longer installed go.
	eventually("oldest data, instance or artifacts were not collected", func() bool {
		_, known := m.Snapshot().Instances[m.instanceID(digests[0], "model-test")]
		return !known && gone(oldest.Data) && gone(artifact(digests[0])) && gone(artifact(digests[1]))
	})
	if _, err := os.Stat(older.Data); err != nil {
		t.Fatal("the second newest stopped revision keeps its data", err)
	}
	// The previous revision stays installed: the data source and rollback target.
	if state := m.Snapshot().Installations[digests[2]].State; state != "installed" {
		t.Fatal("previous revision collected", state)
	}
	if gone(artifact(digests[2])) || gone(artifact(digests[3])) {
		t.Fatal("artifacts of installed revisions are kept")
	}
	// A collected revision installs again once its artifact is staged again.
	install("reinstall", digests[0])
}

func TestDefaultCopyReserveIsAtMostATenthOfTheDisk(t *testing.T) {
	p, _ := stateCopyPolicy(nil)
	if r := copyReserve(nil, p, 5<<30); r != (5<<30)/10 {
		t.Fatal("a 5 GiB disk keeps a tenth free, not 1 GiB:", r)
	}
	if r := copyReserve(nil, p, 100<<30); r != 1<<30 {
		t.Fatal("a large disk keeps 1 GiB free:", r)
	}
	explicit := &StateCopyPolicy{ReserveBytes: 3 << 30}
	p, _ = stateCopyPolicy(explicit)
	if r := copyReserve(explicit, p, 5<<30); r != 3<<30 {
		t.Fatal("an owner's reserve is kept as set:", r)
	}
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

// A revision whose data was copied but which has not started yet is not one
// that ran: it does not push the second newest revision's data out.
func TestAPreparedRevisionDoesNotCountAsOneThatRan(t *testing.T) {
	m, _, first := setup(t)
	m.collectQuiet = time.Hour // collection below is explicit
	digests := []string{first}
	for _, version := range []string{"2", "3", "4"} {
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
	run("start-0", "start", digests[0], 0, nil)
	for i := 1; i < 3; i++ {
		run(fmt.Sprintf("stop-%d", i-1), "stop", digests[i-1], 1, nil)
		run(fmt.Sprintf("install-%d", i), "install", digests[i], 0, nil)
		run(fmt.Sprintf("copy-%d", i), "clone_data", digests[i], 0, &DataSource{Digest: digests[i-1], Generation: 2})
		run(fmt.Sprintf("start-%d", i), "start", digests[i], 0, nil)
	}
	run("stop-2", "stop", digests[2], 1, nil)
	run("install-3", "install", digests[3], 0, nil)
	run("copy-3", "clone_data", digests[3], 0, &DataSource{Digest: digests[2], Generation: 2})
	for _, d := range digests[:2] {
		run("uninstall-"+d[:6], "uninstall", d, 0, nil)
	}
	m.collectLeftovers(true, 0)
	if _, err := os.Stat(m.paths(digests[1], "model-test").Data); err != nil {
		t.Fatal("the second newest revision that ran keeps its data", err)
	}
	if _, err := os.Stat(m.paths(digests[0], "model-test").Data); !os.IsNotExist(err) {
		t.Fatal("the third newest revision that ran gives its data up", err)
	}
	if _, err := os.Stat(m.paths(digests[3], "model-test").Data); err != nil {
		t.Fatal("the prepared revision keeps its copied data", err)
	}
}
