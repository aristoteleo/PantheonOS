package modelservices

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/hosted"
)

// Service is the hosted model-services-management App: the nine Agent tools
// (pantheon/models/management_tools.py) and the desktop's Model Services
// operations (services_api.py) for each owner's cloud deployment.
type Service struct {
	Bus hosted.Bus
	Hub *hosted.Hub
	// Connector is the model connector source of a deployment's release.
	Connector func(ctx context.Context, fleet, deployment string) (*Connector, error)
	// State is the controller's private state root for hosted services.
	State string
	// JoinToken mints a single-use join token for a new (Modal) node.
	JoinToken func(fleet string) (string, error)
	// Revoke removes a node's identity from the owner's Fleet.
	Revoke func(fleet, node string) error

	mu     sync.Mutex
	locks  Locks
	tasks  map[string]*Tasks
	grants map[string]*Grants
}

// LongCall bounds an operation that outlives its caller's request: engine
// starts may take most of an hour and are never abandoned half-way.
const LongCall = 65 * time.Minute

func (s *Service) manager(c *hosted.Call, secret string) *Manager {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.tasks == nil {
		s.tasks, s.grants = map[string]*Tasks{}, map[string]*Grants{}
	}
	if s.tasks[c.Fleet] == nil {
		s.tasks[c.Fleet], s.grants[c.Fleet] = &Tasks{}, &Grants{}
	}
	fleet, deployment := c.Fleet, c.Deployment
	m := &Manager{nodes: nodes{bus: s.Bus, fleet: fleet}, dir: directory{hub: s.Hub, fleet: fleet, secret: secret},
		locks: &s.locks, tasks: s.tasks[fleet], grants: s.grants[fleet],
		plans:     &Plans{Root: filepath.Join(s.State, "model-plans")},
		groupRoot: filepath.Join(s.State, "model-groups"),
		connector: func(ctx context.Context) (*Connector, error) {
			if s.Connector == nil {
				return nil, fmt.Errorf("The model connector source is unavailable")
			}
			return s.Connector(ctx, fleet, deployment)
		}}
	if s.JoinToken != nil {
		m.joinToken = func() (string, error) { return s.JoinToken(fleet) }
	}
	if s.Revoke != nil {
		m.revoke = func(node string) error { return s.Revoke(fleet, node) }
	}
	return m
}

// hubSecret is the name of the owner's Hub credential in the App's config.
func hubSecret(c *hosted.Call) (string, error) {
	var backend struct {
		Credentials struct {
			Hub struct {
				Secret string `json:"$secret"`
			} `json:"hub"`
		} `json:"credentials"`
	}
	if err := json.Unmarshal(c.Spec.Config["backend"], &backend); err != nil || backend.Credentials.Hub.Secret == "" {
		return "", fmt.Errorf("Model management requires the owner's Hub credential")
	}
	return backend.Credentials.Hub.Secret, nil
}

// args are a call's decoded arguments (numbers kept exact).
type args map[string]any

func (a args) str(k, def string) string {
	if v, ok := a[k].(string); ok {
		return v
	}
	return def
}

func (a args) num(k string, def int64) int64 {
	if v, ok := a[k]; ok && v != nil {
		return intOf(v)
	}
	return def
}

func (a args) float(k string, def float64) float64 {
	if v, ok := a[k].(json.Number); ok {
		f, _ := v.Float64()
		return f
	}
	return def
}

// text is a number argument as the caller wrote it (Python's str()).
func (a args) text(k, def string) string {
	if v, ok := a[k].(json.Number); ok {
		return string(v)
	}
	return def
}

func (a args) flag(k string) bool { return a[k] == true }

func (a args) object(k string) map[string]any {
	if m := mapOf(a[k]); m != nil {
		return m
	}
	return map[string]any{}
}

// params lists each method's arguments (name -> required); other names are
// refused as the Python tool schema did.
var params = map[string][]string{
	"list_tools": {},
	// Agent tools
	"model_services_overview":   {},
	"modal_gpu_start":           {"service_id!", "model_id", "gpu", "lifetime_hours", "user_confirmed", "gpu_count"},
	"model_options":             {"node_id", "gpu"},
	"search_models":             {"engine!", "query!", "node_id", "gpu", "limit"},
	"deploy_model":              {"engine!", "model_id", "repo", "file", "revision", "node_id", "gpu", "lifetime_hours", "name", "user_confirmed", "gpu_count"},
	"deploy_status":             {"deployment_id!"},
	"modal_gpu_status":          {"service_id!", "model_id"},
	"modal_gpu_stop":            {"service_id!"},
	"model_service_set_running": {"deployment_id!", "running!"},
	// Desktop Model Services operations
	"model_services_list":               {},
	"model_services_modal_gpu":          {"action", "service_id", "model_id", "gpu", "lifetime_minutes", "cpu", "memory_gib", "gpu_count"},
	"model_services_deploy":             {"action", "node_id", "gpu", "engine", "repo", "revision", "file", "target", "model", "name", "deployment_id", "query", "limit", "sort", "context_length"},
	"model_services_group_deployments":  {"action", "group_id", "config"},
	"model_services_groups":             {"action", "group_id"},
	"model_services_activity":           {"deployment_id!", "action", "request_id"},
	"model_services_inference_jobs":     {"deployment_id!", "policy"},
	"model_services_inference_job":      {"ref!", "action", "policy"},
	"model_services_video_recovery":     {"ref!", "action", "ticket", "confirmation"},
	"model_services_upgrade_connector":  {"deployment_id!"},
	"model_services_upgrade_engine":     {"deployment_id!", "recipe_id!"},
	"model_services_recover":            {"deployment_id!"},
	"model_services_stop_operation":     {"deployment_id!", "revision!"},
	"model_services_engine_idle_status": {"deployment_id!"},
	"model_services_engine_idle":        {"deployment_id!", "idle_seconds!", "revision!"},
	"model_services_routes":             {"action", "route", "route_id", "revision", "requires"},
	"model_services_attach":             {"deployment_id!", "name!", "node_id!", "engine!", "endpoint!", "credential_file", "secret_ref"},
	"model_services_discover":           {"deployment_id!"},
	"model_services_publish":            {"deployment_id!", "models!", "revision!"},
	"model_services_remove":             {"deployment_id!", "revision!"},
	"model_services_set_running":        {"deployment_id!", "running!"},
	"model_services_service_models":     {"deployment_id!"},
	"model_services_set_in_chat":        {"deployment_id!", "model_id!", "enabled!", "context_limit"},
	"model_services_artifacts":          {"deployment_id!", "action", "job_id", "source", "resume"},
	"model_services_resources":          {"node_id!"},
	"model_services_snapshots":          {"deployment_id!", "action", "artifact_job_id", "resume", "job_id"},
	"model_services_speech_models":      {"deployment_id!", "action", "model_id", "resume"},
	"model_services_diffusion_models":   {"deployment_id!", "action", "model_id", "resume"},
	"model_services_llm_models":         {"deployment_id!", "action", "model_id", "resume"},
	"model_services_create_managed":     {"deployment_id!", "name!", "node_id!", "config!"},
	"model_services_engine_recipes":     {"node_id!"},
	"model_services_engines":            {"deployment_id!", "action", "recipe_id", "resume"},
	"model_services_model_operations":   {"deployment_id!", "action", "job_id", "operation", "artifact_job_id", "model_id", "pool_revision"},
}

func decodeArgs(c *hosted.Call) (args, error) {
	spec, ok := params[c.Method]
	if !ok {
		return nil, &hosted.ArgumentError{Reason: "Method " + c.Method + " not found on service model_services"}
	}
	raw := bytes.TrimSpace(c.Args)
	a := args{}
	if len(raw) > 0 && string(raw) != "null" {
		if err := decode(raw, &a); err != nil {
			return nil, &hosted.ArgumentError{Reason: c.Method + ": invalid arguments"}
		}
	}
	known := map[string]bool{}
	for _, p := range spec {
		name, required := p, false
		if p[len(p)-1] == '!' {
			name, required = p[:len(p)-1], true
		}
		known[name] = true
		if _, ok := a[name]; required && !ok {
			return nil, &hosted.ArgumentError{Reason: fmt.Sprintf("%s: missing argument %s", c.Method, name)}
		}
	}
	for k := range a {
		if !known[k] {
			return nil, &hosted.ArgumentError{Reason: fmt.Sprintf("%s: unexpected argument %s", c.Method, k)}
		}
	}
	return a, nil
}

// Call runs one method. The operation keeps running if its caller gives up:
// a node operation's outcome is reconciled by the next status call, never
// abandoned half-way or replayed.
func (s *Service) Call(ctx context.Context, c *hosted.Call) (any, error) {
	a, err := decodeArgs(c)
	if err != nil {
		return nil, err
	}
	if c.Method == "list_tools" {
		raw, _ := hosted.Manifest("model-services-management")
		var m struct {
			Provides struct {
				Tools json.RawMessage `json:"tools"`
			} `json:"provides"`
		}
		_ = json.Unmarshal(raw, &m)
		return map[string]any{"success": true, "tools": m.Provides.Tools}, nil
	}
	secret, err := hubSecret(c)
	if err != nil {
		return nil, err
	}
	m := s.manager(c, secret)
	type outcome struct {
		value any
		err   error
	}
	done := make(chan outcome, 1)
	work, cancel := context.WithTimeout(context.WithoutCancel(ctx), LongCall)
	go func() {
		defer cancel()
		v, err := s.dispatch(work, m, c.Method, a)
		done <- outcome{v, err}
	}()
	select {
	case o := <-done:
		return o.value, o.err
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func row(r Row, err error) (any, error)               { return r, err }
func result(r map[string]any, err error) (any, error) { return r, err }

func (s *Service) dispatch(ctx context.Context, m *Manager, method string, a args) (any, error) {
	id := a.str("deployment_id", "")
	switch method {
	case "model_services_overview":
		return m.overview(ctx)
	case "modal_gpu_start":
		hours := a.float("lifetime_hours", 4)
		count := a.num("gpu_count", 1)
		model, gpu := a.str("model_id", "qwen3.6-35b-a3b-fp8"), a.str("gpu", "H100")
		if !a.flag("user_confirmed") {
			return map[string]any{"started": false, "message": fmt.Sprintf("Ask the user to approve this GPU launch with notify_user "+
				"(model %s, %dx %s, up to %s h), then call again with user_confirmed=True.", model, count, gpu, a.text("lifetime_hours", "4"))}, nil
		}
		if !(hours > 0 && hours <= 24) {
			return nil, fmt.Errorf("lifetime_hours must be between 0 and 24")
		}
		return result(m.ModalStart(ctx, a.str("service_id", ""), model, gpu, int64(hours*60), count))
	case "model_options":
		return result(m.Options(ctx, a.str("node_id", ""), a.str("gpu", "")))
	case "search_models":
		return result(m.Search(ctx, a.str("engine", ""), a.str("query", ""), a.str("node_id", ""), a.str("gpu", ""), a.num("limit", 10), "popular"))
	case "deploy_model":
		return m.deployTool(ctx, a)
	case "deploy_status":
		return result(m.DeployStatus(ctx, id))
	case "modal_gpu_status":
		return result(m.ModalAdvance(ctx, a.str("service_id", ""), a.str("model_id", "qwen3.6-35b-a3b-fp8"), nil, nil))
	case "modal_gpu_stop":
		return result(m.ModalStop(ctx, a.str("service_id", "")))
	case "model_service_set_running":
		r, err := m.SetRunning(ctx, id, a.flag("running"))
		if err != nil {
			return nil, err
		}
		return map[string]any{"deployment_id": id, "state": r["state"]}, nil

	case "model_services_list":
		rows, err := m.dir.deployments(ctx)
		return map[string]any{"deployments": rows}, err
	case "model_services_modal_gpu":
		return m.modalGPU(ctx, a)
	case "model_services_deploy":
		return m.deploy(ctx, a)
	case "model_services_group_deployments":
		return result(m.GroupDeployments(ctx, a.str("action", "list"), a.str("group_id", ""), a["config"]))
	case "model_services_groups":
		return result(m.Groups(ctx, a.str("action", "list"), a.str("group_id", "")))
	case "model_services_activity":
		return result(m.Activity(ctx, id, a.str("action", "list"), a.str("request_id", "")))
	case "model_services_inference_jobs":
		return result(m.InferenceJobs(ctx, id, a.str("policy", "direct_only")))
	case "model_services_inference_job":
		return result(m.JobOperation(ctx, a.str("ref", ""), a.str("action", "status"), a.str("policy", "relay_allowed")))
	case "model_services_video_recovery":
		return result(m.VideoRecovery(ctx, a.str("ref", ""), a.str("action", "inspect"), a.str("ticket", ""), a.str("confirmation", "")))
	case "model_services_upgrade_connector":
		return row(m.UpgradeConnector(ctx, id))
	case "model_services_upgrade_engine":
		return row(m.UpgradeEngine(ctx, id, a.str("recipe_id", "")))
	case "model_services_recover":
		return row(m.Recover(ctx, id))
	case "model_services_stop_operation":
		return row(m.StopOperation(ctx, id, a["revision"]))
	case "model_services_engine_idle_status":
		return result(m.EngineIdleStatus(ctx, id))
	case "model_services_engine_idle":
		return row(m.SetEngineIdle(ctx, id, a["idle_seconds"], a["revision"]))
	case "model_services_routes":
		revision := a["revision"]
		if revision == nil {
			revision = number(0)
		}
		return result(m.dir.routeOperation(ctx, a.str("action", "list"), mapOf(a["route"]), a.str("route_id", ""), revision, a["requires"]))
	case "model_services_attach":
		return row(m.Attach(ctx, id, a.str("name", ""), a.str("node_id", ""), a.str("engine", ""), a.str("endpoint", ""),
			a.str("credential_file", ""), a.str("secret_ref", "")))
	case "model_services_discover":
		return result(m.Discover(ctx, id))
	case "model_services_publish":
		return row(m.Publish(ctx, id, listOf(a["models"]), a["revision"]))
	case "model_services_remove":
		return result(m.Remove(ctx, id, a["revision"]))
	case "model_services_set_running":
		running := a.flag("running")
		r, err := m.SetRunning(ctx, id, running)
		if err == nil && running && r["state"] == "ready" && truthy(r["models"]) {
			// A started service states its models' current context/capabilities.
			if synced, err := m.SyncModels(ctx, id, nil); err == nil {
				r = synced
			}
		}
		return r, err
	case "model_services_service_models":
		return result(m.ServiceModels(ctx, id))
	case "model_services_set_in_chat":
		return row(m.SetInChat(ctx, id, a.str("model_id", ""), a.flag("enabled"), a["context_limit"]))
	case "model_services_artifacts":
		return result(m.Artifacts(ctx, id, a.str("action", "list"), a.str("job_id", ""), a["source"], a.flag("resume")))
	case "model_services_resources":
		return result(m.Resources(ctx, a.str("node_id", "")))
	case "model_services_snapshots":
		return result(m.Snapshots(ctx, id, a.str("action", "jobs"), a.str("artifact_job_id", ""), a.flag("resume"), a.str("job_id", "")))
	case "model_services_speech_models":
		return result(m.ModelCache(ctx, "speech", id, a.str("action", "catalog"), a.str("model_id", ""), a.flag("resume")))
	case "model_services_diffusion_models":
		return result(m.ModelCache(ctx, "diffusion", id, a.str("action", "catalog"), a.str("model_id", ""), a.flag("resume")))
	case "model_services_llm_models":
		return result(m.ModelCache(ctx, "llm", id, a.str("action", "catalog"), a.str("model_id", ""), a.flag("resume")))
	case "model_services_create_managed":
		return row(m.CreateManaged(ctx, id, a.str("name", ""), a.str("node_id", ""), a["config"]))
	case "model_services_engine_recipes":
		return result(m.EngineRecipes(ctx, a.str("node_id", "")))
	case "model_services_engines":
		return result(m.Engines(ctx, id, a.str("action", "catalog"), a.str("recipe_id", ""), a.flag("resume")))
	case "model_services_model_operations":
		return result(m.ModelOperations(ctx, id, a.str("action", "status"), a.str("job_id", ""), a.str("operation", ""),
			a.str("artifact_job_id", ""), a.str("model_id", ""), a["pool_revision"], "", nil))
	}
	return nil, &hosted.ArgumentError{Reason: "Method " + method + " not found on service model_services"}
}

func (m *Manager) llmCatalog(ctx context.Context) ([]map[string]any, error) {
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	return c.LLMCatalog(), nil
}

func catalogSize(entry map[string]any) int64 {
	var total int64
	for _, f := range listOf(entry["files"]) {
		total += intOf(mapOf(f)["size"])
	}
	return total
}

// overview is model_services_overview: the owner's services, routes,
// running Modal GPU services and the launchable catalog.
func (m *Manager) overview(ctx context.Context) (any, error) {
	if _, err := m.SettleExpired(ctx, nil, nil, nil); err != nil {
		return nil, err
	}
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	deployments := []any{}
	for _, d := range rows {
		models := []any{}
		for _, raw := range listOf(d["models"]) {
			x := mapOf(raw)
			name := x["name"]
			if !truthy(name) {
				name = x["id"]
			}
			models = append(models, map[string]any{"ref": fmt.Sprintf("fleet-model://%s/%s", d["deployment_id"], x["id"]),
				"name": name, "operations": x["operations"], "tools": x["tools"], "context": x["context"]})
		}
		node := d["node_name"]
		if !truthy(node) {
			node = d["node_id"]
		}
		deployments = append(deployments, map[string]any{"deployment_id": d["deployment_id"], "name": d["name"], "state": d["state"],
			"node": node, "engine": d["engine"], "mode": d["mode"], "models": models})
	}
	routeRows, err := m.dir.routes(ctx)
	if err != nil {
		return nil, err
	}
	routes := []any{}
	for _, raw := range routeRows {
		r := mapOf(raw)
		routes = append(routes, map[string]any{"ref": "fleet-route://" + strOf(r["route_id"]), "name": r["name"], "candidates": r["candidates"]})
	}
	launches, err := m.modalServices(ctx)
	if err != nil {
		return nil, err
	}
	llm, err := m.llmCatalog(ctx)
	if err != nil {
		return nil, err
	}
	catalog := []any{}
	for _, x := range llm {
		catalog = append(catalog, map[string]any{"model_id": x["id"], "name": x["display_name"], "context_length": x["context_length"],
			"weights_gb": math.Round(float64(catalogSize(x))/1e9*10) / 10, "capabilities": x["capabilities"]})
	}
	return map[string]any{"deployments": deployments, "routes": routes, "modal_available": true, "modal_unavailable_reason": nil,
		"modal_gpu": launches, "catalog": catalog, "gpus": ModalGPUs}, nil
}

func (m *Manager) deployTool(ctx context.Context, a args) (any, error) {
	engine, modelID, repo, node, gpu := a.str("engine", ""), a.str("model_id", ""), a.str("repo", ""), a.str("node_id", ""), a.str("gpu", "")
	count := a.num("gpu_count", 0)
	if node == "" && !a.flag("user_confirmed") {
		shown := gpu
		if shown == "" {
			shown = "H100"
		}
		what := modelID
		if what == "" {
			what = repo
		}
		times := " (count from model_options/search)"
		if count != 0 {
			times = fmt.Sprintf(" x%d", count)
		}
		return map[string]any{"started": false, "message": fmt.Sprintf("Ask the user to approve this Modal launch with notify_user "+
			"(%s, %s, GPU %s%s, up to %s h), then call again with user_confirmed=True.", engine, what, shown, times, a.text("lifetime_hours", "4"))}, nil
	}
	var model any
	switch {
	case modelID != "":
		model = map[string]any{"catalog_id": modelID}
	case repo != "":
		resolved, err := m.Resolve(ctx, engine, repo, a.str("revision", ""), a.str("file", ""))
		if err != nil {
			return nil, err
		}
		model = resolved["model"]
	default:
		return nil, fmt.Errorf("Give a catalog model_id or a Hugging Face repo")
	}
	target := map[string]any{"kind": "node", "node_id": node}
	if node == "" {
		if gpu == "" {
			gpu = "H100"
		}
		target = map[string]any{"kind": "modal", "gpu": gpu, "lifetime_hours": json.Number(a.text("lifetime_hours", "4"))}
		if count != 0 {
			target["gpu_count"] = number(count)
		}
	}
	return result(m.Deploy(ctx, target, engine, model, a.str("name", ""), nil))
}

func (m *Manager) modalGPU(ctx context.Context, a args) (any, error) {
	service, model, gpu := a.str("service_id", ""), a.str("model_id", "qwen3.6-35b-a3b-fp8"), a.str("gpu", "H100")
	lifetime, count := a.num("lifetime_minutes", 240), a.num("gpu_count", 1)
	switch a.str("action", "list") {
	case "catalog":
		llm, err := m.llmCatalog(ctx)
		if err != nil {
			return nil, err
		}
		gpus := append([]string{}, ModalGPUs...)
		sort.Strings(gpus)
		models := []any{}
		for _, x := range llm {
			entry := map[string]any{"size": catalogSize(x), "supported_gpus": x["supported_gpus"]}
			if !truthy(x["supported_gpus"]) {
				entry["supported_gpus"] = gpus
			}
			for _, k := range []string{"id", "display_name", "context_length", "maximum_context_length", "minimum_gpu_memory_bytes", "capabilities"} {
				entry[k] = x[k]
			}
			models = append(models, entry)
		}
		return map[string]any{"models": models, "gpus": gpus, "node_options": map[string]any{"gpus": append(gpus, "none"),
			"cpu": NodeCPU, "memory_gib": NodeMemoryGiB, "gpu_counts": gpuCounts}}, nil
	case "list":
		launches, err := m.modalServices(ctx)
		if err != nil {
			return nil, err
		}
		if _, err := m.SettleExpired(ctx, launches, nil, nil); err != nil {
			return nil, err
		}
		return map[string]any{"services": launches}, nil
	case "start":
		return result(m.ModalStart(ctx, service, model, gpu, lifetime, count))
	case "advance":
		return result(m.ModalAdvance(ctx, service, model, nil, nil))
	case "stop":
		return result(m.ModalStop(ctx, service))
	case "start_node":
		return result(m.ModalStartNode(ctx, service, gpu, lifetime, a["cpu"], a["memory_gib"], count))
	case "stop_node":
		return result(m.ModalStopNode(ctx, service))
	}
	return nil, fmt.Errorf("Unsupported Modal GPU service action")
}

func (m *Manager) deploy(ctx context.Context, a args) (any, error) {
	node, gpu, engine := a.str("node_id", ""), a.str("gpu", ""), a.str("engine", "")
	switch a.str("action", "options") {
	case "options":
		return result(m.Options(ctx, node, gpu))
	case "search":
		return result(m.Search(ctx, engine, a.str("query", ""), node, gpu, a.num("limit", 20), a.str("sort", "popular")))
	case "featured":
		return result(m.Featured(ctx, node, gpu))
	case "resolve":
		return result(m.Resolve(ctx, engine, a.str("repo", ""), a.str("revision", ""), a.str("file", "")))
	case "deploy":
		return result(m.Deploy(ctx, a.object("target"), engine, a.object("model"), a.str("name", ""), a["context_length"]))
	case "status":
		return result(m.DeployStatus(ctx, a.str("deployment_id", "")))
	}
	return nil, fmt.Errorf("Unsupported model deploy action")
}
