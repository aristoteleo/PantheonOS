package codingagents

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const testPlatformKey = "sk-platform-test-0123456789"

// proxyModels is a /v1/models answer like the platform proxy's in its
// OpenRouter mode: vendor routes, direct groups, and models that do not chat.
var proxyModels = []string{
	"openrouter/anthropic/claude-sonnet-4.6", "openrouter/anthropic/claude-opus-4.8", "openrouter/anthropic/claude-haiku-4.5",
	"openrouter/anthropic/claude-sonnet-4.5", "openrouter/anthropic/claude-3.7-sonnet:thinking", "claude-sonnet-4-6",
	"openrouter/openai/gpt-5.5", "openrouter/openai/gpt-5.3-codex", "openrouter/openai/gpt-5.1-codex-mini",
	"openrouter/openai/gpt-5-image", "openrouter/openai/gpt-5-chat", "openrouter/openai/gpt-4o",
	"openrouter/google/gemini-3-pro", "text-embedding-3-large", "anthropic/*",
}

// fakeProxy serves proxyModels to the test key, counting the requests.
func fakeProxy(t *testing.T, ids []string) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var n atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n.Add(1)
		if r.URL.Path != "/v1/models" || r.Header.Get("Authorization") != "Bearer "+testPlatformKey {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		var doc struct {
			Data []map[string]string `json:"data"`
		}
		for _, id := range ids {
			doc.Data = append(doc.Data, map[string]string{"id": id, "object": "model"})
		}
		_ = json.NewEncoder(w).Encode(doc)
	}))
	t.Cleanup(srv.Close)
	return srv, &n
}

// onPlatform gives this process a hosted workspace's model service.
func onPlatform(t *testing.T, base string) {
	t.Helper()
	t.Setenv("PANTHEON_WORKSPACE_ENV", "")
	t.Setenv("LLM_API_BASE", base)
	t.Setenv("LLM_API_KEY", testPlatformKey)
}

func TestPlatformOn(t *testing.T) {
	onPlatform(t, "https://hub.test/litellm/v1/")
	p, ok := Settings{}.PlatformOn()
	if !ok || p.Base != "https://hub.test/litellm" || p.Key != testPlatformKey {
		t.Fatalf("%v %+v", ok, p.Base)
	}
	t.Setenv("LLM_API_KEY", placeholderKeyPrefix+"-x")
	if _, ok := (Settings{}).PlatformOn(); ok {
		t.Error("a placeholder is no key")
	}
	t.Setenv("LLM_API_KEY", "")
	if _, ok := (Settings{}).PlatformOn(); ok {
		t.Error("no key, no platform")
	}
	t.Setenv("LLM_API_KEY", testPlatformKey)
	t.Setenv("LLM_API_BASE", "file:///etc")
	if _, ok := (Settings{}).PlatformOn(); ok {
		t.Error("the base is an http(s) URL")
	}
	// Without one in the environment, the key the desktop handed the node.
	t.Setenv("LLM_API_KEY", "")
	if p, ok := (Settings{Platform: &Platform{Base: "https://hub.test/litellm/v1", Key: testPlatformKey}}).PlatformOn(); !ok || p.Base != "https://hub.test/litellm" {
		t.Errorf("stored key: %v %q", ok, p.Base)
	}
}

func TestPlatformModelsOf(t *testing.T) {
	claude := platformModelsOf("claude", proxyModels)
	want := []string{"openrouter/anthropic/claude-opus-4.8", "openrouter/anthropic/claude-sonnet-4.6",
		"openrouter/anthropic/claude-sonnet-4.5", "openrouter/anthropic/claude-haiku-4.5"}
	if !slices.Equal(claude.IDs, want) {
		t.Errorf("claude models %v", claude.IDs)
	}
	if claude.Default != "openrouter/anthropic/claude-sonnet-4.6" || claude.Labels["openrouter/anthropic/claude-sonnet-4.6"] != "Claude Sonnet 4.6" {
		t.Errorf("claude default %q, labels %v", claude.Default, claude.Labels)
	}
	codex := platformModelsOf("codex", proxyModels)
	if !slices.Equal(codex.IDs, []string{"openrouter/openai/gpt-5.5", "openrouter/openai/gpt-5.3-codex", "openrouter/openai/gpt-5.1-codex-mini"}) {
		t.Errorf("codex models %v", codex.IDs)
	}
	if codex.Default != "openrouter/openai/gpt-5.3-codex" || codex.Labels["openrouter/openai/gpt-5.3-codex"] != "GPT-5.3 Codex" {
		t.Errorf("codex default %q, labels %v", codex.Default, codex.Labels)
	}
	if m := platformModelsOf("codex", []string{"openrouter/openai/gpt-5.1", "openrouter/openai/gpt-5.5", "openrouter/openai/gpt-5.1-codex-mini"}); m.Default != "openrouter/openai/gpt-5.5" {
		t.Errorf("without a full Codex model, GPT-5.5: %q", m.Default)
	}
	// Direct groups count when no vendor route has the model.
	if m := platformModelsOf("claude", []string{"claude-sonnet-4-6", "claude-haiku-4-5-20251001"}); !slices.Equal(m.IDs, []string{"claude-sonnet-4-6", "claude-haiku-4-5-20251001"}) ||
		m.Labels["claude-haiku-4-5-20251001"] != "Claude Haiku 4.5" {
		t.Errorf("direct models %v %v", m.IDs, m.Labels)
	}
	// With an OpenRouter route, the direct vendor groups are left out.
	if m := platformModelsOf("opencode", []string{"anthropic/claude-sonnet-5-5", "openai/gpt-5.5", "claude-opus-4-8",
		"openrouter/anthropic/claude-sonnet-5.5", "openrouter/openai/gpt-5.5"}); !slices.Equal(m.IDs, []string{"openrouter/anthropic/claude-sonnet-5.5", "openrouter/openai/gpt-5.5"}) ||
		m.Default != "openrouter/anthropic/claude-sonnet-5.5" {
		t.Errorf("routed models %v, default %q", m.IDs, m.Default)
	}
	if m := platformModelsOf("gemini", proxyModels); len(m.IDs) != 0 {
		t.Errorf("gemini does not run on the platform: %v", m.IDs)
	}
}

func TestModelVersionSkipsDates(t *testing.T) {
	for id, want := range map[string][]int{"openrouter/openai/gpt-4o-2024-11-20": {4}, "gpt-4o": {4}, "openrouter/openai/gpt-5.5": {5, 5},
		"claude-3-5-sonnet-20241022": {3, 5}, "openrouter/qwen/qwen3-235b-a22b": nil, "openrouter/mistralai/codestral-2508": nil,
		"openrouter/anthropic/claude-sonnet-4.6": {4, 6}} {
		if got := modelVersion(id); !slices.Equal(got, want) {
			t.Errorf("%s: %v, want %v", id, got, want)
		}
	}
	// A dated GPT-4o snapshot is not newer than GPT-5.5.
	m := platformModelsOf("pi", []string{"openrouter/openai/gpt-4o-2024-11-20", "openrouter/openai/gpt-4o-2024-08-06", "openrouter/openai/gpt-5.5",
		"openrouter/openai/gpt-4.1"})
	if !slices.Equal(m.IDs, []string{"openrouter/openai/gpt-5.5", "openrouter/openai/gpt-4.1", "openrouter/openai/gpt-4o-2024-11-20",
		"openrouter/openai/gpt-4o-2024-08-06"}) {
		t.Errorf("openai order %v", m.IDs)
	}
	if got := ModelLabel("openrouter/openai/gpt-4o-2024-11-20"); got != "GPT 4o 2024-11-20" {
		t.Errorf("dated label %q", got)
	}
}

func TestModelLabel(t *testing.T) {
	for id, want := range map[string]string{"openrouter/anthropic/claude-opus-4.8": "Claude Opus 4.8", "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
		"openrouter/openai/gpt-5.1-codex-max": "GPT-5.1 Codex Max", "gpt-5.5": "GPT-5.5", "gpt-5": "GPT-5"} {
		if got := ModelLabel(id); got != want {
			t.Errorf("%s: %q, want %q", id, got, want)
		}
	}
}

func TestPlatformModelMapping(t *testing.T) {
	m := platformModelsOf("claude", proxyModels)
	for in, want := range map[string]string{"": m.Default, "opus": "openrouter/anthropic/claude-opus-4.8",
		"claude-opus-4-8[1m]": "openrouter/anthropic/claude-opus-4.8", "openrouter/anthropic/claude-haiku-4.5": "openrouter/anthropic/claude-haiku-4.5",
		"claude-unknown-9": m.Default} {
		if got := PlatformModel(in, m); got != want {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
	if got := PlatformModel("opus", Models{}); got != "opus" {
		t.Errorf("without the platform's list the model stays: %q", got)
	}
	if SubscriptionModel("openrouter/openai/gpt-5.5") != "" || SubscriptionModel("gpt-5.5") != "gpt-5.5" {
		t.Error("a platform model is none of the account's")
	}
}

func TestPlatformModelsCachedAndFailSoft(t *testing.T) {
	srv, n := fakeProxy(t, proxyModels)
	p := Platform{Base: srv.URL, Key: testPlatformKey}
	first := PlatformModels("codex", p)
	PlatformModels("claude", p)
	if n.Load() != 1 || len(first.IDs) == 0 {
		t.Fatalf("one read for both agents: %d, %v", n.Load(), first.IDs)
	}
	// Old, and the proxy is gone: the last list stays.
	platformModelsMemo.Lock()
	platformModelsMemo.at = time.Now().Add(-time.Hour)
	platformModelsMemo.Unlock()
	srv.Close()
	if again := PlatformModels("codex", p); !slices.Equal(again.IDs, first.IDs) {
		t.Errorf("the last list is kept: %v", again.IDs)
	}
	// A wrong key gets nothing, and nothing breaks.
	srv2, _ := fakeProxy(t, proxyModels)
	if m := PlatformModels("codex", Platform{Base: srv2.URL, Key: "sk-wrong"}); len(m.IDs) != 0 {
		t.Errorf("refused: %v", m.IDs)
	}
}

func TestPlatformEnvAndArgs(t *testing.T) {
	p := Platform{Base: "https://hub.test/litellm", Key: testPlatformKey}
	set, unset := PlatformEnv("claude", p, platformModelsOf("claude", proxyModels))
	for _, want := range []string{"ANTHROPIC_BASE_URL=https://hub.test/litellm", "ANTHROPIC_AUTH_TOKEN=" + testPlatformKey,
		"ANTHROPIC_MODEL=openrouter/anthropic/claude-sonnet-4.6", "ANTHROPIC_DEFAULT_HAIKU_MODEL=openrouter/anthropic/claude-haiku-4.5",
		"ANTHROPIC_SMALL_FAST_MODEL=openrouter/anthropic/claude-haiku-4.5", "ANTHROPIC_DEFAULT_OPUS_MODEL=openrouter/anthropic/claude-opus-4.8"} {
		if !slices.Contains(set, want) {
			t.Errorf("claude env lacks %s: %v", want, set)
		}
	}
	if !slices.Contains(unset, "ANTHROPIC_API_KEY") || !slices.Contains(unset, "CLAUDE_CODE_OAUTH_TOKEN") {
		t.Errorf("claude unsets %v", unset)
	}
	set, unset = PlatformEnv("codex", p, Models{})
	if !slices.Equal(set, []string{"PANTHEON_LLM_API_KEY=" + testPlatformKey}) || !slices.Contains(unset, "OPENAI_API_KEY") {
		t.Errorf("codex env %v, unset %v", set, unset)
	}
	codexArgs, _ := PlatformArgs("codex", p, Models{})
	args := strings.Join(codexArgs, " ")
	if args != `-c model_provider="pantheon" -c model_providers.pantheon={name="PantheonOS",base_url="https://hub.test/litellm/v1",env_key="PANTHEON_LLM_API_KEY",wire_api="responses"}` {
		t.Errorf("codex args %s", args)
	}
	if a, err := PlatformArgs("claude", p, Models{}); a != nil || err != nil {
		t.Error("Claude Code takes the platform from its environment")
	}
}

func TestLaunchEnvOnPlatform(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	srv, _ := fakeProxy(t, proxyModels)
	onPlatform(t, srv.URL)
	t.Setenv("ANTHROPIC_API_KEY", "sk-ant-users-own")
	t.Setenv("OPENAI_API_KEY", "sk-users-own")
	s, err := UpdateSettings(func(s *Settings) error {
		s.Agents["claude"] = AgentSettings{ModelSource: SourcePantheon, Env: map[string]string{"ANTHROPIC_API_KEY": "sk-ant-own-setting"}}
		s.Agents["codex"] = AgentSettings{ModelSource: SourceSubscription}
		return s.Validate()
	})
	if err != nil {
		t.Fatal(err)
	}
	if LoadSettings().Agents["claude"].ModelSource != SourcePantheon {
		t.Fatal("the model source is kept")
	}
	env := strings.Join(LaunchEnv(s, Launch{Agent: "claude"}), "\n")
	for _, want := range []string{"ANTHROPIC_BASE_URL=" + srv.URL, "ANTHROPIC_AUTH_TOKEN=" + testPlatformKey, "ANTHROPIC_MODEL=openrouter/anthropic/claude-sonnet-4.6"} {
		if !strings.Contains(env, want) {
			t.Errorf("claude env lacks %s", want)
		}
	}
	if strings.Contains(env, "ANTHROPIC_API_KEY=") {
		t.Error("the user's own key would take Claude Code off the platform")
	}
	if got := LaunchUnset(s, Launch{Agent: "claude"}); !slices.Contains(got, "ANTHROPIC_API_KEY") {
		t.Errorf("a terminal unsets %v", got)
	}
	// Codex stays on its subscription: nothing of the platform's.
	env = strings.Join(LaunchEnv(s, Launch{Agent: "codex"}), "\n")
	if strings.Contains(env, "PANTHEON_LLM_API_KEY") || !strings.Contains(env, "OPENAI_API_KEY=sk-users-own") || LaunchUnset(s, Launch{Agent: "codex"}) != nil {
		t.Error("a subscription agent gets none of the platform's settings")
	}
	// No key on the node: nothing of the platform's either (the launch refuses earlier).
	t.Setenv("LLM_API_KEY", "")
	if env := strings.Join(LaunchEnv(s, Launch{Agent: "claude"}), "\n"); strings.Contains(env, "ANTHROPIC_BASE_URL") {
		t.Error("no platform without a key")
	}
}

func TestModelSourceSettings(t *testing.T) {
	for _, tc := range []struct {
		agent, source string
		ok            bool
	}{{"claude", SourcePantheon, true}, {"codex", SourceSubscription, true}, {"gemini", SourcePantheon, false}, {"claude", "mine", false}} {
		s := Settings{Agents: map[string]AgentSettings{tc.agent: {ModelSource: tc.source}}}
		if err := s.Validate(); (err == nil) != tc.ok {
			t.Errorf("%s %s: %v", tc.agent, tc.source, err)
		}
	}
	s := Settings{Agents: map[string]AgentSettings{"claude": {ModelSource: SourcePantheon}}}
	if s.ModelSource("claude") != SourcePantheon || s.ModelSource("codex") != SourceSubscription {
		t.Error("the default is the subscription")
	}
}

func TestCodexMetadataNoticeDropped(t *testing.T) {
	p := &codexParser{}
	line := `{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata for ` + "`openrouter/openai/gpt-5.3-codex`" + ` not found. Defaulting to fallback metadata; this can degrade performance and cause issues."}}`
	if ev := p.Parse([]byte(line)); len(ev) != 0 {
		t.Errorf("the notice shows: %+v", ev)
	}
	if ev := p.Parse([]byte(`{"type":"item.completed","item":{"id":"item_1","type":"error","message":"something else"}}`)); len(ev) != 1 {
		t.Errorf("another error item still shows: %+v", ev)
	}
}
