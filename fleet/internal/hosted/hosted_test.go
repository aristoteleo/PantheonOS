package hosted

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
)

const (
	fleetID   = "f_0123456789abcdef"
	revAgent  = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	revShell  = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	releaseOK = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
)

// fakeBus answers node commands from a handler and serves fixed records.
type fakeBus struct {
	mu      sync.Mutex
	records []map[string]any
	handle  func(node string, payload map[string]any) map[string]any
	sent    []map[string]any
}

func (b *fakeBus) Records(context.Context, string) ([]map[string]any, error) { return b.records, nil }
func (b *fakeBus) Subscribe(string, string, func([]byte)) (func(), error)    { return func() {}, nil }
func (b *fakeBus) Request(_ context.Context, _ string, node string, payload any) (json.RawMessage, error) {
	raw, _ := json.Marshal(payload)
	var p map[string]any
	_ = json.Unmarshal(raw, &p)
	b.mu.Lock()
	b.sent = append(b.sent, p)
	b.mu.Unlock()
	out, _ := json.Marshal(b.handle(node, p))
	return out, nil
}

func (b *fakeBus) count(match func(map[string]any) bool) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := 0
	for _, p := range b.sent {
		if match(p) {
			n++
		}
	}
	return n
}

// fakeGrants is an in-memory grant authority with operation idempotency.
type fakeGrants struct {
	mu      sync.Mutex
	byOp    map[string]map[string]any
	issued  []appgateway.DependencyRequest
	revoked map[string]bool
	renewed int
}

func (g *fakeGrants) IssueDependency(_ context.Context, q appgateway.DependencyRequest) (map[string]any, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if old, ok := g.byOp[q.Operation]; ok {
		if g.revoked[old["grant_id"].(string)] {
			return nil, &appgateway.DependencyError{Status: 410, Reason: "revoked"}
		}
		return old, nil
	}
	token := fmt.Sprintf("%064x", len(g.byOp)+1)
	sum := sha256.Sum256([]byte(token))
	grant := map[string]any{"endpoint": "https://" + appgateway.Host(q.Provider.Instance, "backend", "http", q.Provider.Generation, "apps.test") + "/rpc",
		"access_token": token, "grant_id": hex.EncodeToString(sum[:]), "expires": q.Expires}
	g.byOp[q.Operation] = grant
	g.issued = append(g.issued, q)
	return grant, nil
}

func (g *fakeGrants) RenewDependency(_ context.Context, _, id string, expires int64) (map[string]any, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.revoked[id] {
		return nil, &appgateway.DependencyError{Status: 410, Reason: "gone"}
	}
	g.renewed++
	return map[string]any{"grant_id": id, "expires": expires}, nil
}

func (g *fakeGrants) RevokeDependency(_ string, id string) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.revoked[id] = true
	return nil
}

func record(id, name, kind string, caps []string, instances ...map[string]any) map[string]any {
	apps := []any{}
	for _, i := range instances {
		apps = append(apps, i)
	}
	return map[string]any{"node_id": id, "name": name, "kind": kind, "last_seen": time.Now().UTC().Format(time.RFC3339Nano),
		"version": "0.9.0", "labels": []any{"gpu"},
		"capability": map[string]any{"os": "linux", "arch": "amd64", "cpu_cores": 4.0, "ram_gb": 8.0, "caps": anyList(caps),
			"runtimes": map[string]any{"app-lifecycle": "1"}},
		"state": map[string]any{"status": "online", "load": map[string]any{"cpu": 0.1}, "instances": apps}}
}

func anyList(items []string) []any {
	out := []any{}
	for _, s := range items {
		out = append(out, s)
	}
	return out
}

func storeWith(t *testing.T, apps map[string]deployments.AppSpec) *deployments.Store {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "deployments")
	store, err := deployments.Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	if _, err := store.Put(fleetID, "general-team", 0, deployments.Spec{Release: deployments.Release{URL: "https://example.test/r.tar.gz", SHA256: releaseOK}, Apps: apps}); err != nil {
		t.Fatal(err)
	}
	return store
}

func rpc(t *testing.T, h *Host, b apptransport.Binding, method string, args any) (int, map[string]any) {
	t.Helper()
	payload, _ := json.Marshal(map[string]any{"method": method, "args": args, "timeout_s": 10})
	status, body := h.Invoke(context.Background(), b, payload)
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatal(string(body))
	}
	return status, out
}

func TestHostedFleetAnswersOnlyAtItsDeploymentBinding(t *testing.T) {
	store := storeWith(t, map[string]deployments.AppSpec{"fleet": {Hosted: "fleet", Scope: "shared-fleet-management", Intent: deployments.Running}})
	bus := &fakeBus{records: []map[string]any{record("n_mac", "Mac", "machine", []string{"proc", "fs:local"})}}
	h := NewHost(store)
	h.Register("fleet", &Fleet{Bus: bus, Store: store})
	b, _ := Binding(fleetID, "general-team", "fleet", "fleet")
	status, out := rpc(t, h, b, "fleet_list_nodes", map[string]any{})
	result, _ := out["result"].(map[string]any)
	if status != http.StatusOK || out["success"] != true || result["count"] != 1.0 {
		t.Fatalf("%d %v", status, out)
	}
	node := result["nodes"].([]any)[0].(map[string]any)
	if node["shell"] != "posix" || node["status"] != "online" || node["is_self"] != false {
		t.Fatalf("summary: %v", node)
	}
	if status, out := rpc(t, h, b, "fleet_list_nodes", map[string]any{"surprise": 1}); status != http.StatusBadRequest || out["success"] != false {
		t.Fatalf("unknown argument accepted: %d %v", status, out)
	}
	other := b
	other.Instance = InstanceID(fleetID, "general-team", "other")
	if status, _ := rpc(t, h, other, "fleet_list_nodes", map[string]any{}); status != http.StatusNotFound {
		t.Fatalf("another identity answered: %d", status)
	}
	stale := b
	stale.Revision = releaseOK
	if status, _ := rpc(t, h, stale, "fleet_list_nodes", map[string]any{}); status != http.StatusNotFound {
		t.Fatal("a superseded contract answered")
	}
	// Owner stop: the binding stops answering.
	d, _ := store.Get(fleetID, "general-team")
	if _, err := store.Patch(fleetID, "general-team", d.Revision, "fleet", func(a *deployments.AppSpec) error {
		a.Intent = deployments.Stopped
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := h.Verify(b); err == nil {
		t.Fatal("a stopped hosted App still verifies")
	}
}

func TestNodeServiceStartsTheBuiltinOnceAndReusesTheRegistry(t *testing.T) {
	store := storeWith(t, map[string]deployments.AppSpec{"fleet": {Hosted: "fleet", Scope: "s", Intent: deployments.Running}})
	bus := &fakeBus{records: []map[string]any{record("n_mac", "Mac", "machine", []string{"proc", "fs:local"})}}
	bus.handle = func(node string, p map[string]any) map[string]any { return map[string]any{"ok": true} }
	f := &Fleet{Bus: bus, Store: store, NATSURL: "nats://bus.test:4222", MintApps: func(fleet string, ttl time.Duration) (auth.AppsCredential, error) {
		return auth.AppsCredential{JWT: "jwt", Seed: "seed", SubjectPrefix: "fleet." + fleet + ".apps", InboxPrefix: "_INBOX_" + fleet + ".apps.x", Expires: time.Now().Add(ttl).Unix()}, nil
	}}
	call := func(args string) map[string]any {
		out, err := f.Call(context.Background(), &Call{Fleet: fleetID, Deployment: "general-team", App: "fleet", Method: "node_service", Args: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		return out.(map[string]any)
	}
	out := call(`{"service": "file_manager", "node_id": "n_mac"}`)
	if out["success"] != true || out["subject_prefix"] != "fleet."+fleetID+".apps" || out["invocation"] != "direct" {
		t.Fatalf("%v", out)
	}
	start := bus.sent[len(bus.sent)-1]
	app := start["app"].(map[string]any)
	env := app["env"].(map[string]any)
	sum := sha256.Sum256([]byte("n_mac"))
	scope := "node" + hex.EncodeToString(sum[:])[:16] + "-app"
	id := sha256.Sum256([]byte(fleetID + "-app-node-files-" + scope))
	if start["type"] != "app_start" || app["app_id"] != "node-files" || app["runtime"] != "builtin" || app["scope"] != scope ||
		app["service_id"] != hex.EncodeToString(id[:]) || env["NATS_SERVERS"] != "nats://bus.test:4222" || env["PANTHEON_USER_SEED"] != fleetID {
		t.Fatalf("app_start: %v", start)
	}
	// Once the registry advertises it, it is reused without another start.
	bus.records = []map[string]any{record("n_mac", "Mac", "machine", []string{"proc", "fs:local"},
		map[string]any{"app_id": "node-files", "scope": scope, "service_id": "existing", "health": "healthy"})}
	before := len(bus.sent)
	if out := call(`{"service": "file_transfer", "node_id": "n_mac"}`); out["service_id"] != "existing" || out["invocation"] != "file_transfer" || len(bus.sent) != before {
		t.Fatalf("%v", out)
	}
	if out := call(`{"service": "browser", "node_id": "n_mac"}`); out["success"] != false {
		t.Fatal("only Files and PTY are node services")
	}
}

func TestClusterSignInIsTheOwnersNotTheAgents(t *testing.T) {
	store := storeWith(t, map[string]deployments.AppSpec{"fleet": {Hosted: "fleet", Scope: "s", Intent: deployments.Running}})
	r := record("n_mac", "Mac", "machine", []string{"proc"})
	r["capability"].(map[string]any)["runtimes"] = map[string]any{"hpc-connector": "1"}
	bus := &fakeBus{records: []map[string]any{r}}
	bus.handle = func(node string, p map[string]any) map[string]any {
		return map[string]any{"state": "prompt", "echo": p["answer"]}
	}
	f := &Fleet{Bus: bus, Store: store}
	args := json.RawMessage(`{"node_id": "n_mac", "action": "answer", "cluster_id": "sherlock", "answer": "1", "remember": true}`)
	out, err := f.Call(context.Background(), &Call{Fleet: fleetID, Method: "fleet_hpc_cluster", Args: args, Owner: true})
	if err != nil || out.(map[string]any)["success"] != true || out.(map[string]any)["echo"] != "1" {
		t.Fatalf("owner answer: %v %v", out, err)
	}
	agent, err := f.Call(context.Background(), &Call{Fleet: fleetID, Method: "fleet_hpc_cluster",
		Args: json.RawMessage(`{"node_id": "n_mac", "action": "sign_in", "cluster_id": "sherlock"}`)})
	if err != nil || agent.(map[string]any)["success"] != false || !strings.Contains(fmt.Sprint(agent), "Sign-in happens in the Fleet app") {
		t.Fatalf("agent sign-in: %v %v", agent, err)
	}
}

// The Agent's own manifest: it depends on the hosted Fleet and a Shell.
const agentManifest = `{"apiVersion": 2, "id": "agent", "version": "1.0.0", "dependencies": {
 "fleet": {"range": "^0.8.0", "uses": ["fleet-management@1"], "binding": "runtime"},
 "shell": {"range": "*", "uses": ["shell@1"], "binding": "runtime"}}}`

const shellManifest = `{"apiVersion": 2, "id": "shell", "version": "1.0.0", "provides": {
 "interfaces": [{"name": "shell", "version": 1, "tools": ["run_command"]},
   {"name": "resource-session", "version": 1, "tools": ["resource_session_acquire", "resource_session_get", "resource_session_renew", "resource_session_release"]}],
 "tools": [{"name": "run_command", "params": [{"name": "command"}, {"name": "shell_id"}]},
   {"name": "resource_session_acquire", "params": [{"name": "owner_ref"}, {"name": "lease_id"}, {"name": "kind"}, {"name": "ttl_seconds"}]},
   {"name": "resource_session_get", "params": [{"name": "owner_ref"}, {"name": "lease_id"}]},
   {"name": "resource_session_renew", "params": [{"name": "owner_ref"}, {"name": "lease_id"}, {"name": "ttl_seconds"}]},
   {"name": "resource_session_release", "params": [{"name": "owner_ref"}, {"name": "lease_id"}]}]}}`

func TestAllocatorBindsRetiresAndMaintainsAnAgentsSessions(t *testing.T) {
	policies := `{"values": {"dependency_binding": {"protocol": 1, "policies": {"agent": {
	 "consumer": {"$app": "agent", "late": true},
	 "bindings": {
	  "fleet": {"app_id": "fleet", "provider": {"$app": "fleet", "late": true, "component": "backend", "port": "http"},
	            "methods": {"fleet_list_nodes": {"arguments": [], "bound": {}}}},
	  "shell": {"app_id": "shell", "provider": {"$app": "shell", "late": true, "component": "backend", "port": "http"},
	            "methods": {"run_command": {"arguments": ["command"], "bound": {}}},
	            "resource": {"kind": "shell", "arguments": {"run_command": "shell_id"}}}}}}}}}`
	store := storeWith(t, map[string]deployments.AppSpec{
		"fleet":     {Hosted: "fleet", Scope: "fleet", Intent: deployments.Running},
		"allocator": {Hosted: "dependency-binding", Scope: "allocator", Intent: deployments.Running, Config: map[string]json.RawMessage{"backend": json.RawMessage(policies)}},
		"agent":     {Package: "agent", Scope: "agent", Intent: deployments.Running},
		"shell":     {Package: "shell", Scope: "shell", Intent: deployments.Running},
	})
	d, _ := store.Get(fleetID, "general-team")
	fleetBinding, _ := Binding(fleetID, "general-team", "fleet", "fleet")
	if err := store.SetStatus(fleetID, "general-team", d.Revision, deployments.Status{Apps: map[string]deployments.AppStatus{
		"agent": {NodeID: "brain", InstanceID: "i-agent", Revision: revAgent, Generation: 3, State: "ready"},
		"shell": {NodeID: "sandbox", InstanceID: "i-shell", Revision: revShell, Generation: 2, State: "ready"},
		"fleet": {NodeID: Node, InstanceID: fleetBinding.Instance, Revision: fleetBinding.Revision, Generation: 1, State: "ready"},
	}}); err != nil {
		t.Fatal(err)
	}
	agentState := "ready"
	var mu sync.Mutex
	sessionState := "active"
	bus := &fakeBus{}
	bus.handle = func(node string, p map[string]any) map[string]any {
		mu.Lock()
		defer mu.Unlock()
		switch p["method"] {
		case "status":
			instances := map[string]any{}
			if node == "brain" {
				instances["i-agent"] = map[string]any{"digest": revAgent, "generation": 3, "state": agentState, "app_id": "agent"}
			} else {
				instances["i-shell"] = map[string]any{"digest": revShell, "generation": 2, "state": "ready", "app_id": "shell"}
			}
			return map[string]any{"node_id": node, "owner": fleetID, "instances": instances}
		case "app_manifest":
			raw := agentManifest
			if p["revision"] == revShell {
				raw = shellManifest
			}
			var m any
			_ = json.Unmarshal([]byte(raw), &m)
			return map[string]any{"protocol": 1, "revision": p["revision"], "manifest": m}
		case "invoke":
			payload := p["payload"].(map[string]any)
			args := payload["args"].(map[string]any)
			if strings.HasSuffix(payload["method"].(string), "_release") {
				sessionState = "released"
			}
			return map[string]any{"response": map[string]any{"success": true, "result": map[string]any{
				"owner_ref": args["owner_ref"], "lease_id": args["lease_id"], "kind": "shell", "session_id": "sess-1",
				"state": sessionState, "expires": time.Now().Add(15 * time.Minute).Unix()}}}
		}
		return map[string]any{"error": "unexpected"}
	}
	grants := &fakeGrants{byOp: map[string]map[string]any{}, revoked: map[string]bool{}}
	alloc := &Allocator{Bus: bus, Store: store, Grants: grants, Root: t.TempDir()}
	h := NewHost(store)
	h.Register("fleet", &Fleet{Bus: bus, Store: store})
	h.Register("dependency-binding", alloc)
	allocator, _ := Binding(fleetID, "general-team", "allocator", "dependency-binding")
	bind := map[string]any{"policy_id": "agent", "owner_ref": "agent-views", "operation_id": "op-1", "aliases": []string{"shell", "fleet"}}
	status, out := rpc(t, h, allocator, "bind_dependencies", bind)
	if status != http.StatusOK {
		t.Fatalf("%d %v", status, out)
	}
	result := out["result"].(map[string]any)
	bindings := result["bindings"].(map[string]any)
	fleetGrant := bindings["fleet"].(map[string]any)
	if fleetGrant["provider"].(map[string]any)["node_id"] != Node || fleetGrant["consumer"].(map[string]any)["fleet_id"] != fleetID {
		t.Fatalf("fleet grant: %v", fleetGrant)
	}
	var shellReq appgateway.DependencyRequest
	for _, q := range grants.issued {
		if q.AppID == "shell" {
			shellReq = q
		}
	}
	if string(shellReq.Methods["run_command"].Bound["shell_id"]) != `"sess-1"` || shellReq.Consumer.Instance != "i-agent" {
		t.Fatalf("shell grant must bind the session: %+v", shellReq)
	}
	// A retried operation recovers the same grants.
	_, again := rpc(t, h, allocator, "bind_dependencies", bind)
	if again["result"].(map[string]any)["bindings"].(map[string]any)["fleet"].(map[string]any)["grant_id"] != fleetGrant["grant_id"] {
		t.Fatal("a retry minted another grant")
	}
	if status, _ := rpc(t, h, allocator, "bind_dependencies", map[string]any{"policy_id": "agent", "owner_ref": "x",
		"operation_id": "op-2", "aliases": []string{"evil"}}); status != http.StatusBadRequest {
		t.Fatal("an unapproved alias was bound")
	}
	// Retirement revokes every grant, then releases the session.
	status, out = rpc(t, h, allocator, "retire_dependencies", map[string]any{"policy_id": "agent", "owner_ref": "agent-views"})
	retired := out["result"].(map[string]any)
	if status != http.StatusOK || retired["state"] != "retired" || len(grants.revoked) != 2 {
		t.Fatalf("%d %v revoked=%d", status, out, len(grants.revoked))
	}
	if status, _ := rpc(t, h, allocator, "bind_dependencies", bind); status != http.StatusBadRequest {
		t.Fatal("a retired owner was revived")
	}
	// Maintenance revokes the grants of a consumer that stopped.
	bind2 := map[string]any{"policy_id": "agent", "owner_ref": "agent-auxiliary", "operation_id": "op-3", "aliases": []string{"fleet"}}
	if status, out := rpc(t, h, allocator, "bind_dependencies", bind2); status != http.StatusOK {
		t.Fatalf("%v", out)
	}
	mu.Lock()
	agentState = "stopped"
	mu.Unlock()
	alloc.MaintainOnce(context.Background(), fleetID)
	if len(grants.revoked) != 3 {
		t.Fatalf("a stopped consumer kept its grant: %d", len(grants.revoked))
	}
}
