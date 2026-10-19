package modelservices

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// The prepare-all / start-together coordinator of a handed-off group
// (group_coordinator.py, group_security.py, group_install.py,
// group_overlay.py). There are no timers that free possibly owned resources
// and no replay of starts: every claimed RPC is saved in the Hub journal
// before it is sent, aborts fence missing starts, and each advance is one
// bounded pass the caller repeats.

const groupRPCTimeout = 15 * time.Second

func observation(state string, clean bool, stopGeneration any) map[string]any {
	return map[string]any{"state": state, "clean": clean, "stop_generation": stopGeneration}
}

func requestOf(member map[string]any, key string) map[string]any {
	return mapOf(mapOf(member[key])["request"])
}

func sent(member map[string]any, key string) bool { return mapOf(member[key])["sent"] == true }

// settleRequest reconciles an install interrupted by a Runner restart; its id
// derives from the original install so redelivery deduplicates on the node.
func settleRequest(member map[string]any) map[string]any {
	install := requestOf(member, "install")
	return map[string]any{"protocol": number(1), "action": "reconcile", "digest": install["digest"], "scope": install["scope"],
		"generation": number(0), "operation_id": "settle-" + sha256Hex([]byte(strOf(install["operation_id"])))[:40]}
}

// recoverRequest restores a live leader's ingress after a Runner restart
// (no hooks, no new processes).
func recoverRequest(member map[string]any, generation int64) map[string]any {
	target, start := mapOf(member["target"]), requestOf(member, "start")
	return map[string]any{"protocol": number(1), "action": "recover", "digest": target["digest"], "scope": target["scope"],
		"generation": number(generation), "operation_id": "recover-" + sha256Hex([]byte(fmt.Sprintf("%s:%d", start["operation_id"], generation)))[:40]}
}

func emptyInstance(current map[string]any) bool {
	return current == nil || (current["state"] == "stopped" && !truthy(current["resources"]) && !truthy(current["reservations"]))
}

// inspectMember classifies one member from its node snapshot.
func inspectMember(owner string, member, snapshot map[string]any) map[string]any {
	target := mapOf(member["target"])
	digest, scope, base := target["digest"], target["scope"], intOf(target["generation"])
	if intOf(snapshot["protocol"]) != 1 || snapshot["owner"] != owner || snapshot["node_id"] != target["node_id"] {
		return observation("conflict", false, nil)
	}
	identity := instanceIdentity(owner, target)
	instances := mapOf(snapshot["instances"])
	current := mapOf(instances[identity])
	if current != nil && (current["instance_id"] != identity || current["digest"] != digest || current["scope"] != scope) {
		return observation("conflict", false, nil)
	}
	for key, raw := range instances {
		i := mapOf(raw)
		if i["scope"] == scope && key != identity && (i["state"] != "stopped" || truthy(i["resources"]) || truthy(i["reservations"])) {
			return observation("conflict", false, nil)
		}
	}
	operations := mapOf(snapshot["operations"])
	owned := map[string]bool{}
	for _, k := range []string{"install", "prepare", "start", "stop"} {
		if member[k] != nil {
			owned[strOf(requestOf(member, k)["operation_id"])] = true
		}
	}
	if member["install"] != nil {
		owned[strOf(settleRequest(member)["operation_id"])] = true
	}
	if current != nil && truthy(current["generation"]) {
		owned[strOf(recoverRequest(member, intOf(current["generation"]))["operation_id"])] = true
	}
	for key, raw := range operations {
		op := mapOf(raw)
		if mapOf(op["request"])["scope"] == scope && !owned[key] && (op["state"] == "queued" || op["state"] == "running") {
			return observation("conflict", false, nil)
		}
	}
	empty := emptyInstance(current)
	var generation int64
	if current != nil {
		generation = intOf(current["generation"])
	}
	matchesRequest := func(actual, request map[string]any) bool {
		for k, v := range request {
			if !same(actual[k], v) {
				return false
			}
		}
		return true
	}
	if !sent(member, "prepare") {
		if !empty || generation != base {
			return observation("conflict", false, nil)
		}
		install := mapOf(member["install"])
		if install == nil || install["sent"] != true {
			return observation("unsubmitted", true, nil)
		}
		request := mapOf(install["request"])
		op := mapOf(operations[strOf(request["operation_id"])])
		if op == nil {
			o := observation("unknown", false, nil)
			o["missing"] = "install"
			return o
		}
		actual := mapOf(op["request"])
		if !matchesRequest(actual, request) || truthy(actual["if_idle"]) || truthy(actual["data_source"]) || truthy(actual["start_preparation_id"]) {
			return observation("conflict", false, nil)
		}
		switch op["state"] {
		case "queued", "running":
			return observation("pending", false, nil)
		case "failed", "cancelled":
			return observation("failed", true, nil)
		case "unknown":
			o := observation("unknown", false, nil)
			o["settle"] = true
			return o
		case "succeeded":
		default:
			return observation("unknown", false, nil)
		}
		installation := mapOf(mapOf(snapshot["installations"])[strOf(digest)])
		if installation["digest"] != digest || installation["state"] != "installed" {
			return observation("conflict", false, nil)
		}
		return observation("installed", true, nil)
	}
	key := "prepare"
	if member["stop"] != nil {
		key = "stop"
	} else if sent(member, "start") {
		key = "start"
	}
	request := requestOf(member, key)
	op := mapOf(operations[strOf(request["operation_id"])])
	if op == nil {
		// Confirmed absence in this node's durable ledger; only this allows an
		// abort-time fence.
		o := observation("unknown", false, nil)
		o["missing"] = key
		return o
	}
	actual := mapOf(op["request"])
	if !matchesRequest(actual, request) || truthy(actual["if_idle"]) || truthy(actual["data_source"]) ||
		(key != "start" && truthy(actual["start_preparation_id"])) {
		return observation("conflict", false, nil)
	}
	state := op["state"]
	if state == "queued" || state == "running" {
		return observation("pending", false, nil)
	}
	prepared := current != nil && generation == base+1 && current["state"] == "prepared" &&
		current["start_preparation_id"] == requestOf(member, "prepare")["operation_id"] &&
		!truthy(current["resources"]) && truthy(current["reservations"])
	if state == "cancelled" {
		if key == "prepare" && empty && generation == base {
			return observation("failed", true, nil)
		}
		if key == "start" {
			if prepared {
				return observation("failed", false, number(generation))
			}
			if empty && generation == base+2 {
				return observation("released", true, nil)
			}
		}
		return observation("conflict", false, nil)
	}
	if state != "succeeded" && state != "failed" && state != "unknown" {
		return observation("unknown", false, nil)
	}
	uncertain := func() map[string]any {
		if state == "unknown" {
			return observation("unknown", false, nil)
		}
		return observation("conflict", false, nil)
	}
	if key == "stop" {
		if empty && generation == intOf(request["generation"])+1 {
			return observation("released", true, nil)
		}
		return uncertain()
	}
	if prepared {
		if key == "prepare" && state != "failed" {
			return observation("prepared", false, number(generation))
		}
		return observation("failed", false, number(generation))
	}
	if key == "prepare" {
		if empty && generation == base && state == "failed" {
			return observation("failed", true, nil)
		}
		if empty && generation == base+2 {
			return observation("released", true, nil)
		}
		return uncertain()
	}
	if current != nil && generation == base+2 {
		if state == "succeeded" && current["state"] == "ready" && intOf(current["ready_generation"]) == generation {
			return observation("ready", false, number(generation))
		}
		// The recorded start owns only this generation; a normal Fleet stop
		// still runs hooks, the coordinator never force-kills.
		var stop any
		if !empty {
			stop = number(generation)
		}
		o := observation("failed", empty, stop)
		if !empty && (current["state"] == "unknown" || current["state"] == "recovered") && intOf(current["ready_generation"]) == generation {
			o["recover"] = generation
		}
		return o
	}
	if empty && generation == base+3 {
		return observation("released", true, nil)
	}
	return observation("conflict", false, nil)
}

type groupCoordinator struct {
	journal  groupJournal
	nodes    nodes
	packages *packageStore
}

func withTimeout(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, groupRPCTimeout)
}

// each runs fn for every index concurrently.
func each(count int, fn func(i int)) {
	var wg sync.WaitGroup
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			fn(i)
		}(i)
	}
	wg.Wait()
}

func members(row map[string]any) []map[string]any {
	var out []map[string]any
	for _, m := range listOf(row["members"]) {
		out = append(out, mapOf(m))
	}
	return out
}

// stop persists stop intent; a racing sender's claimed RPC stays uncertain.
func (c groupCoordinator) stop(ctx context.Context, groupID string) (map[string]any, error) {
	row, err := c.journal.load(ctx, groupID)
	if err != nil {
		return nil, err
	}
	switch row["phase"] {
	case "aborting", "stopped", "forgotten":
		return row, nil
	}
	row["phase"] = "aborting"
	return c.journal.save(ctx, row)
}

func (c groupCoordinator) observe(ctx context.Context, owner string, member map[string]any) map[string]any {
	ctx, cancel := withTimeout(ctx)
	defer cancel()
	state, err := c.nodes.status(ctx, strOf(mapOf(member["target"])["node_id"]))
	if err != nil {
		return observation("unknown", false, nil)
	}
	return inspectMember(owner, member, state)
}

// send delivers a claimed request; a timeout or error reply may still follow
// a durable submission, so only a later snapshot is evidence.
func (c groupCoordinator) send(ctx context.Context, node string, request map[string]any, fence bool) {
	ctx, cancel := withTimeout(ctx)
	defer cancel()
	if fence {
		_ = c.nodes.fenceStart(ctx, node, request)
		return
	}
	extra := map[string]any{}
	if v, ok := request["start_preparation_id"]; ok {
		extra["start_preparation_id"] = v
	}
	_, _ = c.nodes.submit(ctx, node, strOf(request["action"]), strOf(request["digest"]), strOf(request["scope"]),
		intOf(request["generation"]), strOf(request["operation_id"]), extra)
}

func (c groupCoordinator) saveOrLoad(ctx context.Context, row map[string]any) (map[string]any, error) {
	result, err := c.journal.save(ctx, row)
	var stale *GroupConflict
	if errors.As(err, &stale) {
		return c.journal.load(ctx, strOf(row["group_id"]))
	}
	return result, err
}

// inferenceBarrier advances publication or drain one step; it returns true
// when the row must be saved before any peer teardown.
func (c groupCoordinator) inferenceBarrier(ctx context.Context, row map[string]any, observed []map[string]any, recover []any) (bool, error) {
	value, err := inference(row)
	if err != nil || value == nil {
		return false, err
	}
	if row["phase"] == "aborting" && value["drained"] != true {
		switch {
		case value["activation_sent"] != true:
			value["drained"] = true // no activation RPC was ever authorized
		case value["drain_sent"] != true:
			value["drain_sent"] = true
		default:
			binding := mapOf(value["binding"])
			for i, m := range members(row) {
				if mapOf(m["target"])["node_id"] != binding["node_id"] {
					continue
				}
				if observed[i]["clean"] == true {
					value["drained"] = true // the original generation owns no resources
					break
				}
				if recover[i] != nil {
					c.send(ctx, strOf(binding["node_id"]), recoverRequest(m, recover[i].(int64)), false)
				}
				cctx, cancel := withTimeout(ctx)
				reply, err := c.nodes.groupInference(cctx, binding, "drain", map[string]any{})
				cancel()
				if err == nil {
					value["drained"] = reply["safe_to_stop"] == true && reply["status"] == "succeeded"
				}
				break
			}
		}
		return true, nil
	}
	if row["phase"] == "ready" && value["activated"] != true {
		if value["activation_sent"] != true {
			value["activation_sent"] = true
		} else {
			cctx, cancel := withTimeout(ctx)
			reply, err := c.nodes.groupInference(cctx, mapOf(value["binding"]), "resume",
				map[string]any{"config_revision": value["config_revision"]})
			cancel()
			if err == nil {
				value["activated"] = reply["config_revision"] == value["config_revision"] && reply["accepting"] == true
			}
		}
		return true, nil
	}
	return false, nil
}

// advanceNetwork moves the private network one enrollment barrier; keys
// collected in a call are saved before they are pinned.
func (c groupCoordinator) advanceNetwork(ctx context.Context, row map[string]any, peers *Topology) bool {
	network := mapOf(mapOf(row["peer_security"])["network"])
	if network["closed"] == true {
		return false
	}
	closing := row["phase"] == "aborting"
	if network["mode"] == platformPrivate {
		// The provider isolates the network; barriers keep their order.
		if closing {
			network["closed"] = true
			return false
		}
		network["ready"] = true
		return true
	}
	if network["ready"] == true && !closing {
		return true
	}
	roster := listOf(network["endpoints"])
	action := "prepare"
	if closing {
		action = "close"
	} else if len(roster) > 0 {
		action = "pin"
	}
	addresses := listOf(network["addresses"])
	security := mapOf(row["peer_security"])
	peerList := peers.Members()
	results := make([]map[string]any, len(peerList))
	failed := make([]bool, len(peerList))
	each(len(peerList), func(i int) {
		peer := peerList[i]
		node, rank := strOf(peer["node_id"]), int(intOf(peer["rank"]))
		args := map[string]any{}
		switch action {
		case "prepare":
			args = map[string]any{"manifest": map[string]any{"protocol": number(1), "rank": number(int64(rank)),
				"topology": peers.Document(), "ca_sha256": security["ca_sha256"]}, "address": addresses[rank]}
		case "pin":
			args["endpoints"] = roster
		}
		cctx, cancel := withTimeout(ctx)
		defer cancel()
		reply, err := c.nodes.groupOverlay(cctx, node, action, peers, args)
		want := ""
		if closing {
			want = "closed"
		} else if len(roster) > 0 {
			want = "pinned"
		}
		if err == nil {
			err = checkOverlayReply(reply, node, peers, want)
		}
		if err == nil && !closing {
			endpoint := mapOf(reply["endpoint"])
			if reply["state"] == "closed" || endpoint["address"] != addresses[rank] {
				err = fmt.Errorf("Original overlay is closed or changed")
			} else if len(roster) > 0 && (!same(reply["endpoints"], roster) || !same(endpoint, roster[rank])) {
				err = fmt.Errorf("Node did not pin the original complete roster")
			}
		}
		results[i], failed[i] = reply, err != nil
	})
	for _, f := range failed {
		if f {
			return false
		}
	}
	switch {
	case closing:
		network["closed"] = true
	case len(roster) == 0:
		var endpoints []any
		for _, r := range results {
			endpoints = append(endpoints, r["endpoint"])
		}
		if validateRoster(endpoints, len(results)) != nil {
			return false
		}
		network["endpoints"] = endpoints
	default:
		network["ready"] = true
	}
	return network["ready"] == true && network["closed"] != true
}

// enrollmentClaim checks a node's enrollment against the original prepared
// member and returns the certificate claim for it.
func enrollmentClaim(row map[string]any, peers *Topology, member, enrollment map[string]any) (map[string]any, error) {
	target := mapOf(member["target"])
	var peer map[string]any
	for _, p := range peers.Members() {
		if p["node_id"] == target["node_id"] {
			peer = p
		}
	}
	identity := instanceIdentity(strOf(row["owner"]), target)
	rank := int(intOf(peer["rank"]))
	expected := map[string]any{"protocol": number(1), "instance_id": identity, "revision": target["digest"],
		"generation": peer["generation"], "topology_sha256": peers.Fingerprint, "dns_name": peers.CertificateName(rank)}
	csr, _ := enrollment["csr_pem"].(string)
	for k, v := range expected {
		if !same(enrollment[k], v) {
			return nil, fmt.Errorf("Enrollment differs from the original prepared group member")
		}
	}
	if !isBool(enrollment["certificate_installed"]) || len(csr) == 0 || len(csr) > 16384 {
		return nil, fmt.Errorf("Enrollment differs from the original prepared group member")
	}
	return map[string]any{"rank": peer["rank"], "node_id": target["node_id"], "instance_id": identity, "revision": target["digest"],
		"scope": target["scope"], "generation": peer["generation"], "preparation_id": requestOf(member, "prepare")["operation_id"],
		"csr_pem": csr}, nil
}

// installPeer enrolls a prepared member, has rank zero issue its certificate
// and installs it; every step is idempotent on its original binding.
func (c groupCoordinator) installPeer(ctx context.Context, row map[string]any, peers *Topology, member map[string]any) error {
	ctx, cancel := withTimeout(ctx)
	defer cancel()
	target := mapOf(member["target"])
	binding := map[string]any{"node_id": target["node_id"], "instance_id": instanceIdentity(strOf(row["owner"]), target),
		"revision": target["digest"], "generation": number(intOf(target["generation"]) + 1)}
	enrolled, err := c.nodes.groupPeer(ctx, binding, "", "")
	if err != nil {
		return err
	}
	claim, err := enrollmentClaim(row, peers, member, enrolled)
	if err != nil {
		return err
	}
	issued, err := c.nodes.groupAuthority(ctx, strOf(peers.Member(0)["node_id"]), "issue", peers, claim)
	if err != nil {
		return err
	}
	if issued["ca_sha256"] != mapOf(row["peer_security"])["ca_sha256"] {
		return fmt.Errorf("Issuer root differs from the pinned group authority")
	}
	installed, err := c.nodes.groupPeer(ctx, binding, strOf(issued["certificate_pem"]), strOf(issued["ca_pem"]))
	if err != nil {
		return err
	}
	again, err := enrollmentClaim(row, peers, member, installed)
	if err != nil || !same(again, claim) || installed["certificate_installed"] != true {
		return fmt.Errorf("Node did not confirm installation for the original key")
	}
	return nil
}

// advance runs one bounded observation-and-claim pass of a handed-off group.
func (c groupCoordinator) advance(ctx context.Context, groupID string) (map[string]any, error) {
	row, err := c.journal.load(ctx, groupID)
	if err != nil {
		return nil, err
	}
	if row["phase"] == "stopped" || row["phase"] == "forgotten" {
		return row, nil
	}
	wasAborting := row["phase"] == "aborting"
	security := mapOf(row["peer_security"])
	var peers *Topology
	if security != nil {
		if peers, err = NewTopology(security["topology"]); err != nil {
			return nil, err
		}
	}
	ms := members(row)
	installs := len(ms) > 0 && ms[0]["install"] != nil
	wasStaged := make([]bool, len(ms))
	for i, m := range ms {
		wasStaged[i] = mapOf(m["install"])["staged"] == true
	}
	observed := make([]map[string]any, len(ms))
	each(len(ms), func(i int) { observed[i] = c.observe(ctx, strOf(row["owner"]), ms[i]) })
	// Transient, never persisted: the durable observation schema is strict.
	settle, recover := make([]bool, len(ms)), make([]any, len(ms))
	states := map[string]bool{}
	for i, o := range observed {
		settle[i] = o["settle"] == true
		recover[i] = o["recover"]
		delete(o, "settle")
		delete(o, "recover")
		ms[i]["observation"] = o
		states[strOf(o["state"])] = true
	}
	only := func(state string) bool { return len(states) == 1 && states[state] }
	if row["phase"] != "aborting" && (states["failed"] || states["released"] || states["conflict"]) {
		row["phase"] = "aborting"
	}
	if row["inference"] != nil {
		// Withdraw publication before drain or any peer side effects.
		if row["phase"] == "aborting" && !wasAborting {
			return c.saveOrLoad(ctx, row)
		}
		if row["phase"] == "ready" && !only("ready") {
			row["phase"] = "committing"
			return c.saveOrLoad(ctx, row)
		}
		save, err := c.inferenceBarrier(ctx, row, observed, recover)
		if err != nil {
			return nil, err
		}
		if save {
			return c.saveOrLoad(ctx, row)
		}
	}
	if installs && row["phase"] == "preparing" {
		var staging []int
		for i, m := range ms {
			if mapOf(m["install"])["staged"] != true && observed[i]["state"] == "unsubmitted" {
				staging = append(staging, i)
			}
		}
		if len(staging) > 0 && c.packages == nil {
			return nil, fmt.Errorf("Restore the original group package store before continuing installation")
		}
		each(len(staging), func(k int) {
			m := ms[staging[k]]
			target := mapOf(m["target"])
			payload, err := c.packages.read("artifact", strOf(target["digest"]))
			if err != nil {
				return
			}
			cctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
			defer cancel()
			if c.nodes.stageExact(cctx, strOf(target["node_id"]), payload, strOf(target["digest"])) == nil {
				mapOf(m["install"])["staged"] = true
			}
		})
	}
	network := mapOf(security["network"])
	networkReady, wasNetworkReady := true, network == nil || network["ready"] == true
	if network != nil {
		networkReady = c.advanceNetwork(ctx, row, peers)
	}
	credentialsReady := true
	if peers != nil && (row["phase"] == "preparing" || row["phase"] == "committing") {
		// Credentials only after every rank is reserved; started or unknown
		// members are never adopted as a new attempt.
		var pending []map[string]any
		for i, m := range ms {
			if observed[i]["state"] == "prepared" {
				pending = append(pending, m)
			}
		}
		credentialsReady = security["ready"] == true || only("prepared")
		if credentialsReady && len(pending) > 0 {
			errs := make([]error, len(pending))
			each(len(pending), func(i int) { errs[i] = c.installPeer(ctx, row, peers, pending[i]) })
			for _, e := range errs {
				if e != nil {
					credentialsReady = false
				}
			}
		}
		if credentialsReady && only("prepared") {
			security["ready"] = true
		}
	}
	if peers != nil && row["phase"] == "aborting" && security["closed"] != true {
		cctx, cancel := withTimeout(ctx)
		closed, err := c.nodes.groupAuthority(cctx, strOf(peers.Member(0)["node_id"]), "close", peers, nil)
		cancel()
		if err == nil {
			security["closed"] = closed["state"] == "closed"
		}
	}
	if row["phase"] == "preparing" && only("prepared") && credentialsReady && networkReady {
		// Durable barrier: starts happen on a later observation only.
		row["phase"] = "committing"
		return c.saveOrLoad(ctx, row)
	}
	if row["phase"] == "committing" || row["phase"] == "ready" {
		row["phase"] = "committing"
		if only("ready") {
			row["phase"] = "ready"
		}
	}
	allClean := true
	for _, o := range observed {
		if o["clean"] != true {
			allClean = false
		}
	}
	if wasAborting && row["phase"] == "aborting" && allClean && (peers == nil || security["closed"] == true) &&
		(network == nil || network["closed"] == true) {
		row["phase"] = "stopped"
	}
	type delivery struct {
		node    string
		request map[string]any
		fence   bool
	}
	var sends []delivery
	for i, m := range ms {
		node := strOf(mapOf(m["target"])["node_id"])
		result := observed[i]
		var action map[string]any
		missing, _ := result["missing"].(string)
		switch {
		case settle[i]:
			sends = append(sends, delivery{node, settleRequest(m), false})
		case row["phase"] == "aborting" && missing != "":
			// Fencing is monotonic and redelivers the ORIGINAL request; a
			// missing stop is safe to redeliver with its operation id.
			sends = append(sends, delivery{node, requestOf(m, missing), missing != "stop"})
		case installs && row["phase"] == "preparing" && missing == "install":
			sends = append(sends, delivery{node, requestOf(m, "install"), false})
		case installs && row["phase"] == "preparing" && result["state"] == "unsubmitted" && wasStaged[i]:
			action = mapOf(m["install"])
		case row["phase"] == "preparing" && networkReady && wasNetworkReady &&
			((!installs && result["state"] == "unsubmitted") ||
				(installs && result["state"] == "installed" && subset(states, "installed", "prepared"))):
			action = mapOf(m["prepare"])
		case row["phase"] == "committing" && result["state"] == "prepared" && !states["unknown"] && credentialsReady:
			action = mapOf(m["start"])
		case row["phase"] == "aborting" && result["stop_generation"] != nil && m["stop"] == nil:
			target := mapOf(m["target"])
			action = map[string]any{"sent": false, "request": map[string]any{"protocol": number(1), "action": "stop",
				"digest": target["digest"], "scope": target["scope"], "generation": result["stop_generation"], "operation_id": newID()}}
			m["stop"] = action
		}
		if action != nil && action["sent"] != true {
			action["sent"] = true
			sends = append(sends, delivery{node, mapOf(action["request"]), false})
		}
	}
	saved, err := c.journal.save(ctx, row)
	var stale *GroupConflict
	if errors.As(err, &stale) {
		// Another worker claimed these RPCs; never send from a stale view.
		return c.journal.load(ctx, groupID)
	}
	if err != nil {
		return nil, err
	}
	each(len(sends), func(i int) { c.send(ctx, sends[i].node, sends[i].request, sends[i].fence) })
	return saved, nil
}

func subset(states map[string]bool, allowed ...string) bool {
	ok := map[string]bool{}
	for _, a := range allowed {
		ok[a] = true
	}
	for s := range states {
		if !ok[s] {
			return false
		}
	}
	return true
}

// creationCoordinator moves a creation from intent to built artifacts.
type creationCoordinator struct {
	journal groupJournal
	nodes   nodes
	builder func(ctx context.Context, row map[string]any, rank int) (string, error)
}

func (c creationCoordinator) stop(ctx context.Context, groupID string) (map[string]any, error) {
	row, err := c.journal.load(ctx, groupID)
	if err != nil {
		return nil, err
	}
	if row["phase"] == "handed_off" {
		group := groupCoordinator{journal: groupJournal{dir: c.journal.dir, owner: c.journal.owner}, nodes: c.nodes}
		if _, err := group.stop(ctx, groupID); err != nil {
			return nil, err
		}
		return row, nil
	}
	if row["phase"] != "aborting" && row["phase"] != "stopped" {
		row["phase"] = "aborting"
		return c.journal.save(ctx, row)
	}
	return row, nil
}

func checkAuthority(row map[string]any, peers *Topology, result map[string]any, state string) error {
	expected := map[string]any{"protocol": number(1), "owner": row["owner"], "node_id": peers.Member(0)["node_id"],
		"group_id": row["group_id"], "topology_sha256": peers.Fingerprint, "state": state}
	for k, v := range expected {
		if !same(result[k], v) {
			return fmt.Errorf("Node returned another creation authority")
		}
	}
	return nil
}

// advance runs one durable creation step.
func (c creationCoordinator) advance(ctx context.Context, groupID string) (map[string]any, error) {
	row, err := c.journal.load(ctx, groupID)
	if err != nil {
		return nil, err
	}
	switch row["phase"] {
	case "built", "handed_off", "stopped":
		return row, nil
	}
	peers, err := NewTopology(row["topology"])
	if err != nil {
		return nil, err
	}
	node := strOf(peers.Member(0)["node_id"])
	if row["phase"] == "intent" {
		// Return after the durable claim; no RPC precedes its acknowledgement.
		row["phase"], row["authority_requested"] = "building", true
		return c.journal.save(ctx, row)
	}
	if row["phase"] == "aborting" {
		if row["authority_requested"] == true && row["authority_closed"] != true {
			cctx, cancel := withTimeout(ctx)
			result, err := c.nodes.groupAuthority(cctx, node, "close", peers, nil)
			cancel()
			if err != nil {
				return nil, err
			}
			if err := checkAuthority(row, peers, result, "closed"); err != nil {
				return nil, err
			}
			row["authority_closed"] = true
		}
		row["phase"] = "stopped"
		return c.journal.save(ctx, row)
	}
	if !truthy(row["ca_sha256"]) {
		// Prepare is idempotent on this topology: a lost reply retries the
		// same authority, never a new CA.
		cctx, cancel := withTimeout(ctx)
		result, err := c.nodes.groupAuthority(cctx, node, "prepare", peers, nil)
		cancel()
		if err != nil {
			return nil, err
		}
		if err := checkAuthority(row, peers, result, "open"); err != nil {
			return nil, err
		}
		digest, err := caDigest(result["ca_pem"])
		if err != nil {
			return nil, err
		}
		if result["ca_sha256"] != digest {
			return nil, fmt.Errorf("Authority pin does not match its public certificate")
		}
		row["ca_sha256"] = digest
		return c.journal.save(ctx, row)
	}
	if c.builder == nil {
		return row, nil
	}
	known := map[int64]bool{}
	for _, a := range listOf(row["artifacts"]) {
		known[intOf(mapOf(a)["rank"])] = true
	}
	for _, peer := range peers.Members() {
		rank := intOf(peer["rank"])
		if known[rank] {
			continue
		}
		digest, err := c.builder(ctx, mapOf(clone(row)), int(rank))
		if err != nil {
			return nil, err
		}
		if !sha256RE.MatchString(digest) {
			return nil, fmt.Errorf("Builder did not return an immutable artifact digest")
		}
		artifacts := append(listOf(row["artifacts"]), map[string]any{"rank": number(rank), "digest": digest})
		sortByRank(artifacts)
		row["artifacts"] = artifacts
		if len(artifacts) == len(peers.Members()) {
			row["phase"] = "built"
		}
		return c.journal.save(ctx, row)
	}
	return nil, conflict("Creation has no remaining artifact step")
}

func sortByRank(list []any) {
	for i := 1; i < len(list); i++ {
		for j := i; j > 0 && intOf(mapOf(list[j])["rank"]) < intOf(mapOf(list[j-1])["rank"]); j-- {
			list[j], list[j-1] = list[j-1], list[j]
		}
	}
}
