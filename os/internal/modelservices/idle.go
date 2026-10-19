package modelservices

import (
	"bytes"
	"context"
	"fmt"
	"regexp"
	"time"
)

// Engine idle: the node puts an owned on-demand engine to sleep after a
// timeout and wakes it for the next call; owner management registers and
// cancels that policy (formerly pantheonos/models/idle.py, idle_management.py).

var idleStates = map[string]bool{"active": true, "fencing": true, "stopping": true, "sleeping": true, "waking": true, "rebinding": true}

// observe reads (or, with action "wake", requests) a service's idle state.
func (m *Manager) observe(ctx context.Context, row Row, action string) (Row, map[string]any, error) {
	policy := mapOf(row["engine_idle"])
	if row["state"] != "ready" || policy["phase"] != "enabled" {
		return nil, nil, fmt.Errorf("This service has no enabled engine idle policy")
	}
	var result map[string]any
	if err := m.dir.request(ctx, "POST", "/api/model-services/"+strOf(row["deployment_id"])+"/engine-idle",
		map[string]any{"action": action, "revision": row["revision"]}, &result); err != nil {
		return nil, nil, err
	}
	current, idle := mapOf(result["deployment"]), mapOf(result["idle"])
	changed := current == nil || idle == nil || current["state"] != "ready" || pending(current)
	for _, k := range []string{"deployment_id", "node_id", "engine", "mode", "binding", "managed", "engine_idle"} {
		if current != nil && !bytes.Equal(Compact(current[k]), Compact(row[k])) {
			changed = true
		}
	}
	if changed || idle["id"] != row["deployment_id"] || intOf(idle["revision"]) != intOf(policy["policy_revision"]) ||
		idle["enabled"] != true || !idleStates[strOf(idle["state"])] {
		return nil, nil, fmt.Errorf("Model idle policy changed; no inference was submitted")
	}
	return current, idle, nil
}

// wake restores a sleeping engine before its config or transport is used.
func (m *Manager) wake(ctx context.Context, row Row) (Row, error) {
	policy := mapOf(row["engine_idle"])
	if policy == nil || policy["phase"] == "disabled" {
		return row, nil
	}
	if policy["phase"] != "enabled" {
		return nil, fmt.Errorf("Finish changing the engine idle policy before calling this service")
	}
	deadline := time.Now().Add(610 * time.Second)
	current, idle, err := m.observe(ctx, row, "wake")
	if err != nil {
		return nil, err
	}
	for idle["state"] != "active" {
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("The model engine is still waking. Inspect its status before retrying; no inference was submitted.")
		}
		time.Sleep(500 * time.Millisecond)
		if current, idle, err = m.observe(ctx, row, "status"); err != nil {
			return nil, err
		}
		if idle["state"] == "active" {
			// Refresh the admission grace and publish the new binding via Hub CAS.
			if current, idle, err = m.observe(ctx, row, "wake"); err != nil {
				return nil, err
			}
		}
	}
	if intOf(mapOf(current["engine_binding"])["generation"]) != intOf(mapOf(idle["engine"])["generation"]) ||
		current["config_revision"] != idle["config_revision"] {
		return nil, fmt.Errorf("The awakened engine binding has not been published")
	}
	return current, nil
}

func shortBinding(v any) map[string]any {
	b := mapOf(v)
	return map[string]any{"instance_id": b["instance_id"], "revision": b["revision"], "generation": b["generation"]}
}

func registration(row Row, seconds int64) map[string]any {
	revision := mapOf(row["engine_idle"])["policy_revision"]
	if revision == nil {
		revision = number(0)
	}
	return map[string]any{"id": row["deployment_id"], "revision": revision, "connector": shortBinding(row["binding"]),
		"engine": shortBinding(row["engine_binding"]), "config_revision": row["config_revision"], "idle_seconds": number(seconds)}
}

func (m *Manager) idleCapability(ctx context.Context, row Row) error {
	node, err := m.node(ctx, strOf(row["node_id"]), true)
	if err != nil {
		return err
	}
	if runtimes(node)["model-engine-idle-management"] != "1" {
		return fmt.Errorf("Update Fleet on this node before managing automatic engine idle")
	}
	return nil
}

func same(a, b any) bool { return bytes.Equal(Compact(a), Compact(b)) }

// checkedPolicy verifies a node policy belongs to the saved registration.
func checkedPolicy(row Row, policy map[string]any) (map[string]any, error) {
	q := mapOf(mapOf(row["engine_idle"])["registration"])
	engine := mapOf(policy["engine"])
	qe := mapOf(q["engine"])
	revision := intOf(policy["revision"])
	qr := intOf(q["revision"])
	if !same(policy["registration"], q) || policy["id"] != q["id"] || !isInt(policy["revision"]) || (revision != qr+1 && revision != qr+2) ||
		intOf(policy["idle_seconds"]) != intOf(q["idle_seconds"]) || !same(policy["connector"], q["connector"]) ||
		engine["instance_id"] != qe["instance_id"] || engine["revision"] != qe["revision"] ||
		!isInt(engine["generation"]) || intOf(engine["generation"]) < intOf(qe["generation"]) {
		return nil, fmt.Errorf("Node idle policy does not match this saved registration")
	}
	if policy["enabled"] == true && revision != qr+1 {
		return nil, fmt.Errorf("Node idle policy revision changed")
	}
	return policy, nil
}

// enableIdle registers the policy (caller holds the deployment lock); the
// original request is saved before the node call.
func (m *Manager) enableIdle(ctx context.Context, row Row, seconds int64) (Row, error) {
	managed := mapOf(row["managed"])
	policyName := managed["load_policy"]
	if row["mode"] != "managed" || (row["engine"] != "ollama" && row["engine"] != "lmstudio") ||
		(policyName != "on_demand" && policyName != "warm") || seconds < intOf(managed["keep_alive_seconds"]) || row["state"] != "ready" ||
		row["engine_binding"] == nil || row["binding"] == nil || truthy(row["recovery"]) || truthy(row["engine_update"]) ||
		truthy(row["connector_update"]) || truthy(row["operation_stop"]) {
		return nil, fmt.Errorf("Choose a ready owned on-demand/warm engine and preserve its model TTL")
	}
	if err := m.idleCapability(ctx, row); err != nil {
		return nil, err
	}
	previous := mapOf(row["engine_idle"])
	switch previous["phase"] {
	case "enabled", "registering":
		if intOf(previous["idle_seconds"]) != seconds {
			return nil, fmt.Errorf("Disable the current idle policy before changing its timeout")
		}
	case "disabling":
		return nil, fmt.Errorf("Finish disabling this idle policy before enabling it again")
	default:
		q := registration(row, seconds)
		row["engine_idle"] = map[string]any{"idle_seconds": number(seconds), "policy_revision": q["revision"], "phase": "registering", "registration": q}
		var err error
		if row, err = m.save(ctx, row); err != nil {
			return nil, err
		}
	}
	idle := mapOf(row["engine_idle"])
	q := mapOf(idle["registration"])
	node := strOf(row["node_id"])
	state, err := m.nodes.status(ctx, node)
	if err != nil {
		return nil, err
	}
	policy := mapOf(mapOf(state["model_idle"])[strOf(row["deployment_id"])])
	if policy == nil || !same(policy["registration"], q) {
		if idle["phase"] != "registering" {
			return nil, fmt.Errorf("The node lost this idle policy; inspect service recovery")
		}
		if policy != nil && (intOf(policy["revision"]) != intOf(q["revision"]) || truthy(policy["enabled"]) || policy["state"] != "disabled") {
			return nil, fmt.Errorf("Another node idle policy superseded this registration")
		}
		if policy, err = m.nodes.modelIdle(ctx, node, "register", q); err != nil {
			return nil, err
		}
	}
	if policy, err = checkedPolicy(row, policy); err != nil {
		return nil, err
	}
	if policy["enabled"] != true || policy["state"] == "disabled" || policy["state"] == "recovery_required" {
		return nil, fmt.Errorf("This idle registration was cancelled or needs recovery; it was not restarted")
	}
	if idle["phase"] == "enabled" {
		return row, nil
	}
	idle["phase"], idle["policy_revision"] = "enabled", policy["revision"]
	return m.save(ctx, row)
}

// settledEngine adopts only the policy's own generation or a provable final
// operation of it.
func settledEngine(row Row, policy, state map[string]any) (map[string]any, error) {
	source := clone(row["engine_binding"]).(map[string]any)
	for k, v := range mapOf(policy["engine"]) {
		source[k] = v
	}
	scope := "engine-" + strOf(row["deployment_id"])
	current, err := exactInstance(state, source, scope)
	if err != nil {
		return nil, err
	}
	expected := intOf(source["generation"])
	g := intOf(current["generation"])
	if g == expected || (isStopped(current) && g == expected+1) {
		source["generation"] = current["generation"]
		return source, nil
	}
	for _, pair := range [][2]string{{"start", "start_operation"}, {"stop", "stop_operation"}} {
		op := mapOf(operations(state)[strOf(policy[pair[1]])])
		if op == nil {
			continue
		}
		request := mapOf(op["request"])
		final := op["state"] == "succeeded" || op["state"] == "failed" || op["state"] == "unknown"
		if op["model_idle_id"] == row["deployment_id"] && final && request["action"] == pair[0] && request["scope"] == scope &&
			request["digest"] == source["revision"] && intOf(request["generation"]) == expected && g == expected+1 {
			source["generation"] = current["generation"]
			return source, nil
		}
	}
	return nil, fmt.Errorf("Engine generation changed outside this idle operation; no newer process was adopted")
}

// cancelIdle revokes automatic work before stop/recovery/update, without waking.
func (m *Manager) cancelIdle(ctx context.Context, row Row) (Row, error) {
	current := mapOf(row["engine_idle"])
	if current == nil || current["phase"] == "disabled" {
		return row, nil
	}
	if err := m.idleCapability(ctx, row); err != nil {
		return nil, err
	}
	var err error
	if current["phase"] != "disabling" {
		current["phase"] = "disabling"
		if row, err = m.save(ctx, row); err != nil {
			return nil, err
		}
	}
	node := strOf(row["node_id"])
	reply, err := m.nodes.modelIdle(ctx, node, "cancel", mapOf(row["engine_idle"])["registration"])
	if err != nil {
		return nil, err
	}
	policy, err := checkedPolicy(row, reply)
	if err != nil {
		return nil, err
	}
	if policy["enabled"] != false || policy["state"] != "disabled" {
		return nil, fmt.Errorf("The node has not acknowledged idle cancellation")
	}
	deadline := time.Now().Add(30 * time.Second)
	var state map[string]any
	for {
		if state, err = m.nodes.status(ctx, node); err != nil {
			return nil, err
		}
		observed, err := checkedPolicy(row, mapOf(mapOf(state["model_idle"])[strOf(row["deployment_id"])]))
		if err != nil {
			return nil, err
		}
		if !same(observed, policy) {
			return nil, fmt.Errorf("Idle policy changed during cancellation; inspect this service")
		}
		busy := false
		for _, v := range operations(state) {
			op := mapOf(v)
			scope := mapOf(op["request"])["scope"]
			if (op["state"] == "queued" || op["state"] == "running") && (scope == "engine-"+strOf(row["deployment_id"]) || scope == "model-"+strOf(row["deployment_id"])) {
				busy = true
			}
		}
		if !busy {
			break
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("An owned lifecycle operation is still finishing. Resume cancellation after it finishes.")
		}
		time.Sleep(500 * time.Millisecond)
	}
	if row["engine_binding"], err = settledEngine(row, policy, state); err != nil {
		return nil, err
	}
	idle := mapOf(row["engine_idle"])
	idle["phase"], idle["policy_revision"] = "disabled", policy["revision"]
	return m.save(ctx, row)
}

// resetFence reopens an idle fence on explicit owner recovery only.
func (m *Manager) resetFence(ctx context.Context, binding any, status map[string]any) error {
	idle := mapOf(status["engine_idle"])
	switch idle["phase"] {
	case "fenced", "resuming", "stopped":
	default:
		return nil
	}
	if err := m.drainBinding(ctx, binding); err != nil {
		return err
	}
	latest, err := m.rpc(ctx, binding, "status", nil)
	if err != nil {
		return err
	}
	idle = mapOf(latest["engine_idle"])
	if idle["phase"] != "stopped" || !truthy(idle["suspend_id"]) {
		return fmt.Errorf("Idle fence is not stopped; inspect recovery before reopening it")
	}
	_, err = m.rpc(ctx, binding, "idle_reset", map[string]any{"suspend_id": idle["suspend_id"], "config_revision": latest["config_revision"]})
	return err
}

var revision64RE = regexp.MustCompile(`^[a-f0-9]{64}$`)

// recoveryRevisions are configurations a cancelled in-flight rebind may
// have left, which recovery may accept.
func recoveryRevisions(row Row, state map[string]any) (map[string]bool, error) {
	saved := mapOf(row["engine_idle"])
	if saved["phase"] != "disabled" {
		return map[string]bool{}, nil
	}
	policy, err := checkedPolicy(row, mapOf(mapOf(state["model_idle"])[strOf(row["deployment_id"])]))
	if err != nil {
		return nil, err
	}
	if policy["enabled"] != false || intOf(policy["revision"]) != intOf(saved["policy_revision"]) {
		return nil, fmt.Errorf("Idle cancellation changed before recovery")
	}
	source := row
	if r := mapOf(row["recovery"]); r != nil {
		source = r
	}
	pe, se := mapOf(policy["engine"]), mapOf(source["engine_binding"])
	if !same(shortBinding(source["binding"]), policy["connector"]) || se["instance_id"] != pe["instance_id"] || se["revision"] != pe["revision"] {
		return map[string]bool{}, nil
	}
	out := map[string]bool{}
	for _, k := range []string{"config_revision", "resume_revision"} {
		if v, ok := policy[k].(string); ok && revision64RE.MatchString(v) {
			out[v] = true
		}
	}
	return out, nil
}

func (m *Manager) EngineIdleStatus(ctx context.Context, id string) (map[string]any, error) {
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	_, snapshot, err := m.observe(ctx, row, "status")
	return snapshot, err
}

func (m *Manager) SetEngineIdle(ctx context.Context, id string, seconds any, revision any) (Row, error) {
	if !isInt(seconds) || intOf(seconds) < 0 || intOf(seconds) > 86400 {
		return nil, fmt.Errorf("Idle timeout must be 0 (disabled) or 1–86400 seconds")
	}
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if !isInt(revision) || intOf(revision) != intOf(row["revision"]) {
		return nil, fmt.Errorf("Model service changed; refresh before changing its idle policy")
	}
	if intOf(seconds) > 0 {
		return m.enableIdle(ctx, row, intOf(seconds))
	}
	if row, err = m.cancelIdle(ctx, row); err != nil {
		return nil, err
	}
	// Turning off automatic idle restores normal ready service behavior.
	if row["engine_idle"] != nil && (row["state"] == "ready" || row["state"] == "recovering") {
		return m.recoverLocked(ctx, row)
	}
	return row, nil
}
