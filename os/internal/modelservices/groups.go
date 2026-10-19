package modelservices

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Owner-facing creation and continuation of model groups
// (pantheonos/models/group_management.py, group_package.py).

const (
	maxGroupSource   = 8 << 20
	maxGroupArtifact = 32 << 20
)

// groupSourceFiles are frozen with a creation: the rank runtime plus its
// compiler's inputs, read from the connector source.
var groupSourceFiles = append(append([]string{}, groupRuntimeFiles...), "group_packager.py")

// packageStore keeps the frozen group source and built rank packages,
// content-addressed and never replaced, in the controller's state.
type packageStore struct{ root string }

func newPackageStore(root string) (*packageStore, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0o077 != 0 {
		return nil, fmt.Errorf("Use a private durable group package directory")
	}
	return &packageStore{root: root}, nil
}

func (s *packageStore) path(kind, sha string) (string, error) {
	if (kind != "source" && kind != "artifact") || !sha256RE.MatchString(sha) {
		return "", fmt.Errorf("Use an exact original source or artifact digest")
	}
	return filepath.Join(s.root, kind+"-"+sha), nil
}

func (s *packageStore) read(kind, sha string) ([]byte, error) {
	path, err := s.path(kind, sha)
	if err != nil {
		return nil, err
	}
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("Original group package input is missing; restore the pinned bytes before resuming")
	}
	limit := int64(maxGroupArtifact)
	if kind == "source" {
		limit = maxGroupSource
	}
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > limit {
		return nil, fmt.Errorf("Group package input must be a bounded regular file")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if sha256Hex(data) != sha {
		return nil, fmt.Errorf("Original group package input is corrupt; refusing replacement")
	}
	return data, nil
}

// put publishes bytes exclusively under their digest.
func (s *packageStore) put(kind string, data []byte) (string, error) {
	limit := maxGroupArtifact
	if kind == "source" {
		limit = maxGroupSource
	}
	if len(data) == 0 || len(data) > limit {
		return "", fmt.Errorf("Group package exceeds its size bound")
	}
	sha := sha256Hex(data)
	target, err := s.path(kind, sha)
	if err != nil {
		return "", err
	}
	if _, err := os.Lstat(target); err == nil {
		_, err = s.read(kind, sha)
		return sha, err
	}
	f, err := os.CreateTemp(s.root, ".writing-")
	if err != nil {
		return "", err
	}
	defer os.Remove(f.Name())
	if _, err := f.Write(data); err != nil {
		f.Close()
		return "", err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return "", err
	}
	f.Close()
	if err := os.Chmod(f.Name(), 0o400); err != nil {
		return "", err
	}
	// Exclusive publication: concurrent builders never overwrite
	// acknowledged bytes.
	if err := os.Link(f.Name(), target); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return "", err
		}
		if _, err := s.read(kind, sha); err != nil {
			return "", err
		}
	}
	if dir, err := os.Open(s.root); err == nil {
		_ = dir.Sync()
		dir.Close()
	}
	return sha, nil
}

type groupSource struct {
	Files  map[string]string
	Recipe map[string]any
	Model  map[string]any
}

// capture freezes the connector's group source, the pinned SGLang recipe
// and the model receipt before the creation intent is saved.
func (s *packageStore) capture(connector *Connector, record map[string]any, process bool) (string, error) {
	model, err := modelDescriptor(record)
	if err != nil {
		return "", err
	}
	var recipe map[string]any
	for _, r := range connector.Recipes() {
		if r["id"] == containerRecipe {
			if recipe != nil {
				recipe = nil
				break
			}
			recipe = mapOf(clone(r))
		}
	}
	if recipe == nil {
		return "", fmt.Errorf("Missing unique pinned group engine recipe")
	}
	if process {
		delete(recipe, "image")
		recipe["id"], recipe["runtime"] = processRecipe, "process"
	}
	files := map[string]any{}
	for _, name := range groupSourceFiles {
		f, ok := connector.Tree[name]
		if g, in := connector.group[name]; in {
			f, ok = g, true
		}
		if !ok || len(f.Data) == 0 || len(f.Data) > 2<<20 {
			return "", fmt.Errorf("Group package input %s must be a bounded regular file", name)
		}
		files[name] = string(f.Data)
	}
	return s.put("source", Compact(map[string]any{"protocol": number(1), "files": files, "recipe": recipe, "model": model}))
}

func (s *packageStore) source(sha string) (*groupSource, error) {
	raw, err := s.read("source", sha)
	if err != nil {
		return nil, err
	}
	var doc map[string]any
	if err := decode(raw, &doc); err != nil || !keys(doc, "protocol", "files", "recipe", "model") || !intIn(doc["protocol"], 1, 1) {
		return nil, fmt.Errorf("Original group source has an unsupported format")
	}
	out := &groupSource{Files: map[string]string{}, Recipe: mapOf(doc["recipe"]), Model: mapOf(doc["model"])}
	for name, v := range mapOf(doc["files"]) {
		text, ok := v.(string)
		if !ok || text == "" {
			return nil, fmt.Errorf("Original group source has an unsupported format")
		}
		out.Files[name] = text
	}
	for _, name := range groupRuntimeFiles {
		if _, ok := out.Files[name]; !ok {
			return nil, fmt.Errorf("Original group source has an unsupported format")
		}
	}
	return out, nil
}

// validatePlan rejects impossible memory/topology choices before the
// immutable intent is saved.
func (s *packageStore) validatePlan(plan map[string]any, sha string) error {
	src, err := s.source(sha)
	if err != nil {
		return err
	}
	v, err := validateGroupPlan(plan, src.Model)
	if err == nil {
		err = v.checkRanks(src.Model)
	}
	if err != nil {
		return fmt.Errorf("Group configuration cannot be deployed: %s", err)
	}
	return nil
}

// build compiles one original rank package; it never installs or starts.
func (s *packageStore) build(journal groupJournal) func(context.Context, map[string]any, int) (string, error) {
	return func(_ context.Context, row map[string]any, rank int) (string, error) {
		if _, err := journal.validateCreation(row, ""); err != nil {
			return "", err
		}
		if row["phase"] != "building" || row["authority_requested"] != true || !truthy(row["ca_sha256"]) || row["authority_closed"] == true {
			return "", fmt.Errorf("Build only after the original authority is pinned and before cancellation")
		}
		src, err := s.source(strOf(row["source_sha256"]))
		if err != nil {
			return "", err
		}
		request := map[string]any{"plan": row["plan"], "topology": row["topology"], "ca_sha256": row["ca_sha256"],
			"source_sha256": row["source_sha256"], "rank": number(int64(rank))}
		generated, err := compileRank(request, src.Recipe, src.Model)
		if err != nil {
			return "", fmt.Errorf("Rank package compilation failed: %s", err)
		}
		payload, _, err := packageRank(src.Files, generated)
		if err != nil {
			return "", err
		}
		return s.put("artifact", payload)
	}
}

func (m *Manager) packages() (*packageStore, error) {
	if m.groupRoot == "" {
		return nil, fmt.Errorf("Model groups need durable controller state")
	}
	return newPackageStore(filepath.Join(m.groupRoot, m.nodes.fleet))
}

func (m *Manager) journals() (creations, groups groupJournal) {
	return groupJournal{dir: m.dir, owner: m.nodes.fleet, creation: true}, groupJournal{dir: m.dir, owner: m.nodes.fleet}
}

func notFound(err error) bool { return errors.Is(err, errNoGroup) }

// Groups inspects durable model groups or finishes an explicit stop; it
// never starts anything on view.
func (m *Manager) Groups(ctx context.Context, action, groupID string) (map[string]any, error) {
	_, journal := m.journals()
	coordinator := groupCoordinator{journal: journal, nodes: m.nodes}
	switch action {
	case "list":
		rows, err := journal.list(ctx)
		return map[string]any{"groups": rows}, err
	case "inspect":
		return m.groupLoad(ctx, journal, groupID)
	case "stop":
		if _, err := coordinator.stop(ctx, groupID); err != nil {
			return nil, groupError(err)
		}
	case "continue_stop":
	default:
		return nil, fmt.Errorf("Unsupported model group action")
	}
	row, err := m.groupLoad(ctx, journal, groupID)
	if err != nil {
		return nil, err
	}
	if row["phase"] != "aborting" && row["phase"] != "stopped" {
		return nil, fmt.Errorf("Record an explicit group stop before continuing cleanup")
	}
	return coordinator.advance(ctx, groupID)
}

func (m *Manager) groupLoad(ctx context.Context, journal groupJournal, groupID string) (map[string]any, error) {
	row, err := journal.load(ctx, groupID)
	return row, groupError(err)
}

func groupError(err error) error {
	if notFound(err) {
		return fmt.Errorf("Model group not found")
	}
	return err
}

// GroupDeployments creates or continues an original model group.
func (m *Manager) GroupDeployments(ctx context.Context, action, groupID string, config any) (map[string]any, error) {
	out, err := m.groupDeployments(ctx, action, groupID, config)
	return out, groupError(err)
}

func (m *Manager) groupDeployments(ctx context.Context, action, groupID string, config any) (map[string]any, error) {
	switch action {
	case "list", "inspect", "create", "advance", "stop", "continue_stop", "forget":
	default:
		return nil, fmt.Errorf("Unsupported model group deployment action")
	}
	creations, groups := m.journals()
	if action == "list" {
		rows, err := creations.list(ctx)
		return map[string]any{"creations": rows}, err
	}
	if action == "inspect" {
		row, err := creations.load(ctx, groupID)
		if err != nil {
			return nil, err
		}
		var group any
		if row["phase"] == "handed_off" {
			if group, err = groups.load(ctx, groupID); err != nil {
				return nil, err
			}
		}
		return map[string]any{"creation": row, "group": group}, nil
	}
	if _, err := creations.path(groupID); err != nil {
		return nil, err
	}
	unlock, err := m.lock("group-" + sha256Hex([]byte(groupID))[:58])
	if err != nil {
		return nil, err
	}
	defer unlock()
	if action == "create" {
		row, err := m.createGroup(ctx, creations, groupID, mapOf(clone(config)))
		if err != nil {
			return nil, err
		}
		return map[string]any{"creation": row, "group": nil}, nil
	}
	row, err := creations.load(ctx, groupID)
	if err != nil {
		return nil, err
	}
	var store *packageStore
	if action == "advance" {
		if store, err = m.packages(); err != nil {
			return nil, err
		}
	}
	creation := creationCoordinator{journal: creations, nodes: m.nodes}
	if store != nil {
		creation.builder = store.build(creations)
	}
	if action == "forget" {
		if row["phase"] != "handed_off" {
			return nil, fmt.Errorf("Only a handed-off group can be forgotten; stop its creation instead")
		}
		group, err := groups.load(ctx, groupID)
		if err != nil {
			return nil, err
		}
		group, err = m.forgetGroup(ctx, groups, group, config)
		if err != nil {
			return nil, err
		}
		return map[string]any{"creation": row, "group": group}, nil
	}
	if action == "stop" {
		if row, err = creation.stop(ctx, groupID); err != nil {
			return nil, err
		}
	}
	if row["phase"] == "handed_off" {
		group, err := groups.load(ctx, groupID)
		if err != nil {
			return nil, err
		}
		if action == "continue_stop" && group["phase"] != "aborting" && group["phase"] != "stopped" {
			return nil, fmt.Errorf("Stop the group before continuing cleanup")
		}
		group, err = groupCoordinator{journal: groups, nodes: m.nodes, packages: store}.advance(ctx, groupID)
		if err != nil {
			return nil, err
		}
		return map[string]any{"creation": row, "group": group}, nil
	}
	if action == "continue_stop" && row["phase"] != "aborting" && row["phase"] != "stopped" {
		return nil, fmt.Errorf("Stop the deployment before continuing cleanup")
	}
	if row["phase"] == "built" && action == "advance" {
		return creations.handoff(ctx, groupID)
	}
	row, err = creation.advance(ctx, groupID)
	if err != nil {
		return nil, err
	}
	return map[string]any{"creation": row, "group": nil}, nil
}

const platformCapability = "model-group-platform-network"

var shaArgRE = regexp.MustCompile(`^[a-f0-9]{64}$`)

// createGroup validates the members, measures their GPUs and saves the
// original creation intent with its frozen source.
func (m *Manager) createGroup(ctx context.Context, journal groupJournal, groupID string, config map[string]any) (map[string]any, error) {
	platform := config["network_mode"] == platformPrivate
	want := []string{"model_sha256", "context_length", "parallel", "members"}
	if platform {
		want = append(want, "network_mode")
	}
	configMembers := listOf(config["members"])
	if !groupIDRE.MatchString(groupID) || !keys(config, want...) || len(configMembers) < 2 || len(configMembers) > 8 ||
		!matches(shaArgRE, config["model_sha256"]) || !intIn(config["context_length"], 512, 1048576) || !intIn(config["parallel"], 1, 16) {
		return nil, fmt.Errorf("Choose an original group name, model snapshot, context, parallelism and 2–8 nodes")
	}
	// Existing intent is never overwritten, even if its response was lost.
	if _, err := journal.load(ctx, groupID); err == nil {
		return nil, fmt.Errorf("This group already has a saved deployment; refresh and continue it")
	} else if !notFound(err) {
		return nil, err
	}
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	byID := map[any]Row{}
	for _, r := range rows {
		byID[r["deployment_id"]] = r
	}
	type pick struct {
		row    Row
		member map[string]any
	}
	var selected []pick
	var underlay []any
	seen := map[any]bool{}
	for _, raw := range configMembers {
		member := mapOf(raw)
		memberKeys := []string{"deployment_id", "underlay", "resources"}
		if platform {
			memberKeys = []string{"deployment_id", "resources"}
		}
		if !keys(member, memberKeys...) || !isString(member["deployment_id"]) {
			return nil, fmt.Errorf("Select a snapshot service, private UDP endpoint and resource budgets for every node")
		}
		row := byID[member["deployment_id"]]
		if row == nil || row["mode"] != "managed" || row["engine"] != "sglang" || (row["state"] != "draft" && row["state"] != "ready") ||
			!truthy(row["binding"]) || seen[row["node_id"]] {
			return nil, fmt.Errorf("Choose distinct nodes with configured owned SGLang snapshot connectors")
		}
		seen[row["node_id"]] = true
		if !platform {
			underlay = append(underlay, member["underlay"])
		}
		selected = append(selected, pick{row, member})
	}
	if !platform {
		if err := underlayAddresses(underlay, len(selected)); err != nil {
			return nil, err
		}
	}
	scopes := map[string]bool{}
	devices := map[string]bool{}
	var members []any
	var records []map[string]any
	for rank, p := range selected {
		nodeID := strOf(p.row["node_id"])
		node, err := m.node(ctx, nodeID, true)
		if err != nil {
			return nil, err
		}
		capability, rt := caps(node), runtimes(node)
		capList := map[any]bool{}
		for _, c := range listOf(capability["caps"]) {
			capList[c] = true
		}
		linux := capability["os"] == "linux" && capability["arch"] == "amd64"
		if platform {
			if !linux || !capList[platformCapability] || rt["group-platform-network"] != "modal-i6pn" {
				return nil, fmt.Errorf("%s: start Fleet with --group-platform-network=modal-i6pn on a Modal GPU node", nodeID)
			}
			// One Modal environment (the part before '/'); reachability is
			// proven later by the control-mesh startup barrier.
			scopes[strings.SplitN(strOf(rt["group-platform-scope"]), "/", 2)[0]] = true
			if len(scopes) != 1 || scopes[""] {
				return nil, fmt.Errorf("Every rank must run in the same Modal environment")
			}
		} else if !linux || !capList["model-group-private-network"] {
			return nil, fmt.Errorf("%s: update Fleet for private model groups on Linux NVIDIA nodes", nodeID)
		}
		state, err := m.nodes.status(ctx, nodeID)
		if err != nil {
			return nil, err
		}
		scope := "model-group-" + groupID
		busy := state["owner"] != journal.owner || state["node_id"] != nodeID
		for _, i := range instances(state) {
			busy = busy || mapOf(i)["scope"] == scope
		}
		for _, o := range operations(state) {
			busy = busy || mapOf(mapOf(o)["request"])["scope"] == scope
		}
		if busy {
			return nil, fmt.Errorf("This group scope already has node state; inspect the original deployment")
		}
		status, err := m.nodes.resourceStatus(ctx, nodeID)
		if err != nil {
			return nil, err
		}
		accelerators := map[any]map[string]any{}
		for _, a := range listOf(mapOf(status["inventory"])["accelerators"]) {
			accelerators[mapOf(a)["id"]] = mapOf(a)
		}
		resource := mapOf(p.member["resources"])
		rdevices := listOf(resource["devices"])
		if !keys(resource, "memory_bytes", "devices") || len(rdevices) < 1 || len(rdevices) > 4 || !intIn(resource["memory_bytes"], 256<<20, 1<<50) {
			return nil, fmt.Errorf("Declare system memory and exclusive NVIDIA GPU budgets")
		}
		var physical []any
		for _, raw := range rdevices {
			d := mapOf(raw)
			if !keys(d, "id", "backend", "memory_bytes", "exclusive") || !matches(gpuIDRE, d["id"]) || devices[strOf(d["id"])] ||
				d["backend"] != "cuda" || d["exclusive"] != true || !intIn(d["memory_bytes"], 256<<20, 1<<50) {
				return nil, fmt.Errorf("Declare exclusive NVIDIA GPU budgets")
			}
			devices[strOf(d["id"])] = true
			measured := accelerators[d["id"]]
			total := mapOf(measured["memory"])["total_bytes"]
			if measured["backend"] != "cuda" || !isInt(total) || intOf(total) < intOf(d["memory_bytes"]) {
				return nil, fmt.Errorf("The selected GPU capacity is unknown or below its requested budget")
			}
			physical = append(physical, total)
		}
		// A bounded, non-executable public receipt; no weights, paths or secrets.
		receipt, err := m.rpc(ctx, p.row["binding"], "group_snapshot", map[string]any{"sha256": config["model_sha256"]})
		if err != nil {
			return nil, err
		}
		record, err := modelDescriptor(receipt)
		if err != nil {
			return nil, err
		}
		if record["sha256"] != config["model_sha256"] || (len(records) > 0 && !same(record, records[0])) {
			return nil, fmt.Errorf("Every selected node must have the exact same prepared model snapshot")
		}
		records = append(records, record)
		address, iface := any(fmt.Sprintf("10.231.0.%d", rank+1)), any("wg0")
		if platform {
			address, iface = rt["group-platform-address"], rt["group-platform-interface"]
		}
		members = append(members, map[string]any{"rank": number(int64(rank)), "node_id": nodeID, "generation": number(2),
			"address": address, "control_port": number(18407), "interface": iface, "resources": clone(resource),
			"physical_gpu_bytes": physical})
	}
	tp := 0
	for _, raw := range members {
		tp += len(listOf(mapOf(mapOf(raw)["resources"])["devices"]))
	}
	uniform := true
	for _, raw := range members {
		if len(listOf(mapOf(mapOf(raw)["resources"])["devices"])) != tp/len(members) {
			uniform = false
		}
	}
	if (tp != 2 && tp != 4 && tp != 8) || !uniform {
		return nil, fmt.Errorf("Use 2, 4 or 8 GPUs, distributed equally across the selected nodes")
	}
	recipe := containerRecipe
	if platform {
		recipe = processRecipe
	}
	plan := map[string]any{"protocol": number(1), "owner": journal.owner, "group_id": groupID, "recipe_id": recipe,
		"model_sha256": config["model_sha256"], "context_length": config["context_length"], "parallel": config["parallel"],
		"tensor_parallel_size": number(int64(tp)), "rendezvous_port": number(18408), "inference_protocol": number(1),
		"members": members}
	if platform {
		plan["network_mode"] = platformPrivate
	} else {
		plan["underlay"] = underlay
	}
	plan = mapOf(clone(plan))
	store, err := m.packages()
	if err != nil {
		return nil, err
	}
	connector, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	source, err := store.capture(connector, records[0], platform)
	if err != nil {
		return nil, err
	}
	if err := store.validatePlan(plan, source); err != nil {
		return nil, err
	}
	return journal.createCreation(ctx, plan, source)
}

func isString(v any) bool { _, ok := v.(string); return ok }

// forgetGroup terminates an aborting group whose unclean members' nodes
// left the Fleet: those nodes are revoked first, so the identities can never
// return and act on the old intent.
func (m *Manager) forgetGroup(ctx context.Context, journal groupJournal, group map[string]any, config any) (map[string]any, error) {
	if group["phase"] == "forgotten" {
		return group, nil
	}
	if group["phase"] != "aborting" {
		return nil, fmt.Errorf("Stop the group before forgetting it")
	}
	coordinator := groupCoordinator{journal: journal, nodes: m.nodes}
	ms := members(group)
	observed := make([]map[string]any, len(ms))
	each(len(ms), func(i int) { observed[i] = coordinator.observe(ctx, strOf(group["owner"]), ms[i]) })
	var gone []string
	for i, o := range observed {
		delete(o, "settle")
		delete(o, "recover")
		if o["clean"] != true {
			gone = append(gone, strOf(mapOf(ms[i]["target"])["node_id"]))
		}
	}
	sort.Strings(gone)
	if len(gone) == 0 {
		return nil, fmt.Errorf("Every member is resource-free; continue the stop instead")
	}
	online, err := m.nodes.online(ctx)
	if err != nil {
		return nil, err
	}
	var live []string
	for _, n := range gone {
		if online[n] {
			live = append(live, n)
		}
	}
	if len(live) > 0 {
		return nil, fmt.Errorf("Node(s) %s are online; continue the stop normally", strings.Join(live, ", "))
	}
	var confirm []string
	for _, v := range listOf(mapOf(config)["confirm_node_ids"]) {
		confirm = append(confirm, strOf(v))
	}
	sort.Strings(confirm)
	if strings.Join(confirm, "\x00") != strings.Join(gone, "\x00") || confirm == nil {
		return nil, fmt.Errorf("Confirm the exact nodes to revoke: %s", strings.Join(gone, ", "))
	}
	if m.revoke == nil {
		return nil, fmt.Errorf("Fleet is not configured; no node was revoked")
	}
	for _, node := range gone {
		if err := m.revoke(node); err != nil {
			return nil, fmt.Errorf("Fleet did not confirm node revocation; the group was not forgotten")
		}
	}
	for i, m := range ms {
		m["observation"] = observed[i]
	}
	names := make([]any, len(gone))
	for i, n := range gone {
		names[i] = n
	}
	group["phase"] = "forgotten"
	group["forgotten"] = map[string]any{"node_ids": names, "at": time.Now().UTC().Format("2006-01-02T15:04:05+00:00")}
	return journal.save(ctx, group)
}
