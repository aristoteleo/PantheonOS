package lifecycle

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestStateCopyUpgradeAndLostAcknowledgement(t *testing.T) {
	m, driver, old := setup(t)
	def := definition()
	def.Version = "2"
	b, next := bundle(t, def, nil)
	if _, err := m.Stage(next, 0, b); err != nil {
		t.Fatal(err)
	}
	run := func(id, action, digest string, generation uint64, source *DataSource, ok bool) {
		t.Helper()
		_, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "model-test", Generation: generation, DataSource: source})
		if err != nil {
			t.Fatal(err)
		}
		op := wait(t, m, id)
		if (op.State == "succeeded") != ok {
			t.Fatalf("%s: %+v", id, op)
		}
	}
	run("start-old", "start", old, 0, nil, true)
	run("install-new", "install", next, 0, nil, true)
	sourcePath := m.paths(old, "model-test").Data
	if err := os.WriteFile(filepath.Join(sourcePath, "connector.json"), []byte("private configuration"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(sourcePath, "nested"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sourcePath, "nested", "history.db"), []byte("request metadata"), 0600); err != nil {
		t.Fatal(err)
	}
	run("reject-live", "clone_data", next, 0, &DataSource{old, 1}, false)
	run("stop-old", "stop", old, 1, nil, true)
	run("reject-stale", "clone_data", next, 0, &DataSource{old, 1}, false)
	run("copy", "clone_data", next, 0, &DataSource{old, 2}, true)
	targetPath := m.paths(next, "model-test").Data
	raw, err := os.ReadFile(filepath.Join(targetPath, "nested", "history.db"))
	if err != nil || string(raw) != "request metadata" {
		t.Fatal(string(raw), err)
	}
	// Simulate a lost ledger acknowledgement after the atomic directory rename.
	if err := m.update(func() { delete(m.ledger.Instances, m.instanceID(next, "model-test")) }); err != nil {
		t.Fatal(err)
	}
	run("recover-copy", "clone_data", next, 0, &DataSource{old, 2}, true)
	run("start-new", "start", next, 0, nil, true)
	instance := m.Snapshot().Instances[m.instanceID(next, "model-test")]
	if instance.DataSource == nil || instance.DataSource.Digest != old || instance.Generation != 1 {
		t.Fatal(instance)
	}
	run("reject-overwrite", "clone_data", next, 1, &DataSource{old, 2}, false)
	if driver.starts != 2 {
		t.Fatal("copy must not start processes", driver.starts)
	}
	raw, err = os.ReadFile(filepath.Join(sourcePath, "connector.json"))
	if err != nil || string(raw) != "private configuration" {
		t.Fatal("old state was changed", err)
	}
}

func TestStateCopyRejectsDifferentAppScopeAndExistingData(t *testing.T) {
	m, _, old := setup(t)
	if _, err := m.Submit(Request{Protocol: 1, OperationID: "start", Action: "start", Digest: old, Scope: "model-test"}); err != nil {
		t.Fatal(err)
	}
	if wait(t, m, "start").State != "succeeded" {
		t.Fatal("start failed")
	}
	if _, err := m.Submit(Request{Protocol: 1, OperationID: "stop", Action: "stop", Digest: old, Scope: "model-test", Generation: 1}); err != nil {
		t.Fatal(err)
	}
	if wait(t, m, "stop").State != "succeeded" {
		t.Fatal("stop failed")
	}
	for _, test := range []struct {
		name, app, scope string
		exists           bool
	}{
		{"other-app", "another", "model-test", false}, {"other-scope", "example", "other", false},
		{"existing-data", "example", "model-test", true},
	} {
		t.Run(test.name, func(t *testing.T) {
			def := definition()
			def.AppID = test.app
			def.Version = test.name
			b, digest := bundle(t, def, nil)
			if _, err := m.Stage(digest, 0, b); err != nil {
				t.Fatal(err)
			}
			if _, err := m.Submit(Request{Protocol: 1, OperationID: "install-" + test.name, Action: "install", Digest: digest, Scope: test.scope}); err != nil {
				t.Fatal(err)
			}
			if wait(t, m, "install-"+test.name).State != "succeeded" {
				t.Fatal("install failed")
			}
			if test.exists {
				if err := os.Mkdir(m.paths(digest, test.scope).Data, 0700); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := m.Submit(Request{Protocol: 1, OperationID: test.name, Action: "clone_data", Digest: digest, Scope: test.scope, DataSource: &DataSource{old, 2}}); err != nil {
				t.Fatal(err)
			}
			if wait(t, m, test.name).State != "failed" {
				t.Fatal("unsafe import accepted")
			}
		})
	}
}

func TestStateCopyRefusesLinksAndOversizedFiles(t *testing.T) {
	for _, kind := range []string{"symlink", "oversized", "cancelled"} {
		t.Run(kind, func(t *testing.T) {
			if kind == "symlink" && runtime.GOOS == "windows" {
				t.Skip("Windows link privileges are not guaranteed")
			}
			directory := t.TempDir()
			source := filepath.Join(directory, "source")
			target := filepath.Join(directory, "target")
			for _, path := range []string{source, target} {
				if err := os.Mkdir(path, 0700); err != nil {
					t.Fatal(err)
				}
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			switch kind {
			case "symlink":
				if err := os.Symlink(directory, filepath.Join(source, "outside")); err != nil {
					t.Fatal(err)
				}
			case "oversized":
				f, err := os.Create(filepath.Join(source, "large"))
				if err != nil {
					t.Fatal(err)
				}
				if err = f.Truncate(maxCloneBytes + 1); err != nil {
					t.Fatal(err)
				}
				f.Close()
			case "cancelled":
				cancel()
			}
			src, err := os.OpenRoot(source)
			if err != nil {
				t.Fatal(err)
			}
			defer src.Close()
			dst, err := os.OpenRoot(target)
			if err != nil {
				t.Fatal(err)
			}
			defer dst.Close()
			if err := copyAppState(ctx, src, dst, &DataSource{"revision", 2}); err == nil {
				t.Fatal("invalid source accepted")
			}
			if _, err := os.Stat(filepath.Join(target, importReceipt)); !os.IsNotExist(err) {
				t.Fatal("failed copy has receipt")
			}
		})
	}
}
