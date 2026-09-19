package lifecycle

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func startedUsage(t *testing.T) (*Manager, *fakeDriver, string, string) {
	t.Helper()
	m, f, d := setup(t)
	if op := submit(t, m, d, "start", "start", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	id := m.instanceID(d, "app")
	if err := m.WindowLease(id, d, 1, "window-a", false); err != nil {
		t.Fatal(err)
	}
	return m, f, d, id
}
func ageIdle(m *Manager, id string) {
	m.mu.Lock()
	m.usageLocked(id, time.Now()).idleSince = time.Now().Add(-2 * idleGrace)
	m.mu.Unlock()
}
func stopAndWait(t *testing.T, m *Manager) Operation {
	t.Helper()
	m.stopIdle(time.Now())
	for id, op := range m.Snapshot().Operations {
		if op.Request.IfIdle {
			return wait(t, m, id)
		}
	}
	t.Fatal("idle stop was not scheduled")
	return Operation{}
}
func TestWindowUsageStopsLastWindowAndRestartsWithData(t *testing.T) {
	m, _, d, id := startedUsage(t)
	if err := m.WindowLease(id, d, 1, "window-b", false); err != nil {
		t.Fatal(err)
	}
	_ = m.WindowLease(id, d, 1, "window-a", true)
	ageIdle(m, id)
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 1 {
		t.Fatal("stopped while second window was open")
	}
	_ = m.WindowLease(id, d, 1, "window-b", true)
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 1 {
		t.Fatal("ignored grace period")
	}
	path := filepath.Join(m.paths(d, "app").Data, "document")
	_ = os.WriteFile(path, []byte("saved"), 0600)
	ageIdle(m, id)
	if op := stopAndWait(t, m); op.State != "succeeded" {
		t.Fatal(op)
	}
	if in := m.Snapshot().Instances[id]; in.State != "stopped" || in.Generation != 2 {
		t.Fatal(in)
	}
	if op := submit(t, m, d, "reopen", "start", "app", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if b, e := os.ReadFile(path); e != nil || string(b) != "saved" {
		t.Fatal("lost data", e)
	}
	if err := m.WindowLease(id, d, 1, "stale", false); err == nil {
		t.Fatal("old generation lease accepted")
	}
	if err := m.WindowLease(id, d, 3, "new", false); err != nil {
		t.Fatal(err)
	}
}
func TestCallsAndExpiredWindows(t *testing.T) {
	m, _, d, id := startedUsage(t)
	release, err := m.BeginUse(id, d, 1)
	if err != nil {
		t.Fatal(err)
	}
	m.mu.Lock()
	m.usage[id].leases["window-a"] = time.Now().Add(-time.Second)
	m.mu.Unlock()
	ageIdle(m, id)
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 1 {
		t.Fatal("stopped an active call")
	}
	release()
	release() // safe idempotent completion
	if u := m.Snapshot().Instances[id].Usage; u.Calls != 0 || u.Windows != 0 || u.IdleSince == nil {
		t.Fatal(u)
	}
	ageIdle(m, id)
	if op := stopAndWait(t, m); op.State != "succeeded" {
		t.Fatal(op)
	}
}
func TestQueuedIdleStopRechecksUsage(t *testing.T) {
	m, _, d, id := startedUsage(t)
	_ = m.WindowLease(id, d, 1, "window-a", true)
	ageIdle(m, id)
	m.serial.Lock()
	m.stopIdle(time.Now())
	_ = m.WindowLease(id, d, 1, "reopened", false)
	m.serial.Unlock()
	for key, op := range m.Snapshot().Operations {
		if op.Request.IfIdle {
			wait(t, m, key)
		}
	}
	if in := m.Snapshot().Instances[id]; in.State != "ready" {
		t.Fatal("queued stop killed reopened window", in)
	}
}
func TestKeepAlivePersistsAcrossRestarts(t *testing.T) {
	m, _, d, id := startedUsage(t)
	_ = m.WindowLease(id, d, 1, "window-a", true)
	if err := m.SetKeepAlive(id, d, 1, true); err != nil {
		t.Fatal(err)
	}
	ageIdle(m, id)
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 1 {
		t.Fatal("keep-alive ignored")
	}
	if op := submit(t, m, d, "explicit-stop", "stop", "app", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, d, "restart", "start", "app", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if !m.Snapshot().Instances[id].KeepAlive {
		t.Fatal("policy lost on start")
	}
	_ = m.Close()
	reopened, err := Open(m.root, m.owner, m.node, m.caps, m.driver)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if !reopened.Snapshot().Instances[id].KeepAlive {
		t.Fatal("policy lost on Runner restart")
	}
}
func TestIdleStopHonorsSaveHook(t *testing.T) {
	m, f, d, id := startedUsage(t)
	f.blocked = true
	_ = m.WindowLease(id, d, 1, "window-a", true)
	ageIdle(m, id)
	if op := stopAndWait(t, m); op.State != "failed" {
		t.Fatal(op)
	}
	if in := m.Snapshot().Instances[id]; in.State != "stop_blocked" || len(in.Resources) == 0 {
		t.Fatal("unsafe save interruption", in)
	}
	release, err := m.BeginUse(id, d, 1)
	if err != nil {
		t.Fatal("blocked app cannot finish saving", err)
	}
	release()
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 2 {
		t.Fatal("repeated blocked stop without user recovery")
	}
}
func TestLegacyClientsDoNotOptInOnStatusOrHTTP(t *testing.T) {
	m, _, d := setup(t)
	submit(t, m, d, "start", "start", "app", 0)
	id := m.instanceID(d, "app")
	release, err := m.BeginUse(id, d, 1)
	if err != nil {
		t.Fatal(err)
	}
	release()
	ageIdle(m, id)
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 1 {
		t.Fatal("legacy client killed without lease support")
	}
}

func TestRunnerRestartRecoversAndReclaimsIdleBackend(t *testing.T) {
	m, f, d, id := startedUsage(t)
	_ = m.Close()
	reopened, err := Open(m.root, m.owner, m.node, m.caps, f)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	hooks := len(f.hooks)
	reopened.observeOnce()
	in := reopened.Snapshot().Instances[id]
	if in.State != "recovered" || in.Generation != 1 || len(f.hooks) != hooks {
		t.Fatal("recovery must preserve the binding without replaying hooks", in)
	}
	ageIdle(reopened, id)
	reopened.stopIdle(time.Now())
	if len(reopened.Snapshot().Operations) != 1 {
		t.Fatal("stopped before existing windows had time to reconnect")
	}
	if err := reopened.WindowLease(id, d, 1, "reconnected", false); err != nil {
		t.Fatal(err)
	}
	reopened.mu.Lock()
	reopened.usage[id].reconnectUntil = time.Now().Add(-time.Second)
	reopened.mu.Unlock()
	ageIdle(reopened, id)
	reopened.stopIdle(time.Now())
	if len(reopened.Snapshot().Operations) != 1 {
		t.Fatal("stopped a reconnected window")
	}
	_ = reopened.WindowLease(id, d, 1, "reconnected", true)
	ageIdle(reopened, id)
	if op := stopAndWait(t, reopened); op.State != "succeeded" {
		t.Fatal(op)
	}
	if in = reopened.Snapshot().Instances[id]; in.State != "stopped" || len(in.Resources) != 0 {
		t.Fatal("recovered backend remained running after its last window closed", in)
	}
}

func TestObserveClearsDeadRecordsAfterRunnerRestart(t *testing.T) {
	m, f, d, id := startedUsage(t)
	resource := m.Snapshot().Instances[id].Resources[0]
	_ = m.Close()
	f.mu.Lock()
	f.alive[resource.ID] = false
	f.mu.Unlock()
	reopened, err := Open(m.root, m.owner, m.node, m.caps, f)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	hooks := len(f.hooks)
	reopened.observeOnce()
	in := reopened.Snapshot().Instances[id]
	if in.State != "stopped" || len(in.Resources) != 0 || in.Generation != 2 || len(f.hooks) != hooks {
		t.Fatal("dead record retained or hooks replayed", in, d)
	}
}

func TestRecoveredBackendStillHonorsKeepAliveAndSaveGuard(t *testing.T) {
	m, f, d, id := startedUsage(t)
	_ = m.WindowLease(id, d, 1, "window-a", true)
	_ = m.update(func() { m.ledger.Instances[id].State = "recovered" })
	_ = m.SetKeepAlive(id, d, 1, true)
	ageIdle(m, id)
	m.stopIdle(time.Now())
	if len(m.Snapshot().Operations) != 1 {
		t.Fatal("recovered keep-alive ignored")
	}
	_ = m.SetKeepAlive(id, d, 1, false)
	f.blocked = true
	ageIdle(m, id)
	if op := stopAndWait(t, m); op.State != "failed" {
		t.Fatal(op)
	}
	if in := m.Snapshot().Instances[id]; in.State != "stop_blocked" || !f.alive[in.Resources[0].ID] {
		t.Fatal("recovery bypassed save guard", in)
	}
}
