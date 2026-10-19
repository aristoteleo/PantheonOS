package modelservices

import (
	"fmt"
	"math"
	"net/netip"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Model groups run one SGLang tensor-parallel model across 2–8 Fleet nodes.
// This file holds the pure parts: the canonical peer roster
// (pantheon/models/group_network.py), the model receipt
// (apps/model-service/group_model.py), the memory and launch checks
// (sglang_runtime.py, sglang_group.py) and the rank package compiler
// (group_packager.py). Group values are decoded JSON with json.Number ints.

var (
	groupIDRE   = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
	ownerRE     = regexp.MustCompile(`^f_[a-f0-9]{16}$`)
	sha256RE    = regexp.MustCompile(`^[a-f0-9]{64}$`)
	peerNodeRE  = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
	gpuIDRE     = regexp.MustCompile(`^GPU-[A-Za-z0-9-]{1,124}$`)
	interfaceRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$`)
	operationRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`)
	imageRE     = regexp.MustCompile(`^[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}$`)
	receiptRE   = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$`)
	endpointRE  = regexp.MustCompile(`^(?:\[([^\]]+)\]:([0-9]+)|([^:]+):([0-9]+))$`)
)

var privateNets = []netip.Prefix{netip.MustParsePrefix("10.0.0.0/8"), netip.MustParsePrefix("172.16.0.0/12"),
	netip.MustParsePrefix("192.168.0.0/16"), netip.MustParsePrefix("fc00::/7")}

const sglangVersion = "0.5.20"

func private(ip netip.Addr) bool {
	for _, n := range privateNets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// keys reports whether m has exactly these keys.
func keys(m map[string]any, names ...string) bool {
	if m == nil || len(m) != len(names) {
		return false
	}
	for _, k := range names {
		if _, ok := m[k]; !ok {
			return false
		}
	}
	return true
}

func intIn(v any, low, high int64) bool { return isInt(v) && intOf(v) >= low && intOf(v) <= high }

func matches(re *regexp.Regexp, v any) bool {
	s, ok := v.(string)
	return ok && re.MatchString(s)
}

// Topology is the immutable canonical peer roster of one group launch.
type Topology struct {
	doc         map[string]any
	Fingerprint string
}

// NewTopology validates and canonicalizes a roster (members sorted by rank,
// addresses in canonical form).
func NewTopology(document any) (*Topology, error) {
	doc := mapOf(clone(document))
	if !keys(doc, "protocol", "owner", "group_id", "model_sha256", "launch_sha256", "members") {
		return nil, fmt.Errorf("Declare the exact owner, group, model, launch and peer roster")
	}
	if !intIn(doc["protocol"], 1, 1) || !matches(ownerRE, doc["owner"]) || !matches(groupIDRE, doc["group_id"]) ||
		!matches(sha256RE, doc["model_sha256"]) || !matches(sha256RE, doc["launch_sha256"]) {
		return nil, fmt.Errorf("Invalid group identity")
	}
	members := listOf(doc["members"])
	if len(members) < 2 || len(members) > 16 {
		return nil, fmt.Errorf("Declare 2..16 explicit peers")
	}
	nodes, endpoints, ranks := map[string]bool{}, map[string]bool{}, map[int64]bool{}
	for _, raw := range members {
		m := mapOf(raw)
		if !keys(m, "rank", "node_id", "generation", "address", "port") {
			return nil, fmt.Errorf("Pin each rank, node, generation and private control endpoint")
		}
		node, _ := m["node_id"].(string)
		address, ok := m["address"].(string)
		if !intIn(m["rank"], 0, int64(len(members))-1) || ranks[intOf(m["rank"])] || !peerNodeRE.MatchString(node) || nodes[node] ||
			!intIn(m["generation"], 1, math.MaxInt64-1) || !intIn(m["port"], 1024, 65535) || !ok || strings.Contains(address, "%") {
			return nil, fmt.Errorf("Invalid or duplicate peer identity")
		}
		ip, err := netip.ParseAddr(address)
		if err != nil {
			return nil, fmt.Errorf("Peer address must be a private IP literal")
		}
		if !private(ip) || ip.Is4In6() {
			return nil, fmt.Errorf("Peer address must use RFC1918 or ULA private networking")
		}
		endpoint := fmt.Sprint(ip, "/", intOf(m["port"]))
		if endpoints[endpoint] {
			return nil, fmt.Errorf("Peer endpoints must be distinct")
		}
		m["address"] = ip.String()
		nodes[node], endpoints[endpoint], ranks[intOf(m["rank"])] = true, true, true
	}
	sort.Slice(members, func(i, j int) bool { return intOf(mapOf(members[i])["rank"]) < intOf(mapOf(members[j])["rank"]) })
	return &Topology{doc: doc, Fingerprint: sha256Hex(Compact(doc))}, nil
}

// Document is a copy of the canonical roster.
func (t *Topology) Document() map[string]any { return mapOf(clone(t.doc)) }

func (t *Topology) Members() []map[string]any {
	var out []map[string]any
	for _, m := range listOf(t.Document()["members"]) {
		out = append(out, mapOf(m))
	}
	return out
}

func (t *Topology) Member(rank int) map[string]any { return t.Members()[rank] }

// CertificateName is the one DNS SAN a rank's group certificate carries.
func (t *Topology) CertificateName(rank int) string {
	return fmt.Sprintf("r%d.%s.%s.fleet-model.invalid", rank, t.Fingerprint[:32], t.Fingerprint[32:])
}

// privateEndpoint validates a canonical RFC1918/ULA host UDP endpoint.
func privateEndpoint(value any) error {
	s, ok := value.(string)
	bad := fmt.Errorf("Declare a canonical private UDP endpoint")
	if !ok || len(s) > 64 {
		return bad
	}
	m := endpointRE.FindStringSubmatch(s)
	if m == nil {
		return bad
	}
	host, port := m[3], m[4]
	if m[1] != "" {
		host, port = m[1], m[2]
	}
	if strings.Contains(host, "%") {
		return fmt.Errorf("Scoped UDP endpoints are unsupported")
	}
	ip, err := netip.ParseAddr(host)
	if err != nil {
		return fmt.Errorf("%q does not appear to be an IPv4 or IPv6 address", host)
	}
	p, err := strconv.Atoi(port)
	canonical := fmt.Sprintf("%s:%d", ip, p)
	if ip.Is6() {
		canonical = fmt.Sprintf("[%s]:%d", ip, p)
	}
	if err != nil || canonical != s || p < 1024 || p > 65535 || !private(ip) || ip.Is4In6() {
		return fmt.Errorf("Use canonical RFC1918 or ULA UDP endpoints with unprivileged ports")
	}
	return nil
}

// underlayAddresses pins one distinct host UDP endpoint per rank.
func underlayAddresses(values any, count int) error {
	list, ok := values.([]any)
	if !ok || len(list) != count {
		return fmt.Errorf("Pin one host UDP endpoint per rank")
	}
	seen := map[string]bool{}
	for _, v := range list {
		if err := privateEndpoint(v); err != nil {
			return err
		}
		seen[v.(string)] = true
	}
	if len(seen) != count {
		return fmt.Errorf("Each rank requires a distinct host UDP endpoint")
	}
	return nil
}

// modelDescriptor is the stable, non-executable receipt of a prepared model
// snapshot (group_model.descriptor).
func modelDescriptor(record any) (map[string]any, error) {
	r := mapOf(clone(record))
	for _, k := range []string{"sha256", "files", "config", "weights_bytes", "tensor_parallel_weights"} {
		if _, ok := r[k]; !ok {
			return nil, fmt.Errorf("Use a verified non-executable model snapshot receipt")
		}
	}
	config := mapOf(r["config"])
	files := listOf(r["files"])
	if !matches(sha256RE, r["sha256"]) || !intIn(r["weights_bytes"], 1, 1<<40) || config == nil || truthy(config["auto_map"]) ||
		len(files) < 1 || len(files) > 4096 {
		return nil, fmt.Errorf("Use a verified non-executable model snapshot receipt")
	}
	weights := mapOf(r["tensor_parallel_weights"])
	if weights == nil {
		return nil, fmt.Errorf("Use the prepared tensor parallel weight estimates")
	}
	for k, n := range weights {
		if (k != "2" && k != "4" && k != "8") || !intIn(n, 1, intOf(r["weights_bytes"])) {
			return nil, fmt.Errorf("Use the prepared tensor parallel weight estimates")
		}
	}
	names := map[string]bool{}
	var out []any
	var total, safetensors int64
	for _, raw := range files {
		item := mapOf(raw)
		path, _ := item["path"].(string)
		folded := strings.ToLower(path)
		if item == nil || !receiptRE.MatchString(path) || path == "snapshot.json" ||
			!hasSuffix(path, ".json", ".safetensors", ".txt", ".model", ".tiktoken") || names[folded] ||
			!intIn(item["size"], 1, 1<<40) || !matches(sha256RE, item["sha256"]) {
			return nil, fmt.Errorf("Invalid immutable model file receipt")
		}
		names[folded] = true
		total += intOf(item["size"])
		if strings.HasSuffix(path, ".safetensors") {
			safetensors += intOf(item["size"])
		}
		out = append(out, map[string]any{"path": path, "size": item["size"], "sha256": item["sha256"]})
	}
	if total > 1<<40 || !names["config.json"] || !(names["tokenizer.json"] || names["tokenizer.model"] || names["tokenizer.tiktoken"]) ||
		safetensors != intOf(r["weights_bytes"]) {
		return nil, fmt.Errorf("Incomplete model files or inconsistent weights size")
	}
	sort.Slice(out, func(i, j int) bool { return mapOf(out[i])["path"].(string) < mapOf(out[j])["path"].(string) })
	value := map[string]any{"sha256": r["sha256"], "config": config, "weights_bytes": r["weights_bytes"],
		"tensor_parallel_weights": weights, "files": out}
	if raw, err := PyJSON(value, true, ", ", ": "); err != nil || len(raw) > 2<<20 {
		return nil, fmt.Errorf("Model receipt exceeds its metadata bound")
	}
	return value, nil
}

type memoryEstimate struct{ weights, kv, workspace, estimated int64 }

// estimateMemory is snapshots.memory_estimate: a conservative decoder KV
// estimate that refuses architectures it cannot account for.
func estimateMemory(record map[string]any, context, parallel, tp int64) (memoryEstimate, error) {
	config := mapOf(record["config"])
	switch config["model_type"] {
	case "qwen2", "llama", "mistral", "gemma", "gemma2":
	default:
		return memoryEstimate{}, fmt.Errorf("This architecture needs a supported managed memory estimator; use an attached engine")
	}
	if truthy(config["quantization_config"]) {
		return memoryEstimate{}, fmt.Errorf("This architecture needs a supported managed memory estimator; use an attached engine")
	}
	dims := map[string]int64{}
	for _, k := range []string{"num_hidden_layers", "num_attention_heads", "hidden_size"} {
		if !intIn(config[k], 1, 1<<24-1) {
			return memoryEstimate{}, fmt.Errorf("Missing model dimensions for memory estimation")
		}
		dims[k] = intOf(config[k])
	}
	layers, heads, hidden := dims["num_hidden_layers"], dims["num_attention_heads"], dims["hidden_size"]
	kvHeads, headDim := heads, hidden/heads
	if v, ok := config["num_key_value_heads"]; ok {
		if !isInt(v) {
			return memoryEstimate{}, fmt.Errorf("Invalid model KV dimensions")
		}
		kvHeads = intOf(v)
	}
	if v, ok := config["head_dim"]; ok {
		if !isInt(v) {
			return memoryEstimate{}, fmt.Errorf("Invalid model KV dimensions")
		}
		headDim = intOf(v)
	}
	if kvHeads <= 0 || kvHeads > heads || headDim <= 0 || headDim > hidden {
		return memoryEstimate{}, fmt.Errorf("Invalid model KV dimensions")
	}
	if tp != 1 && tp != 2 && tp != 4 && tp != 8 {
		return memoryEstimate{}, fmt.Errorf("Tensor parallel size must be 1, 2, 4 or 8")
	}
	if heads%tp != 0 || (kvHeads >= tp && kvHeads%tp != 0) || (kvHeads < tp && tp%kvHeads != 0) {
		return memoryEstimate{}, fmt.Errorf("Attention and KV heads cannot be partitioned over these tensor parallel ranks")
	}
	kv := 2 * layers * max(1, kvHeads/tp) * headDim * context * parallel * 2
	weights := intOf(record["weights_bytes"])
	if tp > 1 {
		w := mapOf(record["tensor_parallel_weights"])[strconv.FormatInt(tp, 10)]
		if !isInt(w) || intOf(w) <= 0 {
			return memoryEstimate{}, fmt.Errorf("This snapshot needs a supported tensor-parallel weight estimator; use an attached engine")
		}
		weights = intOf(w)
	}
	workspace := max(2<<30, weights/5)
	return memoryEstimate{weights, kv, workspace, weights + kv + workspace}, nil
}

// memoryLimits is sglang_runtime._memory_limits for one rank's config: the
// estimate and the largest static memory fraction every device allows.
func memoryLimits(config, record map[string]any, totals []int64, localTP int) (memoryEstimate, float64, error) {
	if record["sha256"] != config["model_artifact_sha256"] {
		return memoryEstimate{}, 0, fmt.Errorf("Model snapshot does not match this deployment")
	}
	estimate, err := estimateMemory(record, intOf(config["context_length"]), intOf(config["parallel"]), intOf(config["tensor_parallel_size"]))
	if err != nil {
		return estimate, 0, err
	}
	resources := mapOf(config["resources"])
	devices := listOf(resources["devices"])
	ids := map[string]bool{}
	for _, d := range devices {
		ids[strOf(mapOf(d)["id"])] = true
	}
	if len(devices) != localTP || len(totals) != localTP || len(ids) != localTP {
		return estimate, 0, fmt.Errorf("Declare one distinct GPU and physical memory measurement per rank")
	}
	if int64(localTP)*(intOf(record["weights_bytes"])+1<<30) > intOf(resources["memory_bytes"]) {
		return estimate, 0, fmt.Errorf("Model weights and loading overhead exceed the declared system memory budget")
	}
	limit := math.Inf(1)
	for i, d := range devices {
		budget := intOf(mapOf(d)["memory_bytes"])
		if estimate.estimated > budget {
			return estimate, 0, fmt.Errorf("Weights, KV cache and engine overhead exceed a declared GPU budget")
		}
		if totals[i] < budget {
			return estimate, 0, fmt.Errorf("The declared GPU budget exceeds this device memory")
		}
		limit = math.Min(limit, math.Min(.85, float64(budget-estimate.workspace)/float64(totals[i])))
	}
	return estimate, limit, nil
}

// checkLaunch is the memory part of sglang_runtime._launch at a common
// static fraction.
func checkLaunch(config, record map[string]any, totals []int64, localTP int, fraction float64) error {
	estimate, limit, err := memoryLimits(config, record, totals, localTP)
	if err != nil {
		return err
	}
	if !(fraction > 0 && fraction <= limit) {
		return fmt.Errorf("Static memory fraction exceeds a declared GPU budget")
	}
	for _, total := range totals {
		if fraction*float64(total) < float64(estimate.weights+estimate.kv) {
			return fmt.Errorf("Insufficient per-device static memory budget for this tensor parallel group")
		}
	}
	return nil
}

const (
	containerRecipe = "sglang-0.5.20-linux-amd64"
	processRecipe   = "sglang-0.5.20-linux-amd64-process"
	platformPrivate = "platform-private"
)

func int64s(v any) []int64 {
	var out []int64
	for _, n := range listOf(v) {
		out = append(out, intOf(n))
	}
	return out
}

type validatedPlan struct {
	members  []map[string]any // by rank
	configs  []map[string]any
	fraction float64
	topology *Topology
}

// validatePlan is sglang_group._validate: the complete immutable SGLang group
// plan, its canonical roster and the common static memory fraction.
func validateGroupPlan(planValue any, record map[string]any) (*validatedPlan, error) {
	plan := mapOf(clone(planValue))
	base := []string{"protocol", "owner", "group_id", "recipe_id", "model_sha256", "tensor_parallel_size",
		"context_length", "parallel", "rendezvous_port", "members"}
	required := map[string]bool{}
	for _, k := range base {
		required[k] = true
	}
	for k := range plan {
		if k != "underlay" && k != "inference_protocol" && k != "network_mode" && !required[k] {
			return nil, fmt.Errorf("Declare a complete immutable SGLang group plan")
		}
	}
	for _, k := range base {
		if _, ok := plan[k]; !ok || plan == nil {
			return nil, fmt.Errorf("Declare a complete immutable SGLang group plan")
		}
	}
	_, platform := plan["network_mode"]
	_, underlay := plan["underlay"]
	if platform && (plan["network_mode"] != platformPrivate || underlay) {
		return nil, fmt.Errorf("A platform-private group uses the provider network, not a Fleet underlay")
	}
	if v, ok := plan["inference_protocol"]; ok && (!intIn(v, 1, 1) || !(underlay || platform)) {
		return nil, fmt.Errorf("Group inference requires protocol 1 and the managed private network")
	}
	recipe := containerRecipe
	if platform {
		recipe = processRecipe
	}
	tp := plan["tensor_parallel_size"]
	members := listOf(plan["members"])
	n := int64(len(members))
	if plan["recipe_id"] != recipe || !isInt(tp) || (intOf(tp) != 2 && intOf(tp) != 4 && intOf(tp) != 8) ||
		n < 2 || n > intOf(tp) || intOf(tp)%n != 0 || !intIn(plan["context_length"], 512, 1048576) ||
		!intIn(plan["parallel"], 1, 16) || !intIn(plan["rendezvous_port"], 1024, 65535) {
		return nil, fmt.Errorf("This recipe requires uniform node-local TP and an explicit private rendezvous")
	}
	localTP := int(intOf(tp) / n)
	if underlay {
		if err := underlayAddresses(plan["underlay"], len(members)); err != nil {
			return nil, err
		}
		for _, m := range members {
			if mm := mapOf(m); mm != nil && mm["interface"] != "wg0" {
				return nil, fmt.Errorf("Managed private groups require their owned wg0 interface")
			}
		}
	}
	var peers []any
	devices := map[string]bool{}
	for _, raw := range members {
		m := mapOf(raw)
		if !keys(m, "rank", "node_id", "generation", "address", "control_port", "interface", "resources", "physical_gpu_bytes") {
			return nil, fmt.Errorf("Pin every rank, private interface, resources and measured GPU capacity")
		}
		if !matches(interfaceRE, m["interface"]) || m["interface"] == "lo" {
			return nil, fmt.Errorf("Use an exact private network interface, not a wildcard or loopback")
		}
		if platform {
			ip, err := netip.ParseAddr(strOf(m["address"]))
			if m["interface"] == "wg0" || err != nil || !netip.MustParsePrefix("fc00::/7").Contains(ip) {
				return nil, fmt.Errorf("Platform-private ranks use the provider IPv6 ULA address and interface")
			}
		}
		resources, totals := mapOf(m["resources"]), listOf(m["physical_gpu_bytes"])
		rdevices := listOf(resources["devices"])
		if !keys(resources, "memory_bytes", "devices") || !intIn(resources["memory_bytes"], 256<<20, 1<<50) ||
			len(rdevices) != localTP || len(totals) != len(rdevices) {
			return nil, fmt.Errorf("Declare the exact node-local GPU count, capacity and system budget")
		}
		for _, t := range totals {
			if !intIn(t, 256<<20, 1<<50) {
				return nil, fmt.Errorf("Declare the exact node-local GPU count, capacity and system budget")
			}
		}
		for _, raw := range rdevices {
			d := mapOf(raw)
			if !keys(d, "id", "backend", "memory_bytes", "exclusive") || !matches(gpuIDRE, d["id"]) || devices[strOf(d["id"])] ||
				d["backend"] != "cuda" || d["exclusive"] != true || !intIn(d["memory_bytes"], 256<<20, 1<<50) {
				return nil, fmt.Errorf("Each rank needs distinct exclusive NVIDIA GPU UUIDs and explicit budgets")
			}
			devices[strOf(d["id"])] = true
		}
		peers = append(peers, map[string]any{"rank": m["rank"], "node_id": m["node_id"], "generation": m["generation"],
			"address": m["address"], "port": m["control_port"]})
	}
	peerDoc := map[string]any{"protocol": plan["protocol"], "owner": plan["owner"], "group_id": plan["group_id"],
		"model_sha256": plan["model_sha256"], "launch_sha256": strings.Repeat("0", 64), "members": peers}
	checked, err := NewTopology(peerDoc)
	if err != nil {
		return nil, err
	}
	sorted := make([]map[string]any, 0, len(members))
	for _, m := range members {
		sorted = append(sorted, mapOf(m))
	}
	sort.SliceStable(sorted, func(i, j int) bool { return intOf(sorted[i]["rank"]) < intOf(sorted[j]["rank"]) })
	canonical := mapOf(clone(plan))
	var canonicalMembers []any
	for _, m := range sorted {
		c := mapOf(clone(m))
		c["address"] = checked.Member(int(intOf(m["rank"])))["address"]
		canonicalMembers = append(canonicalMembers, c)
	}
	canonical["members"] = canonicalMembers
	peerDoc["launch_sha256"] = sha256Hex(Compact(canonical))
	topology, err := NewTopology(peerDoc)
	if err != nil {
		return nil, err
	}
	addresses, families := map[string]bool{}, map[bool]bool{}
	var configs []map[string]any
	limit := math.Inf(1)
	for _, m := range sorted {
		ip, _ := netip.ParseAddr(strOf(m["address"]))
		if addresses[ip.String()] {
			return nil, fmt.Errorf("Distributed ranks require distinct private node addresses")
		}
		addresses[ip.String()] = true
		families[ip.Is6()] = true
		rendezvous := intOf(plan["rendezvous_port"])
		if c := intOf(m["control_port"]); c == rendezvous || c == 30000 || rendezvous == 30000 {
			return nil, fmt.Errorf("Control, rendezvous and HTTP ports must be distinct")
		}
		config := map[string]any{"recipe_id": plan["recipe_id"], "model_artifact_sha256": plan["model_sha256"],
			"tensor_parallel_size": tp, "context_length": plan["context_length"], "parallel": plan["parallel"],
			"keep_alive_seconds": number(0), "load_policy": "resident", "resources": clone(m["resources"])}
		_, l, err := memoryLimits(config, record, int64s(m["physical_gpu_bytes"]), localTP)
		if err != nil {
			return nil, err
		}
		limit = math.Min(limit, l)
		configs = append(configs, config)
	}
	if len(families) != 1 {
		return nil, fmt.Errorf("Every collective peer must use the same private address family")
	}
	return &validatedPlan{members: sorted, configs: configs, fraction: math.Trunc(limit*100000) / 100000, topology: topology}, nil
}

// checkRanks compiles every rank's launch (rank_launch for each member), so
// an impossible heterogeneous member prevents all starts.
func (v *validatedPlan) checkRanks(record map[string]any) error {
	localTP := len(listOf(mapOf(v.members[0]["resources"])["devices"]))
	for i, m := range v.members {
		if err := checkLaunch(v.configs[i], record, int64s(m["physical_gpu_bytes"]), localTP, v.fraction); err != nil {
			return err
		}
	}
	return nil
}

// compileRank is group_packager.compile_rank: the generated files of one
// original rank package.
func compileRank(request map[string]any, recipe, modelValue map[string]any) (map[string]any, error) {
	if !keys(request, "plan", "topology", "ca_sha256", "rank", "source_sha256") {
		return nil, fmt.Errorf("Build only an exact original creation rank")
	}
	plan := mapOf(request["plan"])
	model, err := modelDescriptor(modelValue)
	if err != nil {
		return nil, err
	}
	v, err := validateGroupPlan(plan, model)
	if err != nil {
		return nil, err
	}
	rank := request["rank"]
	ordered := true
	for i, m := range listOf(plan["members"]) {
		if intOf(mapOf(m)["rank"]) != int64(i) || !isInt(mapOf(m)["rank"]) {
			ordered = false
		}
	}
	for _, m := range v.members {
		if intOf(m["generation"]) < 2 {
			ordered = false
		}
	}
	if !same(v.topology.Document(), request["topology"]) || !intIn(rank, 0, int64(len(v.members))-1) || !ordered ||
		!matches(sha256RE, request["ca_sha256"]) || !matches(sha256RE, request["source_sha256"]) {
		return nil, fmt.Errorf("Package identity differs from canonical creation intent")
	}
	_, platform := plan["network_mode"]
	platforms := listOf(recipe["platforms"])
	pinned := recipe != nil && recipe["id"] == plan["recipe_id"] && recipe["version"] == sglangVersion &&
		recipe["engine"] == "sglang" && len(platforms) == 1 && platforms[0] == "linux-amd64"
	if platform {
		if _, image := recipe["image"]; !pinned || recipe["runtime"] != "process" || image {
			return nil, fmt.Errorf("Use the pinned Linux SGLang process recipe")
		}
	} else if !pinned || recipe["runtime"] != "container" || !matches(imageRE, recipe["image"]) {
		return nil, fmt.Errorf("Use the original digest-pinned Linux SGLang container recipe")
	}
	if err := v.checkRanks(model); err != nil {
		return nil, err
	}
	r := int(intOf(rank))
	resources := clone(v.members[r]["resources"])
	readiness := func(path string) map[string]any {
		return map[string]any{"argv": []any{"python3", path + "/sglang_group_runtime.py", "ready"}, "timeout_seconds": number(480)}
	}
	var definition map[string]any
	if platform {
		definition = map[string]any{"protocol": number(1), "app_id": "model-service", "version": "0.1.0",
			"requires": map[string]any{"os": []any{"linux"}, "arch": []any{"amd64"}, "caps": []any{"proc", "model-group-platform-network"}},
			"components": []any{map[string]any{"name": "backend", "runtime": "process",
				"argv":       []any{"python3", "${PACKAGE}/sglang_group_runtime.py", "start"},
				"group_peer": true, "group_platform_network": "modal-i6pn", "ports": map[string]any{"http": number(0), "engine": number(0)},
				"stop_seconds": number(30), "resources": resources, "readiness": readiness("${PACKAGE}")}}}
	} else {
		taken := map[int64]bool{intOf(plan["rendezvous_port"]): true}
		for _, m := range v.members {
			taken[intOf(m["control_port"])] = true
		}
		status := int64(30001)
		for taken[status] {
			status++
		}
		definition = map[string]any{"protocol": number(1), "app_id": "model-service", "version": "0.1.0",
			"requires":     map[string]any{"os": []any{"linux"}, "arch": []any{"amd64"}, "caps": []any{"proc", "model-group-private-network"}},
			"dependencies": map[string]any{"container_engine": map[string]any{"provider": "docker", "provision": "never"}},
			"components": []any{map[string]any{"name": "backend", "runtime": "container", "image": recipe["image"],
				"argv":       []any{"python3", "/fleet/package/sglang_group_runtime.py", "start"},
				"group_peer": true, "group_network": true, "run_as_owner": true, "ports": map[string]any{"http": number(status)},
				"mounts": map[string]any{"state": "/fleet/state"}, "read_only_mounts": map[string]any{
					"package": "/fleet/package", "cache/snapshots/" + strOf(model["sha256"]): "/fleet/weights"},
				"stop_seconds": number(30), "resources": resources, "readiness": readiness("/fleet/package")}}}
	}
	return map[string]any{"fleet.json": definition,
		"app.json": map[string]any{"id": "model-service", "name": "Managed SGLang group rank", "version": "0.1.0",
			"apiVersion": number(2), "entry": map[string]any{}, "execution": map[string]any{"protocol": number(1), "manifest": "fleet.json"}},
		"group-peer.json":  map[string]any{"protocol": number(1), "rank": rank, "topology": v.topology.Document(), "ca_sha256": request["ca_sha256"]},
		"group-plan.json":  plan,
		"group-model.json": model,
		"group-build.json": map[string]any{"protocol": number(1), "source_sha256": request["source_sha256"], "rank": rank}}, nil
}

// groupRuntimeFiles are the source files a rank package carries.
var groupRuntimeFiles = []string{"group_network.py", "group_mesh.py", "group_supervisor.py", "group_model.py", "sglang_group.py",
	"sglang_runtime.py", "snapshots.py", "sglang_group_runtime.py", "group_connector.py", "server.py", "activity.py", "idle.py",
	"group_inference.py"}

// packageRank builds the rank package tar from the frozen source files and
// the compiled rank files.
func packageRank(files map[string]string, generated map[string]any) ([]byte, string, error) {
	tree := Tree{}
	for _, name := range groupRuntimeFiles {
		data, ok := files[name]
		if !ok {
			return nil, "", fmt.Errorf("Original group source lacks %s", name)
		}
		tree[name] = File{Data: []byte(data)}
	}
	for name, value := range generated {
		tree[name] = File{Data: Compact(value)}
	}
	return BuildArtifact(tree, "linux-amd64")
}
