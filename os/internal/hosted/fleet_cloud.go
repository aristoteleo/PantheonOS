package hosted

import (
	"context"
	"errors"
	"regexp"
	"slices"
	"strings"
	"time"
)

// OwnerHubSecret is the owner's Hub credential in the controller's custody
// (the Agent setup places it; general_team/release_profile.py).
const OwnerHubSecret = "owner-hub"

// WorkspaceComputer is the id of the owner's Cloud Workspace among their
// cloud computers.
const WorkspaceComputer = "workspace"

var cloudNameRE = regexp.MustCompile(`[^a-z0-9]+`)

// cloud is the owner's cloud computers in one place: the machines the
// platform runs for them and bills to them (the Cloud Workspace, Modal
// nodes). The Agent lists them, starts and stops the Workspace, and adds or
// removes a Modal node, with no page open: the controller asks the owner's
// Hub with the owner's own credential.
func (f *Fleet) cloud(ctx context.Context, c *Call) (any, error) {
	a := struct {
		Action    string `json:"action"`
		Computer  string `json:"computer"`
		GPU       string `json:"gpu"`
		GPUCount  int64  `json:"gpu_count"`
		CPU       int64  `json:"cpu"`
		MemoryGiB int64  `json:"memory_gib"`
		Minutes   int64  `json:"minutes"`
	}{Action: "list", GPU: "none", GPUCount: 1, Minutes: 240}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	if f.Hub == nil {
		return Failure(errors.New("this controller cannot reach the owner's Hub")), nil
	}
	hub := func(method, path string, body, out any) error {
		return f.Hub.Request(ctx, c.Fleet, OwnerHubSecret, method, path, body, out)
	}
	computer := strings.TrimSpace(a.Computer)
	workspace := strings.EqualFold(computer, WorkspaceComputer) || strings.EqualFold(computer, "cloud workspace")
	switch a.Action {
	case "list":
		var out map[string]any
		if err := hub("GET", "/api/computers", nil, &out); err != nil {
			return Failure(err), nil
		}
		return merge(map[string]any{"success": true}, out), nil
	case "start", "stop":
		if workspace {
			var out map[string]any
			if err := hub("POST", "/api/computers/workspace/"+a.Action, map[string]any{}, &out); err != nil {
				return Failure(err), nil
			}
			return merge(map[string]any{"success": true}, out), nil
		}
		if a.Action == "start" {
			return Failure(errors.New("only the Cloud Workspace is started (computer=workspace); a Modal node is added with action=add")), nil
		}
		if !strings.HasPrefix(computer, "node-") {
			return Failure(errors.New("stop takes computer=workspace or a Modal node's id (node-…) from action=list; " +
				"a node that runs a model service is stopped from Model Services")), nil
		}
		return f.removeModalNode(ctx, c.Fleet, computer, hub), nil
	case "add":
		if f.JoinToken == nil {
			return Failure(errors.New("this controller cannot add nodes")), nil
		}
		id := "node-" + strings.Trim(cloudNameRE.ReplaceAllString(strings.ToLower(computer), "-"), "-")
		if id == "node-" {
			id = "node-" + time.Now().UTC().Format("0102-150405")
		}
		if len(id) > 40 {
			id = id[:40]
		}
		token, err := f.JoinToken(c.Fleet, 10*time.Minute)
		if err != nil {
			return Failure(err), nil
		}
		body := map[string]any{"service_id": id, "gpu": a.GPU, "join_token": token, "lifetime_minutes": a.Minutes}
		if a.GPUCount != 1 {
			body["gpu_count"] = a.GPUCount
		}
		if a.CPU > 0 {
			body["cpu"] = a.CPU
		}
		if a.MemoryGiB > 0 {
			body["memory_gib"] = a.MemoryGiB
		}
		var out map[string]any
		if err := hub("POST", "/api/model-services/modal-gpu", body, &out); err != nil {
			return Failure(err), nil
		}
		return map[string]any{"success": true, "computer": merge(map[string]any{"id": id, "kind": "modal-node"}, out),
			"note": "It joins the Fleet in a minute or two; list the nodes to see it."}, nil
	}
	return Failure(errors.New("action is list, start, stop or add")), nil
}

// removeModalNode stops a bare Modal node and takes it out of the fleet.
func (f *Fleet) removeModalNode(ctx context.Context, fleet, id string, hub func(method, path string, body, out any) error) map[string]any {
	var node string
	if records, err := f.records(ctx, fleet); err == nil {
		for _, n := range records {
			if slices.Contains(strings_(n["labels"]), "svc-"+id) {
				node = str(n["node_id"])
			}
		}
	}
	if err := hub("DELETE", "/api/model-services/modal-gpu/"+id, nil, nil); err != nil {
		return Failure(err)
	}
	if node != "" && f.Revoke != nil {
		if err := f.Revoke(fleet, node); err != nil {
			return Failure(err)
		}
	}
	return map[string]any{"success": true, "computer": map[string]any{"id": id, "kind": "modal-node", "state": "stopped"}}
}
