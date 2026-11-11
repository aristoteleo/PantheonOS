package reconciler

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

// activityNodes answers invoke with a fixed Agent RPC reply.
type activityNodes struct {
	reply string
	err   error
	asked []map[string]any
}

func (a *activityNodes) Nodes(context.Context, string) ([]proto.Node, error) { return nil, nil }

func (a *activityNodes) Call(_ context.Context, _, node string, q map[string]any, out any) error {
	a.asked = append(a.asked, q)
	if a.err != nil {
		return a.err
	}
	return json.Unmarshal([]byte(fmt.Sprintf(`{"response": %s}`, a.reply)), out)
}

func activityDeployment(state string) deployments.Deployment {
	return deployments.Deployment{
		Spec: deployments.Spec{Apps: map[string]deployments.AppSpec{
			"agent": {Package: "agent", Scope: "agent", Intent: deployments.Running},
			"shell": {Package: "shell", Scope: "shell", Intent: deployments.Running},
		}},
		Status: deployments.Status{Apps: map[string]deployments.AppStatus{
			"agent": {NodeID: "n1", InstanceID: "i1", Revision: "r", Generation: 3, State: state},
			"shell": {NodeID: "n1", InstanceID: "i2", State: "ready"},
		}},
	}
}

func TestActivityReportsARunningChatAsBusy(t *testing.T) {
	nodes := &activityNodes{reply: `{"success": true, "result": {"running": [{"chat_id": "c1"}]}}`}
	got := DeploymentActivity(context.Background(), nodes, "f", activityDeployment("ready"))
	if !got.Apps["agent"].Busy || got.Apps["agent"].RunningChats != 1 || len(got.Unknown) != 0 {
		t.Fatalf("got %+v", got)
	}
	if len(nodes.asked) != 1 || nodes.asked[0]["method"] != "invoke" || nodes.asked[0]["instance_id"] != "i1" {
		t.Fatalf("asked %+v", nodes.asked)
	}
	payload := nodes.asked[0]["payload"].(map[string]any)
	if payload["method"] != "list_running_chats" {
		t.Fatalf("payload %+v", payload)
	}
}

func TestActivityIdleAgent(t *testing.T) {
	nodes := &activityNodes{reply: `{"success": true, "result": {"running": []}}`}
	got := DeploymentActivity(context.Background(), nodes, "f", activityDeployment("ready"))
	if got.Apps["agent"].Busy || len(got.Unknown) != 0 {
		t.Fatalf("got %+v", got)
	}
}

func TestActivityUnknownWhenTheAgentCannotBeAsked(t *testing.T) {
	for name, nodes := range map[string]*activityNodes{
		"error":   {err: fmt.Errorf("no responders")},
		"refused": {reply: `{"success": false, "error": "x"}`},
		"shape":   {reply: `{"success": true, "result": {}}`},
	} {
		got := DeploymentActivity(context.Background(), nodes, "f", activityDeployment("ready"))
		if len(got.Unknown) != 1 || got.Unknown[0] != "agent" {
			t.Fatalf("%s: got %+v", name, got)
		}
	}
	nodes := &activityNodes{reply: `{"success": true, "result": {"running": []}}`}
	got := DeploymentActivity(context.Background(), nodes, "f", activityDeployment("starting"))
	if len(got.Unknown) != 1 || len(nodes.asked) != 0 {
		t.Fatalf("starting: got %+v asked %d", got, len(nodes.asked))
	}
}

func TestActivityRouteRegistersBesideTheDeploymentsAPI(t *testing.T) {
	store, err := deployments.Open(filepath.Join(t.TempDir(), "deployments"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	mux := http.NewServeMux()
	auth := deployments.Auth{ServiceToken: "svc"}
	deployments.Register(mux, store, nil, auth)
	RegisterActivity(mux, store, &activityNodes{}, auth)
	request := httptest.NewRequest("GET", "/deployments/general-team/activity", nil)
	request.Header.Set("Authorization", "Bearer svc")
	request.Header.Set("X-Fleet-Id", "f_1")
	recorder := httptest.NewRecorder()
	mux.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("status %d %s", recorder.Code, recorder.Body)
	}
	request.Header.Set("Authorization", "Bearer wrong")
	recorder = httptest.NewRecorder()
	mux.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("status %d", recorder.Code)
	}
}
