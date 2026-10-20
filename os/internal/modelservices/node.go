package modelservices

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/hosted"
	"github.com/aristoteleo/pantheonos/os/internal/lifecycle"
)

// Fleet node access for one owner (formerly FleetLifecycle over the owned
// resolver, pantheonos/appkit/lifecycle.py).
type nodes struct {
	bus   hosted.Bus
	fleet string
}

var nodeIDRE = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

// record is a node's registry record.
func (n nodes) record(ctx context.Context, id string) (map[string]any, error) {
	records, err := n.bus.Records(ctx, n.fleet)
	if err != nil {
		return nil, err
	}
	for _, r := range records {
		if r["node_id"] == id {
			return clone(r).(map[string]any), nil
		}
	}
	return nil, fmt.Errorf("Node is not in this user’s Fleet")
}

// online is the set of this owner's nodes seen within the last 90 seconds.
func (n nodes) online(ctx context.Context) (map[string]bool, error) {
	records, err := n.bus.Records(ctx, n.fleet)
	if err != nil {
		return nil, err
	}
	out := map[string]bool{}
	for _, r := range records {
		seen, err := time.Parse(time.RFC3339Nano, strOf(r["last_seen"]))
		status := strOf(mapOf(r["state"])["status"])
		if err == nil && time.Since(seen) <= 90*time.Second && (status == "online" || status == "busy") {
			out[strOf(r["node_id"])] = true
		}
	}
	return out, nil
}

func caps(record map[string]any) map[string]any { return mapOf(record["capability"]) }
func runtimes(record map[string]any) map[string]any {
	return mapOf(caps(record)["runtimes"])
}
func platform(record map[string]any) string {
	return strOf(caps(record)["os"]) + "-" + strOf(caps(record)["arch"])
}

// checked verifies a node may take App lifecycle requests.
func (n nodes) checked(ctx context.Context, id string) (map[string]any, error) {
	if !nodeIDRE.MatchString(id) {
		return nil, fmt.Errorf("A concrete Fleet node is required")
	}
	r, err := n.record(ctx, id)
	if err != nil {
		return nil, err
	}
	status := strOf(mapOf(r["state"])["status"])
	if seen, err := time.Parse(time.RFC3339Nano, strOf(r["last_seen"])); err != nil || time.Since(seen) > 90*time.Second {
		status = "offline"
	}
	if status != "online" && status != "busy" {
		return nil, fmt.Errorf("Node is offline; lifecycle outcome is unknown until it reconnects")
	}
	if runtimes(r)["app-lifecycle"] != "1" {
		return nil, fmt.Errorf("Upgrade Fleet on this node to enable managed App lifecycle v1")
	}
	return r, nil
}

// call sends one app_lifecycle command; a reply with "error" fails.
func (n nodes) call(ctx context.Context, node, method string, data map[string]any) (map[string]any, error) {
	if _, err := n.checked(ctx, node); err != nil {
		return nil, err
	}
	return n.raw(ctx, node, method, data)
}

func (n nodes) raw(ctx context.Context, node, method string, data map[string]any) (map[string]any, error) {
	payload := map[string]any{"type": "app_lifecycle", "protocol": 1, "method": method}
	for k, v := range data {
		payload[k] = v
	}
	timeout := 15 * time.Second
	if method == "model_idle_register" {
		timeout = 35 * time.Second
	}
	if method == "invoke" {
		timeout = time.Duration(intOf(data["timeout_seconds"])+10) * time.Second
	}
	c, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	reply, err := n.bus.Request(c, n.fleet, node, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := decode(reply, &out); err != nil {
		return nil, fmt.Errorf("node returned an invalid reply")
	}
	if e, ok := out["error"]; ok && e != nil && e != "" {
		return nil, fmt.Errorf("%v", e)
	}
	return out, nil
}

func (n nodes) status(ctx context.Context, node string) (map[string]any, error) {
	return n.call(ctx, node, "status", nil)
}

func instances(state map[string]any) map[string]any { return mapOf(state["instances"]) }
func operations(state map[string]any) map[string]any {
	return mapOf(state["operations"])
}

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// submit requests one durable lifecycle operation.
func (n nodes) submit(ctx context.Context, node, action, digest, scope string, generation int64, operationID string, extra map[string]any) (map[string]any, error) {
	if operationID == "" {
		operationID = newID()
	}
	request := map[string]any{"protocol": 1, "operation_id": operationID, "action": action, "digest": digest,
		"scope": scope, "generation": generation}
	for k, v := range extra {
		request[k] = v
	}
	out, err := n.call(ctx, node, "submit", map[string]any{"request": request})
	if err != nil {
		return nil, err
	}
	return mapOf(out["operation"]), nil
}

// wait polls until an operation succeeds; a start may take most of an hour.
func (n nodes) wait(ctx context.Context, node string, operation map[string]any) (map[string]any, error) {
	request := mapOf(operation["request"])
	limit := 610 * time.Second
	if request["action"] == "start" {
		limit = 3660 * time.Second
	}
	deadline := time.Now().Add(limit)
	for time.Now().Before(deadline) {
		state, err := n.status(ctx, node)
		if err != nil {
			return nil, err
		}
		op := mapOf(operations(state)[strOf(request["operation_id"])])
		if op == nil {
			return nil, fmt.Errorf("Operation unavailable; inspect this service in Fleet")
		}
		switch op["state"] {
		case "succeeded":
			return state, nil
		case "queued", "running":
		default:
			if e := strOf(op["error"]); e != "" {
				return nil, fmt.Errorf("%s", e)
			}
			return nil, fmt.Errorf("Model connector failed to start")
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return nil, fmt.Errorf("Setup is still running on the node. Check Fleet before retrying.")
}

// stage builds a package for the node's platform and delivers its bytes,
// unless that exact revision is already installed there.
func (n nodes) stage(ctx context.Context, node string, tree Tree) (string, error) {
	record, err := n.checked(ctx, node)
	if err != nil {
		return "", err
	}
	payload, digest, err := BuildArtifact(tree, platform(record))
	if err != nil {
		return "", err
	}
	return digest, n.stageExact(ctx, node, payload, digest)
}

func (n nodes) stageExact(ctx context.Context, node string, payload []byte, digest string) error {
	snapshot, err := n.status(ctx, node)
	if err != nil {
		return err
	}
	if len(payload) > 1 && payload[0] == 0x1f && payload[1] == 0x8b && snapshot["artifact_compression"] != "gzip-v1" {
		return fmt.Errorf("Update Fleet on this node to install compressed App releases")
	}
	if mapOf(mapOf(snapshot["installations"])[digest])["state"] == "installed" {
		return nil
	}
	for offset := 0; offset < len(payload); offset += lifecycle.MaxChunk {
		end := min(offset+lifecycle.MaxChunk, len(payload))
		if _, err := n.raw(ctx, node, "stage", map[string]any{"digest": digest, "offset": offset,
			"data": base64.StdEncoding.EncodeToString(payload[offset:end])}); err != nil {
			return err
		}
	}
	return nil
}

func (n nodes) keepAlive(ctx context.Context, node string, binding map[string]any) error {
	_, err := n.call(ctx, node, "keep_alive", map[string]any{"instance_id": binding["instance_id"], "revision": binding["revision"],
		"generation": binding["generation"], "lease_id": "", "release": false, "keep_alive": true})
	return err
}

// rpc calls a connector method over its node; configure/resume may restore
// resident memory and are given ten minutes.
func (n nodes) rpc(ctx context.Context, binding map[string]any, method string, args map[string]any) (map[string]any, error) {
	timeout := 15
	if method == "configure" || method == "resume" {
		timeout = 600
	}
	if args == nil {
		args = map[string]any{}
	}
	out, err := n.call(ctx, strOf(binding["node_id"]), "invoke", map[string]any{"app_id": "model-service",
		"instance_id": binding["instance_id"], "revision": binding["revision"], "generation": binding["generation"],
		"timeout_seconds": min(600, max(1, timeout)), "payload": map[string]any{"method": method, "args": args, "timeout_s": timeout}})
	if err != nil {
		return nil, err
	}
	result := mapOf(out["response"])
	if e, ok := result["error"]; ok && e != nil && e != "" {
		return nil, fmt.Errorf("%v", e)
	}
	return result, nil
}

func (n nodes) resourceStatus(ctx context.Context, node string) (map[string]any, error) {
	out, err := n.call(ctx, node, "resource_status", nil)
	if err == nil && intOf(out["protocol"]) != 1 {
		err = fmt.Errorf("Upgrade Fleet on this node for resource reservations")
	}
	return out, err
}

func (n nodes) modelIdle(ctx context.Context, node, action string, registration any) (map[string]any, error) {
	return n.call(ctx, node, "model_idle_"+action, map[string]any{"model_idle": registration})
}

var _ = json.Valid
