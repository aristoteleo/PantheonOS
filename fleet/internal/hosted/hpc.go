package hosted

import (
	"context"
	"fmt"
	"time"
)

// HPC: a Fleet node on a Slurm login node starts more nodes (fleet_hpc); a
// machine holding a signed-in session reaches clusters that forbid Fleet on
// them (fleet_hpc_cluster); a compute node runs one HTTP service and serves
// its allocation workspace (fleet_hpc_service, hpc_workspace).

const joinTokenTTL = 7 * 24 * time.Hour

var clusterActions = map[string]bool{"list": true, "save": true, "remove": true, "sign_in": true, "status": true,
	"answer": true, "sign_out": true, "touch": true, "partitions": true, "jobs": true, "submit": true, "cancel": true}

// The Agent never handles sign-in: prompts and answers stay between the owner and the node.
var agentClusterActions = map[string]bool{"list": true, "status": true, "partitions": true, "jobs": true, "submit": true, "cancel": true}

// hpcNode returns an online node advertising a runtime, or why not.
func (f *Fleet) hpcNode(ctx context.Context, fleet, id, runtimeName, offline, missing string, needOnline bool) (map[string]any, error) {
	inv, err := f.inventory(ctx, fleet)
	if err != nil {
		return nil, err
	}
	n := node(inv, id)
	if n == nil {
		return nil, fmt.Errorf("Node is not in this user’s Fleet")
	}
	if needOnline && !online(n) {
		return nil, fmt.Errorf("%s", offline)
	}
	if runtime(n, runtimeName) != "1" {
		return nil, fmt.Errorf("%s", missing)
	}
	return n, nil
}

func (f *Fleet) hpcCall(ctx context.Context, fleet, node, kind, method string, data map[string]any, timeout time.Duration) (map[string]any, error) {
	payload := map[string]any{}
	for k, v := range data {
		payload[k] = v
	}
	payload["type"], payload["method"] = kind, method
	c, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	reply, err := command(c, f.Bus, fleet, node, payload)
	if err != nil {
		return nil, err
	}
	if truthy(reply["error"]) {
		return nil, fmt.Errorf("%v", reply["error"])
	}
	return reply, nil
}

func (f *Fleet) hpc(ctx context.Context, c *Call) (any, error) {
	a := struct {
		NodeID    string `json:"node_id"`
		Action    string `json:"action"`
		Partition string `json:"partition"`
		CPUs      int    `json:"cpus"`
		MemGB     int    `json:"mem_gb"`
		Minutes   int    `json:"minutes"`
		GPUs      int    `json:"gpus"`
		GPUType   string `json:"gpu_type"`
		Count     int    `json:"count"`
		Name      string `json:"name"`
		Account   string `json:"account"`
		QOS       string `json:"qos"`
		JobID     string `json:"job_id"`
	}{Action: "partitions", CPUs: 4, MemGB: 16, Minutes: 240, Count: 1}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	launcher, err := f.hpcNode(ctx, c.Fleet, a.NodeID, "slurm-launcher", "The login node is offline",
		"This node cannot start HPC compute nodes: run Fleet (0.5.0-model.9 or later) on a Slurm login node", true)
	if err != nil {
		return Failure(err), nil
	}
	switch a.Action {
	case "partitions", "jobs":
		reply, err := f.hpcCall(ctx, c.Fleet, a.NodeID, "hpc", a.Action, nil, 70*time.Second)
		if err != nil {
			return Failure(err), nil
		}
		return merge(map[string]any{"success": true}, reply), nil
	case "cancel":
		reply, err := f.hpcCall(ctx, c.Fleet, a.NodeID, "hpc", "cancel", map[string]any{"job_id": a.JobID}, 70*time.Second)
		if err != nil {
			return Failure(err), nil
		}
		return merge(map[string]any{"success": true}, reply), nil
	case "launch":
		if a.Count < 1 || a.Count > 16 {
			return Failure(fmt.Errorf("count must be 1-16")), nil
		}
		if f.JoinToken == nil {
			return Failure(fmt.Errorf("This controller cannot mint join tokens")), nil
		}
		base := a.Name
		if base == "" {
			base = a.Partition
			if a.GPUs > 0 {
				base += "-gpu"
			}
		}
		jobs := []any{}
		for range a.Count {
			token, err := f.JoinToken(c.Fleet, joinTokenTTL)
			if err != nil {
				return Failure(err), nil
			}
			reply, err := f.hpcCall(ctx, c.Fleet, a.NodeID, "hpc", "submit", map[string]any{"request": map[string]any{
				"join_token": token, "name": base, "partition": a.Partition, "cpus": a.CPUs, "mem_gb": a.MemGB,
				"minutes": a.Minutes, "gpus": a.GPUs, "gpu_type": a.GPUType, "account": a.Account, "qos": a.QOS}}, 70*time.Second)
			if err != nil {
				return Failure(err), nil
			}
			jobs = append(jobs, reply["job"])
		}
		return map[string]any{"success": true, "launcher": launcher["name"], "jobs": jobs}, nil
	}
	return map[string]any{"success": false, "error": "action must be partitions, launch, jobs or cancel"}, nil
}

func (f *Fleet) hpcCluster(ctx context.Context, c *Call) (any, error) {
	var args map[string]any
	if err := c.Decode(&args); err != nil {
		return nil, err
	}
	nodeID, _ := args["node_id"].(string)
	action, _ := args["action"].(string)
	if action == "" {
		action = "list"
	}
	var data map[string]any
	if c.Owner {
		// The owner's Fleet app: every action, its own arguments.
		if !clusterActions[action] {
			return Failure(fmt.Errorf("unknown action")), nil
		}
		data = map[string]any{}
		for k, v := range args {
			if k != "node_id" && k != "action" {
				data[k] = v
			}
		}
	} else {
		a := struct {
			NodeID    string `json:"node_id"`
			Action    string `json:"action"`
			ClusterID string `json:"cluster_id"`
			Partition string `json:"partition"`
			CPUs      int    `json:"cpus"`
			MemGB     int    `json:"mem_gb"`
			Minutes   int    `json:"minutes"`
			GPUs      int    `json:"gpus"`
			GPUType   string `json:"gpu_type"`
			Name      string `json:"name"`
			Account   string `json:"account"`
			JobID     string `json:"job_id"`
		}{Action: "list", CPUs: 4, MemGB: 16, Minutes: 240}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		if !agentClusterActions[a.Action] {
			return map[string]any{"success": false, "error": "Sign-in happens in the Fleet app; ask the user."}, nil
		}
		nodeID, action = a.NodeID, a.Action
		data = map[string]any{"cluster_id": a.ClusterID, "job_id": a.JobID}
		if action == "submit" {
			name := a.Name
			if name == "" {
				name = a.Partition
				if a.GPUs > 0 {
					name += "-gpu"
				}
			}
			data["request"] = map[string]any{"name": name, "partition": a.Partition, "cpus": a.CPUs, "mem_gb": a.MemGB,
				"minutes": a.Minutes, "gpus": a.GPUs, "gpu_type": a.GPUType, "account": a.Account}
		}
	}
	if _, err := f.hpcNode(ctx, c.Fleet, nodeID, "hpc-connector", "The connecting node is offline",
		"Update Fleet on this machine (0.5.0-model.10 or later) to connect HPC clusters", true); err != nil {
		return Failure(err), nil
	}
	reply, err := f.hpcCall(ctx, c.Fleet, nodeID, "hpc_cluster", action, data, 100*time.Second)
	if err != nil {
		return Failure(err), nil
	}
	return merge(map[string]any{"success": true}, reply), nil
}

func (f *Fleet) hpcService(ctx context.Context, c *Call) (any, error) {
	a := struct {
		NodeID     string         `json:"node_id"`
		Action     string         `json:"action"`
		Spec       map[string]any `json:"spec"`
		InstanceID string         `json:"instance_id"`
		Revision   string         `json:"revision"`
		Generation int64          `json:"generation"`
	}{Action: "list"}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	if a.Action != "list" && a.Action != "start" && a.Action != "stop" {
		return Failure(fmt.Errorf("action must be list, start or stop")), nil
	}
	if _, err := f.hpcNode(ctx, c.Fleet, a.NodeID, "hpc-services", "HPC compute node is unavailable",
		"Choose an HPC compute node with service support", a.Action != "list"); err != nil {
		return Failure(err), nil
	}
	data := map[string]any{"instance_id": a.InstanceID, "revision": a.Revision, "generation": a.Generation, "protocol": 1}
	if a.Spec != nil {
		data["spec"] = a.Spec
	}
	reply, err := f.hpcCall(ctx, c.Fleet, a.NodeID, "hpc_service", a.Action, data, 15*time.Second)
	if err != nil {
		return Failure(err), nil
	}
	return merge(map[string]any{"success": true}, reply), nil
}

func (f *Fleet) hpcWorkspace(ctx context.Context, c *Call) (any, error) {
	a := struct {
		NodeID    string `json:"node_id"`
		Operation string `json:"operation"`
		Path      string `json:"path"`
		Data      string `json:"data"`
		Offset    int64  `json:"offset"`
		Size      int64  `json:"size"`
		Overwrite bool   `json:"overwrite"`
	}{Path: ".", Size: 65536}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	switch a.Operation {
	case "list", "read", "write", "mkdir":
	default:
		return Failure(fmt.Errorf("Unsupported workspace operation")), nil
	}
	info := f.nodeInfo(ctx, c.Fleet, a.NodeID)
	n, _ := info["node"].(map[string]any)
	if str(field(n, "capability", "runtimes", "hpc-files")) != "1" {
		return Failure(fmt.Errorf("Choose a ready HPC compute node from fleet_list_nodes")), nil
	}
	cx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	reply, err := command(cx, f.Bus, c.Fleet, a.NodeID, map[string]any{"type": "hpc_file", "file": map[string]any{
		"operation": a.Operation, "path": a.Path, "data": a.Data, "offset": a.Offset, "size": a.Size, "overwrite": a.Overwrite}})
	if err != nil {
		return Failure(err), nil
	}
	reply["success"] = !truthy(reply["error"])
	return reply, nil
}
