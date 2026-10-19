package modelservices

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// Deploy a model: where (a new Modal machine or an existing node), which
// engine (SGLang on platform GPU machines, Ollama anywhere), which model
// (catalog, pinned Hugging Face, Ollama library or GGUF). deploy records a
// plan so status can drive the next step after a restart (formerly
// pantheonos/models/model_deploy.py).

const (
	gib           = int64(1) << 30
	ollamaVersion = "0.34.2"
)

var (
	HFBase         = "https://huggingface.co"
	OllamaBase     = "https://ollama.com"
	OllamaRegistry = "https://registry.ollama.ai"
	gpuMemory      = map[string]int64{"H100": 80 * gib, "H200": 131 * gib, "B200": 179 * gib, "A100-80GB": 80 * gib, "L40S": 45 * gib}
	webClient      = &http.Client{Timeout: 30 * time.Second}
	slugRE         = regexp.MustCompile(`[^a-z0-9-]+`)
)

func modalMemory(hasGPU bool, count int64) int64 {
	if !hasGPU {
		return 16 * gib
	}
	return 64*gib + 32*gib*(count-1)
}

// gpusNeeded is the fewest GPUs (1, 2, 4 or 8 of one machine) of this size
// that hold the model, or 0.
func gpusNeeded(entry map[string]any, memory int64, most int64) int64 {
	for _, n := range gpuCounts {
		if n <= most && RankGPUBytes(entry, n) <= memory*9/10 {
			return n
		}
	}
	return 0
}

func slug(text string, limit int) string {
	s := strings.Trim(slugRE.ReplaceAllString(strings.ToLower(text), "-"), "-")
	if len(s) > limit {
		s = s[:limit]
	}
	s = strings.Trim(s, "-")
	if s == "" {
		return "model"
	}
	return s
}

func gibString(v int64) string { return fmt.Sprintf("%.0f GB", float64(v)/float64(gib)) }

// ---- plans ------------------------------------------------------------------------

// Plans keep one durable deploy plan per deployment: <Root>/<fleet>/<id>.json.
func (p *Plans) path(fleet, id string) (string, error) {
	if !deploymentIDRE.MatchString(id) {
		return "", fmt.Errorf("Invalid deployment id")
	}
	dir := filepath.Join(p.Root, fleet)
	return filepath.Join(dir, id+".json"), os.MkdirAll(dir, 0o700)
}

func (p *Plans) Save(fleet string, plan map[string]any) error {
	path, err := p.path(fleet, strOf(plan["deployment_id"]))
	if err != nil {
		return err
	}
	raw := Compact(plan)
	if len(raw) > 1<<20 {
		return fmt.Errorf("deploy plan exceeds its storage limit")
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func (p *Plans) Load(fleet, id string) (map[string]any, error) {
	path, err := p.path(fleet, id)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var plan map[string]any
	return plan, decode(raw, &plan)
}

// ---- target facts ---------------------------------------------------------------

type target struct {
	NodeID, Platform, ServiceID, GPUName, GPUID string
	Modal, Metal                                bool
	GPUMemory, GPUCount, Memory                 int64
	gpuCountSet                                 bool
}

func (t target) public() map[string]any {
	var count any
	if t.gpuCountSet || t.NodeID != "" {
		count = number(t.GPUCount)
	}
	return map[string]any{"node_id": t.NodeID, "platform": t.Platform, "modal": t.Modal, "gpu_name": t.GPUName,
		"gpu_memory": number(t.GPUMemory), "gpu_count": count, "memory": number(t.Memory)}
}

func (m *Manager) target(ctx context.Context, nodeID, gpu string, count any) (target, error) {
	if nodeID != "" {
		nodes, err := m.records(ctx)
		if err != nil {
			return target{}, err
		}
		var node map[string]any
		for _, n := range nodes {
			if n["node_id"] == nodeID {
				node = n
			}
		}
		if node == nil {
			return target{}, fmt.Errorf("That node is not online in your Fleet")
		}
		t := target{NodeID: nodeID, Platform: platform(node)}
		for _, l := range listOf(node["labels"]) {
			label := strOf(l)
			t.Modal = t.Modal || label == "modal" || label == "modal-gpu"
			if t.ServiceID == "" && strings.HasPrefix(label, "svc-") {
				t.ServiceID = label[4:]
			}
		}
		var launch map[string]any
		if t.ServiceID != "" {
			launches, err := m.modalServices(ctx)
			if err != nil {
				return target{}, err
			}
			for _, s := range launches {
				if s["service_id"] == t.ServiceID {
					launch = s
				}
			}
		}
		var cudas []map[string]any
		for _, a := range listOf(mapOf(caps(node)["resources"])["accelerators"]) {
			acc := mapOf(a)
			switch acc["backend"] {
			case "cuda":
				cudas = append(cudas, acc)
			case "metal":
				t.Metal = true
			}
		}
		if len(cudas) > 0 {
			t.GPUName, t.GPUID = strOf(cudas[0]["name"]), strOf(cudas[0]["id"])
			t.GPUMemory = intOf(mapOf(cudas[0]["memory"])["total_bytes"])
			for _, c := range cudas {
				t.GPUMemory = min(t.GPUMemory, intOf(mapOf(c["memory"])["total_bytes"]))
			}
		}
		if t.GPUName == "" {
			t.GPUName = strOf(launch["gpu"])
		}
		t.GPUCount, t.gpuCountSet = int64(len(cudas)), true
		if gibs := intOf(launch["memory_gib"]); gibs != 0 {
			t.Memory = gibs * gib
		} else {
			ram, _ := caps(node)["ram_gb"].(json.Number)
			f, _ := ram.Float64()
			t.Memory = int64(f * float64(gib))
		}
		return t, nil
	}
	if gpu != "none" && !validGPU(gpu) {
		return target{}, fmt.Errorf("Choose a node or a Modal GPU (H100, A100-80GB, L40S or none)")
	}
	hasGPU := gpu != "none"
	t := target{Platform: "linux-amd64", Modal: true, GPUMemory: gpuMemory[gpu]}
	if hasGPU {
		t.GPUName = gpu
	}
	if count != nil {
		n := intOf(count)
		if !validCount(n) || (!hasGPU && n != 1) {
			return target{}, fmt.Errorf("A machine has 1, 2, 4 or 8 GPUs")
		}
		t.GPUCount, t.gpuCountSet = n, true
	}
	t.Memory = modalMemory(hasGPU, max(1, t.GPUCount))
	return t, nil
}

// sglangPlan: whether the model fits, why not, and how many GPUs to use.
func sglangPlan(entry map[string]any, t target) (bool, string, int64) {
	if t.GPUName == "" {
		return false, "Needs an NVIDIA GPU", 0
	}
	if gpuMismatch(entry, t.GPUName) != "" {
		var names []string
		for _, g := range listOf(entry["supported_gpus"]) {
			names = append(names, strOf(g))
		}
		return false, "Needs " + strings.Join(names, " or "), 0
	}
	need := int64(1)
	if t.GPUMemory != 0 {
		need = gpusNeeded(entry, t.GPUMemory, 8)
	}
	name := strings.TrimPrefix(t.GPUName, "NVIDIA ")
	if need == 0 {
		return false, "Too large even for 8× " + name, 0
	}
	var count int64
	if t.NodeID != "" {
		if !validCount(t.GPUCount) {
			return false, "SGLang uses 1, 2, 4 or 8 GPUs of one machine", 0
		}
		if need > t.GPUCount {
			return false, fmt.Sprintf("Needs %d× %s; this machine has %d", need, name, t.GPUCount), 0
		}
		count = t.GPUCount
	} else {
		count = t.GPUCount
		if !t.gpuCountSet || count == 0 {
			count = need
		}
		if count < need {
			return false, fmt.Sprintf("Needs %d× %s", need, name), 0
		}
	}
	memory := t.Memory
	if t.NodeID == "" {
		memory = modalMemory(true, count)
	}
	if memory != 0 && memory < intOf(entry["minimum_memory_bytes"]) {
		return false, "Needs " + gibString(intOf(entry["minimum_memory_bytes"])) + " system memory", 0
	}
	return true, "", count
}

func ollamaFit(entry map[string]any, t target) (bool, string) {
	need := intOf(entry["min_memory_bytes"])
	if t.GPUMemory != 0 && t.GPUMemory*9/10 >= need {
		return true, ""
	}
	if t.Memory != 0 && t.Memory*3/4 >= need {
		if t.GPUName == "" && !t.Metal {
			return true, "Runs on CPU"
		}
		return true, ""
	}
	return false, "Needs about " + gibString(need) + " of memory"
}

func (c *Connector) ollamaCatalog() ([]map[string]any, error) {
	var doc struct {
		Models []map[string]any `json:"models"`
	}
	return doc.Models, decode(c.Tree["ollama-models.json"].Data, &doc)
}

func (m *Manager) Options(ctx context.Context, nodeID, gpu string) (map[string]any, error) {
	t, err := m.target(ctx, nodeID, gpu, nil)
	if err != nil {
		return nil, err
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	sglangOK := t.GPUName != "" && t.Modal && t.Platform == "linux-amd64"
	ollamaOK := map[string]bool{"linux-amd64": true, "linux-arm64": true, "windows-amd64": true, "windows-arm64": true, "darwin-arm64": true, "darwin-amd64": true}[t.Platform]
	sglangReason := ""
	if !sglangOK {
		sglangReason = "Runs on platform GPU machines (Modal)"
		if t.GPUName == "" {
			sglangReason = "Needs an NVIDIA GPU"
		}
	}
	ollamaReason := ""
	if !ollamaOK {
		ollamaReason = "Not available for this platform"
	}
	engines := []any{
		map[string]any{"id": "sglang", "label": "SGLang", "description": "Fast serving on an NVIDIA GPU; full-precision or FP8 Hugging Face models.", "available": sglangOK, "reason": sglangReason},
		map[string]any{"id": "ollama", "label": "Ollama", "description": "Quantized GGUF models on GPU or CPU; any machine.", "available": ollamaOK, "reason": ollamaReason},
		map[string]any{"id": "lmstudio", "label": "LM Studio", "description": "Your LM Studio app on a Mac.", "available": false, "reason": "Set up LM Studio from Advanced → Add service"},
	}
	sglang := []any{}
	for _, entry := range c.LLMCatalog() {
		fits, reason, count := false, sglangReason, int64(0)
		if sglangOK {
			fits, reason, count = sglangPlan(entry, t)
		}
		supported := entry["supported_gpus"]
		if supported == nil {
			supported = []any{}
		}
		sglang = append(sglang, map[string]any{"id": entry["id"], "display_name": entry["display_name"], "gpu_count": number(count),
			"size": number(filesSize(entry)), "context_length": entry["context_length"], "maximum_context_length": entry["maximum_context_length"],
			"supported_gpus": supported, "capabilities": entry["capabilities"], "fits": fits, "reason": reason})
	}
	catalog, err := c.ollamaCatalog()
	if err != nil {
		return nil, err
	}
	ollama := []any{}
	for _, entry := range catalog {
		fits, reason := false, ollamaReason
		if ollamaOK {
			fits, reason = ollamaFit(entry, t)
		}
		maximum := entry["maximum_context_length"]
		if !truthy(maximum) {
			maximum = entry["context_length"]
		}
		ollama = append(ollama, map[string]any{"id": entry["id"], "display_name": entry["display_name"], "size": mapOf(entry["source"])["size"],
			"min_memory_bytes": entry["min_memory_bytes"], "context_length": entry["context_length"], "maximum_context_length": maximum,
			"capabilities": entry["capabilities"], "gpu_optional": true, "fits": fits, "reason": reason})
	}
	return map[string]any{"target": t.public(), "engines": engines, "sglang_models": sglang, "ollama_models": ollama}, nil
}

// ---- Hugging Face and Ollama ------------------------------------------------------

func getJSON(ctx context.Context, rawURL string, headers map[string]string, out any) (int, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", rawURL, nil)
	if err != nil {
		return 0, nil, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := webClient.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return resp.StatusCode, nil, err
	}
	if out != nil && resp.StatusCode == 200 {
		if err := decode(body, out); err != nil {
			return resp.StatusCode, body, err
		}
	}
	return resp.StatusCode, body, nil
}

func hfJSON(ctx context.Context, path string, out any) error {
	status, _, err := getJSON(ctx, HFBase+path, nil, out)
	switch {
	case err != nil && status == 0:
		return err
	case status == 404:
		return fmt.Errorf("Hugging Face repository or revision not found (private and gated models are not supported)")
	case status == 401 || status == 403:
		return fmt.Errorf("This Hugging Face model is gated or private; only public models can be pinned")
	case status != 200:
		return fmt.Errorf("Hugging Face returned HTTP %d", status)
	}
	return err
}

func parsers(config map[string]any, repo string) (string, string) {
	kind := strings.ToLower(strOf(config["model_type"]))
	if kind == "" {
		kind = strings.ToLower(strOf(mapOf(config["text_config"])["model_type"]))
	}
	name := strings.ToLower(repo)
	switch {
	case strings.HasPrefix(kind, "qwen3_5") || strings.HasPrefix(kind, "qwen3_next"):
		return "qwen3_coder", "qwen3"
	case strings.HasPrefix(kind, "qwen3"):
		if strings.Contains(name, "instruct") && strings.Contains(name, "2507") {
			return "qwen25", ""
		}
		return "qwen25", "qwen3"
	case strings.HasPrefix(kind, "qwen2"):
		return "qwen25", ""
	case strings.HasPrefix(kind, "llama"):
		return "llama3", ""
	case strings.HasPrefix(kind, "mistral"):
		return "mistral", ""
	case strings.HasPrefix(kind, "deepseek_v4"):
		return "deepseekv4", "deepseek-v4"
	case strings.HasPrefix(kind, "deepseek_v32"):
		return "deepseekv32", "deepseek-v3"
	case strings.HasPrefix(kind, "deepseek_v3"):
		return "deepseekv3", ""
	}
	return "", ""
}

var repoInputRE = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

func (m *Manager) resolveHF(ctx context.Context, repo, revision string) (map[string]any, error) {
	if !repoInputRE.MatchString(repo) {
		return nil, fmt.Errorf("Enter a Hugging Face repository like Qwen/Qwen3-8B")
	}
	if revision == "" {
		revision = "main"
	}
	var info map[string]any
	if err := hfJSON(ctx, fmt.Sprintf("/api/models/%s/revision/%s?blobs=true", repo, revision), &info); err != nil {
		return nil, err
	}
	sha := strOf(info["sha"])
	var config map[string]any
	if status, _, err := getJSON(ctx, fmt.Sprintf("%s/%s/resolve/%s/config.json", HFBase, repo, sha), nil, &config); err != nil || status != 200 {
		return nil, fmt.Errorf("could not read the model configuration (HTTP %d)", status)
	}
	if truthy(config["auto_map"]) {
		return nil, fmt.Errorf("This model needs custom code (trust_remote_code), which is not allowed")
	}
	siblings := listOf(info["siblings"])
	sort.Slice(siblings, func(i, j int) bool {
		return strOf(mapOf(siblings[i])["rfilename"]) < strOf(mapOf(siblings[j])["rfilename"])
	})
	files := []any{}
	var weights, total int64
	for _, s := range siblings {
		sibling := mapOf(s)
		name := strOf(sibling["rfilename"])
		if strings.Contains(name, "/") || !(name == "LICENSE" || hasSuffix(name, ".safetensors", ".json", ".txt", ".md", ".jinja")) {
			continue
		}
		fileURL := fmt.Sprintf("%s/%s/resolve/%s/%s", HFBase, repo, sha, name)
		var digest string
		var size int64
		if lfs := mapOf(sibling["lfs"]); lfs != nil {
			digest, size = strOf(lfs["sha256"]), intOf(lfs["size"])
		} else {
			status, body, err := getJSON(ctx, fileURL, nil, nil)
			if err != nil || status != 200 {
				return nil, fmt.Errorf("could not read %s (HTTP %d)", name, status)
			}
			sum := sha256.Sum256(body)
			digest, size = hex.EncodeToString(sum[:]), int64(len(body))
		}
		files = append(files, map[string]any{"name": name, "url": fileURL, "size": number(size), "sha256": digest, "format": "data", "revision": sha})
		total += size
		if strings.HasSuffix(name, ".safetensors") {
			weights += size
		}
	}
	if weights == 0 {
		return nil, fmt.Errorf("Only safetensors models can be served by SGLang here")
	}
	text := mapOf(config["text_config"])
	context := intOf(config["max_position_embeddings"])
	if context == 0 {
		context = intOf(text["max_position_embeddings"])
	}
	if context == 0 {
		context = 32768
	}
	quantization := mapOf(config["quantization_config"])
	if quantization == nil {
		quantization = mapOf(text["quantization_config"])
	}
	quant := strings.ToLower(strOf(quantization["quant_method"]))
	minimumGPU := weights*11/10 + 6*gib
	candidates := []string{"A100", "H100", "H200", "B200", "L40S"}
	if quant == "fp8" {
		candidates = []string{"H100", "H200", "B200", "L40S"}
	}
	sizing := map[string]any{"minimum_gpu_memory_bytes": number(minimumGPU)}
	gpus := []any{}
	for _, g := range candidates {
		key := g
		if g == "A100" {
			key = "A100-80GB"
		}
		if gpusNeeded(sizing, gpuMemory[key], 8) != 0 {
			gpus = append(gpus, g)
		}
	}
	if len(gpus) == 0 {
		return nil, fmt.Errorf("This model needs about %s of GPU memory, more than 8 GPUs of one machine offer", gibString(minimumGPU))
	}
	tool, reasoning := parsers(config, repo)
	license := strOf(mapOf(info["cardData"])["license"])
	if license == "" {
		license = "see model card"
	}
	if len(license) > 64 {
		license = license[:64]
	}
	display := repo[strings.LastIndex(repo, "/")+1:]
	if len(display) > 120 {
		display = display[:120]
	}
	entry := map[string]any{"id": fmt.Sprintf("hf-%s-%s", slug(strings.ReplaceAll(repo, "/", "-"), 70), sha[:8]), "model": repo, "revision": sha,
		"operation": "text", "license": license, "display_name": display, "context_length": number(min(context, 32768)),
		"maximum_context_length": number(min(context, 1048576)), "minimum_memory_bytes": number(min(48*gib, max(16*gib, weights/2))),
		"minimum_gpu_memory_bytes": number(minimumGPU), "tool_call_parser": tool, "reasoning_parser": reasoning, "supported_gpus": gpus,
		"capabilities": map[string]any{"tools": tool != "", "reasoning": reasoning != "", "vision": false}, "files": files}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := c.LLM(clone(entry).(map[string]any)); err != nil {
		return nil, err
	}
	warnings := []any{}
	if tool == "" {
		warnings = append(warnings, "No tool-call parser is known for this model family; it can chat but not use tools.")
	}
	if quant != "" && quant != "fp8" {
		warnings = append(warnings, fmt.Sprintf("Quantization %s is served as-is by SGLang; check the model card.", quant))
	}
	return map[string]any{"model": clone(entry), "warnings": warnings, "size": number(total)}, nil
}

func resolveGGUF(ctx context.Context, repo, file, revision string) (map[string]any, error) {
	if !repoInputRE.MatchString(repo) || !strings.HasSuffix(file, ".gguf") || strings.Contains(file, "/") {
		return nil, fmt.Errorf("Enter a Hugging Face repository and a .gguf file name")
	}
	if revision == "" {
		revision = "main"
	}
	var info map[string]any
	if err := hfJSON(ctx, fmt.Sprintf("/api/models/%s/revision/%s?blobs=true", repo, revision), &info); err != nil {
		return nil, err
	}
	var sibling map[string]any
	for _, s := range listOf(info["siblings"]) {
		if mapOf(s)["rfilename"] == file {
			sibling = mapOf(s)
		}
	}
	lfs := mapOf(sibling["lfs"])
	if sibling == nil || lfs == nil {
		return nil, fmt.Errorf("That GGUF file is not in the repository")
	}
	size, sha := intOf(lfs["size"]), strOf(info["sha"])
	family := strings.ToLower(repo)
	tools := false
	for _, k := range []string{"qwen2.5", "qwen3", "llama-3", "mistral"} {
		tools = tools || strings.Contains(family, k)
	}
	name := strings.TrimSuffix(file, ".gguf")
	display := name
	if len(display) > 120 {
		display = display[:120]
	}
	license := strOf(mapOf(info["cardData"])["license"])
	if license == "" {
		license = "see model card"
	}
	if len(license) > 64 {
		license = license[:64]
	}
	digest := strOf(lfs["sha256"])
	entry := map[string]any{"id": fmt.Sprintf("gguf-%s-%s", slug(name, 60), digest[:8]), "display_name": display, "repo": repo,
		"license": license, "context_length": number(8192), "capabilities": map[string]any{"tools": tools, "reasoning": false, "vision": false},
		"min_memory_bytes": number(int64(float64(size)*1.2) + 2*gib),
		"source": map[string]any{"url": fmt.Sprintf("%s/%s/resolve/%s/%s", HFBase, repo, sha, file), "sha256": digest,
			"size": number(size), "name": file, "revision": sha, "format": "gguf"}}
	warnings := []any{}
	if !tools {
		warnings = append(warnings, "Tool use depends on the GGUF chat template; it may only chat.")
	}
	return map[string]any{"model": entry, "warnings": warnings, "size": number(size)}, nil
}

var ollamaRefRE = regexp.MustCompile(`^(?:([a-z0-9][a-z0-9._-]{0,63})/)?([a-z0-9][a-z0-9._-]{0,95})(?::([A-Za-z0-9][A-Za-z0-9._-]{0,127}))?$`)

var templateParameters = map[string]string{"stop": "list", "temperature": "float", "top_p": "float", "top_k": "int", "min_p": "float",
	"repeat_penalty": "float", "presence_penalty": "float", "repeat_last_n": "int"}

// importSettings applies the connector's bounds on an Ollama template/parameters.
func importSettings(template string, parameters map[string]any) (map[string]any, error) {
	if len(template) > 32<<10 {
		return nil, fmt.Errorf("Model template must be text under 32 KB")
	}
	if len(parameters) > 16 {
		return nil, fmt.Errorf("Model parameters must be a small object")
	}
	clean := map[string]any{}
	for key, value := range parameters {
		switch templateParameters[key] {
		case "":
			return nil, fmt.Errorf("Unsupported model parameter %s", key)
		case "list":
			list, ok := value.([]any)
			if !ok || len(list) > 16 {
				return nil, fmt.Errorf("Stop sequences must be a short list of strings")
			}
			for _, v := range list {
				if s, ok := v.(string); !ok || len(s) > 64 {
					return nil, fmt.Errorf("Stop sequences must be a short list of strings")
				}
			}
		case "int":
			if !isInt(value) {
				return nil, fmt.Errorf("Invalid model parameter %s", key)
			}
		case "float":
			if _, ok := value.(json.Number); !ok {
				return nil, fmt.Errorf("Invalid model parameter %s", key)
			}
		}
		clean[key] = value
	}
	return clean, nil
}

func resolveOllama(ctx context.Context, ref string) (map[string]any, error) {
	match := ollamaRefRE.FindStringSubmatch(ref)
	if match == nil {
		return nil, fmt.Errorf("Enter an Ollama model like qwen3:8b")
	}
	namespace, name, tag := match[1], match[2], match[3]
	if namespace == "" {
		namespace = "library"
	}
	if tag == "" {
		tag = "latest"
	}
	base := fmt.Sprintf("%s/v2/%s/%s", OllamaRegistry, namespace, name)
	status, body, err := getJSON(ctx, base+"/manifests/"+tag, map[string]string{"Accept": "application/vnd.docker.distribution.manifest.v2+json"}, nil)
	if err != nil && status == 0 {
		return nil, err
	}
	if status == 404 {
		return nil, fmt.Errorf("%s:%s is not in the Ollama library", name, tag)
	}
	if status != 200 {
		return nil, fmt.Errorf("the Ollama registry returned HTTP %d", status)
	}
	manifestDigest := sha256Hex(body)
	var manifest map[string]any
	if err := decode(body, &manifest); err != nil {
		return nil, err
	}
	layers := map[string]map[string]any{}
	for _, l := range listOf(manifest["layers"]) {
		layer := mapOf(l)
		media := strOf(layer["mediaType"])
		layers[media[strings.LastIndex(media, ".")+1:]] = layer
	}
	model := layers["model"]
	if model == nil {
		return nil, fmt.Errorf("This Ollama model has no local weights (cloud-only models cannot be deployed)")
	}
	texts := map[string]string{}
	for _, kind := range []string{"template", "params"} {
		layer := layers[kind]
		if layer == nil {
			continue
		}
		if intOf(layer["size"]) > 64<<10 {
			return nil, fmt.Errorf("Unexpectedly large %s layer", kind)
		}
		status, blob, err := getJSON(ctx, base+"/blobs/"+strOf(layer["digest"]), nil, nil)
		if err != nil || status != 200 {
			return nil, fmt.Errorf("could not read the %s layer (HTTP %d)", kind, status)
		}
		if "sha256:"+sha256Hex(blob) != layer["digest"] {
			return nil, fmt.Errorf("The %s layer does not match its digest", kind)
		}
		texts[kind] = string(blob)
	}
	raw := map[string]any{}
	if p, ok := texts["params"]; ok {
		if err := decode([]byte(p), &raw); err != nil {
			return nil, err
		}
	}
	parameters := map[string]any{}
	for k, v := range raw {
		if templateParameters[k] != "" {
			parameters[k] = v
		}
	}
	template := texts["template"]
	if _, err := importSettings(template, parameters); err != nil {
		return nil, err
	}
	digest := strings.TrimPrefix(strOf(model["digest"]), "sha256:")
	size := intOf(model["size"])
	display := name + ":" + tag
	if namespace != "library" {
		display = namespace + "/" + display
	}
	contextLength := intOf(raw["num_ctx"])
	if contextLength == 0 || contextLength > 131072 {
		contextLength = 8192
	}
	_, projector := layers["projector"]
	entry := map[string]any{"id": fmt.Sprintf("ollama-%s-%s", slug(strings.NewReplacer(":", "-", "/", "-").Replace(display), 70), digest[:8]),
		"display_name": display, "repo": "ollama:" + display, "license": "see ollama.com/library/" + name,
		"context_length": number(contextLength),
		"capabilities": map[string]any{"tools": strings.Contains(template, ".Tools"),
			"reasoning": strings.Contains(template, ".Think") || strings.Contains(strings.ToLower(template), "think"), "vision": false},
		"min_memory_bytes": number(int64(float64(size)*1.2) + 2*gib), "template": template, "parameters": parameters,
		"source": map[string]any{"url": fmt.Sprintf("%s/blobs/sha256:%s", base, digest), "sha256": digest, "size": number(size),
			"name": slug(display, 80) + ".gguf", "revision": manifestDigest, "format": "gguf"}}
	warnings := []any{}
	if projector {
		warnings = append(warnings, "Vision projector layers are not imported; the model runs text-only.")
	}
	return map[string]any{"model": entry, "warnings": warnings, "size": number(size)}, nil
}

func (m *Manager) Resolve(ctx context.Context, engine, repo, revision, file string) (map[string]any, error) {
	switch engine {
	case "sglang":
		return m.resolveHF(ctx, repo, revision)
	case "ollama":
		if file != "" {
			return resolveGGUF(ctx, repo, file, revision)
		}
		if revision != "" {
			repo += ":" + revision
		}
		return resolveOllama(ctx, repo)
	}
	return nil, fmt.Errorf("Choose SGLang or Ollama")
}

var (
	dtypeBytes = map[string]float64{"F64": 8, "F32": 4, "I32": 4, "U32": 4, "BF16": 2, "F16": 2, "I16": 2, "F8_E4M3": 1, "F8_E5M2": 1, "I8": 1, "U8": 1}
	hfSorts    = map[string]string{"popular": "downloads", "trending": "trendingScore", "likes": "likes", "recent": "lastModified"}
)

func weightsBytes(safetensors map[string]any, packedFP4 bool) int64 {
	var total float64
	for dtype, n := range mapOf(safetensors["parameters"]) {
		if !isInt(n) {
			continue
		}
		size, ok := dtypeBytes[dtype]
		if !ok {
			size = 2
		}
		if packedFP4 && (dtype == "I8" || dtype == "U8") {
			size = .5
		}
		total += size * float64(intOf(n))
	}
	return int64(total)
}

func (c *Connector) architectures() map[string]bool {
	var doc struct {
		Architectures []string `json:"architectures"`
	}
	_ = decode(c.Tree["sglang-architectures.json"].Data, &doc)
	out := map[string]bool{}
	for _, a := range doc.Architectures {
		out[a] = true
	}
	return out
}

func (m *Manager) searchHF(ctx context.Context, query string, limit int64, gpu, sortBy string, memory int64, count int64) ([]map[string]any, error) {
	params := url.Values{}
	if query != "" {
		params.Add("search", query)
	}
	params.Add("pipeline_tag", "text-generation")
	params.Add("filter", "safetensors")
	sortKey := hfSorts[sortBy]
	if sortKey == "" {
		sortKey = "downloads"
	}
	params.Add("sort", sortKey)
	params.Add("direction", "-1")
	params.Add("limit", fmt.Sprint(max(1, min(limit*2, 100))))
	for _, k := range []string{"config", "downloads", "likes", "lastModified", "safetensors", "gated", "tags"} {
		params.Add("expand[]", k)
	}
	var rows []map[string]any
	status, _, err := getJSON(ctx, HFBase+"/api/models?"+params.Encode(), nil, &rows)
	if err != nil || status != 200 {
		return nil, fmt.Errorf("Hugging Face search failed (HTTP %d)", status)
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	supported := c.architectures()
	if memory == 0 {
		memory = gpuMemory[gpu]
	}
	results := []map[string]any{}
	for _, row := range rows {
		config := mapOf(row["config"])
		var archs []string
		for _, a := range listOf(config["architectures"]) {
			archs = append(archs, strOf(a))
		}
		quantConfig := mapOf(config["quantization_config"])
		quant := strings.ToLower(strOf(quantConfig["quant_method"]))
		total := mapOf(row["safetensors"])["total"]
		if isInt(total) && intOf(total) < 100_000_000 {
			continue // test fixtures and toy checkpoints
		}
		weights := weightsBytes(mapOf(row["safetensors"]), config["expert_dtype"] == "fp4")
		var need int64
		if weights != 0 {
			need = weights*11/10 + 6*gib
		}
		reason := ""
		archSupported := false
		for _, a := range archs {
			archSupported = archSupported || supported[a]
		}
		tags := map[string]bool{}
		for _, t := range listOf(row["tags"]) {
			tags[strOf(t)] = true
		}
		switch {
		case truthy(row["gated"]):
			reason = "Gated on Hugging Face; only public models can be pinned"
		case len(archs) == 0:
			reason = "No model architecture declared"
		case !archSupported:
			reason = "SGLang 0.5.20 does not serve " + archs[0]
		case truthy(config["auto_map"]):
			reason = "Needs custom code (trust_remote_code)"
		case tags["mlx"]:
			reason = "MLX weights run on Apple silicon; choose the original model"
		case (quant == "modelopt" || quant == "modelopt_fp4" || quant == "nvfp4" || quant == "mxfp4") && !strings.Contains(strings.ToLower(pyRepr(quantConfig)), "fp8"):
			reason = quant + " (FP4) needs Blackwell GPUs"
		case quant == "gptq" || quant == "awq" || quant == "bitsandbytes":
			reason = strings.ToUpper(quant) + " quantized weights are not supported here; choose the original or FP8 model"
		case quant == "fp8" && gpu != "" && !strings.Contains(gpu, "H100") && !strings.Contains(gpu, "H200") && !strings.Contains(gpu, "B200") && !strings.Contains(gpu, "L40S"):
			reason = "FP8 needs H100, H200, B200 or L40S"
		}
		var fits, gpus any
		if memory != 0 && need != 0 {
			most := count
			if most == 0 {
				most = 8
			}
			n := gpusNeeded(map[string]any{"minimum_gpu_memory_bytes": number(need)}, memory, most)
			fits = n != 0
			if n != 0 {
				gpus = number(n)
			}
			if n == 0 && reason == "" {
				if count != 0 {
					reason = fmt.Sprintf("Needs more than the %d GPUs of this machine", count)
				} else {
					reason = "Too large even for 8 GPUs of one machine"
				}
			}
		}
		tool, reasoning := parsers(config, strOf(row["id"]))
		arch := ""
		if len(archs) > 0 {
			arch = archs[0]
		}
		var weightsValue, needValue any
		if weights != 0 {
			weightsValue = number(weights)
		}
		if need != 0 {
			needValue = number(need)
		}
		downloads, likes := row["downloads"], row["likes"]
		if downloads == nil {
			downloads = number(0)
		}
		if likes == nil {
			likes = number(0)
		}
		results = append(results, map[string]any{"id": row["id"], "downloads": downloads, "likes": likes, "updated": row["lastModified"],
			"architecture": arch, "quantization": quant, "parameters": total, "weights_bytes": weightsValue, "gpu_memory_needed": needValue,
			"fits": fits, "gpus_needed": gpus, "tools": tool != "", "reasoning": reasoning != "", "supported": reason == "", "reason": reason})
		if int64(len(results)) >= limit {
			break
		}
	}
	return results, nil
}

// pyRepr approximates str(dict) for a substring test.
func pyRepr(v any) string { return string(Compact(v)) }

var (
	ollamaBlockRE = regexp.MustCompile(`(?s)<li[^>]*>\s*<a href="/library/([^"]+)"(.*?)</li>`)
	ollamaDescRE  = regexp.MustCompile(`<p[^>]*>([^<]+)</p>`)
	ollamaChipRE  = regexp.MustCompile(`<span[^>]*rounded-md[^>]*>([^<]+)</span>`)
	ollamaSizeRE  = regexp.MustCompile(`^([0-9.]+[bmk](-[a-z0-9]+)?|[0-9]+x[0-9.]+b|e[0-9]+b)$`)
	ollamaPullRE  = regexp.MustCompile(`x-test-pull-count[^>]*>([^<]+)<`)
)

func parseOllamaSearch(text string, limit int) []any {
	results := []any{}
	for i, block := range ollamaBlockRE.FindAllStringSubmatch(text, -1) {
		if i >= limit {
			break
		}
		name, body := block[1], block[2]
		description := ""
		if d := ollamaDescRE.FindStringSubmatch(body); d != nil {
			description = html.UnescapeString(strings.TrimSpace(d[1]))
		}
		sizes, capabilities := []any{}, []any{}
		cloud := false
		for _, c := range ollamaChipRE.FindAllStringSubmatch(body, -1) {
			chip := strings.TrimSpace(html.UnescapeString(c[1]))
			lower := strings.ToLower(chip)
			if ollamaSizeRE.MatchString(lower) {
				sizes = append(sizes, chip)
			}
			switch lower {
			case "tools", "thinking", "vision", "embedding", "cloud", "audio":
				capabilities = append(capabilities, chip)
				cloud = cloud || chip == "cloud"
			}
		}
		pulls := ""
		if p := ollamaPullRE.FindStringSubmatch(body); p != nil {
			pulls = p[1]
		}
		results = append(results, map[string]any{"name": name, "description": description, "tags": sizes, "capabilities": capabilities,
			"pulls": pulls, "cloud_only": cloud && len(sizes) == 0})
	}
	return results
}

func searchOllama(ctx context.Context, query string, limit int64) ([]any, error) {
	params := url.Values{}
	if query != "" {
		params.Set("q", query)
	} else {
		params.Set("o", "popular")
	}
	status, body, err := getJSON(ctx, OllamaBase+"/search?"+params.Encode(), nil, nil)
	if err != nil || status != 200 {
		return nil, fmt.Errorf("Ollama search failed (HTTP %d)", status)
	}
	return parseOllamaSearch(string(body), int(max(1, min(limit, 50)))), nil
}

// onMachine lists models already served by Ollama or LM Studio on a node.
func (m *Manager) onMachine(ctx context.Context, nodeID string) ([]any, error) {
	rows, err := m.dir.deployments(ctx)
	if err != nil {
		return nil, err
	}
	found := []any{}
	for _, row := range rows {
		if row["node_id"] != nodeID || (row["engine"] != "ollama" && row["engine"] != "lmstudio") || row["state"] != "ready" {
			continue
		}
		models := listOf(row["models"])
		if discovered, err := m.Discover(ctx, strOf(row["deployment_id"])); err == nil {
			models = listOf(discovered["models"])
		}
		for _, item := range models {
			model := mapOf(item)
			name := model["name"]
			if !truthy(name) {
				name = model["id"]
			}
			published := false
			for _, p := range listOf(row["models"]) {
				published = published || mapOf(p)["id"] == model["id"]
			}
			found = append(found, map[string]any{"name": name, "id": model["id"], "deployment_id": row["deployment_id"],
				"engine": row["engine"], "source": "on_machine", "published": published})
		}
	}
	return found, nil
}

func (m *Manager) Search(ctx context.Context, engine, query, nodeID, gpu string, limit int64, sortBy string) (map[string]any, error) {
	switch engine {
	case "sglang":
		var t target
		var err error
		if nodeID != "" || gpu != "" {
			if t, err = m.target(ctx, nodeID, gpu, nil); err != nil {
				return nil, err
			}
		} else {
			t.GPUName = gpu
		}
		var count int64
		if nodeID != "" {
			count = t.GPUCount
		}
		results, err := m.searchHF(ctx, query, limit, t.GPUName, sortBy, t.GPUMemory, count)
		if err != nil {
			return nil, err
		}
		return map[string]any{"engine": "sglang", "sort": sortBy, "results": results}, nil
	case "ollama":
		results, err := searchOllama(ctx, query, limit)
		if err != nil {
			return nil, err
		}
		machine := []any{}
		if nodeID != "" {
			if machine, err = m.onMachine(ctx, nodeID); err != nil {
				return nil, err
			}
		}
		if query != "" {
			var kept []any
			for _, item := range machine {
				if strings.Contains(strings.ToLower(fmt.Sprint(mapOf(item)["name"])), strings.ToLower(query)) {
					kept = append(kept, item)
				}
			}
			machine = append([]any{}, kept...)
		}
		return map[string]any{"engine": "ollama", "results": results, "on_machine": machine}, nil
	}
	return nil, fmt.Errorf("Search SGLang (Hugging Face) or Ollama models")
}

var (
	featuredOrgs = map[string]bool{"deepseek-ai": true, "Qwen": true, "meta-llama": true, "mistralai": true, "google": true, "zai-org": true,
		"moonshotai": true, "openai": true, "nvidia": true, "microsoft": true, "MiniMaxAI": true, "ibm-granite": true, "allenai": true,
		"tencent": true, "baidu": true, "XiaomiMiMo": true}
	familyRE = regexp.MustCompile(`(-(\d{4}|instruct|it|chat|base))+$`)
)

// Featured are official releases worth trying on the target: trending and
// most used, tool-calling, 7B+, servable and fitting; one per family.
func (m *Manager) Featured(ctx context.Context, nodeID, gpu string) (map[string]any, error) {
	var t target
	var err error
	if nodeID != "" || gpu != "" {
		if t, err = m.target(ctx, nodeID, gpu, nil); err != nil {
			return nil, err
		}
	} else {
		t.GPUName = gpu
	}
	var count int64
	if nodeID != "" {
		count = t.GPUCount
	}
	lists := make([][]map[string]any, 2)
	errs := make([]error, 2)
	var wg sync.WaitGroup
	for i, sortBy := range []string{"trending", "popular"} {
		wg.Add(1)
		go func(i int, sortBy string) {
			defer wg.Done()
			lists[i], errs[i] = m.searchHF(ctx, "", 50, t.GPUName, sortBy, t.GPUMemory, count)
		}(i, sortBy)
	}
	wg.Wait()
	for _, e := range errs {
		if e != nil {
			return nil, e
		}
	}
	seen, sections := map[string]bool{}, map[string]any{}
	for i, name := range []string{"trending", "popular"} {
		picked := []any{}
		for _, row := range lists[i] {
			id := strOf(row["id"])
			family := familyRE.ReplaceAllString(strings.ToLower(id), "")
			params := intOf(row["parameters"])
			if !featuredOrgs[strings.SplitN(id, "/", 2)[0]] || row["supported"] != true || row["fits"] == false || row["tools"] != true || params < 7e9 || seen[family] {
				continue
			}
			seen[family] = true
			picked = append(picked, row)
			if len(picked) >= 4 {
				break
			}
		}
		sections[name] = picked
	}
	return sections, nil
}

// ---- deploy / status ------------------------------------------------------------

func (c *Connector) ollamaModel(model any) (map[string]any, error) {
	entry := mapOf(model)
	if entry != nil && truthy(entry["catalog_id"]) {
		catalog, err := c.ollamaCatalog()
		if err != nil {
			return nil, err
		}
		for _, m := range catalog {
			if m["id"] == entry["catalog_id"] {
				return m, nil
			}
		}
		return nil, fmt.Errorf("Choose an Ollama catalog model")
	}
	if entry == nil || !regexp.MustCompile(`^(gguf|ollama)-[a-z0-9][a-z0-9.-]{0,90}$`).MatchString(strOf(entry["id"])) {
		return nil, fmt.Errorf("Choose an Ollama model or resolve a GGUF file first")
	}
	source := mapOf(entry["source"])
	if source["format"] != "gguf" || !digest64RE.MatchString(strOf(source["sha256"])) || !isInt(source["size"]) ||
		!(strings.HasPrefix(strOf(source["url"]), HFBase+"/") || strings.HasPrefix(strOf(source["url"]), OllamaRegistry+"/v2/")) {
		return nil, fmt.Errorf("A custom model needs a pinned Hugging Face or Ollama registry URL, sha256 and size")
	}
	if entry["template"] != nil || entry["parameters"] != nil {
		if _, err := importSettings(strOf(entry["template"]), mapOf(entry["parameters"])); err != nil {
			return nil, err
		}
	}
	return entry, nil
}

func (m *Manager) Deploy(ctx context.Context, tgt map[string]any, engine string, model any, name string, contextLength any) (map[string]any, error) {
	if tgt == nil || (tgt["kind"] != "modal" && tgt["kind"] != "node") {
		return nil, fmt.Errorf("Choose a new Modal machine or one of your nodes")
	}
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	var entry map[string]any
	switch engine {
	case "sglang":
		if catalog := mapOf(model); catalog != nil && truthy(catalog["catalog_id"]) {
			entry, err = c.LLM(catalog["catalog_id"])
		} else {
			entry, err = c.LLM(model)
		}
	case "ollama":
		entry, err = c.ollamaModel(model)
	default:
		return nil, fmt.Errorf("Choose SGLang or Ollama")
	}
	if err != nil {
		return nil, err
	}
	label := strOf(entry["display_name"])
	planModel := model
	if catalog := mapOf(model); catalog != nil && truthy(catalog["catalog_id"]) {
		planModel = map[string]any{"catalog_id": catalog["catalog_id"]}
	}
	planName := name
	if planName == "" {
		planName = label
	}
	if len(planName) > 120 {
		planName = planName[:120]
	}
	plan := map[string]any{"engine": engine, "name": planName, "model": planModel}
	if contextLength != nil {
		maximum := entry["maximum_context_length"]
		if !truthy(maximum) {
			maximum = entry["context_length"]
		}
		if !isInt(contextLength) || intOf(contextLength) < 512 || intOf(contextLength) > intOf(maximum) {
			return nil, fmt.Errorf("Context must be between 512 and %s tokens for this model", commas(intOf(maximum)))
		}
		plan["context_length"] = contextLength
	}
	if tgt["kind"] == "modal" {
		gpu := strOf(tgt["gpu"])
		if gpu == "" {
			gpu = "H100"
		}
		if engine == "sglang" && gpu == "none" {
			return nil, fmt.Errorf("SGLang needs a GPU")
		}
		asked := tgt["gpu_count"]
		if engine != "sglang" && asked != nil && intOf(asked) != 1 {
			return nil, fmt.Errorf("Only SGLang spreads a model over several GPUs")
		}
		facts, err := m.target(ctx, "", gpu, asked)
		if err != nil {
			return nil, err
		}
		var fits bool
		var reason string
		count := int64(1)
		if engine == "sglang" {
			fits, reason, count = sglangPlan(entry, facts)
		} else {
			fits, reason = ollamaFit(entry, facts)
		}
		if !fits {
			return nil, fmt.Errorf("%s", reason)
		}
		hint := name
		if hint == "" {
			hint = label
		}
		serviceID, err := nodeServiceID(hint)
		if err != nil {
			return nil, err
		}
		hours := 4.0
		if h, ok := tgt["lifetime_hours"].(json.Number); ok {
			hours, _ = h.Float64()
		}
		if hours <= 0 || hours > 24 {
			return nil, fmt.Errorf("Time limit must be between 0 and 24 hours")
		}
		sizes := map[string]any{}
		for _, k := range []string{"cpu", "memory_gib"} {
			if tgt[k] != nil {
				sizes[k] = tgt[k]
			}
		}
		if err := m.launch(ctx, serviceID, gpu, count, int64(hours*60), sizes); err != nil {
			return nil, err
		}
		plan["service_id"], plan["deployment_id"], plan["gpu_count"] = serviceID, "modal-"+serviceID, number(count)
	} else {
		facts, err := m.target(ctx, strOf(tgt["node_id"]), "", nil)
		if err != nil {
			return nil, err
		}
		var fits bool
		var reason string
		if engine == "sglang" {
			fits, reason, _ = sglangPlan(entry, facts)
			if !(facts.Modal && facts.GPUName != "") {
				return nil, fmt.Errorf("SGLang runs on platform GPU machines (Modal)")
			}
		} else {
			fits, reason = ollamaFit(entry, facts)
		}
		if !fits {
			return nil, fmt.Errorf("%s", reason)
		}
		if facts.ServiceID != "" {
			plan["service_id"], plan["deployment_id"] = facts.ServiceID, "modal-"+facts.ServiceID
		} else {
			hint := name
			if hint == "" {
				hint = label
			}
			plan["node_id"], plan["deployment_id"] = facts.NodeID, engine+"-"+slug(hint, 50)
		}
	}
	if err := m.plans.Save(m.nodes.fleet, plan); err != nil {
		return nil, err
	}
	return m.DeployStatus(ctx, strOf(plan["deployment_id"]))
}

func (m *Manager) DeployStatus(ctx context.Context, id string) (map[string]any, error) {
	plan, err := m.plans.Load(m.nodes.fleet, id)
	if err != nil {
		return nil, err
	}
	if plan == nil {
		row, err := m.dir.deployment(ctx, id)
		if err != nil {
			return nil, fmt.Errorf("Unknown deployment")
		}
		phase := row["state"]
		if phase == "ready" {
			phase = "ready"
		}
		return map[string]any{"deployment_id": id, "phase": phase, "ready": row["state"] == "ready"}, nil
	}
	var route any
	if truthy(plan["service_id"]) {
		route = "fleet-route://" + strOf(plan["service_id"])
	}
	base := map[string]any{"deployment_id": id, "engine": plan["engine"], "name": plan["name"], "service_id": plan["service_id"], "route": route}
	var result map[string]any
	if plan["engine"] == "sglang" {
		model := mapOf(plan["model"])
		catalogID := strOf(model["catalog_id"])
		var custom map[string]any
		if catalogID == "" {
			catalogID, custom = "qwen3.6-35b-a3b-fp8", model
		}
		if result, err = m.ModalAdvance(ctx, strOf(plan["service_id"]), catalogID, custom, plan["context_length"]); err != nil {
			return nil, err
		}
		out := clone(base).(map[string]any)
		for k, v := range result {
			if _, inBase := base[k]; !inBase || v != nil {
				out[k] = v
			}
		}
		return out, nil
	}
	if result, err = m.advanceOllama(ctx, plan); err != nil {
		return nil, err
	}
	out := clone(base).(map[string]any)
	for k, v := range result {
		out[k] = v
	}
	return out, nil
}

func ollamaRecipe(platform string) string {
	if strings.HasPrefix(platform, "darwin-") {
		return "ollama-" + ollamaVersion + "-darwin"
	}
	return "ollama-" + ollamaVersion + "-" + platform
}

func (m *Manager) advanceOllama(ctx context.Context, plan map[string]any) (map[string]any, error) {
	c, err := m.connector(ctx)
	if err != nil {
		return nil, err
	}
	entry, err := c.ollamaModel(plan["model"])
	if err != nil {
		return nil, err
	}
	dep := strOf(plan["deployment_id"])
	nodeID := strOf(plan["node_id"])
	if serviceID := strOf(plan["service_id"]); serviceID != "" {
		launches, err := m.modalServices(ctx)
		if err != nil {
			return nil, err
		}
		launched := false
		for _, s := range launches {
			launched = launched || s["service_id"] == serviceID
		}
		if !launched {
			return map[string]any{"phase": "stopped", "ready": false}, nil
		}
		nodes, err := m.records(ctx)
		if err != nil {
			return nil, err
		}
		node := nodeFor(nodes, serviceID)
		if node == nil {
			return map[string]any{"phase": "starting_node", "ready": false}, nil
		}
		nodeID = strOf(node["node_id"])
	}
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
	if row == nil {
		facts, err := m.target(ctx, nodeID, "", nil)
		if err != nil {
			return nil, err
		}
		need := intOf(entry["min_memory_bytes"])
		var devices []any
		var memory int64
		switch {
		case facts.GPUID != "" && facts.GPUMemory*9/10 >= need:
			devices = []any{map[string]any{"id": facts.GPUID, "backend": "cuda", "memory_bytes": number(min(facts.GPUMemory*9/10, max(need, 4*gib))), "exclusive": false}}
			memory = 8 * gib
		case facts.Metal:
			memory = max(need, 4*gib)
			devices = []any{map[string]any{"id": "apple-metal", "backend": "metal", "memory_bytes": number(memory), "exclusive": false}}
		default:
			devices, memory = []any{}, max(need, 2*gib)
		}
		contextLength := plan["context_length"]
		if !truthy(contextLength) {
			contextLength = entry["context_length"]
		}
		config := map[string]any{"recipe_id": ollamaRecipe(facts.Platform), "context_length": contextLength, "parallel": number(1),
			"keep_alive_seconds": number(1800), "load_policy": "warm", "resources": map[string]any{"memory_bytes": number(memory), "devices": devices}}
		if row, err = m.CreateManaged(ctx, dep, strOf(plan["name"]), nodeID, config); err != nil {
			return nil, err
		}
	}
	if row["state"] != "ready" {
		catalog, err := m.Engines(ctx, dep, "catalog", "", false)
		if err != nil {
			return nil, err
		}
		var recipe map[string]any
		for _, r := range listOf(catalog["recipes"]) {
			if mapOf(r)["id"] == mapOf(row["managed"])["recipe_id"] {
				recipe = mapOf(r)
			}
		}
		if recipe["prepared"] != true {
			jobs, err := m.Engines(ctx, dep, "jobs", "", false)
			if err != nil {
				return nil, err
			}
			var job map[string]any
			for _, j := range listOf(jobs["jobs"]) {
				if mapOf(j)["job_id"] == recipe["id"] {
					job = mapOf(j)
				}
			}
			if job == nil || job["state"] == "failed" || job["state"] == "cancelled" {
				if _, err := m.Engines(ctx, dep, "prepare", strOf(mapOf(row["managed"])["recipe_id"]), true); err != nil {
					return nil, err
				}
			}
			done := job["bytes_done"]
			if done == nil {
				done = number(0)
			}
			total := mapOf(recipe["source"])["size"]
			if total == nil {
				total = number(0)
			}
			return map[string]any{"phase": "preparing_engine", "ready": false, "progress": map[string]any{"bytes": done, "total": total, "error": strOf(job["error"])}}, nil
		}
		if failure, running := m.tasks.Start("deploy/"+m.nodes.fleet+"/"+dep, func() error {
			_, err := m.SetRunning(context.WithoutCancel(ctx), dep, true)
			return err
		}); failure != "" && !running {
			return map[string]any{"phase": "failed", "ready": false, "error": truncate(failure, 300)}, nil
		}
		return map[string]any{"phase": "starting_engine", "ready": false}, nil
	}
	source := mapOf(entry["source"])
	digest := strOf(source["sha256"])
	jobID := "weights-" + digest[:16]
	artifacts, err := m.Artifacts(ctx, dep, "list", "", nil, false)
	if err != nil {
		return nil, err
	}
	var job map[string]any
	for _, j := range listOf(artifacts["jobs"]) {
		if mapOf(j)["job_id"] == jobID {
			job = mapOf(j)
		}
	}
	if job == nil || job["state"] == "failed" || job["state"] == "cancelled" {
		if _, err := m.Artifacts(ctx, dep, "submit", jobID, source, true); err != nil {
			return nil, err
		}
	}
	if job == nil || job["state"] != "ready" {
		done := job["bytes_done"]
		if done == nil {
			done = number(0)
		}
		return map[string]any{"phase": "downloading_weights", "ready": false, "progress": map[string]any{"bytes": done, "total": source["size"], "error": strOf(job["error"])}}, nil
	}
	modelID := "fleet/" + digest + ":latest"
	published := false
	for _, p := range listOf(row["models"]) {
		published = published || mapOf(p)["id"] == modelID
	}
	if !published {
		state, err := m.ModelOperations(ctx, dep, "status", "", "", "", "", nil, "", nil)
		if err != nil {
			return nil, err
		}
		imported := false
		for _, s := range listOf(state["models"]) {
			imported = imported || mapOf(s)["id"] == modelID
		}
		if !imported {
			importID := "import-" + digest[:16]
			var existing map[string]any
			for _, j := range listOf(state["jobs"]) {
				if mapOf(j)["job_id"] == importID {
					existing = mapOf(j)
				}
			}
			if existing != nil && existing["state"] == "failed" {
				reason := strOf(existing["error"])
				if reason == "" {
					reason = "Model import failed"
				}
				return map[string]any{"phase": "failed", "ready": false, "error": reason}, nil
			}
			if existing == nil {
				params := mapOf(entry["parameters"])
				if _, err := m.ModelOperations(ctx, dep, "submit", importID, "import", jobID, "", nil, strOf(entry["template"]), params); err != nil {
					return nil, err
				}
			}
			return map[string]any{"phase": "publishing", "ready": false}, nil
		}
		caps := mapOf(entry["capabilities"])
		if row, err = m.Publish(ctx, dep, []any{map[string]any{"id": modelID, "name": entry["display_name"], "operations": []any{"text"},
			"tools": caps["tools"], "reasoning": caps["reasoning"], "vision": caps["vision"], "context": mapOf(row["managed"])["context_length"],
			"compute": "node"}}, row["revision"]); err != nil {
			return nil, err
		}
	}
	if serviceID := strOf(plan["service_id"]); serviceID != "" {
		routes, err := m.dir.routes(ctx)
		if err != nil {
			return nil, err
		}
		var route map[string]any
		for _, r := range routes {
			if mapOf(r)["route_id"] == serviceID {
				route = mapOf(r)
			}
		}
		candidate := map[string]any{"deployment_id": dep, "model_id": modelID}
		if route == nil || !same(route["candidates"], []any{candidate}) || !same(route["allowed_nodes"], []any{nodeID}) {
			revision := any(number(0))
			if route != nil {
				revision = route["revision"]
			}
			if _, err := m.dir.routeOperation(ctx, "save", map[string]any{"route_id": serviceID, "name": strOf(entry["display_name"]) + " (Modal)",
				"candidates": []any{candidate}, "allowed_nodes": []any{nodeID},
				"requires": map[string]any{"operation": "text", "tools": mapOf(entry["capabilities"])["tools"], "context": mapOf(row["managed"])["context_length"]},
				"revision": revision}, "", nil, nil); err != nil {
				return nil, err
			}
		}
	}
	return map[string]any{"phase": "ready", "ready": true, "node_id": nodeID, "model": modelRef(dep, modelID)}, nil
}

// modelRef is fleet-model://<deployment>/<model, percent-quoted like Python's quote(safe="")>.
func modelRef(deployment, model string) string {
	var b strings.Builder
	for _, c := range []byte(model) {
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '.' || c == '-' || c == '~' {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return "fleet-model://" + deployment + "/" + b.String()
}
