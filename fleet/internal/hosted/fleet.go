package hosted

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Fleet is the owner's Fleet tool surface (formerly the Python Fleet App,
// apps/fleet/fleet.py): node inventory, code execution, transfers, HPC,
// updates, node services and App lifecycle on one node.
type Fleet struct {
	Bus   Bus
	Store *deployments.Store
	// NATSURL is the bus address nodes reach, handed to node services.
	NATSURL string
	// MintApps issues an App-namespace bus credential for node services.
	MintApps func(fleet string, ttl time.Duration) (auth.AppsCredential, error)
	// JoinToken mints a single-use join token (HPC compute nodes).
	JoinToken func(fleet string, ttl time.Duration) (string, error)
	// Latest is the Fleet release the controller rolls out ("" = none).
	Latest func() string

	mu        sync.Mutex
	apps      map[string]auth.AppsCredential
	transfers map[string]map[string]map[string]any // fleet -> transfer id -> latest progress
}

func (f *Fleet) Call(ctx context.Context, c *Call) (any, error) {
	switch c.Method {
	case "fleet_list_nodes":
		return f.listNodes(ctx, c)
	case "fleet_node_info":
		var a struct {
			NodeID string `json:"node_id"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return f.nodeInfo(ctx, c.Fleet, a.NodeID), nil
	case "fleet_status":
		return f.status(ctx, c)
	case "fleet_list_apps":
		var a struct {
			NodeID *string `json:"node_id"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		id := ""
		if a.NodeID != nil {
			id = *a.NodeID
		}
		return f.listApps(ctx, c.Fleet, id), nil
	case "fleet_inventory":
		if err := c.Decode(&struct{}{}); err != nil {
			return nil, err
		}
		result := f.listApps(ctx, c.Fleet, "")
		if result["success"] == false {
			return result, nil
		}
		result["fleet_release"] = f.latest()
		return result, nil
	case "fleet_app_lifecycle":
		return f.appLifecycle(ctx, c)
	case "node_service":
		return f.nodeService(ctx, c)
	case "fleet_update_nodes":
		return f.updateNodes(ctx, c)
	case "fleet_hpc":
		return f.hpc(ctx, c)
	case "fleet_hpc_cluster":
		return f.hpcCluster(ctx, c)
	case "fleet_hpc_service":
		return f.hpcService(ctx, c)
	case "hpc_workspace":
		return f.hpcWorkspace(ctx, c)
	case "fleet_pick_node":
		return f.pickNode(ctx, c)
	case "run_on_node":
		a := struct {
			NodeID    string `json:"node_id"`
			Code      string `json:"code"`
			Kind      string `json:"kind"`
			Timeout   int    `json:"timeout"`
			Cwd       string            `json:"cwd"`
			MaxOutput int               `json:"max_output"`
			Env       map[string]string `json:"env"`
		}{Kind: "shell", Timeout: 60}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		if err := checkTaskEnv(a.Env); err != nil {
			return Failure(err), nil
		}
		reply := f.runTask(ctx, c.Fleet, a.NodeID, a.Code, a.Kind, a.Timeout, a.Cwd, a.Env)
		if a.MaxOutput > 0 {
			for _, key := range []string{"stdout", "stderr"} {
				if text, ok := reply[key].(string); ok {
					reply[key] = keepTail(text, a.MaxOutput)
				}
			}
		}
		return reply, nil
	case "node_call":
		return f.nodeCall(ctx, c)
	case "run_on_label":
		return f.runOnLabel(ctx, c)
	case "transfer":
		return f.transfer(ctx, c)
	case "broadcast":
		return f.broadcast(ctx, c)
	case "gather":
		return f.gather(ctx, c)
	case "transfer_status":
		var a struct {
			TransferID string `json:"transfer_id"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		f.mu.Lock()
		p := f.transfers[c.Fleet][a.TransferID]
		f.mu.Unlock()
		if p == nil {
			return map[string]any{"success": false, "error": "unknown transfer_id " + a.TransferID}, nil
		}
		return merge(map[string]any{"success": true}, p), nil
	case "list_tools":
		raw, _ := Manifest("fleet")
		var m struct {
			Provides struct {
				Tools json.RawMessage `json:"tools"`
			} `json:"provides"`
		}
		_ = json.Unmarshal(raw, &m)
		return map[string]any{"success": true, "tools": m.Provides.Tools}, nil
	}
	return nil, &ArgumentError{"Method " + c.Method + " not found on service fleet"}
}

func merge(base map[string]any, extra map[string]any) map[string]any {
	for k, v := range extra {
		base[k] = v
	}
	return base
}

func (f *Fleet) latest() string {
	if f.Latest == nil {
		return ""
	}
	return f.Latest()
}

func (f *Fleet) records(ctx context.Context, fleet string) ([]map[string]any, error) {
	c, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	return f.Bus.Records(c, fleet)
}

func (f *Fleet) inventory(ctx context.Context, fleet string) (map[string]any, error) {
	records, err := f.records(ctx, fleet)
	if err != nil {
		return nil, err
	}
	return inventory(records, time.Now()), nil
}

// self is the node the Agent of this deployment runs on: "this machine" for
// the Agent's transfers (the Python App reported its own, co-located node).
func (f *Fleet) self(c *Call) string {
	d, err := f.Store.Get(c.Fleet, c.Deployment)
	if err != nil {
		return ""
	}
	if s, ok := d.Status.Apps["agent"]; ok && s.State == "ready" {
		return s.NodeID
	}
	return ""
}

func summarize(n map[string]any) map[string]any {
	osName := field(n, "capability", "os")
	shell := "posix"
	if osName == "windows" {
		shell = "powershell"
	}
	gpu := field(n, "capability", "gpu")
	if gpu == nil {
		gpu = ""
	}
	version := n["version"]
	if !truthy(version) {
		version = ""
	}
	return map[string]any{
		"node_id": n["node_id"], "name": n["name"], "labels": orListNil(n["labels"]), "delegation": n["delegation"],
		"file_roots": orList(field(n, "capability", "file_roots")), "caps": orList(field(n, "capability", "caps")),
		"status": field(n, "state", "status"),
		"os":     osName, "shell": shell, "arch": field(n, "capability", "arch"), "cpu_cores": field(n, "capability", "cpu_cores"),
		"gpu": gpu, "ram_gb": field(n, "capability", "ram_gb"), "disk_free_gb": field(n, "capability", "disk_free_gb"),
		"load": orEmpty(field(n, "state", "load")), "reachability": field(n, "net", "reachability"),
		"last_seen": n["last_seen"], "fleet_version": version, "is_self": false,
	}
}

func orListNil(v any) any {
	if v == nil {
		return []any{}
	}
	return v
}

func (f *Fleet) listNodes(ctx context.Context, c *Call) (any, error) {
	if err := c.Decode(&struct{}{}); err != nil {
		return nil, err
	}
	records, err := f.records(ctx, c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	self := f.self(c)
	nodes := []map[string]any{}
	for _, n := range records {
		s := summarize(n)
		if self != "" && s["node_id"] == self {
			s["is_self"] = true
		}
		nodes = append(nodes, s)
	}
	var selfID any
	if self != "" {
		selfID = self
	}
	return map[string]any{"success": true, "count": len(nodes), "nodes": nodes, "self_node_id": selfID}, nil
}

func (f *Fleet) nodeInfo(ctx context.Context, fleet, id string) map[string]any {
	records, err := f.records(ctx, fleet)
	if err == nil {
		for _, n := range records {
			if n["node_id"] == id {
				return map[string]any{"success": true, "node": n}
			}
		}
		err = fmt.Errorf("not found")
	}
	return map[string]any{"success": false, "error": fmt.Sprintf("node %s not found: %v", id, err)}
}

func (f *Fleet) status(ctx context.Context, c *Call) (any, error) {
	if err := c.Decode(&struct{}{}); err != nil {
		return nil, err
	}
	records, err := f.records(ctx, c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	onlineCount, cores, ram := 0, 0.0, 0.0
	gpus := []any{}
	for _, n := range records {
		if field(n, "state", "status") == "online" {
			onlineCount++
		}
		cores += num(field(n, "capability", "cpu_cores"))
		ram += num(field(n, "capability", "ram_gb"))
		if truthy(field(n, "capability", "gpu")) {
			gpus = append(gpus, n["node_id"])
		}
	}
	active := 0
	f.mu.Lock()
	for _, p := range f.transfers[c.Fleet] {
		if p["state"] != "done" && p["state"] != "failed" {
			active++
		}
	}
	f.mu.Unlock()
	return map[string]any{"success": true, "nodes_total": len(records), "nodes_online": onlineCount,
		"total_cpu_cores": int(cores), "total_ram_gb": float64(int(ram*10+0.5)) / 10, "gpu_nodes": gpus,
		"active_transfers": active}, nil
}

func (f *Fleet) listApps(ctx context.Context, fleet, nodeID string) map[string]any {
	inv, err := f.inventory(ctx, fleet)
	if err != nil {
		return Failure(err)
	}
	inv = withDesktopApps(ctx, f.Bus, fleet, inv)
	if nodeID != "" {
		if node(inv, nodeID) == nil {
			return map[string]any{"success": false, "error": "Unknown Fleet node"}
		}
		var kept []map[string]any
		for _, i := range inv["instances"].([]map[string]any) {
			if i["node_id"] == nodeID {
				kept = append(kept, i)
			}
		}
		if kept == nil {
			kept = []map[string]any{}
		}
		inv["instances"] = kept
	}
	return inv
}

// lifecycleNode checks a node may take App lifecycle requests.
func (f *Fleet) lifecycleNode(ctx context.Context, fleet, id string) error {
	if id == "" || strings.ContainsAny(id, ".*> ") {
		return fmt.Errorf("A concrete Fleet node is required")
	}
	inv, err := f.inventory(ctx, fleet)
	if err != nil {
		return err
	}
	n := node(inv, id)
	if n == nil {
		return fmt.Errorf("Node is not in this user’s Fleet")
	}
	if !online(n) {
		return fmt.Errorf("Node is offline; lifecycle outcome is unknown until it reconnects")
	}
	if runtime(n, "app-lifecycle") != "1" {
		return fmt.Errorf("Upgrade Fleet on this node to enable managed App lifecycle v1")
	}
	return nil
}

func (f *Fleet) appLifecycle(ctx context.Context, c *Call) (any, error) {
	a := struct {
		NodeID      string `json:"node_id"`
		Action      string `json:"action"`
		Digest      string `json:"digest"`
		Scope       string `json:"scope"`
		Generation  int64  `json:"generation"`
		OperationID string `json:"operation_id"`
		InstanceID  string `json:"instance_id"`
		Revision    string `json:"revision"`
		LeaseID     string `json:"lease_id"`
		Release     bool   `json:"release"`
		KeepAlive   bool   `json:"keep_alive"`
	}{Action: "status", Scope: "app"}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	if a.NodeID == Node {
		return f.hostedLifecycle(c, a.Action, a.Digest), nil
	}
	if err := f.lifecycleNode(ctx, c.Fleet, a.NodeID); err != nil {
		return Failure(err), nil
	}
	var out map[string]any
	var err error
	switch a.Action {
	case "status":
		out, err = lifecycle(ctx, f.Bus, c.Fleet, a.NodeID, "status", nil)
	case "manifest":
		if len(a.Digest) != 64 {
			return map[string]any{"success": false, "error": "Use an exact installed App revision"}, nil
		}
		out, err = lifecycle(ctx, f.Bus, c.Fleet, a.NodeID, "app_manifest", map[string]any{"revision": a.Digest})
	case "lease", "keep_alive":
		out, err = lifecycle(ctx, f.Bus, c.Fleet, a.NodeID, a.Action, map[string]any{"instance_id": a.InstanceID,
			"revision": a.Revision, "generation": a.Generation, "lease_id": a.LeaseID, "release": a.Release, "keep_alive": a.KeepAlive})
	case "install", "uninstall", "start", "stop", "reconcile", "recover":
		id := a.OperationID
		if id == "" {
			id = randomHex(16)
		}
		var submitted map[string]any
		submitted, err = lifecycle(ctx, f.Bus, c.Fleet, a.NodeID, "submit", map[string]any{"request": map[string]any{
			"protocol": 1, "operation_id": id, "action": a.Action, "digest": a.Digest, "scope": a.Scope, "generation": a.Generation}})
		if err == nil {
			return map[string]any{"success": true, "operation": submitted["operation"]}, nil
		}
	default:
		return map[string]any{"success": false, "error": "Unsupported lifecycle operation"}, nil
	}
	if err != nil {
		return Failure(err), nil
	}
	return merge(map[string]any{"success": true}, out), nil
}

// hostedLifecycle answers the Fleet views about Apps hosted here: they have
// no ledger, only their manifest.
func (f *Fleet) hostedLifecycle(c *Call, action, digest string) map[string]any {
	switch action {
	case "status":
		return map[string]any{"success": true, "protocol": 1, "owner": c.Fleet, "node_id": Node,
			"installations": map[string]any{}, "instances": map[string]any{}, "operations": map[string]any{}}
	case "manifest":
		for service := range catalog {
			if rev, _ := Revision(service); rev == digest {
				raw, _ := Manifest(service)
				var m map[string]any
				_ = json.Unmarshal(raw, &m)
				return map[string]any{"success": true, "protocol": 1, "revision": digest, "manifest": m}
			}
		}
	}
	return map[string]any{"success": false, "error": "Apps hosted by the Fleet controller have no node lifecycle"}
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// appsCredential is the App-namespace bus credential node services join with
// (long-lived, one per fleet, renewed a week before it expires).
func (f *Fleet) appsCredential(fleet string) (auth.AppsCredential, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.apps == nil {
		f.apps = map[string]auth.AppsCredential{}
	}
	if c, ok := f.apps[fleet]; ok && c.Expires > time.Now().Add(7*24*time.Hour).Unix() {
		return c, nil
	}
	if f.MintApps == nil {
		return auth.AppsCredential{}, fmt.Errorf("This Fleet has no App bus credential; node services are unavailable")
	}
	c, err := f.MintApps(fleet, 30*24*time.Hour)
	if err != nil {
		return c, err
	}
	f.apps[fleet] = c
	return c, nil
}

var builtinVersions = map[string]string{"node-files": "0.1.0", "pty": "0.6.6"}

func (f *Fleet) nodeService(ctx context.Context, c *Call) (any, error) {
	var a struct {
		Service string `json:"service"`
		NodeID  string `json:"node_id"`
		Restore bool   `json:"restore"`
	}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	if (a.Service != "file_manager" && a.Service != "file_transfer" && a.Service != "pty") || a.NodeID == "" {
		return map[string]any{"success": false, "error": "Node services are Files and PTY on one of your nodes"}, nil
	}
	credential, err := f.appsCredential(c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	serviceID, err := f.ensureNodeService(ctx, c.Fleet, a.Service, a.NodeID, a.Restore, credential)
	if err != nil {
		return Failure(err), nil
	}
	invocation := "direct"
	if a.Service == "file_transfer" {
		invocation = "file_transfer"
	}
	return map[string]any{"success": true, "service_id": serviceID, "subject_prefix": credential.SubjectPrefix,
		"node_id": a.NodeID, "invocation": invocation}, nil
}

// ServiceCaller sends one request to an App service subject; the controller's
// bus implements it.
type ServiceCaller interface {
	CallService(ctx context.Context, fleet, subject string, payload any) (json.RawMessage, error)
}

// nodeFileMethods are the Files service methods an owner's Agent may call on
// any of the owner's nodes through node_call.
var nodeFileMethods = map[string]bool{"read_file": true, "write_file": true, "stat_path": true, "list_files": true,
	"manage_path": true, "get_cwd": true}

// nodeCall invokes one Files method on a node's file service, starting the
// service when needed: an owner's Agent works on files of any of its nodes.
func (f *Fleet) nodeCall(ctx context.Context, c *Call) (any, error) {
	var a struct {
		NodeID  string         `json:"node_id"`
		Method  string         `json:"method"`
		Args    map[string]any `json:"args"`
		Timeout int            `json:"timeout"`
	}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	if a.NodeID == "" || !nodeFileMethods[a.Method] {
		return map[string]any{"success": false, "error": "Call a Files method (read_file, write_file, stat_path, list_files, manage_path, get_cwd) on one of your nodes"}, nil
	}
	caller, ok := f.Bus.(ServiceCaller)
	if !ok {
		return map[string]any{"success": false, "error": "This controller cannot call node services"}, nil
	}
	credential, err := f.appsCredential(c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	serviceID, err := f.ensureNodeService(ctx, c.Fleet, "file_manager", a.NodeID, false, credential)
	if err != nil {
		return Failure(err), nil
	}
	if a.Args == nil {
		a.Args = map[string]any{}
	}
	timeout := time.Duration(min(max(a.Timeout, 30), 120)) * time.Second
	subject := "pantheon.service." + serviceID
	if credential.SubjectPrefix != "" {
		subject = credential.SubjectPrefix + "." + subject
	}
	var raw json.RawMessage
	// A service just started may not subscribe yet: retry briefly.
	for attempt := 0; ; attempt++ {
		call, cancel := context.WithTimeout(ctx, timeout)
		raw, err = caller.CallService(call, c.Fleet, subject, map[string]any{"method": a.Method, "parameters": a.Args})
		cancel()
		if err == nil || attempt >= 5 || !strings.Contains(err.Error(), "no responders") {
			break
		}
		select {
		case <-ctx.Done():
			return Failure(ctx.Err()), nil
		case <-time.After(time.Second):
		}
	}
	if err != nil {
		return Failure(err), nil
	}
	var reply struct {
		Result json.RawMessage `json:"result"`
		Error  string          `json:"error"`
	}
	if json.Unmarshal(raw, &reply) != nil {
		return map[string]any{"success": false, "error": "The node returned an invalid reply"}, nil
	}
	if reply.Error != "" {
		return map[string]any{"success": false, "error": reply.Error, "node_id": a.NodeID}, nil
	}
	var result any
	_ = json.Unmarshal(reply.Result, &result)
	if m, ok := result.(map[string]any); ok {
		m["node_id"] = a.NodeID
		return m, nil
	}
	return map[string]any{"success": true, "result": result, "node_id": a.NodeID}, nil
}

// ensureNodeService finds or starts a node's Files or PTY builtin service in
// the owner's App namespace (formerly AppInstanceResolver.ensure_instance).
func (f *Fleet) ensureNodeService(ctx context.Context, fleet, service, nodeID string, restore bool, credential auth.AppsCredential) (string, error) {
	inv, err := f.inventory(ctx, fleet)
	if err != nil {
		return "", err
	}
	n := node(inv, nodeID)
	if n == nil {
		return "", fmt.Errorf("Node is not a member of this user’s fleet")
	}
	if !online(n) {
		return "", fmt.Errorf("The node is offline; reconnect it before accessing its services")
	}
	caps, _ := n["caps"].([]string)
	appID := "pty"
	if service == "pty" {
		if !slices.Contains(caps, "proc") {
			return "", fmt.Errorf("This node does not allow process execution (proc capability required)")
		}
		if n["os"] != "linux" && n["os"] != "darwin" {
			return "", fmt.Errorf("The Go PTY backend currently supports macOS and Linux; Windows ConPTY is not available yet")
		}
	} else {
		var candidates []map[string]any
		for _, i := range inv["instances"].([]map[string]any) {
			if i["node_id"] == nodeID && (i["app_id"] == "file-manager" || i["app_id"] == "node-files") && str(i["service_id"]) != "" &&
				(i["health"] == "healthy" || i["health"] == "starting" || i["health"] == "degraded") {
				candidates = append(candidates, i)
			}
		}
		if len(candidates) > 0 {
			sort.SliceStable(candidates, func(x, y int) bool {
				ax, ay := str(candidates[x]["scope"]) != "app", str(candidates[y]["scope"]) != "app"
				if ax != ay {
					return !ax
				}
				return str(candidates[x]["scope"]) < str(candidates[y]["scope"])
			})
			return str(candidates[0]["service_id"]), nil
		}
		if !slices.Contains(caps, "fs:local") {
			if slices.Contains(caps, "fs:workspace") {
				return "", fmt.Errorf("Update Fleet on this node to serve its files")
			}
			return "", fmt.Errorf("This node has no file backend. Enable shared folders in Fleet on this machine.")
		}
		appID = "node-files"
	}
	sum := sha256.Sum256([]byte(nodeID))
	scope := "node" + hex.EncodeToString(sum[:])[:16] + "-app"
	seed := fleet + "-app-" + appID + "-" + scope
	idSum := sha256.Sum256([]byte(seed))
	serviceID := hex.EncodeToString(idSum[:])
	if service == "pty" && !restore {
		for _, i := range inv["instances"].([]map[string]any) {
			if i["node_id"] == nodeID && i["service_id"] == serviceID && i["health"] == "healthy" {
				return serviceID, nil
			}
		}
	}
	spec := proto.AppCommand{AppID: appID, Scope: scope, Version: builtinVersions[appID], ServiceID: serviceID,
		Runtime: "builtin", Command: []string{}, Dir: "/", Env: map[string]string{
			"NATS_SERVERS": f.NATSURL, "NATS_JWT": credential.JWT, "NATS_SEED": credential.Seed,
			"NATS_SUBJECT_PREFIX": credential.SubjectPrefix, "NATS_INBOX_PREFIX": credential.InboxPrefix,
			"NATS_ENABLE_JETSTREAM": "false", "PANTHEON_FLEET_NODE_ID": nodeID, "PANTHEON_FLEET_ID": fleet,
			"PANTHEON_USER_SEED": fleet,
		}}
	c, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	reply, err := command(c, f.Bus, fleet, nodeID, map[string]any{"type": "app_start", "app": spec})
	if err != nil {
		return "", err
	}
	if reply["ok"] != true {
		return "", fmt.Errorf("app_start %s on %s: %v", appID, nodeID, reply)
	}
	return serviceID, nil
}

func (f *Fleet) updateNodes(ctx context.Context, c *Call) (any, error) {
	var a struct {
		NodeIDs []string `json:"node_ids"`
		Tag     string   `json:"tag"`
	}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	tag := a.Tag
	if tag == "" {
		tag = f.latest()
	}
	if tag == "" {
		return Failure(fmt.Errorf("No Fleet release is published for updates")), nil
	}
	inv, err := f.inventory(ctx, c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	nodes := inv["nodes"].([]map[string]any)
	var targets []map[string]any
	if len(a.NodeIDs) > 0 {
		for _, id := range a.NodeIDs {
			n := node(inv, id)
			if n == nil {
				return Failure(fmt.Errorf("Node is not in this user’s Fleet")), nil
			}
		}
		for _, n := range nodes {
			if slices.Contains(a.NodeIDs, str(n["node_id"])) {
				targets = append(targets, n)
			}
		}
	} else {
		for _, n := range nodes {
			if n["kind"] == "machine" {
				targets = append(targets, n)
			}
		}
	}
	results := make([]map[string]any, len(targets))
	var wg sync.WaitGroup
	for k, n := range targets {
		wg.Add(1)
		go func(k int, n map[string]any) {
			defer wg.Done()
			out := map[string]any{"node_id": n["node_id"], "name": n["name"], "from": str(n["version"]), "to": tag}
			switch {
			case n["kind"] != "machine":
				out["status"], out["reason"] = "skipped", "updated with its image, not in place"
			case !online(n):
				out["status"], out["reason"] = "skipped", "offline"
			case runtime(n, "self-update") != "1":
				out["status"], out["reason"] = "manual", "this Fleet version cannot update itself; reinstall it once"
			default:
				c2, cancel := context.WithTimeout(ctx, 600*time.Second)
				defer cancel()
				reply, err := command(c2, f.Bus, c.Fleet, str(n["node_id"]), map[string]any{"type": "self_update", "tag": tag})
				switch {
				case err != nil:
					out["status"], out["reason"] = "failed", err.Error()
				case truthy(reply["error"]):
					out["status"], out["reason"] = "failed", fmt.Sprint(reply["error"])
				default:
					status := str(reply["status"])
					if status == "" {
						status = "failed"
					}
					out["status"], out["reason"] = status, str(reply["reason"])
				}
			}
			results[k] = out
		}(k, n)
	}
	wg.Wait()
	if results == nil {
		results = []map[string]any{}
	}
	return map[string]any{"success": true, "tag": tag, "nodes": results}, nil
}

// pickNode filters online nodes by capability, then ranks them.
func (f *Fleet) pickNode(ctx context.Context, c *Call) (any, error) {
	a := struct {
		MinCPU   float64 `json:"min_cpu"`
		MinRAMGB float64 `json:"min_ram_gb"`
		NeedGPU  bool    `json:"need_gpu"`
		Label    string  `json:"label"`
		Prefer   string  `json:"prefer"`
	}{Prefer: "idle"}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	records, err := f.records(ctx, c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	var cands []map[string]any
	for _, n := range records {
		if field(n, "state", "status") != "online" || num(field(n, "capability", "cpu_cores")) < a.MinCPU ||
			num(field(n, "capability", "ram_gb")) < a.MinRAMGB || a.NeedGPU && !truthy(field(n, "capability", "gpu")) ||
			a.Label != "" && !slices.Contains(strings_(n["labels"]), a.Label) {
			continue
		}
		cands = append(cands, n)
	}
	if len(cands) == 0 {
		return map[string]any{"success": false, "error": "no online node matches the requirements"}, nil
	}
	key := func(n map[string]any) float64 {
		switch a.Prefer {
		case "most_cpu":
			return -num(field(n, "capability", "cpu_cores"))
		case "most_ram":
			return -num(field(n, "capability", "ram_gb"))
		}
		if v := field(n, "state", "load", "cpu"); v != nil {
			return num(v)
		}
		return 1
	}
	sort.SliceStable(cands, func(x, y int) bool { return key(cands[x]) < key(cands[y]) })
	best := cands[0]
	return map[string]any{"success": true, "node_id": best["node_id"], "name": best["name"],
		"candidates": len(cands), "reason": "prefer=" + a.Prefer}, nil
}

func (f *Fleet) runOnNode(ctx context.Context, fleet, nodeID, code, kind string, timeout int) map[string]any {
	return f.runOnNodeIn(ctx, fleet, nodeID, code, kind, timeout, "")
}

// keepTail bounds command output for a model: the end of the output (where
// results and errors are) stays, with a note of what was cut.
func keepTail(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	cut := len(text) - limit
	for cut < len(text) && !utf8.RuneStart(text[cut]) {
		cut++
	}
	return fmt.Sprintf("[%d earlier bytes of output omitted]\n", cut) + text[cut:]
}

func (f *Fleet) runOnNodeIn(ctx context.Context, fleet, nodeID, code, kind string, timeout int, cwd string) map[string]any {
	return f.runTask(ctx, fleet, nodeID, code, kind, timeout, cwd, nil)
}

var taskEnvName = regexp.MustCompile(`^[A-Z_][A-Z0-9_]{0,63}$`)

// checkTaskEnv bounds the variables a task is given: the node sets them in
// the task's process, so they appear in no command line.
func checkTaskEnv(env map[string]string) error {
	if len(env) > 16 {
		return fmt.Errorf("env takes at most 16 variables")
	}
	for name, value := range env {
		if !taskEnvName.MatchString(name) || len(value) > 8192 || strings.ContainsRune(value, 0) {
			return fmt.Errorf("invalid env variable %q", name)
		}
	}
	return nil
}

func (f *Fleet) runTask(ctx context.Context, fleet, nodeID, code, kind string, timeout int, cwd string, env map[string]string) map[string]any {
	wait := time.Duration(timeout+5) * time.Second
	if strings.HasPrefix(nodeID, "hpc_") {
		wait += 30 * time.Second
	}
	c, cancel := context.WithTimeout(ctx, wait)
	defer cancel()
	task := map[string]any{"task_id": "t_" + randomHex(4), "kind": kind, "code": code, "timeout_s": timeout}
	if cwd != "" {
		task["cwd"] = cwd
	}
	if len(env) > 0 {
		task["env"] = env
	}
	reply, err := command(c, f.Bus, fleet, nodeID, map[string]any{"type": "run_task", "task": task})
	if err != nil {
		return Failure(err)
	}
	if _, ok := reply["success"]; !ok {
		exit, hasExit := reply["exit_code"]
		code := 1.0
		if hasExit {
			code = num(exit)
		}
		reply["success"] = code == 0 && !truthy(reply["error"])
	}
	return reply
}

func (f *Fleet) runOnLabel(ctx context.Context, c *Call) (any, error) {
	a := struct {
		Label   string `json:"label"`
		Code    string `json:"code"`
		Kind    string `json:"kind"`
		Timeout int    `json:"timeout"`
	}{Kind: "shell", Timeout: 60}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	records, err := f.records(ctx, c.Fleet)
	if err != nil {
		return Failure(err), nil
	}
	var targets []string
	for _, n := range records {
		if slices.Contains(strings_(n["labels"]), a.Label) {
			targets = append(targets, str(n["node_id"]))
		}
	}
	if len(targets) == 0 {
		return map[string]any{"success": false, "error": fmt.Sprintf("no nodes with label %q", a.Label), "nodes": []any{}}, nil
	}
	results := make([]map[string]any, len(targets))
	var wg sync.WaitGroup
	for k, t := range targets {
		wg.Add(1)
		go func(k int, t string) {
			defer wg.Done()
			results[k] = f.runOnNode(ctx, c.Fleet, t, a.Code, a.Kind, a.Timeout)
		}(k, t)
	}
	wg.Wait()
	all, byNode := true, map[string]any{}
	for k, t := range targets {
		byNode[t] = results[k]
		all = all && results[k]["success"] == true
	}
	return map[string]any{"success": all, "label": a.Label, "nodes": targets, "results": byNode}, nil
}

func (f *Fleet) progress(fleet, id string, p map[string]any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.transfers == nil {
		f.transfers = map[string]map[string]map[string]any{}
	}
	if f.transfers[fleet] == nil {
		f.transfers[fleet] = map[string]map[string]any{}
	}
	f.transfers[fleet][id] = p
}

// startTransfer runs one node-to-node transfer in the background, recording
// its progress for transfer_status; done is closed when it ends.
func (f *Fleet) startTransfer(fleet, src, srcPath, dst, dstPath, verify, compress string, resume bool, timeout int) (string, chan struct{}) {
	id := "x_" + randomHex(4)
	f.progress(fleet, id, map[string]any{"transfer_id": id, "state": "pending"})
	done := make(chan struct{})
	go func() {
		defer close(done)
		stop, err := f.Bus.Subscribe(fleet, proto.SubjTransferProgress(fleet, id), func(data []byte) {
			var p map[string]any
			if json.Unmarshal(data, &p) == nil {
				f.progress(fleet, id, p)
			}
		})
		if err == nil {
			defer stop()
		}
		options := map[string]any{"verify": verify}
		if compress != "" && compress != "none" {
			options["compress"] = compress
		}
		if resume {
			options["resume"] = true
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
		defer cancel()
		reply, err := command(ctx, f.Bus, fleet, src, map[string]any{"type": "transfer", "transfer": map[string]any{
			"transfer_id": id, "src_node": src, "dst_node": dst, "src_path": srcPath, "dst_path": dstPath, "options": options}})
		if err != nil {
			f.progress(fleet, id, map[string]any{"transfer_id": id, "state": "failed", "error": err.Error()})
			return
		}
		f.progress(fleet, id, reply)
	}()
	return id, done
}

var selfNames = map[string]bool{"local": true, "workspace": true, "agent": true, "sandbox": true, "here": true, "me": true}

func (f *Fleet) transfer(ctx context.Context, c *Call) (any, error) {
	a := struct {
		SrcNode  string `json:"src_node"`
		SrcPath  string `json:"src_path"`
		DstNode  string `json:"dst_node"`
		DstPath  string `json:"dst_path"`
		Verify   string `json:"verify"`
		Compress string `json:"compress"`
		Resume   bool   `json:"resume"`
		Wait     bool   `json:"wait"`
		Timeout  int    `json:"timeout"`
	}{Verify: "sha256", Compress: "none", Timeout: 600}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	if selfNames[strings.ToLower(strings.TrimSpace(a.DstNode))] {
		self := f.self(c)
		if self == "" {
			return map[string]any{"success": false, "error": "this sandbox hasn't joined the fleet as a node yet — retry in a few seconds"}, nil
		}
		a.DstNode = self
	}
	id, done := f.startTransfer(c.Fleet, a.SrcNode, a.SrcPath, a.DstNode, a.DstPath, a.Verify, a.Compress, a.Resume, a.Timeout)
	if !a.Wait {
		return map[string]any{"success": true, "transfer_id": id, "state": "started", "note": "poll transfer_status(transfer_id) for progress"}, nil
	}
	select {
	case <-done:
	case <-ctx.Done():
		f.mu.Lock()
		p := f.transfers[c.Fleet][id]
		f.mu.Unlock()
		return merge(map[string]any{"success": false, "transfer_id": id, "error": ctx.Err().Error()}, p), nil
	}
	f.mu.Lock()
	p := f.transfers[c.Fleet][id]
	f.mu.Unlock()
	return merge(map[string]any{"success": p["state"] == "done", "transfer_id": id}, p), nil
}

// nodeList accepts a list or a comma/space separated string of ids.
func nodeList(raw json.RawMessage) []string {
	var list []any
	if json.Unmarshal(raw, &list) == nil {
		var out []string
		for _, v := range list {
			if s := strings.TrimSpace(fmt.Sprint(v)); s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	var s string
	_ = json.Unmarshal(raw, &s)
	return strings.Fields(strings.ReplaceAll(s, ",", " "))
}

func (f *Fleet) broadcast(_ context.Context, c *Call) (any, error) {
	a := struct {
		SrcNode  string          `json:"src_node"`
		SrcPath  string          `json:"src_path"`
		DstNodes json.RawMessage `json:"dst_nodes"`
		DstPath  string          `json:"dst_path"`
		Verify   string          `json:"verify"`
		Compress string          `json:"compress"`
	}{Verify: "sha256", Compress: "none"}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	dsts := nodeList(a.DstNodes)
	if len(dsts) == 0 {
		return map[string]any{"success": false, "error": "no dst_nodes given"}, nil
	}
	transfers := map[string]any{}
	for _, d := range dsts {
		transfers[d], _ = f.startTransfer(c.Fleet, a.SrcNode, a.SrcPath, d, a.DstPath, a.Verify, a.Compress, false, 600)
	}
	return map[string]any{"success": true, "count": len(transfers), "transfers": transfers}, nil
}

func (f *Fleet) gather(_ context.Context, c *Call) (any, error) {
	a := struct {
		SrcNodes json.RawMessage `json:"src_nodes"`
		SrcPath  string          `json:"src_path"`
		DstNode  string          `json:"dst_node"`
		DstDir   string          `json:"dst_dir"`
		Verify   string          `json:"verify"`
		Compress string          `json:"compress"`
	}{Verify: "sha256", Compress: "none"}
	if err := c.Decode(&a); err != nil {
		return nil, err
	}
	srcs := nodeList(a.SrcNodes)
	if len(srcs) == 0 {
		return map[string]any{"success": false, "error": "no src_nodes given"}, nil
	}
	base := path.Base(strings.TrimRight(a.SrcPath, "/"))
	if base == "." || base == "/" || base == "" {
		base = "file"
	}
	dir := strings.TrimRight(a.DstDir, "/")
	transfers := map[string]any{}
	for _, s := range srcs {
		transfers[s], _ = f.startTransfer(c.Fleet, s, a.SrcPath, a.DstNode, dir+"/"+s+"_"+base, a.Verify, a.Compress, false, 600)
	}
	return map[string]any{"success": true, "count": len(transfers), "dst_dir": a.DstDir, "transfers": transfers}, nil
}
