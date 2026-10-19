package modelservices

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/aristoteleo/pantheonos/os/internal/hosted"
)

type testSecrets struct{ endpoint string }

func (s testSecrets) Get(fleet, name string) (string, deployments.SecretInfo, error) {
	if name != "owner-hub" {
		return "", deployments.SecretInfo{}, fmt.Errorf("no secret")
	}
	return "pbk_owner", deployments.SecretInfo{Name: name, Endpoint: s.endpoint}, nil
}

type noBus struct{}

func (noBus) Records(context.Context, string) ([]map[string]any, error) { return nil, nil }
func (noBus) Request(context.Context, string, string, any) (json.RawMessage, error) {
	return nil, fmt.Errorf("no node")
}
func (noBus) Subscribe(string, string, func([]byte)) (func(), error) {
	return func() {}, nil
}

func call(method, args string) *hosted.Call {
	return &hosted.Call{Fleet: "f_0123456789abcdef", Deployment: "general-team", App: "model-management", Method: method,
		Args: json.RawMessage(args), Spec: deployments.AppSpec{Hosted: "model-services-management", Config: map[string]json.RawMessage{
			"backend": json.RawMessage(`{"credentials": {"hub": {"$secret": "owner-hub"}}}`)}}}
}

func TestServiceDispatch(t *testing.T) {
	var paths []string
	hub := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.Method+" "+r.URL.Path)
		if r.Header.Get("Authorization") != "Bearer pbk_owner" {
			http.Error(w, "no", 401)
			return
		}
		switch r.URL.Path {
		case "/api/model-services":
			_, _ = w.Write([]byte(`{"deployments": [{"deployment_id": "a", "state": "ready", "revision": 3}]}`))
		case "/api/model-services/groups":
			_, _ = w.Write([]byte(`{"groups": []}`))
		case "/api/model-services/group-creations/missing":
			http.Error(w, `{"detail":"Not found"}`, 404)
		default:
			http.Error(w, "unexpected", 500)
		}
	}))
	defer hub.Close()
	s := &Service{Bus: noBus{}, Hub: &hosted.Hub{Secrets: testSecrets{hub.URL}, HTTP: hub.Client()}, State: t.TempDir()}
	ctx := context.Background()

	out, err := s.Call(ctx, call("list_tools", `{}`))
	if err != nil || len(mapOf(clone(out))["tools"].([]any)) != 10 {
		t.Fatalf("list_tools %v %v", out, err)
	}
	var arg *hosted.ArgumentError
	if _, err := s.Call(ctx, call("model_services_list", `{"x": 1}`)); !errors.As(err, &arg) {
		t.Fatalf("unknown argument accepted: %v", err)
	}
	if _, err := s.Call(ctx, call("model_services_recover", `{}`)); !errors.As(err, &arg) {
		t.Fatalf("missing argument accepted: %v", err)
	}
	out, err = s.Call(ctx, call("model_services_list", `{}`))
	if err != nil || string(Compact(out)) != `{"deployments":[{"deployment_id":"a","revision":3,"state":"ready"}]}` {
		t.Fatalf("list %s %v", Compact(out), err)
	}
	out, err = s.Call(ctx, call("modal_gpu_start", `{"service_id": "q", "lifetime_hours": 2.5}`))
	want := "Ask the user to approve this GPU launch with notify_user (model qwen3.6-35b-a3b-fp8, 1x H100, up to 2.5 h), then call again with user_confirmed=True."
	if err != nil || mapOf(out)["message"] != want || mapOf(out)["started"] != false {
		t.Fatalf("unconfirmed launch %v %v", out, err)
	}
	out, err = s.Call(ctx, call("model_services_groups", `{}`))
	if err != nil || string(Compact(out)) != `{"groups":[]}` {
		t.Fatalf("groups %s %v", Compact(out), err)
	}
	if _, err := s.Call(ctx, call("model_services_group_deployments", `{"action": "inspect", "group_id": "missing"}`)); err == nil ||
		err.Error() != "Model group not found" {
		t.Fatalf("missing group %v", err)
	}
	if _, err := s.Call(ctx, call("model_services_inference_jobs", `{"deployment_id": "a"}`)); err == nil ||
		!strings.Contains(err.Error(), "Nothing was sent to Relay") {
		t.Fatalf("direct_only must not reach the relay: %v", err)
	}
	for _, p := range paths {
		if strings.Contains(p, "workload-connect") {
			t.Fatalf("direct_only requested a relay grant")
		}
	}
}
