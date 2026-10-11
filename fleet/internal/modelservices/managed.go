package modelservices

import (
	"fmt"
	"strings"
)

// Managed engines: constrained recipes become immutable Fleet components
// (formerly pantheon/models/managed.py validate/package).

var managedKeys = map[string]bool{"recipe_id": true, "context_length": true, "parallel": true, "keep_alive_seconds": true,
	"resources": true, "model_artifact_sha256": true, "model_recipe_id": true, "load_policy": true,
	"tensor_parallel_size": true, "model_manifest": true}

func errorf(format string, args ...any) error { return fmt.Errorf(format, args...) }

// ValidateManaged checks a managed engine configuration for a node platform
// and returns its canonical copy.
func (c *Connector) ValidateManaged(raw any, target string) (map[string]any, error) {
	value, ok := clone(raw).(map[string]any)
	if !ok {
		return nil, errorf("Specify the engine recipe, context, concurrency, lifetime and memory budget")
	}
	for k := range value {
		if !managedKeys[k] {
			return nil, errorf("Specify the engine recipe, context, concurrency, lifetime and memory budget")
		}
	}
	for _, k := range []string{"recipe_id", "context_length", "parallel", "keep_alive_seconds", "resources"} {
		if _, ok := value[k]; !ok {
			return nil, errorf("Specify the engine recipe, context, concurrency, lifetime and memory budget")
		}
	}
	selected, err := c.Recipe(strOf(value["recipe_id"]), target)
	if err != nil {
		return nil, err
	}
	if _, has := value["model_manifest"]; has && selected["runtime"] != "preinstalled" {
		return nil, errorf("Custom model manifests run on the node-provided SGLang only")
	}
	engine := strOf(selected["engine"])
	switch engine {
	case "ollama", "lmstudio", "sglang", "speaches":
	default:
		return nil, errorf("This engine does not yet have a managed launch recipe")
	}
	for _, b := range []struct {
		key       string
		low, high int64
	}{{"context_length", 512, 1048576}, {"parallel", 1, 16}, {"keep_alive_seconds", 0, 86400}} {
		if !isInt(value[b.key]) || intOf(value[b.key]) < b.low || intOf(value[b.key]) > b.high {
			return nil, errorf("Invalid %s", b.key)
		}
	}
	policy := "manual"
	if p, has := value["load_policy"]; has {
		s, ok := p.(string)
		if !ok {
			return nil, errorf("Choose an explicit model loading policy")
		}
		policy = s
	}
	switch policy {
	case "manual", "on_demand", "warm", "resident":
	default:
		return nil, errorf("Choose an explicit model loading policy")
	}
	keepAlive := intOf(value["keep_alive_seconds"])
	if (policy == "warm" && keepAlive < 1) || ((policy == "on_demand" || policy == "resident") && keepAlive != 0) {
		return nil, errorf("Warm models need a positive idle TTL; on-demand and resident use zero")
	}
	parallel, contextLength := intOf(value["parallel"]), intOf(value["context_length"])
	if engine == "lmstudio" && (parallel != 1 || (policy == "manual" && keepAlive < 1)) {
		return nil, errorf("The pinned llmster recipe requires parallel=1; manual loading needs a positive idle TTL")
	}
	operation := strOf(selected["operation"])
	diffusion := operation == "image" || operation == "video"
	artifact := strOf(value["model_artifact_sha256"])
	switch {
	case diffusion:
		if _, err := c.Diffusion(value["model_recipe_id"]); err != nil {
			return nil, err
		}
		if value["model_recipe_id"] != selected["model_recipe_id"] || artifact != "" || parallel != 1 || contextLength != 512 ||
			keepAlive != 0 || policy != "resident" {
			return nil, errorf("Managed diffusion requires its pinned resident model and one request at a time")
		}
	case selected["runtime"] == "preinstalled":
		var llm map[string]any
		if manifest, has := value["model_manifest"]; has && manifest != nil {
			m, ok := manifest.(map[string]any)
			if !ok || m["id"] != value["model_recipe_id"] || len(Compact(m)) > 64<<10 {
				return nil, errorf("A custom model manifest must match its model id and stay under 64 KB")
			}
			if llm, err = c.LLM(m); err != nil {
				return nil, err
			}
		} else if llm, err = c.LLM(value["model_recipe_id"]); err != nil {
			return nil, err
		}
		if artifact != "" {
			return nil, errorf("Catalog language models are prepared from their pinned manifest")
		}
		if target != "linux-amd64" || keepAlive != 0 || (policy != "manual" && policy != "resident") {
			return nil, errorf("This SGLang recipe runs resident on Linux NVIDIA; stop the service to unload it")
		}
		if contextLength > intOf(llm["maximum_context_length"]) {
			return nil, errorf("Context length exceeds what this model supports")
		}
	case engine == "sglang":
		if !digest64RE.MatchString(artifact) {
			return nil, errorf("SGLang requires the SHA256 of a safetensors.tar.gz model bundle")
		}
		if target != "linux-amd64" || keepAlive != 0 || (policy != "manual" && policy != "resident") {
			return nil, errorf("This SGLang recipe runs resident on Linux NVIDIA; stop the service to unload it")
		}
	case artifact != "":
		return nil, errorf("This recipe imports models after engine startup")
	}
	resources := mapOf(value["resources"])
	_, hasMemory := resources["memory_bytes"]
	_, hasDevices := resources["devices"]
	if resources == nil || len(resources) != 2 || !hasMemory || !hasDevices {
		return nil, errorf("Declare system memory and the exact accelerator budget")
	}
	memory := intOf(resources["memory_bytes"])
	if !isInt(resources["memory_bytes"]) || memory < 256<<20 || memory > 1<<50 {
		return nil, errorf("Declare a system memory budget of at least 256 MiB")
	}
	_, hasTP := value["tensor_parallel_size"]
	if engine == "speaches" {
		if hasTP {
			return nil, errorf("Tensor parallelism requires the SGLang text recipe")
		}
		speech, err := c.Speech(value["model_recipe_id"])
		if err != nil {
			return nil, err
		}
		devices, ok := resources["devices"].([]any)
		if !ok || len(devices) != 0 || target != "linux-amd64" || parallel != 1 || contextLength != 512 || policy != "resident" || keepAlive != 0 {
			return nil, errorf("This CPU speech recipe requires one resident model, parallel=1, context_length=512 and no accelerators")
		}
		if memory < intOf(speech["minimum_memory_bytes"]) {
			return nil, errorf("The speech model and engine exceed this system memory budget")
		}
		return value, nil
	}
	if value["model_recipe_id"] != nil && strOf(value["model_recipe_id"]) != "" && !diffusion && selected["runtime"] != "preinstalled" {
		return nil, errorf("Pinned speech models require a Speaches recipe")
	}
	devices, isList := resources["devices"].([]any)
	tp := int64(1)
	if hasTP {
		if !isInt(value["tensor_parallel_size"]) {
			return nil, errorf("Tensor parallel size must be 1, 2, 4 or 8")
		}
		tp = intOf(value["tensor_parallel_size"])
	}
	if tp != 1 && tp != 2 && tp != 4 && tp != 8 {
		return nil, errorf("Tensor parallel size must be 1, 2, 4 or 8")
	}
	if hasTP && (engine != "sglang" || diffusion) {
		return nil, errorf("Tensor parallelism requires the SGLang text recipe")
	}
	// Ollama on Linux/Windows may run CPU-only: no accelerator lease.
	cpuOnly := isList && len(devices) == 0 && tp == 1 && !hasTP && engine == "ollama" &&
		(strings.HasPrefix(target, "linux-") || strings.HasPrefix(target, "windows-"))
	if !isList || (int64(len(devices)) != tp && !cpuOnly) {
		return nil, errorf("Declare exactly one distinct accelerator per tensor parallel rank")
	}
	seen := map[string]bool{}
	for _, item := range devices {
		d := mapOf(item)
		_, exclusiveOK := d["exclusive"].(bool)
		id := strOf(d["id"])
		if d == nil || len(d) != 4 || d["backend"] == nil || !deviceIDRE.MatchString(id) || !isInt(d["memory_bytes"]) ||
			intOf(d["memory_bytes"]) < 256<<20 || intOf(d["memory_bytes"]) > 1<<50 || !exclusiveOK {
			return nil, errorf("Invalid accelerator resource declaration")
		}
		if seen[id] {
			return nil, errorf("Tensor parallel ranks require distinct accelerator IDs")
		}
		seen[id] = true
		exclusive := d["exclusive"].(bool)
		if tp > 1 && (target != "linux-amd64" || !exclusive) {
			return nil, errorf("Tensor parallelism requires exclusive NVIDIA GPUs on one Linux node")
		}
		if diffusion && (memory < intOf(selected["minimum_memory_bytes"]) || intOf(d["memory_bytes"]) < intOf(selected["minimum_gpu_memory_bytes"]) || !exclusive) {
			return nil, errorf("The diffusion model needs its declared system/GPU budget and an exclusive device")
		}
		switch {
		case target == "darwin-arm64":
			if id != "apple-metal" || d["backend"] != "metal" || intOf(d["memory_bytes"]) > memory {
				return nil, errorf("Apple unified memory must be included once in the system memory budget")
			}
		case strings.HasPrefix(target, "linux-") || strings.HasPrefix(target, "windows-"):
			if d["backend"] != "cuda" || !strings.HasPrefix(id, "GPU-") {
				return nil, errorf("This managed recipe currently supports NVIDIA CUDA on Linux/Windows")
			}
		default:
			return nil, errorf("Managed execution on this platform is not available yet")
		}
	}
	if selected["runtime"] == "preinstalled" {
		var key any = value["model_recipe_id"]
		if m := value["model_manifest"]; m != nil {
			key = m
		}
		llm, err := c.LLM(key)
		if err != nil {
			return nil, err
		}
		if memory < intOf(llm["minimum_memory_bytes"]) {
			return nil, errorf("The model exceeds this deployment’s system memory budget")
		}
		if len(devices) == 0 {
			return nil, errorf("The model needs more GPU memory than this deployment declares")
		}
		for _, item := range devices {
			if intOf(mapOf(item)["memory_bytes"]) < RankGPUBytes(llm, tp) {
				return nil, errorf("The model needs more GPU memory than this deployment declares")
			}
		}
	}
	for k, v := range value {
		if v == nil {
			delete(value, k)
		}
	}
	return value, nil
}

// engineFiles are the connector files every generated engine package carries.
var engineFiles = []string{"managed_engine.py", "engines.py", "engines.json", "llmster_runtime.py", "sglang_runtime.py",
	"snapshots.py", "speaches_runtime.py", "speech_models.py", "speech-models.json", "pinned_models.py",
	"diffusion_models.py", "diffusion-models.json", "sglang_diffusion_runtime.py", "llm_models.py", "llm-models.json"}

// readinessSeconds bounds a node SGLang's startup by model size.
func readinessSeconds(selected map[string]any) int64 {
	size := float64(filesSize(selected)) / 1e9
	return int64(min(3600, max(600, 12*size)))
}

// EnginePackage generates the immutable engine component for a managed
// configuration (former managed.package); BuildArtifact turns it into bytes.
func (c *Connector) EnginePackage(raw any, target string) (Tree, error) {
	config, err := c.ValidateManaged(raw, target)
	if err != nil {
		return nil, err
	}
	system, arch, _ := strings.Cut(target, "-")
	python := "python3"
	if system == "windows" {
		python = "python"
	}
	selected, err := c.Recipe(strOf(config["recipe_id"]), target)
	if err != nil {
		return nil, err
	}
	tree := Tree{}
	for _, name := range engineFiles {
		f, ok := c.Tree[name]
		if !ok {
			return nil, errorf("connector source lacks %s", name)
		}
		tree[name] = File{Data: f.Data}
	}
	withResources := selected["runtime"] == "container" || (selected["engine"] == "sglang" && selected["runtime"] == "preinstalled")
	engineConfig := config
	if !withResources {
		engineConfig = map[string]any{}
		for k, v := range config {
			if k != "resources" {
				engineConfig[k] = v
			}
		}
	}
	data, err := PyJSON(engineConfig, true, ", ", ": ")
	if err != nil {
		return nil, err
	}
	tree["engine-config.json"] = File{Data: data}
	resources := config["resources"]
	component := map[string]any{"name": "backend", "runtime": "process", "argv": []any{python, "${PACKAGE}/managed_engine.py", "start"},
		"ports": map[string]any{"http": number(0)}, "stop_seconds": number(30), "resources": resources,
		"readiness": map[string]any{"argv": []any{python, "${PACKAGE}/managed_engine.py", "ready"}, "timeout_seconds": number(30)}}
	definition := map[string]any{"protocol": number(1), "app_id": "model-service", "version": "0.1.0",
		"requires": map[string]any{"os": []any{system}, "arch": []any{arch}, "caps": []any{"proc"}},
		"components": []any{component}}
	docker := map[string]any{"container_engine": map[string]any{"provider": "docker", "provision": "never"}}
	container := func(image any, argv []any, port int64, weights string, readiness int64, extra map[string]any) map[string]any {
		out := map[string]any{"name": "backend", "runtime": "container", "image": image, "argv": argv,
			"ports": map[string]any{"http": number(port)}, "mounts": map[string]any{"state": "/fleet/state"},
			"read_only_mounts": map[string]any{"package": "/fleet/package", weights: "/fleet/weights"},
			"stop_seconds": number(30), "resources": resources,
			"readiness": map[string]any{"argv": append(append([]any{}, argv[:len(argv)-1]...), "ready"), "timeout_seconds": number(readiness)}}
		for k, v := range extra {
			out[k] = v
		}
		return out
	}
	operation := strOf(selected["operation"])
	switch {
	case operation == "image" || operation == "video":
		model, err := c.Diffusion(config["model_recipe_id"])
		if err != nil {
			return nil, err
		}
		definition["dependencies"] = docker
		definition["components"] = []any{container(selected["image"], []any{"python3", "/fleet/package/sglang_diffusion_runtime.py", "start"},
			30000, "cache/diffusion-models/"+SnapshotDigest(model), 480, nil)}
	case selected["engine"] == "sglang" && selected["runtime"] == "preinstalled":
		var key any = config["model_recipe_id"]
		if m := config["model_manifest"]; m != nil {
			key = m
		}
		llm, err := c.LLM(key)
		if err != nil {
			return nil, err
		}
		command := selected["command"]
		definition["components"] = []any{map[string]any{"name": "backend", "runtime": "process",
			"argv": []any{command, "${PACKAGE}/sglang_runtime.py", "start"}, "ports": map[string]any{"http": number(0)},
			"stop_seconds": number(60), "resources": resources,
			"readiness": map[string]any{"argv": []any{command, "${PACKAGE}/sglang_runtime.py", "ready"}, "timeout_seconds": number(readinessSeconds(llm))}}}
	case selected["engine"] == "sglang":
		definition["dependencies"] = docker
		definition["components"] = []any{container(selected["image"], []any{"python3", "/fleet/package/sglang_runtime.py", "start"},
			30000, "cache/snapshots/"+strOf(config["model_artifact_sha256"]), 480, nil)}
	}
	if selected["engine"] == "speaches" {
		model, err := c.Speech(config["model_recipe_id"])
		if err != nil {
			return nil, err
		}
		python := "/home/ubuntu/speaches/.venv/bin/python"
		definition["dependencies"] = docker
		definition["components"] = []any{container(selected["image"], []any{python, "/fleet/package/speaches_runtime.py", "start"},
			8000, "cache/speech-models/"+SnapshotDigest(model), 180, map[string]any{"run_as_owner": true})}
	}
	if data, err = PyJSON(definition, true, ", ", ": "); err != nil {
		return nil, err
	}
	tree["fleet.json"] = File{Data: data}
	app := ordered("id", "model-service", "name", "Managed model engine", "version", "0.1.0", "apiVersion", 2,
		"entry", map[string]any{}, "execution", ordered("protocol", 1, "manifest", "fleet.json"))
	if data, err = PyJSON(app, false, ", ", ": "); err != nil {
		return nil, err
	}
	tree["app.json"] = File{Data: data}
	return tree, nil
}
