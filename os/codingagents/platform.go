package codingagents

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
)

// An agent's model source is where its models come from: the user's own
// subscription or API key (the agent's sign-in, the default), or PantheonOS:
// the platform's model service, billed to the user's PantheonOS budget.
const (
	SourceSubscription = "subscription"
	SourcePantheon     = "pantheon"
)

// PlatformAgents are the agents that can run on PantheonOS models: Claude
// Code speaks the proxy's Anthropic Messages API, Codex its Responses API,
// OpenCode and Pi its OpenAI Chat Completions with a provider of their own.
var PlatformAgents = []string{"claude", "codex", "opencode", "pi"}

// PlatformCapable is whether agent id can run on PantheonOS models.
func PlatformCapable(id string) bool { return slices.Contains(PlatformAgents, id) }

// PlatformMissing is the next step on a node without a PantheonOS model key.
const PlatformMissing = "Sign in to PantheonOS on this computer to use PantheonOS models"

// ModelSource is the model source of agent id on this node.
func (s Settings) ModelSource(id string) string {
	if PlatformCapable(id) && s.Agents[id].ModelSource == SourcePantheon {
		return SourcePantheon
	}
	return SourceSubscription
}

// Platform is the PantheonOS model service as this node reaches it: the
// proxy's base URL (without /v1) and the user's key for it.
type Platform struct {
	Base string `json:"base"`
	Key  string `json:"key"`
}

// PlatformOn is the PantheonOS model service of this node: from its
// workspace environment (LLM_API_BASE, LLM_API_KEY: a hosted workspace has
// them), else the one the signed-in desktop handed it; false where the node
// has no key for it.
func (s Settings) PlatformOn() (Platform, bool) {
	var base, key string
	for _, kv := range workspaceEnv() {
		name, value, _ := strings.Cut(kv, "=")
		switch name {
		case "LLM_API_BASE":
			base = strings.TrimSpace(value)
		case "LLM_API_KEY":
			key = strings.TrimSpace(value)
		}
	}
	if p, ok := CheckPlatform(base, key); ok {
		return p, true
	}
	if s.Platform != nil {
		return CheckPlatform(s.Platform.Base, s.Platform.Key)
	}
	return Platform{}, false
}

// CheckPlatform is the model service at base with key, normalised; false
// when either is not one an agent can be pointed at.
func CheckPlatform(base, key string) (Platform, bool) {
	if key == "" || placeholderKey(key) || strings.ContainsAny(key, " \t\r\n") {
		return Platform{}, false
	}
	u, err := url.Parse(base)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.User != nil || u.RawQuery != "" {
		return Platform{}, false
	}
	base = strings.TrimSuffix(strings.TrimRight(base, "/"), "/v1")
	return Platform{Base: base, Key: key}, true
}

// PlatformFor is the PantheonOS model service agent id runs on: set when the
// user chose PantheonOS models for it and the node has a key.
func (s Settings) PlatformFor(id string) (Platform, bool) {
	if s.ModelSource(id) != SourcePantheon {
		return Platform{}, false
	}
	return s.PlatformOn()
}

// platformKeyVar carries the key to Codex, which reads it by this name
// (its provider's env_key): the key never goes in its arguments.
const platformKeyVar = "PANTHEON_LLM_API_KEY"

// platformConflicts are the variables that would take an agent off the
// platform: its own API keys and other providers. They are unset for the
// agent's process; the user's sign-in files are left alone.
var platformConflicts = map[string][]string{
	"claude": {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
		"CLAUDE_CODE_USE_FOUNDRY"},
	"codex": {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"},
}

// PlatformEnv is what an agent's process needs to run on the platform: the
// variables to set (the proxy, the key, the models it defaults to) and those
// to unset.
func PlatformEnv(agent string, p Platform, m Models) (set, unset []string) {
	switch agent {
	case "claude":
		set = []string{"ANTHROPIC_BASE_URL=" + p.Base, "ANTHROPIC_AUTH_TOKEN=" + p.Key}
		if m.Default != "" {
			set = append(set, "ANTHROPIC_MODEL="+m.Default)
		}
		// Claude Code's own aliases (a sub-agent's "sonnet") and its small,
		// fast model name the platform's models too.
		for _, f := range []struct{ family, name string }{{"opus", "OPUS"}, {"sonnet", "SONNET"}, {"haiku", "HAIKU"}} {
			if id := latestOf(m.IDs, f.family); id != "" {
				set = append(set, "ANTHROPIC_DEFAULT_"+f.name+"_MODEL="+id)
				if f.family == "haiku" {
					set = append(set, "ANTHROPIC_SMALL_FAST_MODEL="+id)
				}
			}
		}
	case "codex", "pi":
		set = []string{platformKeyVar + "=" + p.Key}
	case "opencode":
		// Its configuration for this process only, over the user's own: the
		// platform as a provider, its key named, not given.
		set = []string{platformKeyVar + "=" + p.Key, "OPENCODE_CONFIG_CONTENT=" + opencodeConfig(p, m)}
	}
	return set, platformConflicts[agent]
}

// platformProvider is the provider name OpenCode and Pi know the platform by.
const platformProvider = "pantheon"

// opencodeConfig is OpenCode's configuration for a process on the platform:
// the proxy as an OpenAI-compatible provider with the platform's models, and
// its default (also for titles) the platform default.
func opencodeConfig(p Platform, m Models) string {
	models := map[string]any{}
	for _, id := range m.IDs {
		models[id] = map[string]string{"name": m.Labels[id]}
	}
	cfg := map[string]any{"provider": map[string]any{platformProvider: map[string]any{
		"npm": "@ai-sdk/openai-compatible", "name": "PantheonOS",
		"options": map[string]string{"baseURL": p.Base + "/v1", "apiKey": "{env:" + platformKeyVar + "}"},
		"models":  models}}}
	if m.Default != "" {
		cfg["model"] = platformProvider + "/" + m.Default
		cfg["small_model"] = platformProvider + "/" + cmp.Or(latestOf(m.IDs, "haiku"), m.Default)
	}
	b, _ := json.Marshal(cfg)
	return string(b)
}

// piExtension is a Pi extension that adds the platform as a provider, with
// its models; the key comes from the environment when Pi starts. Pi takes it
// with -e for one process, leaving the user's own Pi folder as it is.
func piExtension(p Platform, m Models) []byte {
	type piModel struct {
		ID            string             `json:"id"`
		Name          string             `json:"name"`
		Reasoning     bool               `json:"reasoning"`
		Input         []string           `json:"input"`
		Cost          map[string]float64 `json:"cost"`
		ContextWindow int                `json:"contextWindow"`
		MaxTokens     int                `json:"maxTokens"`
	}
	models := make([]piModel, 0, len(m.IDs))
	for _, id := range m.IDs {
		models = append(models, piModel{ID: id, Name: cmp.Or(m.Labels[id], id), Input: []string{"text"},
			Cost:          map[string]float64{"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
			ContextWindow: 128000, MaxTokens: 16384})
	}
	base, _ := json.Marshal(p.Base + "/v1")
	list, _ := json.Marshal(models)
	return []byte("// Written by PantheonOS: its models for Pi, billed to the user's PantheonOS budget.\n" +
		"export default function (pi) {\n" +
		"  pi.registerProvider(\"" + platformProvider + "\", {\n" +
		"    name: \"PantheonOS\", baseUrl: " + string(base) + ", api: \"openai-completions\",\n" +
		"    apiKey: process.env." + platformKeyVar + ",\n" +
		"    models: " + string(list) + ",\n  })\n}\n")
}

var piExtensionMu sync.Mutex

// writePiExtension keeps the Pi extension of the platform's current models
// in the node's state folder, and returns its path.
func writePiExtension(p Platform, m Models) (string, error) {
	piExtensionMu.Lock()
	defer piExtensionMu.Unlock()
	dir := filepath.Join(Root(), "platform")
	name := "pi-pantheon.js"
	if g, err := url.Parse(p.Base); err == nil && g.Hostname() == "127.0.0.1" {
		// A Team Chat turn's, through the node's gateway (TurnModelKey):
		// a file of its own, so a turn at the proxy itself never reads it.
		name = "pi-pantheon-turn.js"
	}
	path := filepath.Join(dir, name)
	data := piExtension(p, m)
	if old, err := os.ReadFile(path); err == nil && bytes.Equal(old, data) {
		return path, nil
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return "", err
	}
	return path, os.Rename(tmp, path)
}

// PlatformArgs are the arguments that point an agent at the platform (the
// key always comes from the environment): Codex takes the proxy as a model
// provider of its own, Pi as the provider of an extension PantheonOS keeps.
func PlatformArgs(agent string, p Platform, m Models) ([]string, error) {
	switch agent {
	case "codex":
		return []string{"-c", `model_provider="pantheon"`, "-c", `model_providers.pantheon={name="PantheonOS",base_url=` +
			tomlString(p.Base+"/v1") + `,env_key="` + platformKeyVar + `",wire_api="responses"}`}, nil
	case "pi":
		path, err := writePiExtension(p, m)
		if err != nil {
			return nil, err
		}
		return []string{"-e", path, "--provider", platformProvider}, nil
	}
	return nil, nil
}

// PlatformModelArg is how an agent on the platform names a platform model:
// OpenCode by provider/model, the others by its id.
func PlatformModelArg(agent, id string) string {
	if agent == "opencode" && id != "" {
		return platformProvider + "/" + id
	}
	return id
}

// PlatformModel is the platform model a turn of an agent on PantheonOS
// runs: the one named when the platform serves it (by its id, or the same
// model under the platform's name for it: "claude-opus-4-8" is
// "openrouter/anthropic/claude-opus-4.8"), Claude Code's aliases as the
// newest of their family, else the platform default. Unchanged when the
// platform's models are unknown.
func PlatformModel(model string, m Models) string {
	if len(m.IDs) == 0 {
		return model
	}
	if model == "" || slices.Contains(m.IDs, model) {
		return cmp.Or(model, m.Default)
	}
	if id := latestOf(m.IDs, model); id != "" && (model == "opus" || model == "sonnet" || model == "haiku") {
		return id
	}
	want := modelKey(model)
	for _, id := range m.IDs {
		if modelKey(id) == want {
			return id
		}
	}
	return m.Default
}

// SubscriptionModel is the model a turn on the agent's own subscription
// asks for: a platform model or a route (chosen while it ran on PantheonOS)
// is none of its account's, so its default stands in.
func SubscriptionModel(model string) string {
	if strings.HasPrefix(model, "openrouter/") || strings.HasPrefix(model, RouteRef) {
		return ""
	}
	return model
}

// modelKey compares a model id across providers' spellings: its last path
// segment, lower case, dots as dashes, without a variant suffix.
func modelKey(id string) string {
	id = strings.ToLower(id[strings.LastIndex(id, "/")+1:])
	if i := strings.IndexAny(id, "[:"); i >= 0 {
		id = id[:i]
	}
	return strings.ReplaceAll(id, ".", "-")
}

// latestOf is the first of ids (newest first) of a model family ("sonnet").
func latestOf(ids []string, family string) string {
	for _, id := range ids {
		if slices.Contains(strings.Split(modelKey(id), "-"), family) {
			return id
		}
	}
	return ""
}

// ---- the platform's models -------------------------------------------------

const (
	platformModelsTTL   = 5 * time.Minute
	platformModelsRetry = 30 * time.Second
	platformModelsWait  = 5 * time.Second
)

var platformModelsMemo struct {
	sync.Mutex
	base string
	ids  []string
	at   time.Time
}

// PlatformModels is the models the platform serves agent, newest first, with
// readable names and the default; read from the proxy's /v1/models at most
// every few minutes, and the last list kept when it does not answer.
func PlatformModels(agent string, p Platform) Models {
	m := &platformModelsMemo
	m.Lock()
	defer m.Unlock()
	if m.base != p.Base {
		m.base, m.ids, m.at = p.Base, nil, time.Time{}
	}
	ttl := platformModelsTTL
	if m.ids == nil {
		ttl = platformModelsRetry
	}
	if m.at.IsZero() || time.Since(m.at) > ttl {
		if ids, err := fetchModelIDs(p); err == nil && len(ids) > 0 {
			m.ids = ids
		}
		m.at = time.Now()
	}
	return platformModelsOf(agent, m.ids)
}

// fetchModelIDs lists the proxy's models. The key goes in a header only.
func fetchModelIDs(p Platform) ([]string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), platformModelsWait)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.Base+"/v1/models", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+p.Key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, &modelsError{resp.StatusCode}
	}
	var doc struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&doc); err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(doc.Data))
	for _, d := range doc.Data {
		if d.ID != "" {
			ids = append(ids, d.ID)
		}
	}
	return ids, nil
}

type modelsError struct{ status int }

func (e *modelsError) Error() string { return "the model service answered " + strconv.Itoa(e.status) }

var (
	claudeModel = regexp.MustCompile(`^(openrouter/anthropic/)?claude-[a-z0-9.-]+$`)
	codexModel  = regexp.MustCompile(`^(openrouter/openai/)?(gpt-5[a-z0-9.-]*|[a-z0-9.-]*codex[a-z0-9.-]*)$`)
	// deepseekModel is DeepSeek's: through the proxy they also speak Claude
	// Code's Messages API and Codex's Responses API, tools and all.
	deepseekModel = regexp.MustCompile(`^(openrouter/deepseek/)?deepseek-[a-z0-9.-]+$`)
	// anyModel is any one model (not a wildcard group, nor a variant such
	// as ":batch" or ":free").
	anyModel = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$`)
	// notChat are words of the models that do not chat (or not as a coding
	// agent needs).
	notChat = []string{"image", "audio", "realtime", "tts", "transcribe", "search", "embedding", "embed", "moderation",
		"rerank", "whisper", "dall", "sora", "veo", "imagen", "lyria"}
)

// platformModelsOf picks one agent's chat models from the proxy's list:
// Claude Code's are Anthropic's Claude models, Codex's OpenAI's GPT-5 and
// Codex models, each with DeepSeek's (the platform default). One model
// listed both under the platform's vendor route and directly is offered
// once, by the route.
func platformModelsOf(agent string, all []string) Models {
	var patterns []*regexp.Regexp
	skip := notChat
	switch agent {
	case "claude":
		patterns = []*regexp.Regexp{claudeModel, deepseekModel}
	case "codex":
		// A chat-only GPT (gpt-5-chat) is no coding model.
		patterns, skip = []*regexp.Regexp{codexModel, deepseekModel}, append(slices.Clone(notChat), "chat")
	case "opencode", "pi":
		// They take any chat model: every vendor's.
		patterns = []*regexp.Regexp{anyModel}
	default:
		return Models{}
	}
	// When the platform routes vendors through OpenRouter, that route is what
	// it pays for: the direct vendor groups it also lists may have no credit.
	if slices.ContainsFunc(all, isRouted) {
		all = slices.DeleteFunc(slices.Clone(all), func(id string) bool { return !isRouted(id) })
	}
	byKey := map[string]string{}
	for _, id := range all {
		if !slices.ContainsFunc(patterns, func(p *regexp.Regexp) bool { return p.MatchString(id) }) {
			continue
		}
		key := modelKey(id)
		if slices.ContainsFunc(strings.Split(key, "-"), func(w string) bool { return slices.Contains(skip, w) }) {
			continue
		}
		if prev, ok := byKey[key]; !ok || (strings.Contains(id, "/") && !strings.Contains(prev, "/")) {
			byKey[key] = id
		}
	}
	ids := make([]string, 0, len(byKey))
	for _, id := range byKey {
		ids = append(ids, id)
	}
	slices.SortFunc(ids, func(a, b string) int {
		if c := cmp.Compare(vendorRank(a), vendorRank(b)); c != 0 {
			return c
		}
		if c := cmp.Compare(modelVendor(a), modelVendor(b)); c != 0 {
			return c
		}
		if c := slices.Compare(modelVersion(b), modelVersion(a)); c != 0 {
			return c
		}
		if c := cmp.Compare(familyRank(a), familyRank(b)); c != 0 {
			return c
		}
		// Of one model's snapshots, the newest first.
		if c := cmp.Compare(modelDate(b), modelDate(a)); c != 0 {
			return c
		}
		return cmp.Compare(modelKey(a), modelKey(b))
	})
	if len(ids) == 0 {
		return Models{}
	}
	labels := make(map[string]string, len(ids))
	for _, id := range ids {
		labels[id] = ModelLabel(id)
	}
	return Models{IDs: ids, Labels: labels, Default: platformDefault(agent, ids)}
}

func isRouted(id string) bool { return strings.HasPrefix(id, "openrouter/") }

// platformPreferred is the model an agent on PantheonOS runs when a turn
// names none and the platform serves it; Claude Code's stays a Claude model.
const platformPreferred = "deepseek-v4.1-flash"

// preferredModel is platformPreferred as the platform lists it: pinned, by
// its newest dated snapshot ("…-flash-1017"), else by its own id; never by
// an alias that moves ("~…-latest"). "" when the platform has neither.
func preferredModel(ids []string) string {
	want := modelKey(platformPreferred)
	undated := ""
	for _, id := range ids {
		switch {
		case modelDate(id) != "" && undatedKey(id) == want:
			return id // ids list a model's snapshots newest first
		case modelKey(id) == want && undated == "":
			undated = id
		}
	}
	return undated
}

// platformDefault is the model an agent on PantheonOS runs when a turn names
// none: Claude Code the newest Sonnet; the others DeepSeek V4.1 Flash where
// the platform serves it, else OpenCode and Pi the newest Sonnet, Codex the
// newest GPT-5 Codex (not a mini), else GPT-5.5; else the newest.
func platformDefault(agent string, ids []string) string {
	if id := preferredModel(ids); id != "" && agent != "claude" {
		return id
	}
	pick := func(ok func(words []string) bool) string {
		for _, id := range ids {
			if ok(strings.Split(modelKey(id), "-")) {
				return id
			}
		}
		return ""
	}
	var id string
	switch agent {
	case "claude", "opencode", "pi":
		id = latestOf(ids, "sonnet")
	case "codex":
		id = pick(func(w []string) bool { return slices.Contains(w, "codex") && !slices.Contains(w, "mini") })
		if id == "" {
			id = pick(func(w []string) bool { return strings.Join(w, "-") == "gpt-5-5" })
		}
	}
	return cmp.Or(id, ids[0])
}

// modelVendor is who makes a model: the vendor of a route
// ("openrouter/google/…"), else its name's first word ("claude-…").
func modelVendor(id string) string {
	if parts := strings.Split(id, "/"); len(parts) >= 3 {
		return parts[len(parts)-2]
	}
	switch w, _, _ := strings.Cut(modelKey(id), "-"); w {
	case "claude":
		return "anthropic"
	case "gpt", "o1", "o3", "o4", "codex":
		return "openai"
	case "gemini", "gemma":
		return "google"
	default:
		return w
	}
}

// vendorRank puts the coding agents' own vendors first; the rest follow by name.
func vendorRank(id string) int {
	if i := slices.Index([]string{"anthropic", "openai", "google"}, modelVendor(id)); i >= 0 {
		return i
	}
	return 3
}

// modelVersion is the version numbers in a model id ("claude-sonnet-4.6",
// "claude-3-5-sonnet": 4 6, 3 5; "gpt-4o": 4), without a date
// ("20241022", "2024-11-20") or a size ("235b").
func modelVersion(id string) []int {
	var v []int
	words := strings.Split(modelKey(id), "-")
	for i := 0; i < len(words); i++ {
		w := words[i]
		if n := dateWords(words[i:]); n > 0 {
			i += n - 1
			continue
		}
		// The omni models name their version with an "o": "4o" is 4; some
		// put a "v" before it: "v4" is 4.
		if num, ok := strings.CutSuffix(w, "o"); ok && num != "" && len(num) <= 3 && numeric8(num) {
			w = num
		} else if num, ok := strings.CutPrefix(w, "v"); ok && num != "" && len(num) <= 3 && numeric8(num) {
			w = num
		}
		if n, err := strconv.Atoi(w); err == nil && len(w) <= 3 {
			v = append(v, n)
		}
	}
	return v
}

// modelDate is the date in a model id, its digits only ("20241120"); ""
// without one.
func modelDate(id string) string {
	words := strings.Split(modelKey(id), "-")
	for i := range words {
		if n := dateWords(words[i:]); n > 0 {
			return strings.Join(words[i:i+n], "")
		}
	}
	return ""
}

// undatedKey is a model's modelKey without its snapshot's date: the model
// the snapshot is of.
func undatedKey(id string) string {
	words := strings.Split(modelKey(id), "-")
	out := make([]string, 0, len(words))
	for i := 0; i < len(words); i++ {
		if n := dateWords(words[i:]); n > 0 {
			i += n - 1
			continue
		}
		out = append(out, words[i])
	}
	return strings.Join(out, "-")
}

// dateWords is how many of words, from the first, are a date: "20241022",
// a year and its month and day ("2024", "11", "20"), or a month and day
// ending the id ("0731", "1017": a snapshot's); 0 when none is.
func dateWords(words []string) int {
	w := words[0]
	if len(w) == 8 && numeric8(w) && (strings.HasPrefix(w, "19") || strings.HasPrefix(w, "20")) {
		return 1
	}
	if len(words) == 1 && monthDay(w) {
		return 1
	}
	if len(w) != 4 || !numeric8(w) || !(strings.HasPrefix(w, "19") || strings.HasPrefix(w, "20")) {
		return 0
	}
	n := 1
	for n < 3 && n < len(words) && len(words[n]) == 2 && numeric8(words[n]) {
		n++
	}
	return n
}

// familyRank orders models of one version: the most capable first.
func familyRank(id string) int {
	words := strings.Split(modelKey(id), "-")
	for i, f := range []string{"opus", "sonnet", "haiku"} {
		if slices.Contains(words, f) {
			return i
		}
	}
	if slices.Contains(words, "mini") || slices.Contains(words, "nano") {
		return 2
	}
	return 0
}

// ModelLabel is a model id as people name it: "Claude Sonnet 4.6",
// "GPT-5.3 Codex".
func ModelLabel(id string) string {
	seg := id[strings.LastIndex(id, "/")+1:]
	words := strings.Split(seg, "-")
	numeric := func(w string) bool {
		if w == "" || len(w) > 4 {
			return false
		}
		for _, r := range w {
			if (r < '0' || r > '9') && r != '.' {
				return false
			}
		}
		return true
	}
	var out []string
	for i := 0; i < len(words); i++ {
		w := words[i]
		if len(w) == 8 && numeric8(w) || i > 0 && i == len(words)-1 && monthDay(w) {
			continue // a date
		}
		if n := dateWords(words[i:]); n == 3 {
			out = append(out, strings.Join(words[i:i+3], "-")) // a snapshot's date, as written
			i += 2
			continue
		}
		if numeric(w) {
			v := w
			for i+1 < len(words) && numeric(words[i+1]) && !(i+1 == len(words)-1 && monthDay(words[i+1])) {
				i++
				v += "." + words[i]
			}
			if n := len(out); n > 0 && out[n-1] == "GPT" {
				out[n-1] = "GPT-" + v
				continue
			}
			out = append(out, v)
			continue
		}
		switch w {
		case "gpt", "oss", "glm":
			out = append(out, strings.ToUpper(w))
		case "qwq":
			out = append(out, "QwQ")
		case "deepseek":
			out = append(out, "DeepSeek")
		default:
			out = append(out, strings.ToUpper(w[:1])+w[1:])
		}
	}
	return strings.Join(out, " ")
}

// monthDay is whether w is a month and its day, as four digits ("0731").
// A year and month ("2508", as Mistral dates its models) is not one.
func monthDay(w string) bool {
	if len(w) != 4 || !numeric8(w) {
		return false
	}
	month, _ := strconv.Atoi(w[:2])
	day, _ := strconv.Atoi(w[2:])
	return month >= 1 && month <= 12 && day >= 1 && day <= 31
}

func numeric8(w string) bool {
	for _, r := range w {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
