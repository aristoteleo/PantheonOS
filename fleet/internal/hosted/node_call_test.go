package hosted

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
)

// serviceBus is a fakeBus that also answers App service subjects.
type serviceBus struct {
	*fakeBus
	noResponders int
	subjects     []string
	answer       func(method string, params map[string]any) map[string]any
}

func (b *serviceBus) CallService(_ context.Context, _ string, subject string, payload any) (json.RawMessage, error) {
	if b.noResponders > 0 {
		b.noResponders--
		return nil, errors.New("nats: no responders available for request")
	}
	b.subjects = append(b.subjects, subject)
	raw, _ := json.Marshal(payload)
	var p struct {
		Method     string         `json:"method"`
		Parameters map[string]any `json:"parameters"`
	}
	_ = json.Unmarshal(raw, &p)
	out, _ := json.Marshal(b.answer(p.Method, p.Parameters))
	return out, nil
}

func TestNodeCallReachesANodesFileServiceAndRunOnNodeTakesCwd(t *testing.T) {
	store := storeWith(t, map[string]deployments.AppSpec{"fleet": {Hosted: "fleet", Scope: "s", Intent: deployments.Running}})
	bus := &serviceBus{fakeBus: &fakeBus{records: []map[string]any{record("n_mac", "Mac", "machine", []string{"proc", "fs:local"},
		map[string]any{"app_id": "node-files", "scope": "x-app", "service_id": "svc", "health": "healthy"})}}, noResponders: 1}
	bus.handle = func(node string, p map[string]any) map[string]any {
		if p["type"] == "run_task" {
			task := p["task"].(map[string]any)
			return map[string]any{"exit_code": 0, "stdout": strings.Repeat("x", 100) + "END", "cwd_seen": task["cwd"]}
		}
		return map[string]any{"ok": true}
	}
	bus.answer = func(method string, params map[string]any) map[string]any {
		if method == "read_file" && params["file_path"] == "/Users/me/a.txt" {
			return map[string]any{"result": map[string]any{"success": true, "content": "hello"}}
		}
		return map[string]any{"error": "Error processing " + method + ": not found"}
	}
	f := &Fleet{Bus: bus, Store: store, NATSURL: "nats://bus.test:4222", MintApps: func(fleet string, ttl time.Duration) (auth.AppsCredential, error) {
		return auth.AppsCredential{JWT: "jwt", Seed: "seed", SubjectPrefix: "fleet." + fleet + ".apps", Expires: time.Now().Add(ttl).Unix()}, nil
	}}
	call := func(method, args string) map[string]any {
		out, err := f.Call(context.Background(), &Call{Fleet: fleetID, Deployment: "general-team", App: "fleet", Method: method, Args: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		return out.(map[string]any)
	}
	// The node's file service answers (after one "no responders": it was starting).
	out := call("node_call", `{"node_id": "n_mac", "method": "read_file", "args": {"file_path": "/Users/me/a.txt"}}`)
	if out["success"] != true || out["content"] != "hello" || out["node_id"] != "n_mac" {
		t.Fatalf("%v", out)
	}
	if bus.subjects[0] != "fleet."+fleetID+".apps.pantheon.service.svc" {
		t.Fatal("subject", bus.subjects)
	}
	if out := call("node_call", `{"node_id": "n_mac", "method": "read_file", "args": {"file_path": "/missing"}}`); out["success"] != false || !strings.Contains(out["error"].(string), "not found") {
		t.Fatalf("%v", out)
	}
	// Only Files methods.
	if out := call("node_call", `{"node_id": "n_mac", "method": "file_transfer"}`); out["success"] != false {
		t.Fatalf("%v", out)
	}
	// run_on_node passes cwd and keeps the end of long output.
	out = call("run_on_node", `{"node_id": "n_mac", "code": "ls", "cwd": "/Users/me", "max_output": 10}`)
	if out["cwd_seen"] != "/Users/me" || !strings.HasSuffix(out["stdout"].(string), "xxxxxxxEND") || !strings.Contains(out["stdout"].(string), "earlier bytes of output omitted") {
		t.Fatalf("%v", out)
	}
}
