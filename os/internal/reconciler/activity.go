package reconciler

// Whether an owner's deployment is doing work now, for a caller that must not
// interrupt it (the Hub moving System Apps to a new release only when idle,
// docs/apps/app-auto-update.md §5.2). The Agent App knows whether a chat or
// turn is running; the node's window/call counter does not (an open Agent
// window keeps a call open).

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
)

// ActivityTimeout bounds asking one App.
const ActivityTimeout = 4 * time.Second

// AppActivity is one App's answer. Busy is true while it does work.
type AppActivity struct {
	Busy         bool `json:"busy"`
	RunningChats int  `json:"running_chats"`
}

// Activity is a deployment's answer: the Apps that answered, and those that
// could not be asked (no ready instance, a node that did not answer). A
// caller treats unknown as busy.
type Activity struct {
	Apps    map[string]AppActivity `json:"apps"`
	Unknown []string               `json:"unknown"`
}

// activityProbes are the Apps of a deployment that report their work, by
// package, and the App RPC that says so.
var activityProbes = map[string]string{"agent": "list_running_chats"}

// DeploymentActivity asks the deployment's Apps that report work whether
// they are busy now.
func DeploymentActivity(ctx context.Context, nodes Transport, fleet string, d deployments.Deployment) Activity {
	out := Activity{Apps: map[string]AppActivity{}, Unknown: []string{}}
	for name, app := range d.Spec.Apps {
		method, ok := activityProbes[app.Package]
		if !ok || app.Intent != deployments.Running {
			continue
		}
		status, ok := d.Status.Apps[name]
		if !ok || status.State != "ready" || status.NodeID == "" || status.InstanceID == "" {
			out.Unknown = append(out.Unknown, name)
			continue
		}
		activity, err := askActivity(ctx, nodes, fleet, app.Package, status, method)
		if err != nil {
			out.Unknown = append(out.Unknown, name)
			continue
		}
		out.Apps[name] = activity
	}
	return out
}

func askActivity(ctx context.Context, nodes Transport, fleet, appID string, status deployments.AppStatus, method string) (AppActivity, error) {
	ctx, cancel := context.WithTimeout(ctx, ActivityTimeout)
	defer cancel()
	var reply struct {
		Response json.RawMessage `json:"response"`
	}
	err := nodes.Call(ctx, fleet, status.NodeID, map[string]any{
		"method": "invoke", "app_id": appID, "instance_id": status.InstanceID,
		"revision": status.Revision, "generation": status.Generation, "timeout_seconds": int(ActivityTimeout / time.Second),
		"payload": map[string]any{"method": method, "args": map[string]any{}, "timeout_s": int(ActivityTimeout / time.Second)},
	}, &reply)
	if err != nil {
		return AppActivity{}, err
	}
	var rpc struct {
		Success bool `json:"success"`
		Result  struct {
			Running *[]json.RawMessage `json:"running"`
		} `json:"result"`
	}
	if json.Unmarshal(reply.Response, &rpc) != nil || !rpc.Success || rpc.Result.Running == nil {
		return AppActivity{}, errors.New("the App did not report its activity")
	}
	n := len(*rpc.Result.Running)
	return AppActivity{Busy: n > 0, RunningChats: n}, nil
}

// RegisterActivity adds GET /deployments/{name}/activity.
func RegisterActivity(mux *http.ServeMux, store *deployments.Store, nodes Transport, auth deployments.Auth) {
	mux.HandleFunc("GET /deployments/{name}/activity", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		fleet, ok := auth.Fleet(r)
		if !ok {
			writeDetail(w, http.StatusUnauthorized, "unauthorized")
			return
		}
		d, err := store.Get(fleet, r.PathValue("name"))
		if errors.Is(err, deployments.ErrNotFound) {
			writeDetail(w, http.StatusNotFound, "deployment not found")
			return
		} else if err != nil {
			writeDetail(w, http.StatusServiceUnavailable, err.Error())
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(DeploymentActivity(r.Context(), nodes, fleet, d))
	})
}
