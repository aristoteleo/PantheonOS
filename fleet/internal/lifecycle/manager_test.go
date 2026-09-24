package lifecycle

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

type fakeDriver struct {
	mu        sync.Mutex
	starts    int
	alive     map[string]bool
	blocked   bool
	failProbe bool
	hooks     []string
}

func (f *fakeDriver) Prepare(context.Context, Component) error { return nil }
func (f *fakeDriver) Start(_ context.Context, c Component, _ Paths, id string) (Resource, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.starts++
	f.alive[id] = true
	return Resource{ID: id, Component: c.Name, Runtime: c.Runtime, PID: 1, Birth: 1}, nil
}
func (f *fakeDriver) Probe(context.Context, Component, Paths, Resource) error {
	if f.failProbe {
		return fmt.Errorf("readiness failed")
	}
	return nil
}
func (f *fakeDriver) Stop(_ context.Context, _ Component, r Resource) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.alive[r.ID] = false
	return nil
}
func (f *fakeDriver) Release(context.Context, Resource) error { return nil }
func (f *fakeDriver) Alive(_ context.Context, r Resource) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.alive[r.ID], nil
}
func (f *fakeDriver) Hook(_ context.Context, _ Hook, _ Paths, in map[string]any) (Receipt, error) {
	stage := in["stage"].(string)
	f.hooks = append(f.hooks, stage)
	return Receipt{Status: "succeeded", SafeToStop: !f.blocked}, nil
}
func definition() Definition {
	return Definition{Protocol: 1, AppID: "example", Version: "1.0.0", Components: []Component{{Name: "backend", Runtime: "process", Argv: []string{"python3", "server.py"}, Readiness: Probe{Argv: []string{"python3", "probe.py"}, TimeoutSeconds: 3}}}, Hooks: map[string]Hook{"before_stop": {Argv: []string{"python3", "hook.py"}, TimeoutSeconds: 3}}}
}
func bundle(t *testing.T, def Definition, files map[string]string) ([]byte, string) {
	t.Helper()
	b, _ := json.Marshal(def)
	if files == nil {
		files = map[string]string{}
	}
	files["fleet.json"] = string(b)
	var out bytes.Buffer
	tw := tar.NewWriter(&out)
	for name, body := range files {
		if e := tw.WriteHeader(&tar.Header{Name: name, Mode: 0600, Size: int64(len(body))}); e != nil {
			t.Fatal(e)
		}
		tw.Write([]byte(body))
	}
	tw.Close()
	sum := sha256.Sum256(out.Bytes())
	return out.Bytes(), hex.EncodeToString(sum[:])
}
func setup(t *testing.T) (*Manager, *fakeDriver, string) {
	t.Helper()
	f := &fakeDriver{alive: map[string]bool{}}
	m, e := Open(t.TempDir(), "owner", "node", proto.Capability{}, f)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { m.Close() })
	b, d := bundle(t, definition(), nil)
	if _, e = m.Stage(d, 0, b); e != nil {
		t.Fatal(e)
	}
	return m, f, d
}
func wait(t *testing.T, m *Manager, id string) Operation {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		op := m.Snapshot().Operations[id]
		if op != nil && op.State != "running" && op.State != "queued" {
			return *op
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("operation timed out")
	return Operation{}
}
func submit(t *testing.T, m *Manager, d, id, action, scope string, g uint64) Operation {
	t.Helper()
	_, e := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: d, Scope: scope, Generation: g})
	if e != nil {
		t.Fatal(e)
	}
	return wait(t, m, id)
}
func TestIdempotencyGenerationAndRetainedData(t *testing.T) {
	m, f, d := setup(t)
	req := Request{Protocol: 1, OperationID: "open-1", Action: "start", Digest: d, Scope: "app"}
	for range 2 {
		if _, e := m.Submit(req); e != nil {
			t.Fatal(e)
		}
	}
	if op := wait(t, m, "open-1"); op.State != "succeeded" {
		t.Fatal(op)
	}
	if f.starts != 1 {
		t.Fatal("duplicate start", f.starts)
	}
	if op := submit(t, m, d, "stale", "stop", "app", 0); op.State != "failed" {
		t.Fatal("stale generation accepted")
	}
	p := m.paths(d, "app")
	os.WriteFile(filepath.Join(p.Data, "document"), []byte("unsaved document"), 0600)
	if op := submit(t, m, d, "remove-active", "uninstall", "app", 1); op.State != "failed" {
		t.Fatal("removed active installation")
	}
	if op := submit(t, m, d, "stop", "stop", "app", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, d, "remove", "uninstall", "app", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if b, e := os.ReadFile(filepath.Join(p.Data, "document")); e != nil || string(b) != "unsaved document" {
		t.Fatal("user data lost", e)
	}
	req.Action = "stop"
	if _, e := m.Submit(req); e == nil {
		t.Fatal("operation id reused")
	}
}

func TestObserveDoesNotLeaveExitedAppReady(t *testing.T) {
	m, f, d := setup(t)
	op := submit(t, m, d, "start-for-observation", "start", "app", 0)
	if op.State != "succeeded" {
		t.Fatal(op)
	}
	in := m.Snapshot().Instances[m.instanceID(d, "app")]
	f.mu.Lock()
	f.alive[in.Resources[0].ID] = false
	f.mu.Unlock()
	m.observeOnce()
	if m.Snapshot().Instances[in.ID].State != "degraded" {
		t.Fatal("dead App still advertised ready")
	}
}
func TestDrainBlocksAndReadinessNeverLies(t *testing.T) {
	m, f, d := setup(t)
	f.failProbe = true
	if op := submit(t, m, d, "start", "start", "app", 0); op.State != "failed" {
		t.Fatal(op)
	}
	in := m.Snapshot().Instances[m.instanceID(d, "app")]
	if in.State == "ready" {
		t.Fatal("bad readiness")
	}
	f.blocked = true
	if op := submit(t, m, d, "stop", "stop", "app", 1); op.State != "failed" {
		t.Fatal(op)
	}
	in = m.Snapshot().Instances[in.ID]
	if in.State != "stop_blocked" || !f.alive[in.Resources[0].ID] {
		t.Fatal("killed blocked instance")
	}
	f.blocked = false
	if in.Generation != 1 {
		t.Fatal("blocked drain invalidated the running editor's generation")
	}
	if op := submit(t, m, d, "stop-again", "stop", "app", in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
}
func TestRestartDoesNotReplayHooksAndRecoversLiveResources(t *testing.T) {
	m, f, d := setup(t)
	if op := submit(t, m, d, "start", "start", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	m.Close()
	reopened, e := Open(m.root, "owner", "node", proto.Capability{}, f)
	if e != nil {
		t.Fatal(e)
	}
	defer reopened.Close()
	in := reopened.Snapshot().Instances[m.instanceID(d, "app")]
	if in.State != "unknown" {
		t.Fatal(in)
	}
	if op := submit(t, reopened, d, "start-again", "start", "app", 1); op.State != "failed" {
		t.Fatal(op)
	}
	if op := submit(t, reopened, d, "reconcile", "reconcile", "app", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if f.starts != 1 {
		t.Fatal("duplicated child after restart")
	}
	if reopened.Snapshot().Instances[in.ID].Generation != 1 {
		t.Fatal("reconcile changed the identity of an existing live process")
	}
	if op := submit(t, reopened, d, "stop", "stop", "app", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
}
func TestTamperedArtifactAndArchiveTraversal(t *testing.T) {
	m, _, d := setup(t)
	if _, e := m.Stage(d, 0, []byte("tampered")); e == nil {
		t.Fatal("overwrote staged artifact")
	}
	b, bad := bundle(t, definition(), map[string]string{"../outside": "bad"})
	m.Stage(bad, 0, b)
	if op := submit(t, m, bad, "bad", "install", "app", 0); op.State != "failed" {
		t.Fatal("path traversal accepted")
	}
	bad = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	m.Stage(bad, 0, b)
	if op := submit(t, m, bad, "hash", "install", "app", 0); op.State != "failed" {
		t.Fatal("hash mismatch accepted")
	}
}
func TestDifferentScopeCannotUninstallAnotherInstance(t *testing.T) {
	m, _, d := setup(t)
	submit(t, m, d, "start", "start", "window-1", 0)
	if op := submit(t, m, d, "uninstall", "uninstall", "app", 0); op.State != "failed" {
		t.Fatal(op)
	}
}
func TestInterruptedInstallRequiresReconciliation(t *testing.T) {
	m, f, d := setup(t)
	m.update(func() {
		m.ledger.Installations[d] = &Installation{Digest: d, Definition: definition(), State: "installing"}
	})
	m.Close()
	next, e := Open(m.root, "owner", "node", proto.Capability{}, f)
	if e != nil {
		t.Fatal(e)
	}
	defer next.Close()
	if op := submit(t, next, d, "install", "install", "app", 0); op.State != "failed" {
		t.Fatal("uncertain hooks replayed")
	}
}
func TestReconcileSettlesInstallInterruptedByRunnerRestart(t *testing.T) {
	m, f, d := setup(t)
	m.update(func() {
		m.ledger.Installations[d] = &Installation{Digest: d, Definition: definition(), State: "installing"}
		m.ledger.Operations["install-1"] = &Operation{Request: Request{Protocol: 1, OperationID: "install-1", Action: "install", Digest: d, Scope: "rank-0"}, State: "running", Steps: []Step{}}
	})
	m.Close()
	next, e := Open(m.root, "owner", "node", proto.Capability{}, f)
	if e != nil {
		t.Fatal(e)
	}
	defer next.Close()
	if op := next.Snapshot().Operations["install-1"]; op.State != "unknown" {
		t.Fatal(op)
	}
	f.hooks = nil
	if op := submit(t, next, d, "settle-1", "reconcile", "rank-0", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	snap := next.Snapshot()
	if install := snap.Installations[d]; install.State != "absent" {
		t.Fatal(install)
	}
	if op := snap.Operations["install-1"]; op.State != "failed" || op.Error == "" {
		t.Fatal("interrupted install must become a terminal failure", op)
	}
	for _, stage := range f.hooks {
		if stage == "before_install" || stage == "after_install" {
			t.Fatal("install hooks replayed", f.hooks)
		}
	}
	// The exact settle request is idempotent; a new install then succeeds.
	if op := submit(t, next, d, "settle-1", "reconcile", "rank-0", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, next, d, "install-2", "install", "rank-0", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
}
func TestSettleRefusesInstallationStillUsedByAnInstance(t *testing.T) {
	m, f, d := setup(t)
	submit(t, m, d, "start", "start", "app", 0)
	m.update(func() { m.ledger.Installations[d].State = "removing" })
	m.Close()
	next, e := Open(m.root, "owner", "node", proto.Capability{}, f)
	if e != nil {
		t.Fatal(e)
	}
	defer next.Close()
	if op := submit(t, next, d, "settle", "reconcile", "other", 0); op.State != "failed" {
		t.Fatal("settle removed an installation that an instance still uses", op)
	}
}
func TestRealProcessProbeStopAndHook(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	if _, e := exec.LookPath(python); e != nil {
		t.Skip("Python unavailable")
	}
	m, e := Open(t.TempDir(), "owner", "node", proto.Capability{}, NativeDriver{})
	if e != nil {
		t.Fatal(e)
	}
	defer m.Close()
	d := definition()
	d.Components[0].Argv = []string{python, "${PACKAGE}/server.py", "${DATA}/ready"}
	d.Components[0].Readiness.Argv = []string{python, "-c", "import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).exists() else 1)", "${DATA}/ready"}
	d.Hooks["before_stop"] = Hook{Argv: []string{python, "${PACKAGE}/hook.py"}, TimeoutSeconds: 3}
	b, digest := bundle(t, d, map[string]string{"server.py": "import pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text('ready')\nwhile True: time.sleep(1)\n", "hook.py": "import json,sys\nx=json.load(sys.stdin)\nprint(json.dumps({'status':'succeeded','safe_to_stop':True,'checkpoints':['ready']}))\n"})
	m.Stage(digest, 0, b)
	if op := submit(t, m, digest, "start", "start", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "stop", "stop", "app", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
}
