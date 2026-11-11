package reconciler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/appgateway"
	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/aristoteleo/pantheonos/os/internal/lifecycle"
	"github.com/aristoteleo/pantheonos/os/internal/modelcredentials"
	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

// fakeNode applies lifecycle operations instantly.
type fakeNode struct {
	id       string
	caps     []string
	kind     string
	ledger   lifecycle.Ledger
	staged   map[string][]byte
	configs  map[string]lifecycle.AppConfiguration
	importer *modelcredentials.Importer
	vault    string
	defs     map[string]lifecycle.Definition
	mans     map[string]json.RawMessage
	// failConfigure loses the acknowledgement of the next configure calls
	// (the configuration is written), as a node that restarts does.
	failConfigure int
}

type fakeFleet struct {
	mu    sync.Mutex
	nodes map[string]*fakeNode
	calls []string
	// hang holds "node method revision" calls until their context ends, as a
	// node replaced mid-pass does (the bus still routes to its old connection).
	hang map[string]bool
}

func (f *fakeFleet) Nodes(ctx context.Context, fleet string) ([]proto.Node, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []proto.Node
	for id, n := range f.nodes {
		out = append(out, proto.Node{NodeID: id, Kind: n.kind, State: proto.State{Status: proto.StatusOnline},
			Capability: proto.Capability{OS: "linux", Arch: "amd64", Caps: n.caps, Runtimes: map[string]string{"app-lifecycle": "1"}}})
	}
	return out, nil
}

func (f *fakeFleet) Call(ctx context.Context, fleet, node string, q map[string]any, out any) error {
	f.mu.Lock()
	if f.hang[fmt.Sprint(node, " ", q["method"], " ", q["revision"])] {
		f.mu.Unlock()
		<-ctx.Done()
		return ctx.Err()
	}
	defer f.mu.Unlock()
	n := f.nodes[node]
	if n == nil {
		return fmt.Errorf("no responders")
	}
	raw, _ := json.Marshal(q)
	var c lifecycle.Command
	if err := json.Unmarshal(raw, &c); err != nil {
		return err
	}
	f.calls = append(f.calls, node+" "+c.Method)
	result, err := n.handle(c)
	if err != nil {
		return err
	}
	encoded, _ := json.Marshal(result)
	return json.Unmarshal(encoded, out)
}

func (n *fakeNode) handle(c lifecycle.Command) (any, error) {
	l := &n.ledger
	switch c.Method {
	case "status":
		return l, nil
	case "stage":
		n.staged[c.Digest] = append(n.staged[c.Digest][:c.Offset], c.Data...)
		return map[string]any{"offset": len(n.staged[c.Digest])}, nil
	case "app_manifest":
		return map[string]any{"protocol": 1, "revision": c.Revision, "manifest": n.mans[c.Revision], "definition": n.defs[c.Revision]}, nil
	case "configure":
		in := l.Instances[c.Instance]
		if in == nil || in.State != "prepared" || in.Generation != c.Generation || in.StartPreparationID != c.Configuration.Preparation {
			return nil, fmt.Errorf("configure needs the exact prepared start")
		}
		// A prepared start's configuration is written once (lifecycle
		// putAppConfigFile): only an exact retry succeeds.
		if old, ok := n.configs[c.Instance]; ok && old.Preparation == c.Configuration.Preparation {
			a, _ := json.Marshal(old)
			b, _ := json.Marshal(c.Configuration)
			if string(a) != string(b) {
				return nil, fmt.Errorf("App configuration is immutable; cancel and prepare a new start to change it")
			}
		}
		n.configs[c.Instance] = *c.Configuration
		if n.failConfigure > 0 {
			n.failConfigure--
			return nil, fmt.Errorf("no responders")
		}
		return map[string]bool{"ok": true}, nil
	case "keep_alive":
		in := l.Instances[c.Instance]
		if in == nil || in.Digest != c.Revision || in.Generation != c.Generation {
			return nil, fmt.Errorf("keep_alive needs the exact running instance")
		}
		in.KeepAlive, in.AutoStop = c.KeepAlive, true
		return map[string]bool{"ok": true}, nil
	case "credential_prepare", "credential_ensure":
		if c.Method == "credential_prepare" {
			return n.importer.Prepare(c.CredentialRef, c.CredentialEndpoint)
		}
		return map[string]bool{"ok": true}, n.importer.Ensure(c.CredentialChallenge, *c.CredentialEnvelope)
	case "submit":
		req := *c.Request
		if op := l.Operations[req.OperationID]; op != nil {
			return map[string]any{"operation": op}, nil
		}
		op := &lifecycle.Operation{Request: req, State: "succeeded"}
		l.Operations[req.OperationID] = op
		id := "i-" + req.Digest[:6]
		in := l.Instances[id]
		switch req.Action {
		case "install":
			sum := sha256.Sum256(n.staged[req.Digest])
			if hex.EncodeToString(sum[:]) != req.Digest {
				op.State, op.Error = "failed", "artifact SHA-256 mismatch"
				break
			}
			l.Installations[req.Digest] = &lifecycle.Installation{Digest: req.Digest, State: "installed", Definition: n.defs[req.Digest]}
		case "prepare_start":
			gen := uint64(1)
			if in != nil {
				gen = in.Generation + 1
			}
			l.Instances[id] = &lifecycle.Instance{ID: id, AppID: n.defs[req.Digest].AppID, Digest: req.Digest, Scope: req.Scope,
				State: "prepared", Generation: gen, StartPreparationID: req.OperationID}
		case "start":
			if in == nil || in.StartPreparationID != req.StartPreparationID || in.Generation != req.Generation {
				op.State, op.Error = "failed", "start requires the exact preparation id and generation"
				break
			}
			in.State, in.Generation, in.StartPreparationID = "ready", in.Generation+1, ""
			in.ReadyGeneration = in.Generation
		case "stop":
			if in != nil {
				in.State = "stopped"
			}
		}
		return map[string]any{"operation": op}, nil
	}
	return nil, fmt.Errorf("unsupported %s", c.Method)
}

type fakeGrants struct {
	issued  map[string]appgateway.DependencyRequest
	revoked []string
	// expireNext makes the next operation's grant expire for good, as the
	// gateway's durable record does once grantTTL has passed.
	expireNext bool
	expired    map[string]bool
	// policies is the gateway's durable operation record: a repeated
	// operation ID must carry the request it first minted.
	policies map[string]string
	// conflictNext makes the next operation ID one minted for another policy.
	conflictNext bool
}

func (g *fakeGrants) IssueDependency(ctx context.Context, q appgateway.DependencyRequest) (map[string]any, error) {
	if g.expireNext {
		g.expireNext, g.expired = false, map[string]bool{q.Operation: true}
	}
	if g.expired[q.Operation] {
		return nil, &appgateway.DependencyError{Status: 410, Reason: "dependency operation revoked or expired"}
	}
	policy := q
	policy.Expires = 0
	raw, _ := json.Marshal(policy)
	if g.policies == nil {
		g.policies = map[string]string{}
	}
	if old, ok := g.policies[q.Operation]; ok && old != string(raw) {
		return nil, &appgateway.DependencyError{Status: 409, Reason: appgateway.PolicyMismatch}
	}
	if g.conflictNext {
		g.conflictNext, g.policies[q.Operation] = false, "minted for another request"
		return nil, &appgateway.DependencyError{Status: 409, Reason: appgateway.PolicyMismatch}
	}
	g.policies[q.Operation] = string(raw)
	token := fmt.Sprintf("%064x", len(g.issued)+1)
	sum := sha256.Sum256([]byte(token))
	g.issued[hex.EncodeToString(sum[:])] = q
	return map[string]any{"grant_id": hex.EncodeToString(sum[:]), "access_token": token, "expires": q.Expires,
		"endpoint": "https://provider.apps.test/rpc"}, nil
}
func (g *fakeGrants) RenewDependency(ctx context.Context, fleet, id string, expires int64) (map[string]any, error) {
	return map[string]any{"grant_id": id, "expires": expires}, nil
}
func (g *fakeGrants) RevokeDependency(fleet, id string) error {
	g.revoked = append(g.revoked, id)
	return nil
}

func artifact(t *testing.T, dir, body string) (string, Variant) {
	sum := sha256.Sum256([]byte(body))
	rev := hex.EncodeToString(sum[:])
	if err := os.WriteFile(filepath.Join(dir, "artifacts", rev), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return rev, Variant{Revision: rev, Bytes: int64(len(body)), Artifact: "artifacts/" + rev, Version: "1.0.0"}
}

func TestReconcilerConvergesWithGrantsAndSecrets(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "release", "artifacts"), 0o700)
	allocRev, alloc := artifact(t, filepath.Join(dir, "release"), "allocator package")
	agentRev, agent := artifact(t, filepath.Join(dir, "release"), "agent package")
	alloc.AppID, agent.AppID = "pantheon-allocator", "pantheon-agent"
	release := &Release{SHA256: "x", dir: filepath.Join(dir, "release"), Index: Index{Protocol: 2, Apps: map[string]map[string]Variant{
		"allocator": {"linux-amd64": alloc}, "agent": {"linux-amd64": agent}}}}
	releases, _ := NewReleases(filepath.Join(dir, "cache"), nil)

	defs := map[string]lifecycle.Definition{
		allocRev: {AppID: "pantheon-allocator", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend"}}},
		agentRev: {AppID: "pantheon-agent", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend",
			Configuration: &lifecycle.ConfigDeclaration{Values: map[string]lifecycle.ConfigField{"name": {Required: true}, "secret_ref": {}},
				Credentials: map[string]lifecycle.ConfigField{"allocator": {Required: true}, "budget": {Required: true}}}}}},
	}
	mans := map[string]json.RawMessage{
		allocRev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-allocator","version":"1.2.0","provides":{"interfaces":[{"name":"alloc","tools":["allocate"]}],"tools":[{"name":"allocate","params":[{"name":"kind"},{"name":"session","required":false}]}]}}`),
		agentRev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-agent","version":"1.0.0","dependencies":{"pantheon-allocator":{"range":"^1.0.0","uses":["alloc@1"]}}}`),
	}
	vault := filepath.Join(dir, "vault")
	brain := &fakeNode{id: "brain", kind: "pod", caps: []string{"proc"}, staged: map[string][]byte{}, configs: map[string]lifecycle.AppConfiguration{},
		importer: modelcredentials.NewImporter(vault, "f_1", "brain"), vault: vault, defs: defs, mans: mans,
		ledger: lifecycle.Ledger{Owner: "f_1", Node: "brain", Installations: map[string]*lifecycle.Installation{}, Instances: map[string]*lifecycle.Instance{}, Operations: map[string]*lifecycle.Operation{}}}
	fleet := &fakeFleet{nodes: map[string]*fakeNode{"brain": brain}}

	store, err := deployments.Open(filepath.Join(dir, "deployments"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	secrets, err := deployments.OpenSecrets(filepath.Join(dir, "secrets"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := secrets.Put("f_1", "budget", "sk-test-budget", "https://hub.test/litellm/v1"); err != nil {
		t.Fatal(err)
	}
	pin := fmt.Sprintf("%064d", 0)
	releases.cache[pin] = release
	spec := deployments.Spec{Release: deployments.Release{URL: "https://releases.test/set.tar.gz", SHA256: pin}, Secrets: []string{"budget"},
		Apps: map[string]deployments.AppSpec{
			"allocator": {Package: "allocator", Scope: "deployment", Intent: deployments.Running},
			"agent": {Package: "agent", Scope: "deployment", Intent: deployments.Running,
				Config: map[string]json.RawMessage{"backend": json.RawMessage(`{"values":{"name":"general","secret_ref":{"$secret_ref":"budget"}},"credentials":{"budget":{"$secret":"budget"}}}`)},
				Bindings: map[string]deployments.Binding{"allocator": {App: "allocator", Component: "backend",
					Methods: json.RawMessage(`{"allocate":{"arguments":["kind"],"bound":{"session":"s1"}}}`)}}},
		}}
	if _, err := store.Put("f_1", "general-team", 0, spec); err != nil {
		t.Fatal(err)
	}
	grants := &fakeGrants{issued: map[string]appgateway.DependencyRequest{}}
	now := time.Unix(2_000_000, 0)
	r := &Reconciler{Store: store, Secrets: secrets, Releases: releases, Nodes: fleet, Grants: grants, Now: func() time.Time { return now }}
	settled := false
	for i := 0; i < 12 && !settled; i++ {
		if settled, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
	}
	d, _ := store.Get("f_1", "general-team")
	if !settled || !d.Status.Conditions[0].Status {
		t.Fatalf("did not converge: %+v\ncalls %v", d.Status, fleet.calls)
	}
	allocIn, agentIn := brain.ledger.Instances["i-"+allocRev[:6]], brain.ledger.Instances["i-"+agentRev[:6]]
	if allocIn.State != "ready" || agentIn.State != "ready" {
		t.Fatalf("instances: %+v %+v", allocIn, agentIn)
	}
	config := brain.configs["i-"+agentRev[:6]].Components["backend"]
	grant := config.Dependencies["allocator"]
	issued := grants.issued[grant.ID]
	if grant.Token == "" || issued.Provider.Instance != allocIn.ID || issued.Provider.Generation != allocIn.Generation ||
		issued.Consumer.Generation != agentIn.Generation || issued.Methods["allocate"].Bound["session"] == nil {
		t.Fatalf("grant pinned to the exact instances: %+v %+v", grant, issued)
	}
	if issued.Timeout != appgateway.MaxDependencyTimeout {
		t.Fatalf("a dependency call may run as long as an App RPC: %d", issued.Timeout)
	}
	ref := config.Credentials["budget"]
	if value, err := modelcredentials.Read(vault, ref.Ref, ref.Endpoint); err != nil || value != "sk-test-budget" {
		t.Fatalf("secret delivered to the node vault: %v %q (%+v)", err, value, ref)
	}
	if string(config.Values["secret_ref"]) != `"`+ref.Ref+`"` {
		t.Fatalf("$secret_ref renders the bare vault reference: %s", config.Values["secret_ref"])
	}
	if d.Status.Apps["agent"].Providers["allocator"] != fmt.Sprintf("brain/%s/%d", allocIn.ID, allocIn.Generation) {
		t.Fatalf("agent status pins its provider: %+v", d.Status.Apps["agent"])
	}

	// Restarting the provider restarts the consumer with a fresh grant; the
	// old grant is revoked.
	oldGrant := grant.ID
	allocIn.Generation++
	allocIn.ReadyGeneration = allocIn.Generation
	settled = false
	for i := 0; i < 12 && !settled; i++ {
		if settled, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
	}
	d, _ = store.Get("f_1", "general-team")
	newGrant := brain.configs["i-"+agentRev[:6]].Components["backend"].Dependencies["allocator"]
	if !settled || newGrant.ID == oldGrant || grants.issued[newGrant.ID].Provider.Generation != allocIn.Generation {
		t.Fatalf("consumer restarted on the new provider: %+v", d.Status.Apps["agent"])
	}
	if len(grants.revoked) != 1 || grants.revoked[0] != oldGrant {
		t.Fatalf("old grant revoked: %v", grants.revoked)
	}

	// A release that only changes an App's configuration restarts it with
	// the new configuration (no revision changes).
	d, _ = store.Get("f_1", "general-team")
	agentSpec := d.Spec.Apps["agent"]
	agentSpec.Config = map[string]json.RawMessage{"backend": json.RawMessage(`{"values":{"name":"renamed","secret_ref":{"$secret_ref":"budget"}},"credentials":{"budget":{"$secret":"budget"}}}`)}
	d.Spec.Apps["agent"] = agentSpec
	if _, err := store.Put("f_1", "general-team", d.Revision, d.Spec); err != nil {
		t.Fatal(err)
	}
	before := agentIn.Generation
	settled = false
	for i := 0; i < 12 && !settled; i++ {
		if settled, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
	}
	after := brain.ledger.Instances["i-"+agentRev[:6]].Generation
	if values := brain.configs["i-"+agentRev[:6]].Components["backend"].Values; !settled || string(values["name"]) != `"renamed"` || after == before {
		t.Fatalf("configuration change applied by a restart: %s (generation %d -> %d)", values["name"], before, after)
	}

	// A start whose grant expired before it ran (the node stayed busy past
	// grantTTL) is prepared again instead of retrying the dead grant forever.
	grants.expireNext = true
	allocIn.Generation++
	allocIn.ReadyGeneration = allocIn.Generation
	settled = false
	for i := 0; i < 16 && !settled; i++ {
		if settled, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
	}
	d, _ = store.Get("f_1", "general-team")
	renewed := brain.configs["i-"+agentRev[:6]].Components["backend"].Dependencies["allocator"]
	if !settled || len(grants.expired) != 1 || grants.issued[renewed.ID].Provider.Generation != allocIn.Generation ||
		grants.expired[grants.issued[renewed.ID].Operation] {
		t.Fatalf("expired start prepared again: %+v", d.Status.Apps["agent"])
	}

	// The provider restarts between the consumer's grant and its start: the
	// grant was minted, configure failed, and the retry of the same
	// preparation names the provider's new generation. It gets a grant for
	// that provider instead of a policy conflict on every retry.
	allocIn.Generation++
	allocIn.ReadyGeneration = allocIn.Generation
	brain.failConfigure = 1
	for i := 0; i < 16; i++ {
		if _, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
		if brain.failConfigure == 0 {
			break
		}
	}
	if agentIn = brain.ledger.Instances["i-"+agentRev[:6]]; brain.failConfigure != 0 || agentIn.State != "prepared" {
		t.Fatalf("consumer left prepared after a failed configure: %+v", agentIn)
	}
	allocIn.Generation++
	allocIn.ReadyGeneration = allocIn.Generation
	settled = false
	for i := 0; i < 16 && !settled; i++ {
		if settled, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
	}
	d, _ = store.Get("f_1", "general-team")
	raced := brain.configs["i-"+agentRev[:6]].Components["backend"].Dependencies["allocator"]
	if !settled || d.Status.Apps["agent"].State != "ready" || grants.issued[raced.ID].Provider.Generation != allocIn.Generation {
		t.Fatalf("start after a provider restart converged: %+v", d.Status.Apps["agent"])
	}

	// Any other policy conflict on a preparation's grant is final for that
	// preparation, as an expiry is: the start is prepared again.
	grants.conflictNext = true
	allocIn.Generation++
	allocIn.ReadyGeneration = allocIn.Generation
	settled = false
	for i := 0; i < 16 && !settled; i++ {
		if settled, err = r.Pass(context.Background(), "f_1", "general-team"); err != nil {
			t.Fatal(err)
		}
	}
	d, _ = store.Get("f_1", "general-team")
	if !settled || d.Status.Apps["agent"].State != "ready" || grants.conflictNext {
		t.Fatalf("conflicting start prepared again: %+v", d.Status.Apps["agent"])
	}
}

func TestCyclicUnitStartsTogetherAndRestartsTogether(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "release", "artifacts"), 0o700)
	allocRev, alloc := artifact(t, filepath.Join(dir, "release"), "allocator")
	agentRev, agent := artifact(t, filepath.Join(dir, "release"), "agent")
	alloc.AppID, agent.AppID = "pantheon-allocator", "pantheon-agent"
	release := &Release{dir: filepath.Join(dir, "release"), Index: Index{Protocol: 2, Apps: map[string]map[string]Variant{
		"allocator": {"linux-amd64": alloc}, "agent": {"linux-amd64": agent}}}}
	releases, _ := NewReleases(filepath.Join(dir, "cache"), nil)
	pin := fmt.Sprintf("%064d", 1)
	releases.cache[pin] = release
	defs := map[string]lifecycle.Definition{
		allocRev: {AppID: "pantheon-allocator", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend",
			Configuration: &lifecycle.ConfigDeclaration{Values: map[string]lifecycle.ConfigField{"policy": {Required: true}, "events": {}}}}}},
		agentRev: {AppID: "pantheon-agent", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend",
			Configuration: &lifecycle.ConfigDeclaration{Credentials: map[string]lifecycle.ConfigField{"allocator": {Required: true}}}}}},
	}
	mans := map[string]json.RawMessage{
		allocRev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-allocator","version":"1.0.0","provides":{"interfaces":[{"name":"alloc","tools":["allocate"]}],"tools":[{"name":"allocate","params":[]}]}}`),
		agentRev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-agent","version":"1.0.0","dependencies":{"pantheon-allocator":{"uses":["alloc@1"]}}}`),
	}
	brain := &fakeNode{id: "brain", kind: "pod", caps: []string{"proc"}, staged: map[string][]byte{}, configs: map[string]lifecycle.AppConfiguration{},
		importer: modelcredentials.NewImporter(filepath.Join(dir, "vault"), "f_1", "brain"), defs: defs, mans: mans,
		ledger: lifecycle.Ledger{Owner: "f_1", Node: "brain", Installations: map[string]*lifecycle.Installation{}, Instances: map[string]*lifecycle.Instance{}, Operations: map[string]*lifecycle.Operation{}}}
	fleet := &fakeFleet{nodes: map[string]*fakeNode{"brain": brain}}
	store, _ := deployments.Open(filepath.Join(dir, "deployments"))
	defer store.Close()
	spec := deployments.Spec{Release: deployments.Release{URL: "https://releases.test/set.tar.gz", SHA256: pin},
		Apps: map[string]deployments.AppSpec{
			"allocator": {Package: "allocator", Scope: "deployment", Intent: deployments.Running,
				Config: map[string]json.RawMessage{"backend": json.RawMessage(`{"values":{"policy":{"consumer":{"$app":"agent"}},"events":{"$fleet":"event_prefix"}}}`)}},
			"agent": {Package: "agent", Scope: "deployment", Intent: deployments.Running,
				Bindings: map[string]deployments.Binding{"allocator": {App: "allocator", Component: "backend", Methods: json.RawMessage(`{"allocate":{"arguments":[],"bound":{}}}`)}}},
		}}
	if _, err := store.Put("f_1", "team", 0, spec); err != nil {
		t.Fatal(err)
	}
	clock := time.Unix(3_000_000, 0)
	grants := &fakeGrants{issued: map[string]appgateway.DependencyRequest{}}
	r := &Reconciler{Store: store, Releases: releases, Nodes: fleet, Grants: grants,
		Now: func() time.Time { return clock }}
	converge := func() {
		t.Helper()
		for i := 0; i < 16; i++ {
			clock = clock.Add(11 * time.Second) // past any failure backoff
			settled, err := r.Pass(context.Background(), "f_1", "team")
			if err != nil {
				t.Fatal(err)
			}
			if settled {
				return
			}
		}
		d, _ := store.Get("f_1", "team")
		t.Fatalf("did not converge: %+v\ncalls %v", d.Status, fleet.calls)
	}
	check := func() {
		t.Helper()
		allocIn, agentIn := brain.ledger.Instances["i-"+allocRev[:6]], brain.ledger.Instances["i-"+agentRev[:6]]
		var policy struct {
			Consumer map[string]any `json:"consumer"`
		}
		json.Unmarshal(brain.configs[allocIn.ID].Components["backend"].Values["policy"], &policy)
		if policy.Consumer["instance_id"] != agentIn.ID || policy.Consumer["generation"] != float64(agentIn.Generation) {
			t.Fatalf("allocator names the running agent: %v vs %+v", policy.Consumer, agentIn)
		}
		grant := brain.configs[agentIn.ID].Components["backend"].Dependencies["allocator"]
		if grant.Provider.Generation != allocIn.Generation || grant.Consumer.Generation != agentIn.Generation {
			t.Fatalf("agent's grant pins the running allocator: %+v", grant)
		}
		if string(brain.configs[allocIn.ID].Components["backend"].Values["events"]) != `"fleet.f_1.apps.allocator"` {
			t.Fatalf("fleet marker: %s", brain.configs[allocIn.ID].Components["backend"].Values["events"])
		}
	}
	converge()
	check()
	// The agent crashes: the unit restarts as a whole and converges again.
	brain.ledger.Instances["i-"+agentRev[:6]].State = "failed"
	converge()
	check()
	if brain.ledger.Instances["i-"+allocRev[:6]].Generation != 4 {
		t.Fatalf("allocator restarted with the unit: %+v", brain.ledger.Instances["i-"+allocRev[:6]])
	}
	// A member's preparation that can no longer start (its grant expired,
	// or was minted for a replaced provider) is prepared again, as outside
	// a unit, instead of retrying the dead preparation forever.
	grants.expireNext = true
	brain.ledger.Instances["i-"+agentRev[:6]].State = "failed"
	converge()
	check()
	if len(grants.expired) != 1 {
		t.Fatalf("expired preparation exercised: %v", grants.expired)
	}
}

// A start takes several passes (reconcile, prepare, start). Each follows the
// last as soon as it made progress, not on the periodic tick.
func TestProgressIsFollowedWithoutWaitingForTheTick(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "release", "artifacts"), 0o700)
	rev, variant := artifact(t, filepath.Join(dir, "release"), "allocator package")
	variant.AppID = "pantheon-allocator"
	release := &Release{SHA256: "x", dir: filepath.Join(dir, "release"), Index: Index{Protocol: 2, Apps: map[string]map[string]Variant{
		"allocator": {"linux-amd64": variant}}}}
	releases, _ := NewReleases(filepath.Join(dir, "cache"), nil)
	pin := fmt.Sprintf("%064d", 0)
	releases.cache[pin] = release
	node := &fakeNode{id: "brain", kind: "pod", caps: []string{"proc"}, staged: map[string][]byte{}, configs: map[string]lifecycle.AppConfiguration{},
		defs:   map[string]lifecycle.Definition{rev: {AppID: "pantheon-allocator", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend"}}}},
		mans:   map[string]json.RawMessage{rev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-allocator","version":"1.2.0"}`)},
		ledger: lifecycle.Ledger{Owner: "f_1", Node: "brain", Installations: map[string]*lifecycle.Installation{}, Instances: map[string]*lifecycle.Instance{}, Operations: map[string]*lifecycle.Operation{}}}
	store, err := deployments.Open(filepath.Join(dir, "deployments"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	r := &Reconciler{Store: store, Releases: releases, Nodes: &fakeFleet{nodes: map[string]*fakeNode{"brain": node}},
		Tick: time.Hour, Next: 10 * time.Millisecond}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go r.Run(ctx)
	spec := deployments.Spec{Release: deployments.Release{URL: "https://releases.test/set.tar.gz", SHA256: pin},
		Apps: map[string]deployments.AppSpec{"allocator": {Package: "allocator", Scope: "deployment", Intent: deployments.Running}}}
	if _, err := store.Put("f_1", "team", 0, spec); err != nil {
		t.Fatal(err)
	}
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		d, _ := store.Get("f_1", "team")
		if len(d.Status.Conditions) > 0 && d.Status.Conditions[0].Status {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("waited for the tick: %+v", d.Status)
		}
	}
}

// A node that connects (its /join or /token) kicks its fleet's deployments:
// an App waiting for it is placed at once, not on the next Tick.
func TestNodeConnectingKicksItsFleet(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "release", "artifacts"), 0o700)
	rev, variant := artifact(t, filepath.Join(dir, "release"), "allocator package")
	variant.AppID = "pantheon-allocator"
	release := &Release{SHA256: "x", dir: filepath.Join(dir, "release"), Index: Index{Protocol: 2, Apps: map[string]map[string]Variant{
		"allocator": {"linux-amd64": variant}}}}
	releases, _ := NewReleases(filepath.Join(dir, "cache"), nil)
	pin := fmt.Sprintf("%064d", 0)
	releases.cache[pin] = release
	node := &fakeNode{id: "brain", kind: "pod", caps: []string{"proc"}, staged: map[string][]byte{}, configs: map[string]lifecycle.AppConfiguration{},
		defs:   map[string]lifecycle.Definition{rev: {AppID: "pantheon-allocator", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend"}}}},
		mans:   map[string]json.RawMessage{rev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-allocator","version":"1.2.0"}`)},
		ledger: lifecycle.Ledger{Owner: "f_1", Node: "brain", Installations: map[string]*lifecycle.Installation{}, Instances: map[string]*lifecycle.Instance{}, Operations: map[string]*lifecycle.Operation{}}}
	store, err := deployments.Open(filepath.Join(dir, "deployments"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	fleet := &fakeFleet{nodes: map[string]*fakeNode{}}
	r := &Reconciler{Store: store, Releases: releases, Nodes: fleet, Tick: time.Hour, Settled: time.Hour, Next: 10 * time.Millisecond}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go r.Run(ctx)
	spec := deployments.Spec{Release: deployments.Release{URL: "https://releases.test/set.tar.gz", SHA256: pin},
		Apps: map[string]deployments.AppSpec{"allocator": {Package: "allocator", Scope: "deployment", Intent: deployments.Running}}}
	if _, err := store.Put("f_1", "team", 0, spec); err != nil {
		t.Fatal(err)
	}
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		d, _ := store.Get("f_1", "team")
		if d.Status.Apps["allocator"].State == "waiting" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("no node yet: the App waits: %+v", d.Status)
		}
	}
	fleet.mu.Lock()
	fleet.nodes["brain"] = node
	fleet.mu.Unlock()
	time.Sleep(100 * time.Millisecond)
	if d, _ := store.Get("f_1", "team"); d.Status.Apps["allocator"].State != "waiting" {
		t.Fatalf("nothing passed it before the kick: %+v", d.Status)
	}
	r.Kick("f_1")
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		d, _ := store.Get("f_1", "team")
		if len(d.Status.Conditions) > 0 && d.Status.Conditions[0].Status {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("waited for the tick after the node connected: %+v", d.Status)
		}
	}
}

// A provider whose node takes a request and never answers fails only its
// consumer's start, in CallTimeout; the pass returns and the other Apps keep
// converging instead of waiting for the pass's deadline.
func TestUnansweredProviderDoesNotHoldThePass(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "release", "artifacts"), 0o700)
	allocRev, alloc := artifact(t, filepath.Join(dir, "release"), "allocator package")
	agentRev, agent := artifact(t, filepath.Join(dir, "release"), "agent package")
	webRev, web := artifact(t, filepath.Join(dir, "release"), "web package")
	alloc.AppID, agent.AppID, web.AppID = "pantheon-allocator", "pantheon-agent", "pantheon-web"
	release := &Release{SHA256: "x", dir: filepath.Join(dir, "release"), Index: Index{Protocol: 2, Apps: map[string]map[string]Variant{
		"allocator": {"linux-amd64": alloc}, "agent": {"linux-amd64": agent}, "web": {"linux-amd64": web}}}}
	releases, _ := NewReleases(filepath.Join(dir, "cache"), nil)
	pin := fmt.Sprintf("%064d", 0)
	releases.cache[pin] = release
	defs := map[string]lifecycle.Definition{
		allocRev: {AppID: "pantheon-allocator", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend"}}},
		agentRev: {AppID: "pantheon-agent", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend",
			Configuration: &lifecycle.ConfigDeclaration{Credentials: map[string]lifecycle.ConfigField{"allocator": {Required: true}}}}}},
		webRev: {AppID: "pantheon-web", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend"}}},
	}
	mans := map[string]json.RawMessage{
		allocRev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-allocator","version":"1.2.0","provides":{"interfaces":[{"name":"alloc","tools":["allocate"]}],"tools":[{"name":"allocate","params":[{"name":"kind"},{"name":"session","required":false}]}]}}`),
		agentRev: json.RawMessage(`{"apiVersion":2,"id":"pantheon-agent","version":"1.0.0","dependencies":{"pantheon-allocator":{"range":"^1.0.0","uses":["alloc@1"]}}}`),
		webRev:   json.RawMessage(`{"apiVersion":2,"id":"pantheon-web","version":"1.0.0"}`),
	}
	brain := &fakeNode{id: "brain", kind: "pod", caps: []string{"proc"}, staged: map[string][]byte{}, configs: map[string]lifecycle.AppConfiguration{},
		defs: defs, mans: mans,
		ledger: lifecycle.Ledger{Owner: "f_1", Node: "brain", Installations: map[string]*lifecycle.Installation{}, Instances: map[string]*lifecycle.Instance{}, Operations: map[string]*lifecycle.Operation{}}}
	fleet := &fakeFleet{nodes: map[string]*fakeNode{"brain": brain}}
	store, err := deployments.Open(filepath.Join(dir, "deployments"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	spec := deployments.Spec{Release: deployments.Release{URL: "https://releases.test/set.tar.gz", SHA256: pin},
		Apps: map[string]deployments.AppSpec{
			"allocator": {Package: "allocator", Scope: "deployment", Intent: deployments.Running},
			"agent": {Package: "agent", Scope: "deployment", Intent: deployments.Running,
				Bindings: map[string]deployments.Binding{"allocator": {App: "allocator", Component: "backend",
					Methods: json.RawMessage(`{"allocate":{"arguments":["kind"],"bound":{"session":"s1"}}}`)}}},
			"web": {Package: "web", Scope: "deployment", Intent: deployments.Running},
		}}
	if _, err := store.Put("f_1", "team", 0, spec); err != nil {
		t.Fatal(err)
	}
	r := &Reconciler{Store: store, Releases: releases, Nodes: fleet, Grants: &fakeGrants{issued: map[string]appgateway.DependencyRequest{}},
		CallTimeout: 50 * time.Millisecond}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	for i := 0; i < 12; i++ {
		started := time.Now()
		if _, err := r.Pass(ctx, "f_1", "team"); err != nil {
			t.Fatal(err)
		}
		if took := time.Since(started); took > 5*time.Second {
			t.Fatalf("pass %d waited %s on the unanswered provider", i, took)
		}
		// Once the provider runs, its node stops answering.
		if d, _ := store.Get("f_1", "team"); d.Status.Apps["allocator"].State == "ready" && fleet.hang == nil {
			fleet.mu.Lock()
			fleet.hang = map[string]bool{"brain app_manifest " + allocRev: true}
			fleet.mu.Unlock()
		}
	}
	d, _ := store.Get("f_1", "team")
	if d.Status.Apps["web"].State != "ready" || d.Status.Apps["allocator"].State != "ready" {
		t.Fatalf("independent Apps converged: %+v", d.Status.Apps)
	}
	if s := d.Status.Apps["agent"]; s.State == "ready" || !strings.Contains(s.Reason, "provider allocator: brain did not answer app_manifest") {
		t.Fatalf("consumer reports its unanswered provider: %+v", s)
	}

	// The provider's node answers again: the next passes start the consumer.
	fleet.mu.Lock()
	fleet.hang = nil
	fleet.mu.Unlock()
	settled := false
	for i := 0; i < 12 && !settled; i++ {
		if settled, err = r.Pass(ctx, "f_1", "team"); err != nil {
			t.Fatal(err)
		}
	}
	if d, _ = store.Get("f_1", "team"); !settled || d.Status.Apps["agent"].State != "ready" {
		t.Fatalf("consumer started once its provider answered: %+v", d.Status.Apps["agent"])
	}
}
