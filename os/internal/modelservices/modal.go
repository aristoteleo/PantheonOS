package modelservices

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Platform Modal GPU model services: one GPU node, one managed catalog LLM,
// one route (formerly pantheonos/models/modal_gpu.py). The Hub launches the
// node with a one-use join token this controller mints; afterwards it is an
// ordinary managed deployment. advance is idempotent: it observes and takes
// at most the next step.

const modalRecipe = "sglang-0.5.20-linux-amd64-node"

var (
	ModalGPUs      = []string{"A100-80GB", "B200", "H100", "H200", "L40S"}
	gpuCounts      = []int64{1, 2, 4, 8}
	NodeCPU        = []int64{2, 4, 8, 16, 32, 64}
	NodeMemoryGiB  = []int64{8, 16, 32, 64, 128, 256, 512}
	serviceIDRE    = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,40}$`)
	nodeHintCharRE = regexp.MustCompile(`[^a-z0-9-]+`)
)

func validGPU(g string) bool {
	for _, x := range ModalGPUs {
		if x == g {
			return true
		}
	}
	return false
}

func validCount(n int64) bool {
	for _, x := range gpuCounts {
		if x == n {
			return true
		}
	}
	return false
}

func checkService(id string) error {
	if !serviceIDRE.MatchString(id) {
		return fmt.Errorf("Service id must be lowercase letters, digits and dashes")
	}
	return nil
}

func (m *Manager) modalServices(ctx context.Context) ([]map[string]any, error) {
	var out struct {
		Services []map[string]any `json:"services"`
	}
	err := m.dir.request(ctx, "GET", "/api/model-services/modal-gpu", nil, &out)
	return out.Services, err
}

func (m *Manager) records(ctx context.Context) ([]map[string]any, error) {
	return m.nodes.bus.Records(ctx, m.nodes.fleet)
}

// SettleExpired marks Modal services stopped once their launch ended; their
// node never returns, so it is revoked.
func (m *Manager) SettleExpired(ctx context.Context, launches []map[string]any, nodes []map[string]any, rows []Row) ([]string, error) {
	var err error
	if launches == nil {
		if launches, err = m.modalServices(ctx); err != nil {
			return nil, err
		}
	}
	live := map[any]bool{}
	for _, l := range launches {
		live[l["service_id"]] = true
	}
	if nodes == nil {
		if nodes, err = m.records(ctx); err != nil {
			return nil, err
		}
	}
	online := map[any]bool{}
	for _, n := range nodes {
		online[n["node_id"]] = true
	}
	if rows == nil {
		if rows, err = m.dir.deployments(ctx); err != nil {
			return nil, err
		}
	}
	settled := []string{}
	for _, row := range rows {
		id := strOf(row["deployment_id"])
		if !strings.HasPrefix(id, "modal-") || live[strings.TrimPrefix(id, "modal-")] || row["state"] == "stopped" || row["state"] == "draft" || online[row["node_id"]] {
			continue
		}
		if m.revoke != nil {
			_ = m.revoke(strOf(row["node_id"])) // already revoked or unknown: the sandbox is gone
		}
		row = clone(row).(map[string]any)
		row["state"] = "stopped"
		if _, err := m.save(ctx, row); err != nil {
			return nil, err
		}
		settled = append(settled, id)
	}
	return settled, nil
}

func nodeFor(nodes []map[string]any, serviceID string) map[string]any {
	label := "svc-" + serviceID
	var best map[string]any
	for _, n := range nodes {
		labelled := false
		for _, l := range listOf(n["labels"]) {
			labelled = labelled || l == label
		}
		status := strOf(mapOf(n["state"])["status"])
		if status == "" {
			status = "online"
		}
		if labelled && status == "online" && (best == nil || strOf(n["last_seen"]) > strOf(best["last_seen"])) {
			best = n
		}
	}
	return best
}

// nodeGPUs are every NVIDIA GPU of a node; a model service leases all of them.
func nodeGPUs(node map[string]any) ([]map[string]any, error) {
	var cuda []map[string]any
	for _, a := range listOf(mapOf(caps(node)["resources"])["accelerators"]) {
		acc := mapOf(a)
		if acc["backend"] == "cuda" && strings.HasPrefix(strOf(acc["id"]), "GPU-") {
			cuda = append(cuda, acc)
		}
	}
	sort.Slice(cuda, func(i, j int) bool { return strOf(cuda[i]["id"]) < strOf(cuda[j]["id"]) })
	if !validCount(int64(len(cuda))) {
		return nil, fmt.Errorf("The Modal GPU node must report 1, 2, 4 or 8 NVIDIA GPUs")
	}
	return cuda, nil
}

func launchBody(serviceID, gpu string, count int64, token string, lifetime int64, sizes map[string]any) map[string]any {
	body := map[string]any{"service_id": serviceID, "gpu": gpu, "join_token": token, "lifetime_minutes": lifetime}
	if count != 1 {
		body["gpu_count"] = count
	}
	for k, v := range sizes {
		body[k] = v
	}
	return body
}

func machineLabel(gpu string, count int64) string {
	if count == 1 {
		return gpu
	}
	return fmt.Sprintf("%d× %s", count, gpu)
}

// gpuMismatch explains when a GPU cannot run a model (e.g. FP8 kernels).
func gpuMismatch(selected map[string]any, gpuName string) string {
	supported := listOf(selected["supported_gpus"])
	if len(supported) == 0 {
		for _, g := range ModalGPUs {
			supported = append(supported, g)
		}
	}
	names := make([]string, 0, len(supported))
	for _, g := range supported {
		head := strings.ToLower(strings.Fields(strings.ReplaceAll(strOf(g), "-", " "))[0])
		if strings.Contains(strings.ToLower(gpuName), head) {
			return ""
		}
		names = append(names, strOf(g))
	}
	return fmt.Sprintf("%s needs %s; this node has %s", selected["display_name"], strings.Join(names, " or "), gpuName)
}

func (m *Manager) mintJoin() (string, error) {
	if m.joinToken == nil {
		return "", fmt.Errorf("Fleet is not configured on this Agent")
	}
	return m.joinToken()
}

func (m *Manager) ModalStart(ctx context.Context, serviceID, modelID, gpu string, lifetime, count int64) (map[string]any, error) {
	if err := checkService(serviceID); err != nil {
		return nil, err
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	selected, err := c.LLM(modelID)
	if err != nil {
		return nil, err
	}
	if !validGPU(gpu) {
		return nil, fmt.Errorf("Choose %s", strings.Join(ModalGPUs, ", "))
	}
	if !validCount(count) {
		return nil, fmt.Errorf("A machine has 1, 2, 4 or 8 GPUs")
	}
	if message := gpuMismatch(selected, gpu); message != "" {
		return nil, fmt.Errorf("%s", message)
	}
	if err := m.launch(ctx, serviceID, gpu, count, lifetime, nil); err != nil {
		return nil, err
	}
	return m.ModalAdvance(ctx, serviceID, modelID, nil, nil)
}

// launch asks the Hub for the sandbox once, with a fresh one-use join token.
func (m *Manager) launch(ctx context.Context, serviceID, gpu string, count, lifetime int64, sizes map[string]any) error {
	launches, err := m.modalServices(ctx)
	if err != nil {
		return err
	}
	for _, s := range launches {
		if s["service_id"] == serviceID {
			return nil
		}
	}
	token, err := m.mintJoin()
	if err != nil {
		return err
	}
	return m.dir.request(ctx, "POST", "/api/model-services/modal-gpu", launchBody(serviceID, gpu, count, token, lifetime, sizes), nil)
}

// retire forgets a previous launch whose node is gone.
func (m *Manager) retire(ctx context.Context, row Row, nodes []map[string]any) error {
	for _, n := range nodes {
		if n["node_id"] == row["node_id"] {
			return fmt.Errorf("The previous GPU node is still online; stop it before starting again")
		}
	}
	if m.revoke != nil {
		if err := m.revoke(strOf(row["node_id"])); err != nil {
			return err
		}
	}
	routes, err := m.dir.routes(ctx)
	if err != nil {
		return err
	}
	for _, r := range routes {
		route := mapOf(r)
		for _, cand := range listOf(route["candidates"]) {
			if mapOf(cand)["deployment_id"] == row["deployment_id"] {
				if _, err := m.dir.routeOperation(ctx, "delete", nil, strOf(route["route_id"]), route["revision"], nil); err != nil {
					return err
				}
				break
			}
		}
	}
	if row["state"] != "stopped" && row["state"] != "draft" {
		row = clone(row).(map[string]any)
		row["state"] = "stopped"
		if row, err = m.save(ctx, row); err != nil {
			return err
		}
	}
	_, err = m.dir.remove(ctx, strOf(row["deployment_id"]), row["revision"])
	return err
}

// ModalAdvance takes the next step of a Modal model service; model is an
// explicit custom (Hugging Face pinned) entry, else modelID names a catalog model.
func (m *Manager) ModalAdvance(ctx context.Context, serviceID, modelID string, model map[string]any, contextLength any) (map[string]any, error) {
	if err := checkService(serviceID); err != nil {
		return nil, err
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	var key any = modelID
	if model != nil {
		key = model
	}
	selected, err := c.LLM(key)
	if err != nil {
		return nil, err
	}
	modelID = strOf(selected["id"])
	dep := "modal-" + serviceID
	launches, err := m.modalServices(ctx)
	if err != nil {
		return nil, err
	}
	var launched map[string]any
	for _, s := range launches {
		if s["service_id"] == serviceID {
			launched = s
		}
	}
	nodes, err := m.records(ctx)
	if err != nil {
		return nil, err
	}
	node := nodeFor(nodes, serviceID)
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	var row Row
	for _, r := range rows {
		if r["deployment_id"] == dep {
			row = r
		}
	}
	count := intOf(launched["gpu_count"])
	if count == 0 {
		count = 1
	}
	base := map[string]any{"service_id": serviceID, "model_id": modelID, "gpu": launched["gpu"], "gpu_count": number(count),
		"expires_at": launched["expires_at"], "route": "fleet-route://" + serviceID}
	with := func(extra map[string]any) map[string]any {
		out := clone(base).(map[string]any)
		for k, v := range extra {
			out[k] = v
		}
		return out
	}
	if launched == nil {
		var only []Row
		if row != nil {
			only = []Row{row}
		} else {
			only = []Row{}
		}
		if _, err := m.SettleExpired(ctx, []map[string]any{}, nodes, only); err != nil {
			return nil, err
		}
		return with(map[string]any{"phase": "stopped", "ready": false}), nil
	}
	if node == nil {
		return with(map[string]any{"phase": "starting_node", "ready": false}), nil
	}
	if row != nil && row["node_id"] != node["node_id"] {
		if err := m.retire(ctx, row, nodes); err != nil {
			return nil, err
		}
		row = nil
	}
	if row == nil {
		gpus, err := nodeGPUs(node)
		if err != nil {
			return nil, err
		}
		name := strOf(gpus[0]["name"])
		if name == "" {
			name = strOf(launched["gpu"])
		}
		if message := gpuMismatch(selected, name); message != "" {
			return with(map[string]any{"phase": "failed", "ready": false, "error": message}), nil
		}
		tp := int64(len(gpus))
		need := RankGPUBytes(selected, tp)
		for _, g := range gpus {
			if intOf(mapOf(g["memory"])["total_bytes"])*9/10 < need {
				return with(map[string]any{"phase": "failed", "ready": false, "error": fmt.Sprintf(
					"%s needs about %.0f GB on each of %d GPU(s); choose more or larger GPUs", selected["display_name"], float64(need)/(1<<30), tp)}), nil
			}
		}
		context := intOf(selected["context_length"])
		if contextLength != nil && intOf(contextLength) != 0 {
			context = intOf(contextLength)
		}
		if context < 512 || context > intOf(selected["maximum_context_length"]) {
			return with(map[string]any{"phase": "failed", "ready": false, "error": fmt.Sprintf(
				"Context must be between 512 and %s tokens", commas(intOf(selected["maximum_context_length"])))}), nil
		}
		devices := []any{}
		for _, g := range gpus {
			devices = append(devices, map[string]any{"id": g["id"], "backend": "cuda",
				"memory_bytes": number(intOf(mapOf(g["memory"])["total_bytes"]) * 9 / 10), "exclusive": true})
		}
		config := map[string]any{"recipe_id": modalRecipe, "model_recipe_id": modelID, "context_length": number(context),
			"parallel": number(4), "keep_alive_seconds": number(0), "load_policy": "resident",
			"resources": map[string]any{"memory_bytes": selected["minimum_memory_bytes"], "devices": devices}}
		if model != nil {
			config["model_manifest"] = selected
		}
		if tp > 1 {
			config["tensor_parallel_size"] = number(tp)
		}
		if row, err = m.CreateManaged(ctx, dep, fmt.Sprintf("%s on Modal %s", selected["display_name"], machineLabel(strOf(launched["gpu"]), tp)),
			strOf(node["node_id"]), config); err != nil {
			return nil, err
		}
	}
	if row["state"] == "ready" {
		return m.modalPublish(ctx, row, selected, node, base)
	}
	manifest := mapOf(mapOf(row["managed"])["model_manifest"])
	args := func(extra map[string]any) map[string]any {
		out := map[string]any{"model_id": modelID}
		if manifest != nil {
			out["manifest"] = manifest
		}
		for k, v := range extra {
			out[k] = v
		}
		return out
	}
	weights, err := m.rpc(ctx, row["binding"], "llm_models", args(map[string]any{"action": "status"}))
	if err != nil {
		return nil, err
	}
	if weights["ready"] != true {
		jobs, err := m.rpc(ctx, row["binding"], "llm_models", map[string]any{"action": "jobs"})
		if err != nil {
			return nil, err
		}
		var job map[string]any
		for _, j := range listOf(jobs["jobs"]) {
			if mapOf(j)["job_id"] == modelID {
				job = mapOf(j)
			}
		}
		if job == nil || job["state"] == "failed" || job["state"] == "cancelled" {
			if _, err := m.rpc(ctx, row["binding"], "llm_models", args(map[string]any{"action": "prepare", "resume": true})); err != nil {
				return nil, err
			}
		}
		state := strOf(job["state"])
		if state == "" {
			state = "queued"
		}
		done := job["bytes_done"]
		if done == nil {
			done = number(0)
		}
		return with(map[string]any{"phase": "downloading_weights", "ready": false, "progress": map[string]any{
			"bytes": done, "total": number(filesSize(selected)), "state": state, "error": strOf(job["error"])}}), nil
	}
	if failure, running := m.tasks.Start("modal/"+m.nodes.fleet+"/"+dep, func() error {
		_, err := m.SetRunning(context.WithoutCancel(ctx), dep, true)
		return err
	}); failure != "" && !running {
		return with(map[string]any{"phase": "failed", "ready": false, "error": truncate(failure, 300)}), nil
	}
	return with(map[string]any{"phase": "starting_engine", "ready": false}), nil
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func commas(n int64) string {
	s := fmt.Sprint(n)
	var out []string
	for len(s) > 3 {
		out = append([]string{s[len(s)-3:]}, out...)
		s = s[:len(s)-3]
	}
	return strings.Join(append([]string{s}, out...), ",")
}

func servedName(selected map[string]any) string {
	return "fleet-llm-" + strOf(LLMSource(selected)["sha256"])
}

func (m *Manager) modalPublish(ctx context.Context, row Row, selected, node, base map[string]any) (map[string]any, error) {
	model := servedName(selected)
	published := false
	for _, p := range listOf(row["models"]) {
		published = published || mapOf(p)["id"] == model
	}
	caps := mapOf(selected["capabilities"])
	contextLength := mapOf(row["managed"])["context_length"]
	var err error
	if !published {
		if row, err = m.Publish(ctx, strOf(row["deployment_id"]), []any{map[string]any{"id": model, "name": selected["display_name"],
			"operations": []any{"text"}, "tools": caps["tools"], "vision": caps["vision"], "reasoning": caps["reasoning"],
			"structured_output": true, "context": contextLength, "compute": "node"}}, row["revision"]); err != nil {
			return nil, err
		}
	}
	routes, err := m.dir.routes(ctx)
	if err != nil {
		return nil, err
	}
	var route map[string]any
	for _, r := range routes {
		if mapOf(r)["route_id"] == base["service_id"] {
			route = mapOf(r)
		}
	}
	candidate := map[string]any{"deployment_id": row["deployment_id"], "model_id": model}
	if route == nil || !same(route["candidates"], []any{candidate}) || !same(route["allowed_nodes"], []any{node["node_id"]}) {
		revision := any(number(0))
		if route != nil {
			revision = route["revision"]
		}
		if _, err := m.dir.routeOperation(ctx, "save", map[string]any{"route_id": base["service_id"],
			"name": strOf(selected["display_name"]) + " (Modal GPU)", "candidates": []any{candidate}, "allowed_nodes": []any{node["node_id"]},
			"requires": map[string]any{"operation": "text", "tools": caps["tools"], "context": contextLength},
			"revision": revision}, "", nil, nil); err != nil {
			return nil, err
		}
	}
	out := clone(base).(map[string]any)
	out["phase"], out["ready"], out["node_id"] = "ready", true, node["node_id"]
	out["model"] = modelRef(strOf(row["deployment_id"]), model)
	return out, nil
}

// ModalStop stops the engine while the node lives, ends the sandbox and
// revokes the node.
func (m *Manager) ModalStop(ctx context.Context, serviceID string) (map[string]any, error) {
	if err := checkService(serviceID); err != nil {
		return nil, err
	}
	dep := "modal-" + serviceID
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	nodes, err := m.records(ctx)
	if err != nil {
		return nil, err
	}
	node := nodeFor(nodes, serviceID)
	for _, r := range rows {
		if r["deployment_id"] == dep && r["state"] != "stopped" && r["state"] != "draft" && node != nil {
			if _, err := m.SetRunning(ctx, dep, false); err != nil {
				return nil, err
			}
		}
	}
	if err := m.dir.request(ctx, "DELETE", "/api/model-services/modal-gpu/"+serviceID, nil, nil); err != nil {
		return nil, err
	}
	if node != nil && m.revoke != nil {
		if err := m.revoke(strOf(node["node_id"])); err != nil {
			return nil, err
		}
	}
	return map[string]any{"service_id": serviceID, "phase": "stopped", "ready": false}, nil
}

func nodeServiceID(hint string) (string, error) {
	id := "node-" + strings.Trim(nodeHintCharRE.ReplaceAllString(strings.ToLower(hint), "-"), "-")
	if len(id) > 40 {
		id = id[:40]
	}
	return id, checkService(id)
}

func (m *Manager) ModalStartNode(ctx context.Context, hint, gpu string, lifetime int64, cpu, memory any, count int64) (map[string]any, error) {
	serviceID, err := nodeServiceID(hint)
	if err != nil {
		return nil, err
	}
	if gpu != "none" && !validGPU(gpu) {
		return nil, fmt.Errorf("Choose %s or none (CPU only)", strings.Join(ModalGPUs, ", "))
	}
	if !validCount(count) || (gpu == "none" && count != 1) {
		return nil, fmt.Errorf("A machine has 1, 2, 4 or 8 GPUs")
	}
	sizes := map[string]any{}
	for k, v := range map[string]any{"cpu": cpu, "memory_gib": memory} {
		if v == nil {
			continue
		}
		if !isInt(v) {
			return nil, fmt.Errorf("CPU cores and memory must be whole numbers")
		}
		sizes[k] = v
	}
	if err := m.launch(ctx, serviceID, gpu, count, lifetime, sizes); err != nil {
		return nil, err
	}
	nodes, err := m.records(ctx)
	if err != nil {
		return nil, err
	}
	node := nodeFor(nodes, serviceID)
	phase, nodeID := "starting_node", any(nil)
	if node != nil {
		phase, nodeID = "ready", node["node_id"]
	}
	return map[string]any{"service_id": serviceID, "gpu": gpu, "gpu_count": count, "phase": phase, "node_id": nodeID}, nil
}

func (m *Manager) ModalStopNode(ctx context.Context, serviceID string) (map[string]any, error) {
	if err := checkService(serviceID); err != nil {
		return nil, err
	}
	if !strings.HasPrefix(serviceID, "node-") {
		return nil, fmt.Errorf("This Modal GPU node runs a model service; stop it from Model Services")
	}
	nodes, err := m.records(ctx)
	if err != nil {
		return nil, err
	}
	node := nodeFor(nodes, serviceID)
	if err := m.dir.request(ctx, "DELETE", "/api/model-services/modal-gpu/"+serviceID, nil, nil); err != nil {
		return nil, err
	}
	if node != nil && m.revoke != nil {
		if err := m.revoke(strOf(node["node_id"])); err != nil {
			return nil, err
		}
	}
	return map[string]any{"service_id": serviceID, "phase": "stopped", "ready": false}, nil
}
