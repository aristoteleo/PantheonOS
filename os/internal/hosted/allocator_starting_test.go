package hosted

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
)

// A bind whose provider App is meant to run but has not started yet (its
// Workspace paused or starting) gets the one answer the Agent waits on, and
// writes no journal; a provider the owner stopped stays the generic failure.
func TestAllocatorSaysProviderNotRunningYet(t *testing.T) {
	policies := `{"values": {"dependency_binding": {"protocol": 1, "policies": {"agent": {
	 "consumer": {"$app": "agent", "late": true},
	 "bindings": {"shell": {"app_id": "shell", "provider": {"$app": "shell", "late": true, "component": "backend", "port": "http"},
	            "methods": {"run_command": {"arguments": ["command", "shell_id"], "bound": {}}}}}}}}}}`
	shellStates := []deployments.AppStatus{{State: "waiting", Reason: "waiting for node sandbox to return"},
		// Placed and starting, not serving yet: still "not running yet".
		{NodeID: "sandbox", InstanceID: "i-shell", Revision: revShell, Generation: 2, State: "starting"}}
	for i, intent := range []deployments.Intent{deployments.Running, deployments.Running, deployments.Stopped} {
		shell := shellStates[i%2]
		store := storeWith(t, map[string]deployments.AppSpec{
			"allocator": {Hosted: "dependency-binding", Scope: "allocator", Intent: deployments.Running, Config: map[string]json.RawMessage{"backend": json.RawMessage(policies)}},
			"agent":     {Package: "agent", Scope: "agent", Intent: deployments.Running},
			"shell":     {Package: "shell", Scope: "shell", Intent: intent},
		})
		d, _ := store.Get(fleetID, "general-team")
		if err := store.SetStatus(fleetID, "general-team", d.Revision, deployments.Status{Apps: map[string]deployments.AppStatus{
			"agent": {NodeID: "brain", InstanceID: "i-agent", Revision: revAgent, Generation: 3, State: "ready"},
			"shell": shell,
		}}); err != nil {
			t.Fatal(err)
		}
		root := t.TempDir()
		h := NewHost(store)
		h.Register("dependency-binding", &Allocator{Bus: &fakeBus{}, Store: store, Grants: &fakeGrants{byOp: map[string]map[string]any{}, revoked: map[string]bool{}}, Root: root})
		allocator, _ := Binding(fleetID, "general-team", "allocator", "dependency-binding")
		status, out := rpc(t, h, allocator, "bind_dependencies", map[string]any{"policy_id": "agent", "owner_ref": "inst-1", "operation_id": "op-1", "aliases": []string{"shell"}})
		want := errProviderStarting.Error()
		if intent == deployments.Stopped {
			want = errAllocation.Error()
		}
		if status != http.StatusBadRequest || out["error"] != want {
			t.Fatalf("%s: %d %v", intent, status, out)
		}
		if journals, _ := filepath.Glob(filepath.Join(root, "*", "bindings", "*.json")); len(journals) != 0 {
			t.Fatal("a refused bind wrote a journal", journals)
		}
		_ = os.Remove
	}
}
