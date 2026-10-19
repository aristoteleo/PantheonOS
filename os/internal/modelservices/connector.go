package modelservices

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"slices"
	"strings"
)

// Connector is the model connector's source, as the release set ships it
// (the tree the Python management App bundled as models/_connector): the
// connector package itself, the files generated engine packages carry, and
// the pinned engine and model catalogs.
type Connector struct {
	Tree Tree
	// group holds the group network sources the rank packages carry
	// (pantheon/models/group_{network,mesh,inference}.py), shipped as group/*.
	group     Tree
	engines   []map[string]any
	llm       []map[string]any
	speech    []map[string]any
	diffusion []map[string]any
}

func decode(raw []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	return d.Decode(v)
}

// NewConnector reads the catalogs from a connector source tree.
func NewConnector(tree Tree) (*Connector, error) {
	c := &Connector{Tree: Tree{}, group: Tree{}}
	for name, f := range tree {
		if rest, ok := strings.CutPrefix(name, "group/"); ok {
			c.group[rest] = f
		} else {
			c.Tree[name] = f
		}
	}
	tree = c.Tree
	for _, item := range []struct {
		file, key string
		into      *[]map[string]any
	}{{"engines.json", "recipes", &c.engines}, {"llm-models.json", "models", &c.llm},
		{"speech-models.json", "models", &c.speech}, {"diffusion-models.json", "models", &c.diffusion}} {
		f, ok := tree[item.file]
		if !ok {
			return nil, fmt.Errorf("connector source lacks %s", item.file)
		}
		var doc map[string]json.RawMessage
		if err := decode(f.Data, &doc); err != nil {
			return nil, fmt.Errorf("%s: %w", item.file, err)
		}
		if err := decode(doc[item.key], item.into); err != nil {
			return nil, fmt.Errorf("%s: %w", item.file, err)
		}
	}
	return c, nil
}

// ---- value helpers (JSON decoded with UseNumber) -----------------------------

func isInt(v any) bool {
	n, ok := v.(json.Number)
	if !ok || strings.ContainsAny(string(n), ".eE") {
		return false
	}
	_, err := n.Int64()
	return err == nil
}

func intOf(v any) int64 {
	switch t := v.(type) {
	case json.Number:
		n, err := t.Int64()
		if err != nil {
			f, _ := t.Float64()
			return int64(f)
		}
		return n
	case float64:
		return int64(t)
	case int:
		return int64(t)
	case int64:
		return t
	}
	return 0
}

func strOf(v any) string {
	s, _ := v.(string)
	return s
}

func listOf(v any) []any {
	l, _ := v.([]any)
	return l
}

func mapOf(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

func number(n int64) json.Number { return json.Number(fmt.Sprint(n)) }

// clone deep-copies a decoded JSON value.
func clone(v any) any {
	raw := Compact(v)
	var out any
	_ = decode(raw, &out)
	return out
}

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// ---- catalogs ----------------------------------------------------------------

// Recipes is the engine catalog.
func (c *Connector) Recipes() []map[string]any { return c.engines }

// Recipe is an engine recipe available for a node platform.
func (c *Connector) Recipe(id, target string) (map[string]any, error) {
	for _, r := range c.engines {
		if r["id"] == id {
			for _, p := range listOf(r["platforms"]) {
				if p == target {
					return r, nil
				}
			}
		}
	}
	return nil, fmt.Errorf("This engine recipe is not available for the selected node platform")
}

func (c *Connector) LLMCatalog() []map[string]any       { return c.llm }
func (c *Connector) SpeechCatalog() []map[string]any    { return c.speech }
func (c *Connector) DiffusionCatalog() []map[string]any { return c.diffusion }

var (
	repoRE       = regexp.MustCompile(`^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$`)
	commitRE     = regexp.MustCompile(`^[a-f0-9]{40}$`)
	customLLMRE  = regexp.MustCompile(`^hf-[a-z0-9][a-z0-9.-]{0,90}$`)
	filePartRE   = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$`)
	digest64RE   = regexp.MustCompile(`^[a-f0-9]{64}$`)
	deviceIDRE   = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$`)
	llmGPUs      = map[string]bool{"A100": true, "H100": true, "H200": true, "B200": true, "L40S": true}
	toolParsers  = map[string]bool{"qwen3_coder": true, "qwen25": true, "llama3": true, "mistral": true, "deepseekv3": true, "deepseekv31": true, "deepseekv32": true, "deepseekv4": true, "": true}
	reasonParser = map[string]bool{"qwen3": true, "deepseek-r1": true, "deepseek-v3": true, "deepseek-v4": true, "": true}
)

func reservedName(part string) bool {
	head := strings.ToUpper(strings.SplitN(part, ".", 2)[0])
	switch head {
	case "CON", "PRN", "AUX", "NUL":
		return true
	}
	if len(head) == 4 && (strings.HasPrefix(head, "COM") || strings.HasPrefix(head, "LPT")) && head[3] >= '1' && head[3] <= '9' {
		return true
	}
	return false
}

func validFilename(name string) bool {
	parts := strings.Split(name, "/")
	if len(name) > 240 || len(parts) > 8 {
		return false
	}
	for _, p := range parts {
		if !filePartRE.MatchString(p) || strings.HasSuffix(p, ".") || reservedName(p) {
			return false
		}
	}
	return true
}

// LLM is a validated language model entry: a catalog id or an explicit
// custom (hf-) manifest (former llm_models.model).
func (c *Connector) LLM(idOrEntry any) (map[string]any, error) {
	var selected map[string]any
	if entry, ok := idOrEntry.(map[string]any); ok {
		selected = clone(entry).(map[string]any)
		var pinned map[string]any
		for _, m := range c.llm {
			if m["id"] == selected["id"] {
				pinned = m
			}
		}
		if pinned != nil {
			if !bytes.Equal(Compact(selected), Compact(pinned)) {
				return nil, fmt.Errorf("Catalog language model entry does not match its pinned manifest")
			}
		} else if !customLLMRE.MatchString(strOf(selected["id"])) {
			return nil, fmt.Errorf("Custom language models need an hf- id")
		}
	} else {
		for _, m := range c.llm {
			if m["id"] == idOrEntry {
				selected = m
			}
		}
	}
	if selected == nil {
		return nil, fmt.Errorf("Choose a pinned language model")
	}
	bad := func(reason string) (map[string]any, error) { return nil, fmt.Errorf("%s", reason) }
	license, _ := selected["license"].(string)
	display, _ := selected["display_name"].(string)
	if !repoRE.MatchString(strOf(selected["model"])) || !commitRE.MatchString(strOf(selected["revision"])) ||
		selected["operation"] != "text" || len(license) == 0 || len(license) > 64 || len(display) == 0 || len(display) > 120 {
		return bad("Invalid pinned language model identity")
	}
	if gpus, ok := selected["supported_gpus"]; ok {
		list, isList := gpus.([]any)
		if !isList {
			return bad("Invalid pinned language model requirements")
		}
		for _, g := range list {
			if !llmGPUs[strOf(g)] {
				return bad("Invalid pinned language model requirements")
			}
		}
	}
	for _, k := range []string{"minimum_memory_bytes", "minimum_gpu_memory_bytes"} {
		if !isInt(selected[k]) || intOf(selected[k]) < 256<<20 {
			return bad("Invalid pinned language model requirements")
		}
	}
	caps := mapOf(selected["capabilities"])
	if len(caps) != 3 {
		return bad("Invalid pinned language model requirements")
	}
	for _, k := range []string{"tools", "reasoning", "vision"} {
		if _, ok := caps[k].(bool); !ok {
			return bad("Invalid pinned language model requirements")
		}
	}
	if v, ok := selected["tool_call_parser"]; ok && !toolParsers[strOf(v)] || ok && v == nil {
		return bad("Unsupported serving parser for this model")
	}
	if v, ok := selected["reasoning_parser"]; ok && !reasonParser[strOf(v)] || ok && v == nil {
		return bad("Unsupported serving parser for this model")
	}
	ctx, max := selected["context_length"], selected["maximum_context_length"]
	if !isInt(ctx) || !isInt(max) || !(512 <= intOf(ctx) && intOf(ctx) <= intOf(max) && intOf(max) <= 1048576) {
		return bad("Invalid pinned model context length")
	}
	names := map[string]bool{}
	safetensors := false
	for _, item := range listOf(selected["files"]) {
		f := mapOf(item)
		name := strOf(f["name"])
		if !validFilename(name) {
			return bad("Invalid pinned model filename")
		}
		folded := strings.ToLower(name)
		if names[folded] || strings.Contains(name, "/") || !(name == "LICENSE" || hasSuffix(name, ".safetensors", ".json", ".txt", ".md", ".jinja")) {
			return bad("Invalid pinned language model filename")
		}
		names[folded] = true
		safetensors = safetensors || strings.HasSuffix(name, ".safetensors")
		expected := fmt.Sprintf("https://huggingface.co/%s/resolve/%s/%s", selected["model"], selected["revision"], name)
		if f["url"] != expected || f["revision"] != selected["revision"] {
			return bad("Language model files must use the exact pinned revision")
		}
	}
	if !names["config.json"] || !names["tokenizer_config.json"] || len(names) > 256 || !safetensors {
		return bad("Language model needs a bounded manifest, safetensors weights, config and tokenizer")
	}
	return selected, nil
}

func hasSuffix(s string, suffixes ...string) bool {
	for _, x := range suffixes {
		if strings.HasSuffix(s, x) {
			return true
		}
	}
	return false
}

const rankOverhead = 6 << 30

// RankGPUBytes is the GPU memory each tensor-parallel rank needs.
func RankGPUBytes(selected map[string]any, tp int64) int64 {
	total := intOf(selected["minimum_gpu_memory_bytes"])
	weights := max(0, total-rankOverhead)
	return (weights+tp-1)/tp + min(total, rankOverhead)
}

func filesSize(selected map[string]any) int64 {
	var size int64
	for _, f := range listOf(selected["files"]) {
		size += intOf(mapOf(f)["size"])
	}
	return size
}

// LLMSource is the snapshot identity of a language model.
func LLMSource(selected map[string]any) map[string]any {
	identity := Compact(map[string]any{"model": selected["model"], "revision": selected["revision"], "files": selected["files"]})
	return map[string]any{"name": selected["id"], "revision": selected["revision"], "format": "hf-llm-snapshot",
		"url":    fmt.Sprintf("https://huggingface.co/%s/tree/%s", selected["model"], selected["revision"]),
		"sha256": sha256Hex(identity), "size": number(filesSize(selected))}
}

func (c *Connector) pinned(catalog []map[string]any, id any, kind string) (map[string]any, error) {
	for _, m := range catalog {
		if m["id"] == id {
			if !repoRE.MatchString(strOf(m["model"])) || !commitRE.MatchString(strOf(m["revision"])) {
				return nil, fmt.Errorf("Invalid %s model identity", kind)
			}
			return m, nil
		}
	}
	return nil, fmt.Errorf("Choose a pinned %s model", kind)
}

func (c *Connector) Speech(id any) (map[string]any, error) { return c.pinned(c.speech, id, "speech") }
func (c *Connector) Diffusion(id any) (map[string]any, error) {
	m, err := c.pinned(c.diffusion, id, "diffusion")
	if err == nil && m["operation"] != "image" && m["operation"] != "video" {
		return nil, fmt.Errorf("Invalid diffusion model identity")
	}
	return m, err
}

// SnapshotDigest is a speech or diffusion model's snapshot identity.
func SnapshotDigest(selected map[string]any) string { return sha256Hex(Compact(selected)) }

// ---- attached endpoints ----------------------------------------------------------

var secretRefRE = regexp.MustCompile(`^node-secret://[a-z][a-z0-9_-]{0,63}$`)

// ValidateAttach is the connector's own configuration check
// (apps/model-service/server.py validate_config).
func ValidateAttach(engine, endpoint, credentialFile, secretRef string) (map[string]any, error) {
	switch engine {
	case "ollama", "lmstudio", "sglang", "speaches", "api":
	default:
		return nil, fmt.Errorf("Choose Ollama, LM Studio, SGLang, Speaches or an API endpoint")
	}
	endpoint = strings.TrimRight(endpoint, "/")
	p, err := url.Parse(endpoint)
	if err != nil || (p.Scheme != "http" && p.Scheme != "https") || p.Hostname() == "" || p.User != nil || p.RawQuery != "" || p.Fragment != "" || strings.Contains(endpoint, "#") {
		return nil, fmt.Errorf("Use an HTTP(S) endpoint without credentials, query or fragment")
	}
	local := p.Hostname() == "localhost" || p.Hostname() == "127.0.0.1" || p.Hostname() == "::1"
	if engine != "api" && !local {
		return nil, fmt.Errorf("An attached local engine must use a loopback endpoint on the selected node")
	}
	if p.Scheme != "https" && !local {
		return nil, fmt.Errorf("Remote API endpoints require HTTPS")
	}
	if secretRef != "" && !secretRefRE.MatchString(secretRef) {
		return nil, fmt.Errorf("Use a node credential reference such as node-secret://openrouter")
	}
	if secretRef != "" && credentialFile != "" {
		return nil, fmt.Errorf("Choose a named credential or a legacy credential file, not both")
	}
	if credentialFile != "" && !strings.HasPrefix(credentialFile, "/") && !regexp.MustCompile(`^([a-zA-Z]:[\\/]|\\\\)`).MatchString(credentialFile) {
		return nil, fmt.Errorf("Credential file must be an absolute path on the selected node")
	}
	if strings.TrimRight(p.Path, "/") == "" {
		endpoint += "/v1"
	}
	config := map[string]any{"engine": engine, "endpoint": endpoint, "credential_file": credentialFile}
	if secretRef != "" {
		config["secret_ref"] = secretRef
	}
	return config, nil
}

var _ = slices.Contains[[]string]
