package modelservices

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Recovery of pinned instances after a runner/node restart, explicit stop of
// an unfinished operation, and engine updates (formerly
// pantheon/models/recovery.py, operation_stop.py, engine_upgrade.py). Hub
// intent survives restarts; deterministic operation ids fence lost replies;
// a later unrelated generation is never adopted.

func exactInstance(state, binding map[string]any, scope string) (map[string]any, error) {
	current := mapOf(instances(state)[strOf(binding["instance_id"])])
	if current == nil || current["digest"] != binding["revision"] || current["scope"] != scope || current["app_id"] != "model-service" {
		return nil, fmt.Errorf("Model service identity changed; inspect its exact Fleet binding")
	}
	return current, nil
}

func withGeneration(binding map[string]any, generation any) map[string]any {
	out := clone(binding).(map[string]any)
	out["generation"] = generation
	return out
}

func (m *Manager) restore(ctx context.Context, row Row, key, scope string) (map[string]any, error) {
	recovery := mapOf(row["recovery"])
	source := mapOf(recovery[key])
	node := strOf(row["node_id"])
	operationID := "recover-" + strOf(recovery["operation_id"]) + "-" + key
	state, err := m.nodes.status(ctx, node)
	if err != nil {
		return nil, err
	}
	current, err := exactInstance(state, source, scope)
	if err != nil {
		return nil, err
	}
	sg := intOf(source["generation"])
	previous := mapOf(operations(state)[operationID])
	if previous != nil {
		request := mapOf(previous["request"])
		rg := intOf(request["generation"])
		if request["action"] != "start" || request["scope"] != scope || request["digest"] != source["revision"] || (rg != sg && rg != sg+1) {
			return nil, fmt.Errorf("Recovery operation identity changed; inspect it in Fleet")
		}
		if previous["state"] == "queued" || previous["state"] == "running" {
			if state, err = m.nodes.wait(ctx, node, previous); err != nil {
				return nil, err
			}
			if current, err = exactInstance(state, source, scope); err != nil {
				return nil, err
			}
		}
		if intOf(current["generation"]) != rg+1 {
			return nil, fmt.Errorf("Recovery start did not leave its expected generation; inspect it in Fleet")
		}
	} else if g := intOf(current["generation"]); g != sg && !(isStopped(current) && g == sg+1) {
		return nil, fmt.Errorf("Model service generation changed; recovery did not adopt another process")
	}
	op, err := m.nodes.submit(ctx, node, "recover", strOf(source["revision"]), scope, intOf(current["generation"]), "", nil)
	if err != nil {
		return nil, err
	}
	if state, err = m.nodes.wait(ctx, node, op); err != nil {
		return nil, err
	}
	if current, err = exactInstance(state, source, scope); err != nil {
		return nil, err
	}
	if isStopped(current) {
		if previous != nil {
			return nil, fmt.Errorf("The recovery process exited again; inspect its logs in Fleet before starting another recovery")
		}
		// Only this single recorded start may advance a dead source generation.
		op, err := m.nodes.submit(ctx, node, "start", strOf(source["revision"]), scope, intOf(current["generation"]), operationID, nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, node, op); err != nil {
			return nil, err
		}
		if current, err = exactInstance(state, source, scope); err != nil {
			return nil, err
		}
	}
	if current["state"] != "ready" {
		return nil, fmt.Errorf("The owned process is not ready; inspect it in Fleet")
	}
	binding := withGeneration(source, current["generation"])
	return binding, m.nodes.keepAlive(ctx, node, binding)
}

func (m *Manager) Recover(ctx context.Context, id string) (Row, error) {
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["mode"] == "group" {
		return nil, fmt.Errorf("Recover the original model group; individual ranks cannot be restarted")
	}
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	if row, err = m.dir.deployment(ctx, id); err != nil {
		return nil, err
	}
	return m.recoverLocked(ctx, row)
}

func (m *Manager) recoverLocked(ctx context.Context, row Row) (Row, error) {
	id, node := strOf(row["deployment_id"]), strOf(row["node_id"])
	if truthy(row["operation_stop"]) {
		return nil, fmt.Errorf("Finish stopping this operation before recovering the service")
	}
	if truthy(row["connector_update"]) || truthy(row["engine_update"]) || (row["state"] != "ready" && row["state"] != "recovering") {
		return nil, fmt.Errorf("Finish setup, stopping or the connector update before recovering this service")
	}
	record, err := m.node(ctx, node, row["mode"] == "managed")
	if err != nil {
		return nil, err
	}
	if runtimes(record)["app-recovery"] != "1" {
		return nil, fmt.Errorf("Update Fleet on this node to verify and recover owned processes")
	}
	if row, err = m.cancelIdle(ctx, row); err != nil {
		return nil, err
	}
	if !truthy(row["recovery"]) {
		if row["binding"] == nil || (row["mode"] == "managed" && row["engine_binding"] == nil) {
			return nil, fmt.Errorf("Complete service setup before recovery")
		}
		state, err := m.nodes.status(ctx, node)
		if err != nil {
			return nil, err
		}
		definition := mapOf(mapOf(mapOf(state["installations"])[strOf(mapOf(row["binding"])["revision"])])["definition"])
		if !versionAtLeast(strOf(definition["version"]), [3]int64{0, 1, 2}) {
			return nil, fmt.Errorf("Update the connector before using service recovery")
		}
		if _, _, err := boundInstance(state, mapOf(row["binding"]), "model-"+id, false); err != nil {
			return nil, err
		}
		if row["engine_binding"] != nil {
			if _, _, err := boundInstance(state, mapOf(row["engine_binding"]), "engine-"+id, false); err != nil {
				return nil, err
			}
		}
		row["state"] = "recovering"
		row["recovery"] = map[string]any{"operation_id": newID(), "binding": clone(row["binding"]), "engine_binding": row["engine_binding"]}
		if row, err = m.save(ctx, row); err != nil {
			return nil, err
		}
	}
	binding, err := m.restore(ctx, row, "binding", "model-"+id)
	if err != nil {
		return nil, err
	}
	status, err := m.rpc(ctx, binding, "status", nil)
	if err != nil {
		return nil, err
	}
	if intOf(status["recovery_protocol"]) != 1 {
		return nil, fmt.Errorf("This connector lacks the required recovery protocol")
	}
	if err := m.drainBinding(ctx, binding); err != nil {
		return nil, err
	}
	if row["engine_idle"] != nil {
		if status, err = m.rpc(ctx, binding, "status", nil); err != nil {
			return nil, err
		}
	}
	if err := m.resetFence(ctx, binding, status); err != nil {
		return nil, err
	}
	idleRevisions := map[string]bool{}
	if row["engine_idle"] != nil {
		state, err := m.nodes.status(ctx, node)
		if err != nil {
			return nil, err
		}
		if idleRevisions, err = recoveryRevisions(row, state); err != nil {
			return nil, err
		}
	}
	engineBinding := row["engine_binding"]
	expected := row["config_revision"]
	if row["mode"] == "managed" {
		restored, err := m.restore(ctx, row, "engine_binding", "engine-"+id)
		if err != nil {
			return nil, err
		}
		engineBinding = restored
		configuration, err := m.managedConfiguration(ctx, row, restored)
		if err != nil {
			return nil, err
		}
		preview, err := m.rpc(ctx, binding, "preview_configuration", configuration)
		if err != nil {
			return nil, err
		}
		target := preview["config_revision"]
		current := strOf(status["config_revision"])
		if current != strOf(expected) && current != strOf(target) && !idleRevisions[current] {
			return nil, fmt.Errorf("Connector configuration changed outside recovery; it was not overwritten")
		}
		if current != strOf(target) {
			configuration["expected_revision"] = current
			result, err := m.rpc(ctx, binding, "configure", configuration)
			if err != nil {
				return nil, err
			}
			if result["config_revision"] != target {
				return nil, fmt.Errorf("Recovered engine configuration did not match its preview")
			}
		}
		expected = target
	} else if status["config_revision"] != expected {
		return nil, fmt.Errorf("Attached endpoint configuration changed; recovery did not overwrite it")
	}
	discovered, err := m.rpc(ctx, binding, "discover", nil)
	if err != nil {
		return nil, err
	}
	if discovered["config_revision"] != expected {
		return nil, fmt.Errorf("Configuration changed while validating recovery")
	}
	for _, pair := range []struct {
		binding any
		scope   string
	}{{binding, "model-" + id}, {engineBinding, "engine-" + id}} {
		if pair.binding == nil || mapOf(pair.binding) == nil {
			continue
		}
		state, err := m.nodes.status(ctx, node)
		if err != nil {
			return nil, err
		}
		instance, dead, err := boundInstance(state, mapOf(pair.binding), pair.scope, false)
		if err != nil {
			return nil, err
		}
		if dead || instance["state"] != "ready" {
			return nil, fmt.Errorf("An owned process changed during recovery; retry after inspecting Fleet")
		}
	}
	if _, err := m.rpc(ctx, binding, "resume", map[string]any{"config_revision": expected}); err != nil {
		return nil, err
	}
	row["binding"], row["engine_binding"], row["state"], row["recovery"], row["config_revision"] = binding, engineBinding, "ready", nil, expected
	return m.save(ctx, row)
}

// ---- stopping an unfinished operation ---------------------------------------

var pendingKinds = []string{"recovery", "connector_update", "engine_update"}

func idleOperations(state map[string]any, scopes map[string]bool) error {
	for _, v := range operations(state) {
		op := mapOf(v)
		if (op["state"] == "queued" || op["state"] == "running") && scopes[strOf(mapOf(op["request"])["scope"])] {
			return fmt.Errorf("Fleet is still running this operation. Wait for it to finish before stopping it.")
		}
	}
	return nil
}

func snapshotTarget(state, binding map[string]any, scope, role string) (map[string]any, error) {
	current, err := exactInstance(state, binding, scope)
	if err != nil {
		return nil, err
	}
	g, bg := intOf(current["generation"]), intOf(binding["generation"])
	if g != bg && !(isStopped(current) && g == bg+1) {
		return nil, fmt.Errorf("The operation generation changed; no newer process was stopped")
	}
	return map[string]any{"role": role, "scope": scope, "binding": withGeneration(binding, current["generation"])}, nil
}

func validatePlan(state, intent map[string]any) error {
	scopes, known := map[string]bool{}, map[any]bool{}
	for _, t := range listOf(intent["targets"]) {
		scopes[strOf(mapOf(t)["scope"])] = true
		known[mapOf(mapOf(t)["binding"])["instance_id"]] = true
	}
	if err := idleOperations(state, scopes); err != nil {
		return err
	}
	for _, v := range instances(state) {
		in := mapOf(v)
		if scopes[strOf(in["scope"])] && !known[in["instance_id"]] && !isStopped(in) {
			return fmt.Errorf("An unplanned instance appeared; inspect Fleet before stopping this operation")
		}
	}
	for _, t := range listOf(intent["targets"]) {
		target := mapOf(t)
		if _, err := snapshotTarget(state, mapOf(target["binding"]), strOf(target["scope"]), strOf(target["role"])); err != nil {
			return err
		}
	}
	return nil
}

func stopPlan(row Row, state map[string]any) (map[string]any, error) {
	var kinds []string
	for _, k := range pendingKinds {
		if truthy(row[k]) {
			kinds = append(kinds, k)
		}
	}
	if len(kinds) != 1 {
		return nil, fmt.Errorf("Select one unfinished recovery or update")
	}
	kind := kinds[0]
	pend := mapOf(row[kind])
	id := strOf(row["deployment_id"])
	connectorScope, engineScope := "model-"+id, "engine-"+id
	if err := idleOperations(state, map[string]bool{connectorScope: true, engineScope: true}); err != nil {
		return nil, err
	}
	var targets []any
	for _, pair := range [][2]string{{"binding", connectorScope}, {"engine_binding", engineScope}} {
		key, scope := pair[0], pair[1]
		if row[key] == nil {
			continue
		}
		binding := clone(row[key]).(map[string]any)
		if kind == "recovery" {
			source := mapOf(pend[key])
			op := mapOf(operations(state)["recover-"+strOf(pend["operation_id"])+"-"+key])
			if op != nil {
				req := mapOf(op["request"])
				rg, sg := intOf(req["generation"]), intOf(source["generation"])
				if req["action"] != "start" || req["scope"] != scope || req["digest"] != source["revision"] || (rg != sg && rg != sg+1) {
					return nil, fmt.Errorf("Recovery start identity changed")
				}
				current, err := exactInstance(state, source, scope)
				if err != nil {
					return nil, err
				}
				// A start can fail preflight without incrementing its generation.
				if intOf(current["generation"]) == rg && isStopped(current) {
					binding["generation"] = req["generation"]
				} else {
					binding["generation"] = number(rg + 1)
				}
			}
		}
		t, err := snapshotTarget(state, binding, scope, key)
		if err != nil {
			return nil, err
		}
		targets = append(targets, t)
	}
	if kind == "connector_update" || kind == "engine_update" {
		scope := connectorScope
		if kind == "engine_update" {
			scope = engineScope
		}
		var matches []map[string]any
		for _, v := range instances(state) {
			in := mapOf(v)
			if in["digest"] == pend["target_revision"] && in["scope"] == scope {
				matches = append(matches, in)
			}
		}
		if len(matches) > 1 {
			return nil, fmt.Errorf("Replacement instance identity is ambiguous")
		}
		if len(matches) == 1 {
			current := matches[0]
			g := intOf(current["generation"])
			if current["app_id"] != "model-service" || (g != 0 && g != 1) {
				return nil, fmt.Errorf("Replacement generation changed outside this operation")
			}
			if kind == "engine_update" {
				if current["instance_id"] != pend["target_instance_id"] {
					return nil, fmt.Errorf("Replacement engine identity changed")
				}
				op := mapOf(operations(state)["engine-start-"+strOf(pend["operation_id"])])
				req := mapOf(op["request"])
				if g != 0 && (op == nil || req["action"] != "start" || req["scope"] != scope || req["digest"] != pend["target_revision"] || intOf(req["generation"]) != 0) {
					return nil, fmt.Errorf("Replacement engine was started outside this operation")
				}
			} else {
				original, err := exactInstance(state, mapOf(pend["source"]), scope)
				if err != nil {
					return nil, err
				}
				if !isStopped(original) || !same(current["data_source"], map[string]any{"digest": mapOf(pend["source"])["revision"], "generation": original["generation"]}) {
					return nil, fmt.Errorf("Replacement connector does not retain this source configuration")
				}
			}
			binding := bindingOf(strOf(row["node_id"]), current, strOf(pend["target_revision"]))
			t, err := snapshotTarget(state, binding, scope, "replacement")
			if err != nil {
				return nil, err
			}
			targets = append(targets, t)
		}
	}
	// Stop every connector before stopping any owned engine.
	sort.SliceStable(targets, func(i, j int) bool {
		return mapOf(targets[i])["scope"] == connectorScope && mapOf(targets[j])["scope"] != connectorScope
	})
	return map[string]any{"operation_id": newID(), "kind": kind, "started_at": float64(time.Now().UnixMilli()) / 1000, "targets": targets}, nil
}

func (m *Manager) StopOperation(ctx context.Context, id string, revision any) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["state"] == "stopped" && truthy(row["last_operation_stop"]) {
		return row, nil
	}
	if intOf(row["revision"]) != intOf(revision) {
		return nil, fmt.Errorf("Service changed. Refresh before stopping this operation.")
	}
	node := strOf(row["node_id"])
	if _, err := m.node(ctx, node, false); err != nil {
		return nil, err
	}
	state, err := m.nodes.status(ctx, node)
	if err != nil {
		return nil, err
	}
	if !truthy(row["operation_stop"]) {
		if row["operation_stop"], err = stopPlan(row, state); err != nil {
			return nil, err
		}
		if row, err = m.save(ctx, row); err != nil {
			return nil, err
		}
	}
	intent := mapOf(row["operation_stop"])
	if err := validatePlan(state, intent); err != nil {
		return nil, err
	}
	finished := map[string]map[string]any{}
	for _, t := range listOf(intent["targets"]) {
		target := mapOf(t)
		binding, scope := mapOf(target["binding"]), strOf(target["scope"])
		if state, err = m.nodes.status(ctx, node); err != nil {
			return nil, err
		}
		if err := validatePlan(state, intent); err != nil {
			return nil, err
		}
		current, err := exactInstance(state, binding, scope)
		if err != nil {
			return nil, err
		}
		if !isStopped(current) && current["state"] != "ready" {
			// Confirm dead processes and release their reservations.
			if op, err := m.nodes.submit(ctx, node, "recover", strOf(binding["revision"]), scope, intOf(binding["generation"]), "", nil); err == nil {
				_, _ = m.nodes.wait(ctx, node, op)
			}
			if state, err = m.nodes.status(ctx, node); err != nil {
				return nil, err
			}
			if err := validatePlan(state, intent); err != nil {
				return nil, err
			}
			if current, err = exactInstance(state, binding, scope); err != nil {
				return nil, err
			}
		}
		if !isStopped(current) {
			if scope == "model-"+id && current["state"] == "ready" {
				if err := m.drainBinding(ctx, binding); err != nil {
					return nil, err
				}
			}
			state, err := m.nodes.status(ctx, node)
			if err != nil {
				return nil, err
			}
			if err := validatePlan(state, intent); err != nil {
				return nil, err
			}
			op, err := m.nodes.submit(ctx, node, "stop", strOf(binding["revision"]), scope, intOf(binding["generation"]), "", nil)
			if err != nil {
				return nil, err
			}
			if state, err = m.nodes.wait(ctx, node, op); err != nil {
				return nil, err
			}
			if _, err := snapshotTarget(state, binding, scope, strOf(target["role"])); err != nil {
				return nil, err
			}
			if current, err = exactInstance(state, binding, scope); err != nil {
				return nil, err
			}
			if !isStopped(current) {
				return nil, fmt.Errorf("Fleet has not confirmed release of the operation resources")
			}
		}
		finished[strOf(target["role"])] = withGeneration(binding, current["generation"])
	}
	// Recheck the complete plan at publication.
	if state, err = m.nodes.status(ctx, node); err != nil {
		return nil, err
	}
	if err := validatePlan(state, intent); err != nil {
		return nil, err
	}
	for _, t := range listOf(intent["targets"]) {
		target := mapOf(t)
		done := finished[strOf(target["role"])]
		current, err := exactInstance(state, done, strOf(target["scope"]))
		if err != nil {
			return nil, err
		}
		if !isStopped(current) || intOf(current["generation"]) != intOf(done["generation"]) {
			return nil, fmt.Errorf("An operation instance changed before completion")
		}
	}
	row["binding"] = finished["binding"]
	if e, ok := finished["engine_binding"]; ok {
		row["engine_binding"] = e
	} else {
		row["engine_binding"] = nil
	}
	for _, t := range listOf(intent["targets"]) {
		target := mapOf(t)
		if target["role"] == "replacement" && intOf(mapOf(target["binding"])["generation"]) > 0 {
			// Retain the explicitly chosen replacement; starting it is separate.
			if intent["kind"] == "engine_update" {
				row["engine_binding"], row["managed"] = finished["replacement"], mapOf(row["engine_update"])["target_config"]
			} else {
				row["binding"] = finished["replacement"]
			}
		}
	}
	completed := clone(intent).(map[string]any)
	completed["completed_at"] = float64(time.Now().UnixMilli()) / 1000
	row["state"], row["operation_stop"], row["last_operation_stop"] = "stopped", nil, completed
	for _, k := range pendingKinds {
		row[k] = nil
	}
	return m.save(ctx, row)
}

// ---- engine update --------------------------------------------------------------

func (m *Manager) UpgradeEngine(ctx context.Context, id, recipeID string) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if truthy(row["operation_stop"]) {
		return nil, fmt.Errorf("Finish stopping this operation before updating the engine")
	}
	if row["mode"] != "managed" || row["engine_binding"] == nil || truthy(row["recovery"]) || truthy(row["connector_update"]) {
		return nil, fmt.Errorf("An owned engine with no pending recovery or connector update is required")
	}
	upd := mapOf(row["engine_update"])
	if upd == nil && row["state"] != "ready" {
		return nil, fmt.Errorf("Start and verify this service before updating its engine")
	}
	if upd != nil && recipeID != mapOf(upd["target_config"])["recipe_id"] {
		return nil, fmt.Errorf("Resume the pinned engine update before selecting another version")
	}
	nodeID := strOf(row["node_id"])
	node, err := m.node(ctx, nodeID, true)
	if err != nil {
		return nil, err
	}
	rt := runtimes(node)
	if row["engine"] == "speaches" && (rt["app-readonly-mounts"] != "1" || rt["app-owner-user"] != "1") {
		return nil, fmt.Errorf("Update Fleet on this Linux node before updating the owned speech engine")
	}
	scope := "engine-" + id
	state, err := m.nodes.status(ctx, nodeID)
	if err != nil {
		return nil, err
	}
	connector, dead, err := boundInstance(state, mapOf(row["binding"]), "model-"+id, false)
	if err != nil {
		return nil, err
	}
	if dead || connector["state"] != "ready" {
		return nil, fmt.Errorf("The exact connector must be ready before resuming an engine update")
	}
	status, err := m.rpc(ctx, row["binding"], "status", nil)
	if err != nil {
		return nil, err
	}
	if intOf(status["recovery_protocol"]) != 1 {
		return nil, fmt.Errorf("Update the connector before updating its engine")
	}
	if upd == nil {
		c, err := m.connector(ctx)
		if err != nil {
			return nil, err
		}
		target := platform(node)
		config := clone(row["managed"]).(map[string]any)
		config["recipe_id"] = recipeID
		valid, err := c.ValidateManaged(config, target)
		if err != nil {
			return nil, err
		}
		recipe, err := c.Recipe(recipeID, target)
		if err != nil {
			return nil, err
		}
		if recipe["engine"] != row["engine"] {
			return nil, fmt.Errorf("An engine update cannot change the model engine family")
		}
		catalog, err := m.rpc(ctx, row["binding"], "engines_catalog", nil)
		if err != nil {
			return nil, err
		}
		var selected map[string]any
		for _, r := range listOf(catalog["recipes"]) {
			if mapOf(r)["id"] == recipeID {
				selected = mapOf(r)
			}
		}
		if selected == nil {
			return nil, fmt.Errorf("Update the connector to include the selected engine recipe")
		}
		if truthy(selected["unavailable_reason"]) || (recipe["runtime"] != "container" && selected["prepared"] != true) {
			return nil, fmt.Errorf("Prepare the selected engine version before updating; the current engine is unchanged")
		}
		tree, err := c.EnginePackage(valid, target)
		if err != nil {
			return nil, err
		}
		digest, err := m.nodes.stage(ctx, nodeID, tree)
		if err != nil {
			return nil, err
		}
		if digest == mapOf(row["engine_binding"])["revision"] {
			return row, nil
		}
		if state, err = m.nodes.status(ctx, nodeID); err != nil {
			return nil, err
		}
		if current := findInstance(state, digest, scope); current != nil && (intOf(current["generation"]) != 0 || !isStopped(current)) {
			return nil, fmt.Errorf("This engine revision already ran; inspect it in Fleet before updating")
		}
		if row["engine_idle"] != nil {
			if row, err = m.recoverLocked(ctx, row); err != nil {
				return nil, err
			}
			if state, err = m.nodes.status(ctx, nodeID); err != nil {
				return nil, err
			}
			connector, dead, err := boundInstance(state, mapOf(row["binding"]), "model-"+id, false)
			if err != nil {
				return nil, err
			}
			if dead || connector["state"] != "ready" {
				return nil, fmt.Errorf("The exact connector must be ready before updating the engine")
			}
			if status, err = m.rpc(ctx, row["binding"], "status", nil); err != nil {
				return nil, err
			}
			if intOf(status["recovery_protocol"]) != 1 {
				return nil, fmt.Errorf("Update the connector before updating its engine")
			}
		}
		original, dead, err := boundInstance(state, mapOf(row["engine_binding"]), scope, false)
		if err != nil {
			return nil, err
		}
		if dead || original["state"] != "ready" || status["config_revision"] != row["config_revision"] {
			return nil, fmt.Errorf("Verify service recovery before updating the engine")
		}
		op, err := m.nodes.submit(ctx, nodeID, "install", digest, scope, 0, "", nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, nodeID, op); err != nil {
			return nil, err
		}
		if intOf(state["protocol"]) != 1 || !truthy(state["owner"]) || state["node_id"] != nodeID {
			return nil, fmt.Errorf("Fleet did not provide this node’s owned instance identity")
		}
		sum := sha256.Sum256([]byte(strings.Join([]string{strOf(state["owner"]), nodeID, digest, scope}, "\x00")))
		targetID := hex.EncodeToString(sum[:])[:32]
		if current := mapOf(instances(state)[targetID]); current != nil && (current["app_id"] != "model-service" || intOf(current["generation"]) != 0 || !isStopped(current)) {
			return nil, fmt.Errorf("The prepared engine target is not an unused owned instance")
		}
		upd = map[string]any{"operation_id": newID(), "source": clone(row["engine_binding"]), "connector": clone(row["binding"]),
			"target_revision": digest, "target_instance_id": targetID, "target_config": valid,
			"started_at": float64(time.Now().UnixMilli()) / 1000, "phase": "draining"}
		row["engine_update"], row["state"] = upd, "stopping"
		if row, err = m.save(ctx, row); err != nil {
			return nil, err
		}
		upd = mapOf(row["engine_update"])
	}
	phase := func(value string) error {
		u := mapOf(row["engine_update"])
		if u["phase"] == value {
			return nil
		}
		u["phase"] = value
		var err error
		row, err = m.save(ctx, row)
		return err
	}
	source := mapOf(upd["source"])
	targetBinding := map[string]any{"node_id": nodeID, "instance_id": upd["target_instance_id"], "revision": upd["target_revision"],
		"generation": number(1), "component": "backend", "port": "http"}
	if err := m.drainBinding(ctx, row["binding"]); err != nil {
		return nil, err
	}
	if err := phase("stopping"); err != nil {
		return nil, err
	}
	if state, err = m.nodes.status(ctx, nodeID); err != nil {
		return nil, err
	}
	_, dead, err = boundInstance(state, source, scope, false)
	if err != nil {
		return nil, err
	}
	if !dead {
		op, err := m.nodes.submit(ctx, nodeID, "stop", strOf(source["revision"]), scope, intOf(source["generation"]), "engine-stop-"+strOf(upd["operation_id"]), nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, nodeID, op); err != nil {
			return nil, err
		}
		if _, dead, err = boundInstance(state, source, scope, false); err != nil {
			return nil, err
		}
	}
	if !dead {
		return nil, fmt.Errorf("The previous engine has not released its processes and reservations")
	}
	if err := phase("starting"); err != nil {
		return nil, err
	}
	current := mapOf(instances(state)[strOf(targetBinding["instance_id"])])
	if current != nil {
		if _, err := exactInstance(state, targetBinding, scope); err != nil {
			return nil, err
		}
	}
	operationID := "engine-start-" + strOf(upd["operation_id"])
	if previous := mapOf(operations(state)[operationID]); previous != nil {
		req := mapOf(previous["request"])
		if req["action"] != "start" || req["scope"] != scope || req["digest"] != targetBinding["revision"] || intOf(req["generation"]) != 0 {
			return nil, fmt.Errorf("Engine update operation identity changed")
		}
		if state, err = m.nodes.wait(ctx, nodeID, previous); err != nil {
			return nil, err
		}
	} else {
		if current != nil && (intOf(current["generation"]) != 0 || !isStopped(current)) {
			return nil, fmt.Errorf("The target was started outside this engine update")
		}
		op, err := m.nodes.submit(ctx, nodeID, "start", strOf(targetBinding["revision"]), scope, 0, operationID, nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, nodeID, op); err != nil {
			return nil, err
		}
	}
	if current, err = exactInstance(state, targetBinding, scope); err != nil {
		return nil, err
	}
	if intOf(current["generation"]) != 1 || current["state"] != "ready" {
		return nil, fmt.Errorf("The pinned engine did not leave its expected ready generation; inspect Fleet")
	}
	if err := m.nodes.keepAlive(ctx, nodeID, targetBinding); err != nil {
		return nil, err
	}
	if err := phase("verifying"); err != nil {
		return nil, err
	}
	withTarget := clone(row).(map[string]any)
	withTarget["managed"] = upd["target_config"]
	configuration, err := m.managedConfiguration(ctx, withTarget, targetBinding)
	if err != nil {
		return nil, err
	}
	preview, err := m.rpc(ctx, row["binding"], "preview_configuration", configuration)
	if err != nil {
		return nil, err
	}
	target := preview["config_revision"]
	if status, err = m.rpc(ctx, row["binding"], "status", nil); err != nil {
		return nil, err
	}
	if status["config_revision"] != row["config_revision"] && status["config_revision"] != target {
		return nil, fmt.Errorf("Connector configuration changed outside this engine update; it was not overwritten")
	}
	if status["config_revision"] != target {
		configuration["expected_revision"] = status["config_revision"]
		result, err := m.rpc(ctx, row["binding"], "configure", configuration)
		if err != nil {
			return nil, err
		}
		if result["config_revision"] != target {
			return nil, fmt.Errorf("Updated engine configuration does not match its preview")
		}
	}
	discovered, err := m.rpc(ctx, row["binding"], "discover", nil)
	if err != nil {
		return nil, err
	}
	offered := map[any]bool{}
	for _, d := range listOf(discovered["models"]) {
		offered[mapOf(d)["id"]] = true
	}
	retained := discovered["config_revision"] == target
	for _, p := range listOf(row["models"]) {
		retained = retained && offered[mapOf(p)["id"]]
	}
	if !retained {
		return nil, fmt.Errorf("The updated engine did not retain the published models; inspect Fleet before resuming")
	}
	if state, err = m.nodes.status(ctx, nodeID); err != nil {
		return nil, err
	}
	for _, pair := range []struct {
		binding map[string]any
		scope   string
	}{{mapOf(row["binding"]), "model-" + id}, {targetBinding, scope}} {
		instance, dead, err := boundInstance(state, pair.binding, pair.scope, false)
		if err != nil {
			return nil, err
		}
		if dead || instance["state"] != "ready" {
			return nil, fmt.Errorf("An owned process changed during engine verification")
		}
	}
	if _, err := m.rpc(ctx, row["binding"], "resume", map[string]any{"config_revision": target}); err != nil {
		return nil, err
	}
	row["managed"], row["engine_binding"], row["config_revision"], row["engine_update"], row["state"] = upd["target_config"], targetBinding, target, nil, "ready"
	return m.save(ctx, row)
}
