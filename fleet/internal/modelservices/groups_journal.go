package modelservices

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/hosted"
)

// Group intent lives in the owner's Hub (group_hub.py, group_creation.py,
// group_inference.py). The Hub validates every complete record and revision
// transition; this side checks identities and never replays a lost commit.

// GroupConflict means the durable intent changed; inspect it before continuing.
type GroupConflict struct{ msg string }

func (e *GroupConflict) Error() string { return e.msg }

func conflict(format string, args ...any) error { return &GroupConflict{fmt.Sprintf(format, args...)} }

var errNoGroup = errors.New("not found")

type groupJournal struct {
	dir      directory
	owner    string
	creation bool
}

func (j groupJournal) path(groupID string) (string, error) {
	if !groupIDRE.MatchString(groupID) {
		return "", fmt.Errorf("Invalid group id")
	}
	if j.creation {
		return "/api/model-services/group-creations/" + groupID, nil
	}
	return "/api/model-services/groups/" + groupID, nil
}

func (j groupJournal) request(ctx context.Context, method, path string, body any) (map[string]any, error) {
	var out map[string]any
	err := j.dir.request(ctx, method, path, body, &out)
	var status *hosted.StatusError
	if errors.As(err, &status) {
		switch status.Status {
		case 409:
			return nil, conflict("Group changed; inspect its current durable intent")
		case 404:
			return nil, errNoGroup
		}
	}
	return out, err
}

func (j groupJournal) validate(row map[string]any, groupID string) (map[string]any, error) {
	if j.creation {
		return j.validateCreation(row, groupID)
	}
	if !intIn(row["protocol"], 1, 1) || row["owner"] != j.owner || (groupID != "" && row["group_id"] != groupID) ||
		!intIn(row["revision"], 1, 1<<62) {
		return nil, conflict("Hub returned a different group identity or protocol")
	}
	return row, nil
}

func (j groupJournal) validateCreation(row map[string]any, groupID string) (map[string]any, error) {
	phases := map[any]bool{"intent": true, "building": true, "built": true, "handed_off": true, "aborting": true, "stopped": true}
	if !keys(row, "protocol", "owner", "group_id", "revision", "plan", "topology", "source_sha256", "phase",
		"authority_requested", "ca_sha256", "authority_closed", "artifacts") ||
		!intIn(row["protocol"], 1, 1) || row["owner"] != j.owner || (groupID != "" && row["group_id"] != groupID) ||
		!intIn(row["revision"], 1, 1<<62) || !phases[row["phase"]] ||
		!isBool(row["authority_requested"]) || !isBool(row["authority_closed"]) || !matches(sha256RE, row["source_sha256"]) ||
		(row["ca_sha256"] != nil && !matches(sha256RE, row["ca_sha256"])) {
		return nil, conflict("Hub returned a different creation identity or protocol")
	}
	plan := mapOf(row["plan"])
	peers, err := topologyFor(plan)
	if err != nil {
		return nil, conflict("%s", err)
	}
	if !same(peers.Document(), row["topology"]) || plan["owner"] != j.owner || plan["group_id"] != row["group_id"] {
		return nil, conflict("Creation plan differs from its pinned topology")
	}
	if _, err := j.path(strOf(row["group_id"])); err != nil {
		return nil, err
	}
	count := len(peers.Members())
	artifacts := listOf(row["artifacts"])
	if row["artifacts"] == nil || len(artifacts) > count {
		return nil, conflict("Creation has invalid original rank artifacts")
	}
	for i, raw := range artifacts {
		a := mapOf(raw)
		if !keys(a, "rank", "digest") || !intIn(a["rank"], 0, int64(count)-1) || !matches(sha256RE, a["digest"]) ||
			(i > 0 && intOf(a["rank"]) <= intOf(mapOf(artifacts[i-1])["rank"])) {
			return nil, conflict("Creation has invalid original rank artifacts")
		}
	}
	if (row["phase"] == "built" || row["phase"] == "handed_off") &&
		(len(artifacts) != count || !truthy(row["ca_sha256"]) || row["authority_requested"] != true || row["authority_closed"] == true) {
		return nil, conflict("Incomplete or closed creation cannot transfer lifecycle ownership")
	}
	return row, nil
}

func isBool(v any) bool { _, ok := v.(bool); return ok }

func (j groupJournal) load(ctx context.Context, groupID string) (map[string]any, error) {
	path, err := j.path(groupID)
	if err != nil {
		return nil, err
	}
	row, err := j.request(ctx, "GET", path, nil)
	if err != nil {
		return nil, err
	}
	return j.validate(row, groupID)
}

// save commits the next revision of a loaded row (compare-and-set at the Hub).
func (j groupJournal) save(ctx context.Context, row map[string]any) (map[string]any, error) {
	groupID := strOf(row["group_id"])
	if _, err := j.validate(row, groupID); err != nil {
		return nil, err
	}
	path, _ := j.path(groupID)
	result, err := j.request(ctx, "PUT", path, clone(row))
	if err != nil {
		return nil, err
	}
	if result, err = j.validate(result, groupID); err != nil {
		return nil, err
	}
	if intOf(result["revision"]) != intOf(row["revision"])+1 {
		return nil, conflict("Hub did not acknowledge this exact journal revision")
	}
	return result, nil
}

func (j groupJournal) list(ctx context.Context) ([]any, error) {
	path, key := "/api/model-services/groups", "groups"
	if j.creation {
		path, key = "/api/model-services/group-creations", "creations"
	}
	out, err := j.request(ctx, "GET", path, nil)
	if err != nil {
		return nil, err
	}
	rows := []any{}
	for _, r := range listOf(out[key]) {
		row, err := j.validate(mapOf(r), "")
		if err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// topologyFor is the creation's pinned roster: the plan's peers bound to the
// hash of the complete plan.
func topologyFor(plan map[string]any) (*Topology, error) {
	if plan == nil {
		return nil, fmt.Errorf("Declare the exact owner, group, model, launch and peer roster")
	}
	if u, ok := plan["underlay"]; ok {
		if err := underlayAddresses(u, len(listOf(plan["members"]))); err != nil {
			return nil, err
		}
	}
	var members []any
	for _, raw := range listOf(plan["members"]) {
		m := mapOf(raw)
		members = append(members, map[string]any{"rank": m["rank"], "node_id": m["node_id"], "generation": m["generation"],
			"address": m["address"], "port": m["control_port"]})
	}
	return NewTopology(map[string]any{"protocol": number(1), "owner": plan["owner"], "group_id": plan["group_id"],
		"model_sha256": plan["model_sha256"], "launch_sha256": sha256Hex(Compact(plan)), "members": members})
}

// createCreation saves the original creation intent; no authority request
// can follow from it, even on a lost reply.
func (j groupJournal) createCreation(ctx context.Context, plan map[string]any, source string) (map[string]any, error) {
	peers, err := topologyFor(plan)
	if err != nil {
		return nil, err
	}
	row := map[string]any{"protocol": number(1), "owner": j.owner, "group_id": plan["group_id"], "revision": number(0),
		"plan": clone(plan), "topology": peers.Document(), "source_sha256": source, "phase": "intent",
		"authority_requested": false, "ca_sha256": nil, "authority_closed": false, "artifacts": []any{}}
	path, err := j.path(strOf(plan["group_id"]))
	if err != nil {
		return nil, err
	}
	result, err := j.request(ctx, "PUT", path, row)
	if err != nil {
		return nil, err
	}
	if result, err = j.validate(result, strOf(plan["group_id"])); err != nil {
		return nil, err
	}
	row["revision"] = number(1)
	if !same(result, row) {
		return nil, conflict("Hub did not acknowledge this exact creation intent")
	}
	return result, nil
}

// handoff transfers a built creation to the coordinated group lifecycle and
// checks the Hub-derived lifecycle independently.
func (j groupJournal) handoff(ctx context.Context, groupID string) (map[string]any, error) {
	row, err := j.load(ctx, groupID)
	if err != nil {
		return nil, err
	}
	if row["phase"] != "built" && row["phase"] != "handed_off" {
		return nil, conflict("Build all original rank packages before handoff")
	}
	path, _ := j.path(groupID)
	result, err := j.request(ctx, "POST", path+"/handoff", map[string]any{"revision": row["revision"]})
	if err != nil {
		return nil, err
	}
	if !keys(result, "creation", "group") {
		return nil, conflict("Hub returned an invalid handoff acknowledgement")
	}
	creation, err := j.validate(mapOf(result["creation"]), groupID)
	if err != nil {
		return nil, err
	}
	expected := mapOf(clone(row))
	if row["phase"] != "handed_off" {
		expected["revision"] = number(intOf(row["revision"]) + 1)
	}
	expected["phase"] = "handed_off"
	if !same(creation, expected) {
		return nil, conflict("Hub did not transfer this exact built intent")
	}
	group, err := groupJournal{dir: j.dir, owner: j.owner}.validate(mapOf(result["group"]), groupID)
	if err != nil {
		return nil, err
	}
	original := lifecyclePlan(row)
	security := mapOf(group["peer_security"])
	members, pinned := listOf(group["members"]), listOf(original["members"])
	if len(members) != len(pinned) || !same(security["topology"], row["topology"]) || security["ca_sha256"] != row["ca_sha256"] ||
		!same(mapOf(security["network"])["addresses"], plannedAddresses(mapOf(row["plan"]))) {
		return nil, conflict("Lifecycle differs from original creation trust")
	}
	a, b := mapOf(group["inference"]), mapOf(original["inference"])
	if (a == nil) != (b == nil) {
		return nil, conflict("Lifecycle inference differs from original creation")
	}
	for _, k := range []string{"protocol", "binding", "config_revision", "context_length", "parallel"} {
		if b != nil && !same(a[k], b[k]) {
			return nil, conflict("Lifecycle inference differs from original creation")
		}
	}
	for i := range members {
		actual, want := mapOf(members[i]), mapOf(pinned[i])
		if !same(actual["target"], want["target"]) || !same(mapOf(actual["prepare"])["request"], mapOf(want["prepare"])["request"]) ||
			!same(mapOf(actual["start"])["request"], mapOf(want["start"])["request"]) ||
			!same(mapOf(actual["install"])["request"], mapOf(want["install"])["request"]) {
			return nil, conflict("Lifecycle differs from original rank targets or operations")
		}
	}
	// A retried acknowledgement may carry an advanced or stopped group; it is
	// never overwritten with this initial plan.
	return result, nil
}

// plannedAddresses are the network addresses a handed-off lifecycle carries.
func plannedAddresses(plan map[string]any) any {
	if u, ok := plan["underlay"]; ok {
		return u
	}
	if _, ok := plan["network_mode"]; ok {
		members := listOf(clone(plan["members"]))
		out := make([]any, len(members))
		for _, raw := range members {
			m := mapOf(raw)
			out[intOf(m["rank"])] = m["address"]
		}
		return out
	}
	return nil
}

// instanceIdentity is a rank's Fleet instance id.
func instanceIdentity(owner string, target map[string]any) string {
	return sha256Hex([]byte(strings.Join([]string{owner, strOf(target["node_id"]), strOf(target["digest"]), strOf(target["scope"])}, "\x00")))[:32]
}

func operationID(parts ...any) string {
	raw, _ := PyJSON(parts, false, ",", ":")
	return sha256Hex(raw)
}

// lifecyclePlan derives the group lifecycle the Hub must create at handoff,
// with deterministic operation identities (group_creation.lifecycle_plan).
func lifecyclePlan(row map[string]any) map[string]any {
	plan := mapOf(row["plan"])
	owner, groupID := strOf(row["owner"]), strOf(row["group_id"])
	_, underlay := plan["underlay"]
	_, platform := plan["network_mode"]
	install := underlay || platform
	var members []any
	artifacts := listOf(row["artifacts"])
	for i, raw := range listOf(plan["members"]) {
		m := mapOf(raw)
		target := map[string]any{"node_id": m["node_id"], "digest": mapOf(artifacts[i])["digest"],
			"scope": "model-group-" + groupID, "generation": number(intOf(m["generation"]) - 2)}
		base := func(action string, generation int64, id string) map[string]any {
			return map[string]any{"protocol": number(1), "digest": target["digest"], "scope": target["scope"],
				"generation": number(generation), "action": action, "operation_id": id}
		}
		gen := intOf(target["generation"])
		ids := map[string]string{}
		for _, action := range []string{"install", "prepare", "start"} {
			ids[action] = operationID(owner, groupID, row["source_sha256"], number(int64(i)), action)
		}
		start := base("start", gen+1, ids["start"])
		start["start_preparation_id"] = ids["prepare"]
		member := map[string]any{"target": target,
			"prepare": map[string]any{"sent": false, "request": base("prepare_start", gen, ids["prepare"])},
			"start":   map[string]any{"sent": false, "request": start}, "stop": nil}
		if install {
			member["install"] = map[string]any{"staged": false, "sent": false, "request": map[string]any{"protocol": number(1),
				"action": "install", "digest": target["digest"], "scope": target["scope"], "generation": number(0), "operation_id": ids["install"]}}
		}
		members = append(members, member)
	}
	result := map[string]any{"protocol": number(1), "owner": owner, "group_id": groupID, "revision": number(1), "phase": "preparing",
		"members": members, "peer_security": map[string]any{"topology": clone(row["topology"]), "ca_sha256": row["ca_sha256"],
			"ready": false, "closed": false}}
	if underlay {
		mapOf(result["peer_security"])["network"] = map[string]any{"addresses": clone(plan["underlay"]), "endpoints": []any{},
			"ready": false, "closed": false}
	} else if platform {
		mapOf(result["peer_security"])["network"] = map[string]any{"mode": plan["network_mode"], "addresses": plannedAddresses(plan),
			"endpoints": []any{}, "ready": false, "closed": false}
	}
	if intOf(plan["inference_protocol"]) == 1 {
		result["inference"] = inferenceIntent(result, plan["context_length"], plan["parallel"])
	}
	return mapOf(clone(result))
}

var inferenceFlags = []string{"activation_sent", "activated", "drain_sent", "drained"}

// inferenceOriginal is the immutable leader identity of an inference group.
func inferenceOriginal(row map[string]any, contextLength, parallel any) map[string]any {
	topology := mapOf(mapOf(row["peer_security"])["topology"])
	peer := mapOf(listOf(topology["members"])[0])
	var target map[string]any
	for _, m := range listOf(row["members"]) {
		if t := mapOf(mapOf(m)["target"]); t["node_id"] == peer["node_id"] {
			target = t
		}
	}
	instance := instanceIdentity(strOf(row["owner"]), target)
	config := map[string]any{"engine": "sglang", "endpoint": "http://127.0.0.1:30000/v1", "credential_file": "",
		"group": clone(topology), "instance": map[string]any{"instance_id": instance, "generation": peer["generation"]},
		"context_length": contextLength, "parallel": parallel}
	raw, _ := PyJSON(config, true, ", ", ": ")
	return map[string]any{"protocol": number(1), "binding": map[string]any{"node_id": peer["node_id"], "instance_id": instance,
		"generation": peer["generation"], "revision": target["digest"], "component": "backend", "port": "http"},
		"context_length": contextLength, "parallel": parallel, "config_revision": sha256Hex(raw)}
}

func inferenceIntent(row map[string]any, contextLength, parallel any) map[string]any {
	value := inferenceOriginal(row, contextLength, parallel)
	for _, f := range inferenceFlags {
		value[f] = false
	}
	return value
}

// inference returns a group's inference intent after checking its leader
// identity (group_inference.validate's identity part).
func inference(row map[string]any) (map[string]any, error) {
	value := mapOf(row["inference"])
	if value == nil {
		return nil, nil
	}
	expected := inferenceOriginal(row, value["context_length"], value["parallel"])
	for k, v := range expected {
		if !same(value[k], v) {
			return nil, fmt.Errorf("Inference identity differs from the original leader")
		}
	}
	return value, nil
}
