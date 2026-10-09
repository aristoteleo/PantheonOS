package reconciler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
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
}

type fakeFleet struct {
	mu    sync.Mutex
	nodes map[string]*fakeNode
	calls []string
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
		n.configs[c.Instance] = *c.Configuration
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
}

func (g *fakeGrants) IssueDependency(ctx context.Context, q appgateway.DependencyRequest) (map[string]any, error) {
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
			Configuration: &lifecycle.ConfigDeclaration{Values: map[string]lifecycle.ConfigField{"name": {Required: true}},
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
				Config: map[string]json.RawMessage{"backend": json.RawMessage(`{"values":{"name":"general"},"credentials":{"budget":{"$secret":"budget"}}}`)},
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
	ref := config.Credentials["budget"]
	if value, err := modelcredentials.Read(vault, ref.Ref, ref.Endpoint); err != nil || value != "sk-test-budget" {
		t.Fatalf("secret delivered to the node vault: %v %q (%+v)", err, value, ref)
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
}
