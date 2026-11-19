package hosted

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// The owner's cloud computers go to the owner's Hub with the owner's
// credential: the Workspace is started and stopped, a Modal node is added
// with a join token and removed with its fleet record.
func TestFleetCloudAsksTheOwnersHub(t *testing.T) {
	var calls []string
	var launch map[string]any
	hub := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		if r.Header.Get("Authorization") != "Bearer pbk_owner" {
			http.Error(w, "no", 401)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.Method + " " + r.URL.Path {
		case "GET /api/computers":
			_, _ = w.Write([]byte(`{"computers":[{"id":"workspace","kind":"workspace","state":"paused","actions":["start"]}]}`))
		case "POST /api/computers/workspace/start":
			_, _ = w.Write([]byte(`{"success":true,"computer":{"id":"workspace","state":"starting"}}`))
		case "POST /api/computers/workspace/stop":
			http.Error(w, `{"detail":"node 'workspace' pause failed: exit unconfirmed"}`, 400)
		case "POST /api/model-services/modal-gpu":
			_ = json.NewDecoder(r.Body).Decode(&launch)
			_, _ = w.Write([]byte(`{"service_id":"node-fit","state":"starting","hourly_usd":0.3}`))
		case "DELETE /api/model-services/modal-gpu/node-fit":
			_, _ = w.Write([]byte(`{"state":"stopped"}`))
		default:
			http.Error(w, `{"detail":"unexpected"}`, 404)
		}
	}))
	defer hub.Close()
	var revoked []string
	bus := &fakeBus{records: []map[string]any{{"node_id": "n_modal", "labels": []any{"modal", "svc-node-fit"}}}}
	f := &Fleet{Bus: bus, Hub: &Hub{Secrets: fakeSecrets{endpoint: hub.URL}, HTTP: hub.Client()},
		JoinToken: func(fleet string, ttl time.Duration) (string, error) { return "join-" + fleet, nil },
		Revoke:    func(fleet, node string) error { revoked = append(revoked, node); return nil }}
	call := func(args string) map[string]any {
		t.Helper()
		out, err := f.Call(context.Background(), &Call{Fleet: fleetID, App: "fleet", Method: "fleet_cloud", Args: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		return out.(map[string]any)
	}
	if out := call(`{}`); out["success"] != true || len(out["computers"].([]any)) != 1 {
		t.Fatalf("list: %v", out)
	}
	if out := call(`{"action":"start","computer":"Workspace"}`); out["success"] != true {
		t.Fatalf("start: %v", out)
	}
	// The Hub's refusal reaches the caller in its own words.
	if out := call(`{"action":"stop","computer":"workspace"}`); out["success"] != false || !strings.Contains(out["error"].(string), "exit unconfirmed") {
		t.Fatalf("stop: %v", out)
	}
	if out := call(`{"action":"add","computer":"Fit","gpu":"none","minutes":30}`); out["success"] != true ||
		launch["service_id"] != "node-fit" || launch["join_token"] != "join-"+fleetID || launch["lifetime_minutes"] != 30.0 {
		t.Fatalf("add: %v %v", out, launch)
	}
	if out := call(`{"action":"stop","computer":"node-fit"}`); out["success"] != true || len(revoked) != 1 || revoked[0] != "n_modal" {
		t.Fatalf("remove: %v %v", out, revoked)
	}
	// Only the Workspace is started, and a model service's node is not stopped from here.
	for _, args := range []string{`{"action":"start","computer":"node-fit"}`, `{"action":"stop","computer":"qwen-serve"}`, `{"action":"wipe"}`} {
		if out := call(args); out["success"] != false {
			t.Fatalf("%s: %v", args, out)
		}
	}
	want := "GET /api/computers,POST /api/computers/workspace/start,POST /api/computers/workspace/stop," +
		"POST /api/model-services/modal-gpu,DELETE /api/model-services/modal-gpu/node-fit"
	if got := strings.Join(calls, ","); got != want {
		t.Fatalf("calls: %s", got)
	}
	// Without the owner's credential nothing is asked.
	none := &Fleet{Bus: bus}
	out, _ := none.Call(context.Background(), &Call{Fleet: fleetID, Method: "fleet_cloud"})
	if out.(map[string]any)["success"] != false {
		t.Fatalf("no hub: %v", out)
	}
}

// A notification for the owner's devices goes to the owner's Hub with the
// owner's credential; the Hub's refusal comes back as a plain failure.
func TestFleetNotifyAsksTheOwnersHub(t *testing.T) {
	var got map[string]any
	status := 200
	hub := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/push/notify" || r.Header.Get("Authorization") != "Bearer pbk_owner" {
			http.Error(w, "no", 401)
			return
		}
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.Header().Set("Content-Type", "application/json")
		if status != 200 {
			http.Error(w, `{"detail":"Too many notifications; try again in a minute"}`, status)
			return
		}
		_, _ = w.Write([]byte(`{"devices":2,"sent":2}`))
	}))
	defer hub.Close()
	f := &Fleet{Bus: &fakeBus{}, Hub: &Hub{Secrets: fakeSecrets{endpoint: hub.URL}, HTTP: hub.Client()}}
	call := func(args string) map[string]any {
		t.Helper()
		out, err := f.Call(context.Background(), &Call{Fleet: fleetID, App: "fleet", Method: "fleet_notify", Args: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		return out.(map[string]any)
	}
	out := call(`{"title":"Notes","body":"The answer is ready.","url":"/app/agent?chatId=c1","tag":"agent.c1"}`)
	if out["success"] != true || out["sent"] != float64(2) {
		t.Fatalf("notify: %v", out)
	}
	if got["title"] != "Notes" || got["body"] != "The answer is ready." || got["url"] != "/app/agent?chatId=c1" || got["tag"] != "agent.c1" {
		t.Fatalf("sent to the Hub: %v", got)
	}
	if out := call(`{"title":"  "}`); out["success"] != false {
		t.Fatalf("no title: %v", out)
	}
	status = 429
	if out := call(`{"title":"Notes"}`); out["success"] != false || !strings.Contains(out["error"].(string), "Too many") {
		t.Fatalf("a refusal: %v", out)
	}
}
