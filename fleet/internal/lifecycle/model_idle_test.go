package lifecycle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

type idleTestDriver struct {
	*fakeDriver
	connector, engine string
}

func (d *idleTestDriver) Start(ctx context.Context, c Component, p Paths, id string) (Resource, error) {
	r, err := d.fakeDriver.Start(ctx, c, p, id)
	url := d.engine
	if c.Argv[0] == "connector" {
		url = d.connector
	}
	r.Endpoints = map[string]string{"http": url}
	return r, err
}

type idleTestConnector struct {
	mu                                              sync.Mutex
	config                                          string
	receipt                                         modelIdleReceipt
	busy, loaded, loseDrain, loseResume, badReceipt bool
	drains, resumes                                 int
}

func idleTestHash(value any) string {
	b, _ := json.Marshal(value)
	var canonical any
	json.Unmarshal(b, &canonical)
	b, _ = json.Marshal(canonical)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// This fault-injection server speaks the same receipt protocol. The native
// acceptance below separately exercises the shipped Python implementation.
func (c *idleTestConnector) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var q struct {
		Method string         `json:"method"`
		Args   map[string]any `json:"args"`
	}
	if json.NewDecoder(r.Body).Decode(&q) != nil || r.Header.Get("X-Fleet-RPC-Token") == "" {
		http.Error(w, "invalid owner request", 403)
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	result := modelRPCResult{ConfigRevision: c.config, Accepting: !c.receipt.Fenced}
	switch q.Method {
	case "preview_configuration":
		result.ConfigRevision = idleTestHash(q.Args)
	case "status":
		result.EngineIdle = c.receipt
	case "idle_drain":
		c.drains++
		if c.busy {
			result.Status, result.Accepting = "busy", false
		} else if c.loaded {
			result.Status = "models_loaded_or_unknown"
		} else {
			if !c.receipt.Fenced {
				c.receipt = modelIdleReceipt{Protocol: 1, Phase: "fenced", Fenced: true, SuspendID: q.Args["suspend_id"].(string), ConfigRevision: c.config, Epoch: uint64(q.Args["idle_epoch"].(float64)) + 1}
			}
			result.Status, result.SafeToStop, result.Accepting = "fenced", true, false
			if c.loseDrain {
				c.loseDrain = false
				http.Error(w, "lost fence acknowledgement", 502)
				return
			}
		}
		result.Idle = c.receipt
		if c.badReceipt {
			result.Idle.SuspendID = "different-cycle"
		}
	case "idle_resume":
		c.resumes++
		delete(q.Args, "suspend_id")
		delete(q.Args, "config_revision")
		c.config = idleTestHash(q.Args)
		c.receipt.Phase, c.receipt.Fenced, c.receipt.ResumeRevision = "resumed", false, c.config
		result.Status, result.ConfigRevision, result.Accepting, result.Idle = "resumed", c.config, true, c.receipt
		if c.loseResume {
			c.loseResume = false
			http.Error(w, "lost wake acknowledgement", 502)
			return
		}
	default:
		http.Error(w, "unexpected method", 400)
		return
	}
	json.NewEncoder(w).Encode(result)
}

func setupIdleTest(t *testing.T) (*Manager, *idleTestDriver, *idleTestConnector, ModelIdle) {
	t.Helper()
	c := &idleTestConnector{receipt: modelIdleReceipt{Protocol: 1, Phase: "active"}}
	server := httptest.NewServer(c)
	t.Cleanup(server.Close)
	d := &idleTestDriver{fakeDriver: &fakeDriver{alive: map[string]bool{}}, connector: server.URL, engine: "http://127.0.0.1:19001"}
	m, err := Open(t.TempDir(), "idle-owner", "idle-node", proto.Capability{}, d)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Close() })
	m.SetResourceSampler(resourceInventory)
	bindings := map[string]ModelIdleBinding{}
	managed := ModelIdleManaged{RecipeID: "ollama-0.34.2-darwin", ContextLength: 2048, Parallel: 1, LoadPolicy: "on_demand"}
	for _, kind := range []string{"connector", "engine"} {
		def := definition()
		def.AppID = "model-service"
		def.Hooks = nil
		def.Components[0].Argv = []string{kind}
		def.Components[0].Ports = map[string]int{"http": 0}
		files := map[string]string{}
		scope := "model-test"
		if kind == "engine" {
			scope = "engine-test"
			def.Components[0].Resources = &ResourceRequest{MemoryBytes: 1 << 30}
			b, _ := json.Marshal(managed)
			files["engine-config.json"] = string(b)
		}
		payload, digest := bundle(t, def, files)
		if _, err = m.Stage(digest, 0, payload); err != nil {
			t.Fatal(err)
		}
		if op := submit(t, m, digest, "start-"+kind, "start", scope, 0); op.State != "succeeded" {
			t.Fatal(op.Error)
		}
		in := m.Snapshot().Instances[m.instanceID(digest, scope)]
		bindings[kind] = ModelIdleBinding{in.ID, in.Digest, in.Generation}
	}
	managed.Scope, managed.MemoryBytes = "engine-test", 1<<30
	c.config = idleTestHash(modelConfigArgs(ModelIdleConfig{Engine: "ollama", Endpoint: d.engine + "/v1", Managed: managed}))
	p, err := m.RegisterModelIdle(context.Background(), ModelIdleRegistration{ID: "test", Connector: bindings["connector"], Engine: bindings["engine"], ConfigRevision: c.config, IdleSeconds: 1})
	if err != nil {
		t.Fatal(err)
	}
	return m, d, c, p
}

func idleTestExpire(m *Manager, id string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.ledger.ModelIdle[id].HoldUntil = time.Now().Add(-time.Minute)
}

func waitIdleTest(t *testing.T, m *Manager, id, state string) ModelIdle {
	t.Helper()
	deadline := time.Now().Add(6 * time.Second)
	for time.Now().Before(deadline) {
		m.driveModelIdle(id)
		p, err := m.ModelIdleStatus(id)
		if err != nil {
			t.Fatal(err)
		}
		if p.State == state {
			return p
		}
		if p.State == "recovery_required" || p.State == "disabled" {
			t.Fatalf("expected %s, got %+v", state, p)
		}
		time.Sleep(5 * time.Millisecond)
	}
	p, _ := m.ModelIdleStatus(id)
	t.Fatalf("idle state deadline: %+v", p)
	return ModelIdle{}
}

func TestModelIdleCycleRebindsAndBoundsHistory(t *testing.T) {
	m, d, c, p := setupIdleTest(t)
	for cycle := 0; cycle < 7; cycle++ {
		idleTestExpire(m, p.ID)
		p = waitIdleTest(t, m, p.ID, "sleeping")
		in := m.Snapshot().Instances[p.Engine.InstanceID]
		if in.State != "stopped" || len(in.Resources) != 0 || len(in.Reservations) != 0 {
			t.Fatal("sleep released before exit", in)
		}
		if _, err := m.BeginUse(p.Engine.InstanceID, p.Engine.Revision, p.Engine.Generation); err == nil {
			t.Fatal("sleeping engine admitted direct use")
		}
		if err := m.SetKeepAlive(p.Connector.InstanceID, p.Connector.Revision, p.Connector.Generation, false); err == nil {
			t.Fatal("connector generic idle shutdown bypassed coordination")
		}
		d.engine = fmt.Sprintf("http://127.0.0.1:%d", 19002+cycle)
		if _, err := m.WakeModelIdle(p.ID, p.Revision); err != nil {
			t.Fatal(err)
		}
		p = waitIdleTest(t, m, p.ID, "active")
		if p.Engine.Generation != uint64(3+2*cycle) || p.Configuration.Endpoint != d.engine+"/v1" {
			t.Fatal("wake did not bind the new owned generation", p)
		}
		in = m.Snapshot().Instances[p.Engine.InstanceID]
		if in.ReadyGeneration != p.Engine.Generation || len(in.Reservations) != 1 {
			t.Fatal("awake resource budget missing", in)
		}
		c.mu.Lock()
		hash := c.config
		c.mu.Unlock()
		if p.ConfigRevision != hash {
			t.Fatal("published configuration differs from resumed connector")
		}
	}
	count := 0
	for _, op := range m.Snapshot().Operations {
		if op.ModelIdleID == p.ID {
			count++
		}
	}
	if count > 11 {
		t.Fatalf("unbounded automatic operation history: %d", count)
	}
	if p.Connector.Generation != 1 {
		t.Fatal("idle cycles unnecessarily restarted connector")
	}
}

func TestModelIdleBusyLoadedDirectUseAndLostAcknowledgements(t *testing.T) {
	m, d, c, p := setupIdleTest(t)
	idleTestExpire(m, p.ID)
	c.mu.Lock()
	c.busy = true
	c.mu.Unlock()
	m.driveModelIdle(p.ID)
	p, _ = m.ModelIdleStatus(p.ID)
	if p.State != "fencing" || p.Error != "" {
		t.Fatal("temporary maintenance treated as permanent failure", p)
	}
	c.mu.Lock()
	c.busy = false
	c.loaded = true
	c.mu.Unlock()
	m.driveModelIdle(p.ID)
	if m.Snapshot().Instances[p.Engine.InstanceID].State != "ready" {
		t.Fatal("loaded/unknown models were stopped")
	}
	release, err := m.BeginUse(p.Engine.InstanceID, p.Engine.Revision, p.Engine.Generation)
	if err != nil {
		t.Fatal(err)
	}
	c.mu.Lock()
	c.loaded = false
	c.loseDrain = true
	c.mu.Unlock()
	m.driveModelIdle(p.ID) // durable fence, lost response
	m.driveModelIdle(p.ID) // same receipt, direct caller still owns engine
	if m.Snapshot().Instances[p.Engine.InstanceID].State != "ready" {
		t.Fatal("engine stopped with active direct call")
	}
	release()
	p = waitIdleTest(t, m, p.ID, "sleeping")
	fence := p.FenceID
	c.mu.Lock()
	c.loseResume = true
	c.mu.Unlock()
	if _, err = m.WakeModelIdle(p.ID, p.Revision); err != nil {
		t.Fatal(err)
	}
	p = waitIdleTest(t, m, p.ID, "active")
	d.mu.Lock()
	starts := d.starts
	d.mu.Unlock()
	if starts != 3 || p.FenceID != fence {
		t.Fatal("lost receipt replayed lifecycle action", starts, p)
	}
}

func TestModelIdleBadReceiptCannotStopAndUnknownCannotRestart(t *testing.T) {
	t.Run("receipt", func(t *testing.T) {
		m, _, c, p := setupIdleTest(t)
		c.mu.Lock()
		c.badReceipt = true
		c.mu.Unlock()
		idleTestExpire(m, p.ID)
		m.driveModelIdle(p.ID)
		p, _ = m.ModelIdleStatus(p.ID)
		if p.State != "recovery_required" || m.Snapshot().Instances[p.Engine.InstanceID].State != "ready" {
			t.Fatal(p)
		}
	})
	t.Run("unknown", func(t *testing.T) {
		m, d, _, p := setupIdleTest(t)
		idleTestExpire(m, p.ID)
		p = waitIdleTest(t, m, p.ID, "sleeping")
		m.mu.Lock()
		row := m.ledger.ModelIdle[p.ID]
		row.State = "waking"
		row.StartOperation = "uncertain-wake"
		m.ledger.Operations[row.StartOperation] = &Operation{ModelIdleID: p.ID, State: "unknown", Request: Request{OperationID: row.StartOperation, Action: "start", Digest: p.Engine.Revision, Scope: "engine-test", Generation: p.Engine.Generation}}
		m.mu.Unlock()
		m.driveModelIdle(p.ID)
		m.driveModelIdle(p.ID)
		p, _ = m.ModelIdleStatus(p.ID)
		d.mu.Lock()
		starts := d.starts
		d.mu.Unlock()
		if p.State != "recovery_required" || starts != 2 {
			t.Fatal("unknown wake was replayed", p, starts)
		}
	})
}

func TestModelIdleExplicitStopSupersedesQueuedWake(t *testing.T) {
	m, d, _, p := setupIdleTest(t)
	idleTestExpire(m, p.ID)
	p = waitIdleTest(t, m, p.ID, "sleeping")
	// Hold the actual lifecycle executor, not the status/RPC observer.
	m.serial.Lock()
	locked := true
	defer func() {
		if locked {
			m.serial.Unlock()
		}
	}()
	if _, err := m.WakeModelIdle(p.ID, p.Revision); err != nil {
		t.Fatal(err)
	}
	p = waitIdleTest(t, m, p.ID, "waking")
	_, err := m.Submit(Request{Protocol: 1, OperationID: "owner-stop", Action: "stop", Digest: p.Connector.Revision, Scope: "model-test", Generation: p.Connector.Generation})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = m.WakeModelIdle(p.ID, p.Revision); err == nil {
		t.Fatal("old wake overrode Stop")
	}
	m.serial.Unlock()
	locked = false
	if op := wait(t, m, "owner-stop"); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := wait(t, m, p.StartOperation); op.State != "failed" {
		t.Fatal("superseded start executed", op)
	}
	d.mu.Lock()
	starts := d.starts
	d.mu.Unlock()
	if starts != 2 {
		t.Fatal("Stop allowed another engine process", starts)
	}
	if row, _ := m.ModelIdleStatus(p.ID); row.Enabled || row.State != "disabled" {
		t.Fatal(row)
	}
}

func TestModelIdleRegistrationRejectsChangedOwnershipAndPreservesLedger(t *testing.T) {
	m, _, _, p := setupIdleTest(t)
	q := ModelIdleRegistration{ID: p.ID, Revision: p.Revision, Connector: p.Connector, Engine: p.Engine, ConfigRevision: p.ConfigRevision, IdleSeconds: 1}
	for _, change := range []func(*ModelIdleRegistration){
		func(q *ModelIdleRegistration) { q.Engine.Generation++ },
		func(q *ModelIdleRegistration) { q.ID = "another-deployment" },
		func(q *ModelIdleRegistration) { q.ConfigRevision = strings.Repeat("0", 64) },
		func(q *ModelIdleRegistration) { q.Revision-- },
		func(q *ModelIdleRegistration) { q.Revision = ^uint64(0) },
	} {
		bad := q
		change(&bad)
		if _, err := m.RegisterModelIdle(context.Background(), bad); err == nil {
			t.Fatal("invalid registration accepted", bad)
		}
	}
	// Owner policy writes and wake are durable, not attached to a UI session.
	if _, err := m.WakeModelIdle(p.ID, p.Revision); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	if err != nil {
		t.Fatal(err)
	}
	var saved Ledger
	if json.Unmarshal(body, &saved) != nil {
		t.Fatal("invalid durable ledger")
	}
	if saved.ModelIdleProtocol != 1 || !saved.ModelIdle[p.ID].Enabled || saved.ModelIdle[p.ID].Engine != p.Engine {
		t.Fatal("missing durable exact binding")
	}
}

func TestModelIdleWakeBeforeStopAndRegistrationDeadline(t *testing.T) {
	m, _, c, p := setupIdleTest(t)
	c.mu.Lock()
	c.busy = true
	c.mu.Unlock()
	idleTestExpire(m, p.ID)
	m.driveModelIdle(p.ID)
	if _, err := m.WakeModelIdle(p.ID, p.Revision); err != nil {
		t.Fatal(err)
	}
	p = waitIdleTest(t, m, p.ID, "active")
	if p.Engine.Generation != 1 || !p.HoldUntil.After(time.Now()) {
		t.Fatal("new demand did not preserve the live generation", p)
	}
	m.modelIdleSerial.Lock()
	defer m.modelIdleSerial.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	_, err := m.RegisterModelIdle(ctx, ModelIdleRegistration{ID: p.ID, Revision: p.Revision, Connector: p.Connector, Engine: p.Engine, ConfigRevision: p.ConfigRevision, IdleSeconds: 1})
	if err != context.DeadlineExceeded {
		t.Fatal("registration ignored caller deadline", err)
	}
}

func TestModelIdlePersistenceFailureDoesNotAuthorizeStop(t *testing.T) {
	m, _, _, p := setupIdleTest(t)
	// Prevent atomic ledger replacement while keeping the previous durable
	// state readable. No filesystem permission assumptions (tests may be root).
	path := filepath.Join(m.root, "ledger.json")
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(path, path+".saved"); err != nil {
		t.Fatal(err)
	}
	if err = os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	defer func() { os.Remove(path); os.Rename(path+".saved", path) }()
	if _, err = m.WakeModelIdle(p.ID, p.Revision); err == nil {
		t.Fatal("wake acknowledged before durable write")
	}
	idleTestExpire(m, p.ID)
	m.driveModelIdle(p.ID)
	row, _ := m.ModelIdleStatus(p.ID)
	if row.State != "active" || row.Cycle != 0 || m.Snapshot().Instances[p.Engine.InstanceID].State != "ready" {
		t.Fatal("undurable intent stopped engine", row)
	}
	retained, err := os.ReadFile(path + ".saved")
	if err != nil || string(retained) != string(before) {
		t.Fatal("previous durable record changed", err)
	}
}

func TestModelIdleRunnerRestartNeverReplaysUncertainWake(t *testing.T) {
	m, d, _, p := setupIdleTest(t)
	idleTestExpire(m, p.ID)
	p = waitIdleTest(t, m, p.ID, "sleeping")
	m.mu.Lock()
	row := m.ledger.ModelIdle[p.ID]
	row.State = "waking"
	row.WakeRequested = true
	row.StartOperation = "crashed-wake"
	m.ledger.Operations[row.StartOperation] = &Operation{ModelIdleID: p.ID, State: "running", Request: Request{Protocol: 1, OperationID: row.StartOperation, Action: "start", Digest: p.Engine.Revision, Scope: "engine-test", Generation: p.Engine.Generation}}
	err := m.persist()
	m.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	if err = m.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, d)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	reopened.driveModelIdle(p.ID)
	snapshot := reopened.Snapshot()
	if snapshot.Operations["crashed-wake"].State != "unknown" || snapshot.ModelIdle[p.ID].StartOperation != "crashed-wake" || snapshot.ModelIdle[p.ID].Engine != p.Engine {
		t.Fatal("restart lost uncertainty or durable binding")
	}
	d.mu.Lock()
	starts := d.starts
	d.mu.Unlock()
	if starts != 2 || snapshot.Instances[p.Engine.InstanceID].State != "stopped" {
		t.Fatal("restart replayed uncertain start", starts)
	}
}
