package modelservices

import (
	"bytes"
	"context"
	"fmt"
	"log"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"
)

// Manager runs one owner's model service lifecycle (formerly
// pantheon/models/manager.py ModelServiceManager). Closing a UI never closes
// an attached engine; every mutation is saved to the Hub row before the
// next node step, so an interrupted call resumes from the directory.
type Manager struct {
	nodes     nodes
	dir       directory
	connector func(context.Context) (*Connector, error)
	locks     *Locks
	tasks     *Tasks
	plans     *Plans
	// controller operations the management App reached at its controller
	joinToken func() (string, error)
	revoke    func(node string) error
	// groupRoot holds each owner's frozen group sources and rank packages.
	groupRoot string
	grants    *Grants
}

// Locks serializes operations per owner deployment.
type Locks struct {
	mu sync.Mutex
	m  map[string]*sync.Mutex
}

var deploymentIDRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)

func (m *Manager) lock(id string) (func(), error) {
	if !deploymentIDRE.MatchString(id) {
		return nil, fmt.Errorf("Invalid deployment id")
	}
	m.locks.mu.Lock()
	if m.locks.m == nil {
		m.locks.m = map[string]*sync.Mutex{}
	}
	key := m.nodes.fleet + "/" + id
	l := m.locks.m[key]
	if l == nil {
		l = &sync.Mutex{}
		m.locks.m[key] = l
	}
	m.locks.mu.Unlock()
	l.Lock()
	return l.Unlock, nil
}

func (m *Manager) rpc(ctx context.Context, binding any, method string, args map[string]any) (map[string]any, error) {
	return m.nodes.rpc(ctx, mapOf(binding), method, args)
}

func bindingOf(node string, instance map[string]any, digest string) map[string]any {
	return map[string]any{"node_id": node, "instance_id": instance["instance_id"], "revision": digest,
		"generation": instance["generation"], "component": "backend", "port": "http"}
}

func findInstance(state map[string]any, digest, scope string) map[string]any {
	for _, v := range instances(state) {
		in := mapOf(v)
		if in["digest"] == digest && in["scope"] == scope {
			return in
		}
	}
	return nil
}

// ensure installs (or verifies) and starts a connector or engine instance in
// its scope and returns its exact binding.
func (m *Manager) ensure(ctx context.Context, row Row, key string, tree Tree, scope string) (map[string]any, error) {
	node := strOf(row["node_id"])
	if scope == "" {
		scope = "model-" + strOf(row["deployment_id"])
	}
	var digest string
	var state map[string]any
	var err error
	if bound := mapOf(row[key]); bound != nil {
		digest = strOf(bound["revision"])
		if state, err = m.nodes.status(ctx, node); err != nil {
			return nil, err
		}
		in := mapOf(instances(state)[strOf(bound["instance_id"])])
		if in == nil || in["digest"] != digest || in["scope"] != scope || in["app_id"] != "model-service" {
			return nil, fmt.Errorf("Model service binding is missing; inspect it in Fleet")
		}
	} else {
		if tree == nil {
			c, err := m.connector(ctx)
			if err != nil {
				return nil, err
			}
			tree = c.Tree
		}
		if digest, err = m.nodes.stage(ctx, node, tree); err != nil {
			return nil, err
		}
		op, err := m.nodes.submit(ctx, node, "install", digest, scope, 0, "", nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, node, op); err != nil {
			return nil, err
		}
	}
	current := findInstance(state, digest, scope)
	if current != nil && current["state"] == "starting" {
		for _, v := range operations(state) {
			op := mapOf(v)
			req := mapOf(op["request"])
			if req["scope"] == scope && req["action"] == "start" && (op["state"] == "queued" || op["state"] == "running") {
				if state, err = m.nodes.wait(ctx, node, op); err != nil {
					return nil, err
				}
				current = mapOf(instances(state)[strOf(current["instance_id"])])
				break
			}
		}
	}
	if current == nil || current["state"] == "stopped" {
		var generation int64
		if current != nil {
			generation = intOf(current["generation"])
		}
		op, err := m.nodes.submit(ctx, node, "start", digest, scope, generation, "", nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, node, op); err != nil {
			return nil, err
		}
		current = findInstance(state, digest, scope)
	}
	if current == nil || current["state"] != "ready" {
		return nil, fmt.Errorf("This connector needs recovery in Fleet; no engine was restarted")
	}
	binding := bindingOf(node, current, digest)
	return binding, m.nodes.keepAlive(ctx, node, binding)
}

// node is a registry node checked for authenticated management.
func (m *Manager) node(ctx context.Context, id string, managed bool) (map[string]any, error) {
	r, err := m.nodes.record(ctx, id)
	if err != nil {
		return nil, fmt.Errorf("Select a node in your Fleet")
	}
	if runtimes(r)["app-rpc-auth"] != "1" {
		return nil, fmt.Errorf("Update Fleet on the selected node to enable authenticated Model Services management, then retry. No connector was installed.")
	}
	if managed && runtimes(r)["app-resources"] != "1" {
		return nil, fmt.Errorf("Update Fleet on this node for managed model resource reservations")
	}
	return r, nil
}

func (m *Manager) save(ctx context.Context, row Row) (Row, error) { return m.dir.save(ctx, row) }

func nodeName(r map[string]any, id string) string {
	if n := strOf(r["name"]); n != "" {
		return n
	}
	return id
}

func (m *Manager) CreateManaged(ctx context.Context, id, name, nodeID string, config any) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	node, err := m.node(ctx, nodeID, true)
	if err != nil {
		return nil, err
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	target := platform(node)
	valid, err := c.ValidateManaged(config, target)
	if err != nil {
		return nil, err
	}
	selected, err := c.Recipe(strOf(valid["recipe_id"]), target)
	if err != nil {
		return nil, err
	}
	rt := runtimes(node)
	if selected["engine"] == "speaches" && (rt["app-readonly-mounts"] != "1" || rt["app-owner-user"] != "1") {
		return nil, fmt.Errorf("Update Fleet on this Linux node for private, read-only speech model mounts")
	}
	if selected["engine"] == "sglang" && selected["runtime"] != "preinstalled" && rt["app-readonly-mounts"] != "1" {
		return nil, fmt.Errorf("Update Fleet on this GPU node for read-only managed model mounts")
	}
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	var existing Row
	for _, r := range rows {
		if r["deployment_id"] == id {
			existing = r
		}
	}
	if existing != nil && (existing["mode"] != "managed" || existing["node_id"] != nodeID || !bytes.Equal(Compact(existing["managed"]), Compact(valid))) {
		return nil, fmt.Errorf("This deployment already refers to a different configuration")
	}
	row := existing
	if row == nil {
		if err := m.releaseScopes(ctx, nodeID, id); err != nil {
			log.Printf("[models] %s leftover cleanup skipped for %s: %v", m.nodes.fleet, id, err)
		}
		if row, err = m.save(ctx, Row{"deployment_id": id, "name": name, "node_id": nodeID, "node_name": nodeName(node, nodeID),
			"engine": selected["engine"], "mode": "managed", "managed": valid, "state": "draft", "models": []any{}, "revision": number(0)}); err != nil {
			return nil, err
		}
	}
	if row["state"] != "draft" {
		return row, nil
	}
	if row["binding"], err = m.ensure(ctx, row, "binding", nil, ""); err != nil {
		return nil, err
	}
	if row, err = m.save(ctx, row); err != nil {
		return nil, err
	}
	recipeID := valid["model_recipe_id"]
	switch {
	case row["engine"] == "speaches":
		_, err = m.rpc(ctx, row["binding"], "speech_models", map[string]any{"action": "prepare", "model_id": recipeID, "resume": true})
	case selected["operation"] == "image" || selected["operation"] == "video":
		_, err = m.rpc(ctx, row["binding"], "diffusion_models", map[string]any{"action": "prepare", "model_id": recipeID, "resume": true})
	case selected["runtime"] == "preinstalled":
		args := map[string]any{"action": "prepare", "model_id": recipeID, "resume": true}
		if manifest := valid["model_manifest"]; manifest != nil {
			args["manifest"] = manifest
		}
		_, err = m.rpc(ctx, row["binding"], "llm_models", args)
	case row["engine"] != "sglang":
		_, err = m.rpc(ctx, row["binding"], "engines_prepare", map[string]any{"recipe_id": valid["recipe_id"], "resume": true})
	}
	return row, err
}

func pick(item map[string]any, keys ...string) map[string]any {
	out := map[string]any{}
	for _, k := range keys {
		out[k] = item[k]
	}
	return out
}

func (m *Manager) EngineRecipes(ctx context.Context, nodeID string) (map[string]any, error) {
	node, err := m.node(ctx, nodeID, true)
	if err != nil {
		return nil, err
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	target := platform(node)
	modal := false
	for _, l := range listOf(node["labels"]) {
		modal = modal || l == "modal-gpu"
	}
	recipes := []any{}
	for _, r := range c.Recipes() {
		available := false
		for _, p := range listOf(r["platforms"]) {
			available = available || p == target
		}
		if !available || target == "darwin-amd64" {
			continue
		}
		if r["runtime"] == "preinstalled" {
			copied := clone(r).(map[string]any)
			copied["unavailable_reason"] = ""
			if !modal {
				copied["unavailable_reason"] = "Only Modal GPU nodes provide this engine"
			}
			recipes = append(recipes, copied)
		} else {
			recipes = append(recipes, r)
		}
	}
	llm, speech, diffusion := []any{}, []any{}, []any{}
	if target == "linux-amd64" {
		for _, item := range c.LLMCatalog() {
			llm = append(llm, pick(item, "id", "display_name", "context_length", "maximum_context_length",
				"minimum_gpu_memory_bytes", "minimum_memory_bytes", "capabilities"))
		}
		for _, item := range c.SpeechCatalog() {
			speech = append(speech, pick(item, "id", "model", "operation", "minimum_memory_bytes"))
		}
		for _, item := range c.DiffusionCatalog() {
			diffusion = append(diffusion, pick(item, "id", "model", "operation", "minimum_memory_bytes"))
		}
	}
	return map[string]any{"recipes": recipes, "llm_models": llm, "speech_models": speech, "diffusion_models": diffusion}, nil
}

func stopped(state any) bool {
	return state == "stopped" || state == "stopping" || state == "recovering"
}

func (m *Manager) Engines(ctx context.Context, id, action, recipeID string, resume bool) (map[string]any, error) {
	if action != "catalog" && action != "jobs" && action != "prepare" && action != "cancel" {
		return nil, fmt.Errorf("Unsupported engine preparation action")
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["binding"] == nil || stopped(row["state"]) {
		return nil, fmt.Errorf("Start the connector before preparing an engine")
	}
	args := map[string]any{}
	switch action {
	case "prepare":
		args = map[string]any{"recipe_id": recipeID, "resume": resume}
	case "cancel":
		args = map[string]any{"job_id": recipeID}
	}
	return m.rpc(ctx, row["binding"], "engines_"+action, args)
}

// recipeOf finds a managed row's recipe in the catalog, if any.
func (c *Connector) recipeByID(id any) map[string]any {
	for _, r := range c.Recipes() {
		if r["id"] == id {
			return r
		}
	}
	return map[string]any{}
}

func (m *Manager) startManaged(ctx context.Context, row Row) (Row, error) {
	node, err := m.node(ctx, strOf(row["node_id"]), true)
	if err != nil {
		return nil, err
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	rt := runtimes(node)
	managed := mapOf(row["managed"])
	if row["engine"] == "speaches" && (rt["app-readonly-mounts"] != "1" || rt["app-owner-user"] != "1") {
		return nil, fmt.Errorf("Update Fleet on this Linux node for private, read-only speech model mounts")
	}
	preinstalled := c.recipeByID(managed["recipe_id"])["runtime"] == "preinstalled"
	if row["engine"] == "sglang" && !preinstalled && rt["app-readonly-mounts"] != "1" {
		return nil, fmt.Errorf("Update Fleet on this GPU node for read-only managed model mounts")
	}
	if row["binding"], err = m.ensure(ctx, row, "binding", nil, ""); err != nil {
		return nil, err
	}
	row["state"] = "draft"
	if row, err = m.save(ctx, row); err != nil {
		return nil, err
	}
	managed = mapOf(row["managed"])
	catalog, err := m.rpc(ctx, row["binding"], "engines_catalog", nil)
	if err != nil {
		return nil, err
	}
	var selected map[string]any
	for _, r := range listOf(catalog["recipes"]) {
		if mapOf(r)["id"] == managed["recipe_id"] {
			selected = mapOf(r)
		}
	}
	if selected == nil || (selected["runtime"] != "container" && selected["prepared"] != true) {
		return nil, fmt.Errorf("Engine preparation has not completed. Inspect Downloads before starting it.")
	}
	resources := mapOf(managed["resources"])
	memory := intOf(resources["memory_bytes"])
	devices := listOf(resources["devices"])
	switch {
	case selected["operation"] == "image" || selected["operation"] == "video":
		snapshot, err := m.rpc(ctx, row["binding"], "diffusion_models", map[string]any{"action": "status", "model_id": managed["model_recipe_id"]})
		if err != nil {
			return nil, err
		}
		if snapshot["ready"] != true {
			return nil, fmt.Errorf("Finish preparing the pinned diffusion weights in Downloads before starting")
		}
		if intOf(snapshot["minimum_memory_bytes"]) > memory || len(devices) == 0 || intOf(selected["minimum_gpu_memory_bytes"]) > intOf(mapOf(devices[0])["memory_bytes"]) {
			return nil, fmt.Errorf("The diffusion model exceeds this deployment’s memory budget")
		}
	case preinstalled:
		args := map[string]any{"action": "status", "model_id": managed["model_recipe_id"]}
		if manifest := managed["model_manifest"]; manifest != nil {
			args["manifest"] = manifest
		}
		snapshot, err := m.rpc(ctx, row["binding"], "llm_models", args)
		if err != nil {
			return nil, err
		}
		if snapshot["ready"] != true {
			return nil, fmt.Errorf("Finish preparing the pinned model weights in Downloads before starting")
		}
	case row["engine"] == "sglang":
		tp := managed["tensor_parallel_size"]
		if tp == nil {
			tp = number(1)
		}
		snapshot, err := m.rpc(ctx, row["binding"], "snapshots_status", map[string]any{"sha256": managed["model_artifact_sha256"],
			"context_length": managed["context_length"], "parallel": managed["parallel"], "tensor_parallel_size": tp})
		if err != nil {
			return nil, err
		}
		if snapshot["ready"] != true {
			return nil, fmt.Errorf("Download and prepare the pinned model bundle in Downloads before starting SGLang")
		}
		for _, d := range devices {
			if intOf(mapOf(snapshot["estimate"])["estimated_bytes"]) > intOf(mapOf(d)["memory_bytes"]) {
				return nil, fmt.Errorf("Weights, KV cache and workspace exceed this deployment’s GPU budget")
			}
		}
	}
	if row["engine"] == "speaches" {
		snapshot, err := m.rpc(ctx, row["binding"], "speech_models", map[string]any{"action": "status", "model_id": managed["model_recipe_id"]})
		if err != nil {
			return nil, err
		}
		if snapshot["ready"] != true {
			return nil, fmt.Errorf("Finish preparing the pinned speech model in Downloads before starting")
		}
		if intOf(snapshot["minimum_memory_bytes"]) > memory {
			return nil, fmt.Errorf("The speech model exceeds this deployment’s system memory budget")
		}
	}
	scope := "engine-" + strOf(row["deployment_id"])
	if row["engine_binding"] != nil {
		// Restart the installed artifact; a newer catalog must never change it.
		if row["engine_binding"], err = m.ensure(ctx, row, "engine_binding", nil, scope); err != nil {
			return nil, err
		}
	} else {
		if err := m.releaseFailedEngines(ctx, row); err != nil {
			return nil, err
		}
		tree, err := c.EnginePackage(managed, platform(node))
		if err != nil {
			return nil, err
		}
		if row["engine_binding"], err = m.ensure(ctx, row, "engine_binding", tree, scope); err != nil {
			return nil, err
		}
	}
	// Persist engine ownership before configuring the connector.
	if row, err = m.save(ctx, row); err != nil {
		return nil, err
	}
	if row["engine_idle"] != nil {
		status, err := m.rpc(ctx, row["binding"], "status", nil)
		if err != nil {
			return nil, err
		}
		if err := m.resetFence(ctx, row["binding"], status); err != nil {
			return nil, err
		}
	}
	configuration, err := m.managedConfiguration(ctx, row, mapOf(row["engine_binding"]))
	if err != nil {
		return nil, err
	}
	configured, err := m.rpc(ctx, row["binding"], "configure", configuration)
	if err != nil {
		return nil, err
	}
	if row["engine_idle"] != nil {
		if _, err := m.rpc(ctx, row["binding"], "resume", map[string]any{"config_revision": configured["config_revision"]}); err != nil {
			return nil, err
		}
	}
	row["state"], row["config_revision"] = "ready", configured["config_revision"]
	return m.save(ctx, row)
}

// releaseScopes stops instances still running under a deployment's scopes.
func (m *Manager) releaseScopes(ctx context.Context, nodeID, id string) error {
	state, err := m.nodes.status(ctx, nodeID)
	if err != nil {
		return err
	}
	for _, v := range instances(state) {
		in := mapOf(v)
		scope := strOf(in["scope"])
		if (scope == "model-"+id || scope == "engine-"+id) && in["app_id"] == "model-service" && in["state"] != "stopped" {
			op, err := m.nodes.submit(ctx, nodeID, "stop", strOf(in["digest"]), scope, intOf(in["generation"]), "", nil)
			if err != nil {
				return err
			}
			if _, err := m.nodes.wait(ctx, nodeID, op); err != nil {
				return err
			}
		}
	}
	return nil
}

// releaseFailedEngines stops engines of this deployment that failed before it
// recorded them (their reservation would refuse every retry).
func (m *Manager) releaseFailedEngines(ctx context.Context, row Row) error {
	node, scope := strOf(row["node_id"]), "engine-"+strOf(row["deployment_id"])
	state, err := m.nodes.status(ctx, node)
	if err != nil {
		return err
	}
	for _, v := range instances(state) {
		in := mapOf(v)
		if in["scope"] == scope && in["state"] == "failed" && in["app_id"] == "model-service" {
			op, err := m.nodes.submit(ctx, node, "stop", strOf(in["digest"]), scope, intOf(in["generation"]), "", nil)
			if err != nil {
				return err
			}
			if _, err := m.nodes.wait(ctx, node, op); err != nil {
				return err
			}
		}
	}
	return nil
}

func (m *Manager) managedConfiguration(ctx context.Context, row Row, binding map[string]any) (map[string]any, error) {
	state, err := m.nodes.status(ctx, strOf(row["node_id"]))
	if err != nil {
		return nil, err
	}
	instance, dead, err := boundInstance(state, binding, "engine-"+strOf(row["deployment_id"]), false)
	if err != nil {
		return nil, err
	}
	if dead || instance["state"] != "ready" {
		return nil, fmt.Errorf("Managed engine changed while configuring the connector")
	}
	var endpoint string
	for _, r := range listOf(instance["resources"]) {
		if mapOf(r)["component"] == "backend" {
			endpoint = strOf(mapOf(mapOf(r)["endpoints"])["http"])
			break
		}
	}
	u, err := url.Parse(endpoint)
	if endpoint == "" || err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("Fleet returned an invalid managed engine endpoint")
	}
	managed := map[string]any{}
	for k, v := range mapOf(row["managed"]) {
		if k != "resources" {
			managed[k] = v
		}
	}
	managed["scope"] = "engine-" + strOf(row["deployment_id"])
	managed["memory_bytes"] = mapOf(mapOf(row["managed"])["resources"])["memory_bytes"]
	return map[string]any{"config": map[string]any{"engine": row["engine"], "endpoint": endpoint}, "managed": managed}, nil
}

func (m *Manager) Attach(ctx context.Context, id, name, nodeID, engine, endpoint, credentialFile, secretRef string) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	node, err := m.node(ctx, nodeID, false)
	if err != nil {
		return nil, err
	}
	config, err := ValidateAttach(engine, endpoint, credentialFile, secretRef)
	if err != nil {
		return nil, err
	}
	if secretRef != "" && runtimes(node)["model-credentials"] != "1" {
		return nil, fmt.Errorf("Update Fleet on the selected node to use named credentials. No connector was installed.")
	}
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	var existing Row
	for _, r := range rows {
		if r["deployment_id"] == id {
			existing = r
		}
	}
	if existing != nil {
		mode := strOf(existing["mode"])
		if mode == "" {
			mode = "attached"
		}
		if mode != "attached" || existing["state"] != "draft" || existing["node_id"] != nodeID || existing["engine"] != engine {
			return nil, fmt.Errorf("This deployment already exists. Refresh and resume it instead of creating another.")
		}
	}
	row := existing
	if row == nil {
		if err := m.releaseScopes(ctx, nodeID, id); err != nil {
			log.Printf("[models] %s leftover cleanup skipped for %s: %v", m.nodes.fleet, id, err)
		}
		if row, err = m.save(ctx, Row{"deployment_id": id, "name": name, "node_id": nodeID, "node_name": nodeName(node, nodeID),
			"engine": engine, "state": "draft", "models": []any{}, "revision": number(0)}); err != nil {
			return nil, err
		}
	}
	binding, err := m.ensure(ctx, row, "binding", nil, "")
	if err != nil {
		return nil, err
	}
	row["binding"] = binding
	if row, err = m.save(ctx, row); err != nil {
		return nil, err
	}
	result, err := m.rpc(ctx, binding, "configure", map[string]any{"config": config})
	if err != nil {
		return nil, err
	}
	row["binding"], row["config_revision"], row["state"] = binding, result["config_revision"], "ready"
	return m.save(ctx, row)
}

func pending(row Row) bool {
	for _, k := range []string{"recovery", "connector_update", "engine_update", "operation_stop"} {
		if truthy(row[k]) {
			return true
		}
	}
	return false
}

func truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case map[string]any:
		return len(t) > 0
	case []any:
		return len(t) > 0
	}
	return true
}

func (m *Manager) Discover(ctx context.Context, id string) (map[string]any, error) {
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if truthy(row["recovery"]) || truthy(row["engine_update"]) || truthy(row["connector_update"]) {
		return nil, fmt.Errorf("Resume the pending service operation before managing its models")
	}
	if row["binding"] == nil {
		return nil, fmt.Errorf("The connector has not finished setup. Inspect it in Fleet.")
	}
	if row, err = m.wake(ctx, row); err != nil {
		return nil, err
	}
	result, err := m.rpc(ctx, row["binding"], "discover", nil)
	if err != nil {
		return nil, err
	}
	if row["engine"] == "api" {
		// Only when a provider states no context is an OpenRouter estimate offered.
		var unknown []string
		for _, item := range listOf(result["models"]) {
			model := mapOf(item)
			if _, stated := mapOf(model["reported"])["context"]; !stated {
				unknown = append(unknown, strOf(model["id"]))
			}
		}
		if len(unknown) > 0 {
			found := suggest(ctx, unknown, "")
			for _, item := range listOf(result["models"]) {
				model := mapOf(item)
				if s, ok := found[strOf(model["id"])]; ok {
					model["suggested"] = s
				}
			}
		}
	}
	return result, nil
}

func (m *Manager) Artifacts(ctx context.Context, id, action, jobID string, source any, resume bool) (map[string]any, error) {
	switch action {
	case "list", "submit", "cancel", "forget":
	default:
		return nil, fmt.Errorf("Unsupported artifact operation")
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if stopped(row["state"]) || row["binding"] == nil {
		return nil, fmt.Errorf("Start this service before managing its downloads")
	}
	args := map[string]any{}
	if action != "list" {
		args["job_id"] = jobID
	}
	if action == "submit" {
		args["source"], args["resume"] = source, resume
	}
	return m.rpc(ctx, row["binding"], "artifacts_"+action, args)
}

func (m *Manager) Snapshots(ctx context.Context, id, action, artifactJobID string, resume bool, jobID string) (map[string]any, error) {
	if action != "jobs" && action != "prepare" && action != "cancel" {
		return nil, fmt.Errorf("Unsupported snapshot operation")
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if stopped(row["state"]) || row["binding"] == nil || row["mode"] != "managed" || row["engine"] != "sglang" {
		return nil, fmt.Errorf("An owned SGLang connector is required")
	}
	args := map[string]any{}
	switch action {
	case "prepare":
		args = map[string]any{"artifact_job_id": artifactJobID, "resume": resume}
	case "cancel":
		args = map[string]any{"job_id": jobID}
	}
	return m.rpc(ctx, row["binding"], "snapshots_"+action, args)
}

func (m *Manager) Resources(ctx context.Context, nodeID string) (map[string]any, error) {
	return m.nodes.resourceStatus(ctx, nodeID)
}

var modelActions = map[string]bool{"catalog": true, "status": true, "prepare": true, "jobs": true, "cancel": true, "forget": true}

// ModelCache prepares speech, diffusion or language models in a connector's cache.
func (m *Manager) ModelCache(ctx context.Context, kind, id, action, modelID string, resume bool) (map[string]any, error) {
	if !modelActions[action] {
		return nil, fmt.Errorf("Unsupported %s model preparation action", map[string]string{"speech_models": "speech",
			"diffusion_models": "diffusion", "llm_models": "language"}[kind])
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	engine, message := "sglang", ""
	switch kind {
	case "speech_models":
		engine, message = "speaches", "Start a Speaches connector before preparing its local model cache"
	case "diffusion_models":
		message = "Start an SGLang connector before preparing its diffusion model cache"
	default:
		message = "Start an SGLang connector before preparing its language model cache"
	}
	if (row["state"] != "draft" && row["state"] != "ready") || row["binding"] == nil || row["engine"] != engine {
		return nil, fmt.Errorf("%s", message)
	}
	args := map[string]any{"action": action, "model_id": modelID, "resume": resume}
	if kind == "llm_models" {
		manifest := mapOf(mapOf(row["managed"])["model_manifest"])
		if manifest != nil && manifest["id"] == modelID && (action == "status" || action == "prepare") {
			args["manifest"] = manifest
		}
	}
	return m.rpc(ctx, row["binding"], kind, args)
}

func (m *Manager) Activity(ctx context.Context, id, action, requestID string) (map[string]any, error) {
	if action != "list" && action != "cancel" {
		return nil, fmt.Errorf("Unsupported request activity action")
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["binding"] == nil || (row["state"] != "ready" && row["state"] != "stopping") {
		return nil, fmt.Errorf("Start the connector to inspect its saved request activity")
	}
	if action == "cancel" {
		return m.rpc(ctx, row["binding"], "cancel_request", map[string]any{"request_id": requestID})
	}
	return m.rpc(ctx, row["binding"], "activity", nil)
}

var jobRefRE = regexp.MustCompile(`^fleet-job://([a-z0-9][a-z0-9_-]{0,63})/([A-Za-z0-9_-]{1,100})$`)

func parseJobRef(ref string) (string, string, error) {
	m := jobRefRE.FindStringSubmatch(ref)
	if m == nil {
		return "", "", fmt.Errorf("Invalid Fleet inference job reference")
	}
	return m[1], m[2], nil
}

func (m *Manager) VideoRecovery(ctx context.Context, ref, action, ticket, confirmation string) (map[string]any, error) {
	id, job, err := parseJobRef(ref)
	if err != nil {
		return nil, err
	}
	if action != "inspect" && action != "release" {
		return nil, fmt.Errorf("Unsupported video recovery action")
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
	if row["binding"] == nil || (row["state"] != "ready" && row["state"] != "stopping") {
		return nil, fmt.Errorf("Inspect the original connector before recovering this video")
	}
	result, err := m.rpc(ctx, row["binding"], "video_recovery", map[string]any{"job_id": job, "config_revision": row["config_revision"],
		"action": action, "ticket": ticket, "confirmation": confirmation})
	if err != nil {
		return nil, err
	}
	result["ref"] = ref
	return result, nil
}

func (m *Manager) ModelOperations(ctx context.Context, id, action, jobID, operation, artifactJobID, modelID string, poolRevision any, template string, parameters map[string]any) (map[string]any, error) {
	if action != "status" && action != "submit" && action != "forget" {
		return nil, fmt.Errorf("Unsupported model management action")
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["mode"] != "managed" || row["state"] != "ready" || row["engine_binding"] == nil {
		return nil, fmt.Errorf("Start an owned engine before managing its models")
	}
	if mapOf(row["engine_idle"])["phase"] == "enabled" && action == "status" {
		_, snapshot, err := m.observe(ctx, row, "status")
		if err != nil {
			return nil, err
		}
		result, err := m.rpc(ctx, row["binding"], "models_status", nil)
		if err != nil {
			return nil, err
		}
		result["engine_idle"] = snapshot["state"]
		return result, nil
	}
	if action == "submit" {
		switch operation {
		case "import", "load", "unload", "preload", "unpin":
		default:
			return nil, fmt.Errorf("Unsupported model operation")
		}
		if operation == "preload" && mapOf(row["managed"])["load_policy"] != "resident" {
			return nil, fmt.Errorf("Choose a resident service before enabling preloading")
		}
		if row, err = m.wake(ctx, row); err != nil {
			return nil, err
		}
	}
	if action != "forget" {
		state, err := m.nodes.status(ctx, strOf(row["node_id"]))
		if err != nil {
			return nil, err
		}
		instance, dead, err := boundInstance(state, mapOf(row["engine_binding"]), "engine-"+id, false)
		if err != nil {
			return nil, err
		}
		if dead || instance["state"] != "ready" {
			return nil, fmt.Errorf("Owned engine is no longer ready; inspect it in Fleet")
		}
	}
	args := map[string]any{}
	if action != "status" {
		args["job_id"] = jobID
	}
	if action == "submit" {
		args["action"], args["artifact_job_id"], args["model_id"] = operation, artifactJobID, modelID
		if poolRevision != nil {
			args["pool_revision"] = poolRevision
		}
		if template != "" || len(parameters) > 0 {
			if parameters == nil {
				parameters = map[string]any{}
			}
			args["template"], args["parameters"] = template, parameters
		}
	}
	return m.rpc(ctx, row["binding"], "models_"+action, args)
}

func (m *Manager) Publish(ctx context.Context, id string, models []any, revision any) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["mode"] == "group" {
		return nil, fmt.Errorf("Manage this model through its original group lifecycle")
	}
	if intOf(row["revision"]) != intOf(revision) {
		return nil, fmt.Errorf("Service changed. Refresh before publishing.")
	}
	if pending(row) {
		return nil, fmt.Errorf("Resume the pending service operation before publishing models")
	}
	if row, err = m.wake(ctx, row); err != nil {
		return nil, err
	}
	discovered, err := m.rpc(ctx, row["binding"], "discover", nil)
	if err != nil {
		return nil, err
	}
	ids := map[any]bool{}
	for _, d := range listOf(discovered["models"]) {
		ids[mapOf(d)["id"]] = true
	}
	seen := map[any]bool{}
	for _, item := range models {
		model := mapOf(item)
		if seen[model["id"]] || !ids[model["id"]] {
			return nil, fmt.Errorf("Publish unique models returned by this endpoint")
		}
		seen[model["id"]] = true
	}
	row["models"], row["config_revision"] = models, discovered["config_revision"]
	return m.save(ctx, row)
}

// chatEntry is what chat uses for one model: the service's report (or, for an
// API that states nothing, the labelled suggestion), capped by the owner.
func chatEntry(modelID string, reported, suggested, existing map[string]any, compute string, limit any) (map[string]any, error) {
	if reported == nil {
		reported = map[string]any{}
	}
	if suggested == nil {
		suggested = map[string]any{}
	}
	var entry map[string]any
	if existing != nil {
		entry = clone(existing).(map[string]any)
	} else {
		entry = map[string]any{"id": modelID, "name": modelID, "operations": []any{"text"}, "compute": compute}
	}
	for _, key := range []string{"tools", "vision", "reasoning"} {
		if b, ok := reported[key].(bool); ok {
			entry[key] = b
		} else if _, has := entry[key]; !has {
			if b, ok := suggested[key].(bool); ok {
				entry[key] = b
			}
		}
	}
	if ops := listOf(reported["operations"]); len(ops) == 1 && (ops[0] == "text" || ops[0] == "embedding") {
		entry["operations"] = ops
	}
	if limit == nil {
		limit = entry["context_limit"]
	}
	delete(entry, "context_limit")
	if limit != nil {
		if !isInt(limit) || intOf(limit) < 512 {
			return nil, fmt.Errorf("A context limit is at least 512 tokens")
		}
		entry["context_limit"] = limit
	}
	stated := reported["context"]
	if !truthy(stated) {
		stated = suggested["context"]
	}
	if !truthy(stated) {
		stated = entry["context"]
	}
	if limit != nil && truthy(stated) {
		entry["context"] = number(min(intOf(limit), intOf(stated)))
	} else if limit != nil {
		entry["context"] = limit
	} else if truthy(stated) {
		entry["context"] = stated
	}
	textual := false
	for _, op := range listOf(entry["operations"]) {
		textual = textual || op == "text"
	}
	if textual && !truthy(entry["context"]) {
		return nil, fmt.Errorf("This service does not state a context length for this model; set a context limit")
	}
	return entry, nil
}

func compute(row Row) string {
	if row["engine"] == "api" {
		return "provider"
	}
	return "node"
}

func (m *Manager) ServiceModels(ctx context.Context, id string) (map[string]any, error) {
	discovered, err := m.Discover(ctx, id)
	if err != nil {
		return nil, err
	}
	row, err := m.SyncModels(ctx, id, discovered)
	if err != nil {
		return nil, err
	}
	inChat := map[any]map[string]any{}
	for _, item := range listOf(row["models"]) {
		inChat[mapOf(item)["id"]] = mapOf(item)
	}
	models := []any{}
	for _, item := range listOf(discovered["models"]) {
		d := mapOf(item)
		reported, suggested := d["reported"], d["suggested"]
		if reported == nil {
			reported = map[string]any{}
		}
		if suggested == nil {
			suggested = map[string]any{}
		}
		entry, in := inChat[d["id"]]
		var entryValue any
		if in {
			entryValue = entry
		}
		models = append(models, map[string]any{"id": d["id"], "reported": reported, "suggested": suggested, "in_chat": in, "entry": entryValue})
	}
	return map[string]any{"deployment_id": id, "revision": row["revision"], "models": models}, nil
}

// SyncModels keeps models in chat equal to what the service reports.
func (m *Manager) SyncModels(ctx context.Context, id string, discovered map[string]any) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if !truthy(row["models"]) || row["mode"] == "group" || pending(row) {
		return row, nil
	}
	if discovered == nil {
		if discovered, err = m.rpc(ctx, row["binding"], "discover", nil); err != nil {
			return nil, err
		}
	}
	found := map[any]map[string]any{}
	for _, item := range listOf(discovered["models"]) {
		found[mapOf(item)["id"]] = mapOf(item)
	}
	var models []any
	changed := false
	for _, item := range listOf(row["models"]) {
		entry := mapOf(item)
		seen := found[entry["id"]]
		if seen == nil {
			models = append(models, entry)
			continue
		}
		synced, err := chatEntry(strOf(entry["id"]), mapOf(seen["reported"]), mapOf(seen["suggested"]), entry, compute(row), nil)
		if err != nil {
			synced = entry
		}
		changed = changed || !bytes.Equal(Compact(synced), Compact(entry))
		models = append(models, synced)
	}
	if !changed {
		return row, nil
	}
	row["models"] = models
	if rev, ok := discovered["config_revision"]; ok {
		row["config_revision"] = rev
	}
	return m.save(ctx, row)
}

func (m *Manager) SetInChat(ctx context.Context, id, modelID string, enabled bool, limit any) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["mode"] == "group" {
		return nil, fmt.Errorf("Manage this model through its original group lifecycle")
	}
	if pending(row) {
		return nil, fmt.Errorf("Resume the pending service operation first")
	}
	models := []any{}
	var existing map[string]any
	for _, item := range listOf(row["models"]) {
		if mapOf(item)["id"] == modelID {
			existing = mapOf(item)
		} else {
			models = append(models, item)
		}
	}
	if enabled {
		if row, err = m.wake(ctx, row); err != nil {
			return nil, err
		}
		discovered, err := m.rpc(ctx, row["binding"], "discover", nil)
		if err != nil {
			return nil, err
		}
		var seen map[string]any
		for _, item := range listOf(discovered["models"]) {
			if mapOf(item)["id"] == modelID {
				seen = mapOf(item)
			}
		}
		if seen == nil {
			return nil, fmt.Errorf("This service does not offer that model now")
		}
		var suggested map[string]any
		if _, stated := mapOf(seen["reported"])["context"]; row["engine"] == "api" && !stated {
			suggested = mapOf(suggest(ctx, []string{modelID}, "")[modelID])
		}
		// limit: nil keeps the owner's current limit, 0 removes it.
		var cap any
		if limit == nil {
			if existing != nil {
				cap = existing["context_limit"]
			}
		} else if intOf(limit) != 0 {
			cap = limit
		}
		var base map[string]any
		if existing != nil {
			base = map[string]any{}
			for k, v := range existing {
				if k != "context_limit" {
					base[k] = v
				}
			}
		}
		entry, err := chatEntry(modelID, mapOf(seen["reported"]), suggested, base, compute(row), cap)
		if err != nil {
			return nil, err
		}
		models = append(models, entry)
		row["config_revision"] = discovered["config_revision"]
	}
	row["models"] = models
	return m.save(ctx, row)
}

// boundInstance is the exact instance of a binding in its scope, and whether
// it has fully stopped (generation+1 after an acknowledged or lost stop).
func boundInstance(state map[string]any, binding map[string]any, scope string, stopping bool) (map[string]any, bool, error) {
	instance := mapOf(instances(state)[strOf(binding["instance_id"])])
	if instance == nil || instance["digest"] != binding["revision"] || instance["scope"] != scope || instance["app_id"] != "model-service" {
		return nil, false, fmt.Errorf("Model service identity changed; inspect its exact Fleet binding")
	}
	dead := isStopped(instance)
	g, bg := intOf(instance["generation"]), intOf(binding["generation"])
	allowed := g == bg || (dead && g == bg+1)
	newerInactive := stopping && g > bg && (dead || instance["state"] == "failed")
	if !allowed && !newerInactive {
		return nil, false, fmt.Errorf("Model service generation changed; inspect its exact Fleet binding")
	}
	return instance, dead, nil
}

func isStopped(instance map[string]any) bool {
	return instance["state"] == "stopped" && !truthy(instance["resources"]) && !truthy(instance["reservations"])
}

func (m *Manager) stopBinding(ctx context.Context, row Row, key, scope string) (Row, error) {
	node := strOf(row["node_id"])
	binding := mapOf(row[key])
	state, err := m.nodes.status(ctx, node)
	if err != nil {
		return nil, err
	}
	instance, dead, err := boundInstance(state, binding, scope, true)
	if err != nil {
		return nil, err
	}
	if !dead {
		op, err := m.nodes.submit(ctx, node, "stop", strOf(binding["revision"]), scope, intOf(instance["generation"]), "", nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, node, op); err != nil {
			return nil, err
		}
		if instance, dead, err = boundInstance(state, binding, scope, true); err != nil {
			return nil, err
		}
		if !dead {
			return nil, fmt.Errorf("Model service stop has not been confirmed by Fleet")
		}
	}
	updated := clone(binding).(map[string]any)
	updated["generation"] = instance["generation"]
	row[key] = updated
	// Persist before the next component stop, so partial failures stay retryable.
	return m.save(ctx, row)
}

func (m *Manager) drainBinding(ctx context.Context, binding any) error {
	deadline := time.Now().Add(30 * time.Second)
	for {
		drained, err := m.rpc(ctx, binding, "drain", nil)
		if err != nil {
			return err
		}
		if drained["safe_to_stop"] == true {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("Service is draining active calls or model operations. New calls are blocked; retry after they finish.")
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func versionAtLeast(version string, minimum [3]int64) bool {
	parts := strings.Split(version, ".")
	if len(parts) != 3 {
		return false
	}
	var v [3]int64
	for i, p := range parts {
		for _, c := range p {
			if c < '0' || c > '9' {
				return false
			}
		}
		fmt.Sscan(p, &v[i])
	}
	for i := range 3 {
		if v[i] != minimum[i] {
			return v[i] > minimum[i]
		}
	}
	return true
}

func (m *Manager) UpgradeConnector(ctx context.Context, id string) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	switch {
	case row["mode"] == "group":
		return nil, fmt.Errorf("Manage this model through its original group lifecycle")
	case truthy(row["operation_stop"]):
		return nil, fmt.Errorf("Finish stopping this operation before updating its connector")
	case truthy(row["recovery"]) || truthy(row["engine_update"]):
		return nil, fmt.Errorf("Resume service recovery or engine update before updating its connector")
	case row["binding"] == nil || row["state"] == "draft":
		return nil, fmt.Errorf("Complete connector setup before updating it")
	}
	nodeID := strOf(row["node_id"])
	node, err := m.node(ctx, nodeID, false)
	if err != nil {
		return nil, err
	}
	if runtimes(node)["app-data-clone"] != "1" {
		return nil, fmt.Errorf("Update Fleet on this node to preserve connector state during an update")
	}
	scope := "model-" + id
	upd := mapOf(row["connector_update"])
	if upd == nil {
		c, err := m.connector(ctx)
		if err != nil {
			return nil, err
		}
		digest, err := m.nodes.stage(ctx, nodeID, c.Tree)
		if err != nil {
			return nil, err
		}
		if digest == mapOf(row["binding"])["revision"] {
			return row, nil
		}
		state, err := m.nodes.status(ctx, nodeID)
		if err != nil {
			return nil, err
		}
		if findInstance(state, digest, scope) != nil {
			return nil, fmt.Errorf("This connector revision already has an instance; inspect it in Fleet before updating")
		}
		if row["engine_idle"] != nil && row["state"] == "ready" {
			if row, err = m.recoverLocked(ctx, row); err != nil {
				return nil, err
			}
		}
		op, err := m.nodes.submit(ctx, nodeID, "install", digest, scope, 0, "", nil)
		if err != nil {
			return nil, err
		}
		if _, err := m.nodes.wait(ctx, nodeID, op); err != nil {
			return nil, err
		}
		upd = map[string]any{"source": clone(row["binding"]), "target_revision": digest}
		row["connector_update"], row["state"] = upd, "stopping"
		if row, err = m.save(ctx, row); err != nil {
			return nil, err
		}
	}
	// Resume the saved target even if the controller has since updated.
	source, digest := mapOf(upd["source"]), strOf(upd["target_revision"])
	state, err := m.nodes.status(ctx, nodeID)
	if err != nil {
		return nil, err
	}
	instance, dead, err := boundInstance(state, source, scope, false)
	if err != nil {
		return nil, err
	}
	if !dead {
		if err := m.drainBinding(ctx, source); err != nil {
			return nil, err
		}
		op, err := m.nodes.submit(ctx, nodeID, "stop", strOf(source["revision"]), scope, intOf(source["generation"]), "", nil)
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, nodeID, op); err != nil {
			return nil, err
		}
		if instance, dead, err = boundInstance(state, source, scope, false); err != nil {
			return nil, err
		}
	}
	if !dead {
		return nil, fmt.Errorf("The old connector has not stopped; its state was not copied")
	}
	origin := map[string]any{"digest": source["revision"], "generation": instance["generation"]}
	current := findInstance(state, digest, scope)
	if current == nil || intOf(current["generation"]) == 0 {
		op, err := m.nodes.submit(ctx, nodeID, "clone_data", digest, scope, 0, "", map[string]any{"data_source": origin})
		if err != nil {
			return nil, err
		}
		if state, err = m.nodes.wait(ctx, nodeID, op); err != nil {
			return nil, err
		}
		current = findInstance(state, digest, scope)
	}
	if current == nil || !bytes.Equal(Compact(current["data_source"]), Compact(origin)) || current["app_id"] != "model-service" || intOf(current["generation"]) > 1 {
		return nil, fmt.Errorf("Updated connector identity changed; inspect its exact Fleet binding")
	}
	row["binding"] = bindingOf(nodeID, current, digest)
	binding, err := m.ensure(ctx, row, "binding", nil, "")
	if err != nil {
		return nil, err
	}
	result, err := m.rpc(ctx, binding, "status", nil)
	if err != nil {
		return nil, err
	}
	if result["config_revision"] != row["config_revision"] {
		return nil, fmt.Errorf("Updated connector configuration differs; it was not published")
	}
	if row["mode"] == "managed" && row["engine_binding"] != nil {
		state, err := m.nodes.status(ctx, nodeID)
		if err != nil {
			return nil, err
		}
		engine, dead, err := boundInstance(state, mapOf(row["engine_binding"]), "engine-"+id, false)
		if err != nil {
			return nil, err
		}
		if dead || engine["state"] != "ready" {
			return nil, fmt.Errorf("Owned engine is no longer ready; inspect it before resuming the connector update")
		}
	}
	if row["engine_idle"] != nil {
		if err := m.resetFence(ctx, binding, result); err != nil {
			return nil, err
		}
	}
	resumed, err := m.rpc(ctx, binding, "resume", map[string]any{"config_revision": row["config_revision"]})
	if err != nil {
		return nil, err
	}
	if resumed["config_revision"] != row["config_revision"] || resumed["accepting"] != true {
		return nil, fmt.Errorf("Updated connector has not confirmed readiness; resume its update")
	}
	row["binding"], row["state"], row["connector_update"] = binding, "ready", nil
	return m.save(ctx, row)
}

func (m *Manager) Remove(ctx context.Context, id string, revision any) (map[string]any, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	defer unlock()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["mode"] == "group" {
		return nil, fmt.Errorf("Manage this model through its original group lifecycle")
	}
	if intOf(row["revision"]) != intOf(revision) {
		return nil, fmt.Errorf("Service changed. Refresh before removing it.")
	}
	if row["state"] != "stopped" && row["state"] != "draft" {
		return nil, fmt.Errorf("Stop this service before removing it")
	}
	// A draft keeps its connector running; stop it so the id can be reused.
	_ = m.releaseScopes(ctx, strOf(row["node_id"]), id)
	return m.dir.remove(ctx, id, revision)
}

func (m *Manager) SetRunning(ctx context.Context, id string, running bool) (Row, error) {
	unlock, err := m.lock(id)
	if err != nil {
		return nil, err
	}
	locked := true
	defer func() {
		if locked {
			unlock()
		}
	}()
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	if row["mode"] == "group" {
		if running {
			if row["state"] == "ready" {
				return row, nil
			}
			return nil, fmt.Errorf("Continue or create an original model group; individual ranks cannot be started")
		}
		if _, err := m.Groups(ctx, "stop", strOf(row["group_id"])); err != nil {
			return nil, err
		}
		return m.dir.deployment(ctx, id)
	}
	switch {
	case truthy(row["operation_stop"]):
		return nil, fmt.Errorf("Finish stopping this operation before changing service state")
	case truthy(row["recovery"]):
		return nil, fmt.Errorf("Resume service recovery before changing service state")
	case truthy(row["connector_update"]) || truthy(row["engine_update"]):
		return nil, fmt.Errorf("Resume the pending update before changing service state")
	}
	if running {
		switch {
		case row["state"] == "ready":
			return m.wake(ctx, row)
		case row["state"] == "stopping":
			return nil, fmt.Errorf("Finish stopping this service before restarting it")
		case row["mode"] == "managed":
			return m.startManaged(ctx, row)
		case row["state"] == "draft":
			return nil, fmt.Errorf("Complete this service’s endpoint configuration first")
		}
		binding, err := m.ensure(ctx, row, "binding", nil, "")
		if err != nil {
			return nil, err
		}
		result, err := m.rpc(ctx, binding, "status", nil)
		if err != nil {
			return nil, err
		}
		row["binding"], row["config_revision"], row["state"] = binding, result["config_revision"], "ready"
		return m.save(ctx, row)
	}
	if row, err = m.cancelIdle(ctx, row); err != nil {
		return nil, err
	}
	if row["binding"] == nil {
		return nil, fmt.Errorf("Check the unfinished setup in Fleet")
	}
	binding := mapOf(row["binding"])
	state, err := m.nodes.status(ctx, strOf(row["node_id"]))
	if err != nil {
		return nil, err
	}
	_, dead, err := boundInstance(state, binding, "model-"+id, false)
	if err != nil {
		return nil, err
	}
	row["state"] = "stopping"
	if row, err = m.save(ctx, row); err != nil {
		return nil, err
	}
	if !dead {
		// Stop new admissions before waiting; an interrupted stop is durable.
		if err := m.drainBinding(ctx, binding); err != nil {
			return nil, err
		}
	}
	if row, err = m.stopBinding(ctx, row, "binding", "model-"+id); err != nil {
		return nil, err
	}
	if row["mode"] == "managed" && row["engine_binding"] != nil {
		if row, err = m.stopBinding(ctx, row, "engine_binding", "engine-"+id); err != nil {
			return nil, err
		}
	}
	row["state"] = "stopped"
	return m.save(ctx, row)
}
