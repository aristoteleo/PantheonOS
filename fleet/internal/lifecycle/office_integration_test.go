package lifecycle

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	goruntime "runtime"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Explicit opt-in. Uses the real pinned Office image, real NativeDriver and
// existing local Docker, with temporary App data only. No user App is stopped.
func TestOfficeContainerLifecycleIntegration(t *testing.T) {
	directory := os.Getenv("PANTHEON_TEST_OFFICE_PACKAGE")
	if directory == "" {
		t.Skip("requires a prepared Office App package and Docker")
	}
	testOfficeLifecycle(t, directory, "container")
}

// Runs in a disposable Debian/Ubuntu Fleet node. Downloads the pinned native
// package into temporary installation storage; no Docker daemon is contacted.
func TestOfficeNativeLifecycleIntegration(t *testing.T) {
	directory := os.Getenv("PANTHEON_TEST_OFFICE_NATIVE_PACKAGE")
	if directory == "" {
		t.Skip("requires a native Office App package matching this node")
	}
	testOfficeLifecycle(t, directory, "process")
}

func testOfficeLifecycle(t *testing.T, directory, runtime string) {
	b, err := os.ReadFile(filepath.Join(directory, "fleet.json"))
	if err != nil {
		t.Fatal(err)
	}
	var def Definition
	if err = StrictDecode(b, &def); err != nil {
		t.Fatal(err)
	}
	if err = def.Validate(); err != nil {
		t.Fatal(err)
	}
	if def.Components[0].Runtime != runtime {
		t.Fatal("wrong Office package runtime")
	}
	root := t.TempDir()
	driver := NativeDriver{Engine: &ContainerEngine{Root: filepath.Join(root, "dependencies/docker")}}
	caps := proto.Capability{OS: goruntime.GOOS, Arch: goruntime.GOARCH, RAMGB: 8, DiskFreeGB: 32}
	m, err := Open(root, "f_office_test", "node_office_test", caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	files := map[string]string{}
	if err := filepath.WalkDir(directory, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		contents, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(directory, path)
		if err != nil {
			return err
		}
		files[filepath.ToSlash(rel)] = string(contents)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	payload, digest := bundle(t, def, files)
	for offset := 0; offset < len(payload); offset += 64 << 10 {
		end := offset + (64 << 10)
		if end > len(payload) {
			end = len(payload)
		}
		if _, err = m.Stage(digest, int64(offset), payload[offset:end]); err != nil {
			t.Fatal(err)
		}
	}
	run := func(id, action string, generation uint64) {
		t.Helper()
		if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "app", Generation: generation}); err != nil {
			t.Fatal(err)
		}
		deadline := time.Now().Add(6 * time.Minute)
		for time.Now().Before(deadline) {
			op := m.Snapshot().Operations[id]
			if op.State != "running" && op.State != "queued" {
				if op.State != "succeeded" {
					b, _ := json.Marshal(op)
					t.Fatal(string(b))
				}
				return
			}
			time.Sleep(200 * time.Millisecond)
		}
		t.Fatal("Office operation timed out")
	}
	// Keep cleanup scoped to this test's owned resources even when a probe fails.
	defer func() {
		for _, in := range m.Snapshot().Instances {
			for _, r := range in.Resources {
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
				_ = driver.Stop(ctx, def.Components[0], r)
				_ = driver.Release(ctx, r)
				cancel()
			}
		}
	}()
	run("install-office", "install", 0)
	run("start-office", "start", 0)
	apiOrigin := func() string {
		t.Helper()
		if runtime != "process" {
			return ""
		}
		contents, err := os.ReadFile(filepath.Join(m.paths(digest, "app").Data, "runtime.json"))
		if err != nil {
			t.Fatal(err)
		}
		var config struct {
			APIOrigin string `json:"api_origin"`
		}
		if err := json.Unmarshal(contents, &config); err != nil || config.APIOrigin == "" {
			t.Fatal("missing native API origin", err)
		}
		return config.APIOrigin
	}
	originalAPIOrigin := apiOrigin()
	key := m.instanceID(digest, "app")
	in := m.Snapshot().Instances[key]
	url := in.Resources[0].Endpoints["http"]
	if service, err := m.Service(key, digest, in.Generation, "office", "http"); err != nil || service != url {
		t.Fatal("native service binding missing", err)
	}
	client := http.Client{Timeout: 5 * time.Second}
	response, err := client.Get(url + "/api/office/sessions")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatalf("Office API must be authenticated, got %d", response.StatusCode)
	}
	if err := os.WriteFile(filepath.Join(m.paths(digest, "app").Data, "office", "retained.txt"), []byte("working copy"), 0600); err != nil {
		t.Fatal(err)
	}
	run("stop-office", "stop", in.Generation)
	if _, err = os.Stat(filepath.Join(m.paths(digest, "app").Data, "office/checkpoint.db")); err != nil {
		t.Fatal(err)
	}
	in = m.Snapshot().Instances[key]
	run("restart-office", "start", in.Generation)
	if apiOrigin() != originalAPIOrigin {
		t.Fatal("restart invalidated the resource URLs in retained working copies")
	}
	if b, err := os.ReadFile(filepath.Join(m.paths(digest, "app").Data, "office/retained.txt")); err != nil || string(b) != "working copy" {
		t.Fatal("Office working copy lost", err)
	}
	in = m.Snapshot().Instances[key]
	run("stop-again", "stop", in.Generation)
	run("uninstall-office", "uninstall", 0)
	if b, err := os.ReadFile(filepath.Join(m.paths(digest, "app").Data, "office/retained.txt")); err != nil || string(b) != "working copy" {
		t.Fatal("uninstall removed working copies", err)
	}
}
