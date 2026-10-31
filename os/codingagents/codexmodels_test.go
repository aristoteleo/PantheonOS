package codingagents

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"
)

// codexHomeWith is a CODEX_HOME holding the given models_cache.json and
// config.toml (each left out when empty).
func codexHomeWith(t *testing.T, cache, config string) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("CODEX_HOME", dir)
	if cache != "" {
		if err := os.WriteFile(filepath.Join(dir, "models_cache.json"), []byte(cache), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if config != "" {
		if err := os.WriteFile(filepath.Join(dir, "config.toml"), []byte(config), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// As codex-cli 0.166 writes it (fields trimmed).
const codexCache = `{"fetched_at":"2026-10-31T18:25:16Z","etag":"W/\"x\"","client_version":"0.166.0","models":[
 {"slug":"gpt-6.1-sol","display_name":"GPT-6.1-Sol","visibility":"list","priority":1,"supported_reasoning_levels":[]},
 {"slug":"gpt-6-astra","display_name":"GPT-6-Astra","visibility":"list","priority":2},
 {"slug":"gpt-reserve","display_name":"GPT-Reserve","visibility":"hide","priority":4},
 {"slug":"gpt-6-luna","visibility":"list","priority":5},
 {"slug":"gpt-5.6-terra","display_name":"gpt-5.6-terra","visibility":"list"},
 {"slug":"codex-auto-review","display_name":"Codex Auto Review","visibility":"hide"},
 {"slug":"gpt-6-astra","display_name":"Again","visibility":"list"}
]}`

func TestCodexModelsFromCache(t *testing.T) {
	codexHomeWith(t, codexCache, "# mine\napproval_policy = \"never\"\nmodel = \"gpt-6-astra\" # the default\n\n[profiles.fast]\nmodel = \"gpt-6-luna\"\n")
	m := CodexModels()
	// Listed ones only, in the cache's order, each once.
	if want := []string{"gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-5.6-terra"}; !slices.Equal(m.IDs, want) {
		t.Fatalf("ids = %v, want %v", m.IDs, want)
	}
	// Display names that differ from the id.
	if len(m.Labels) != 2 || m.Labels["gpt-6.1-sol"] != "GPT-6.1-Sol" || m.Labels["gpt-6-astra"] != "GPT-6-Astra" {
		t.Fatalf("labels = %v", m.Labels)
	}
	if m.Default != "gpt-6-astra" {
		t.Fatalf("default = %q", m.Default)
	}
	// What a caller does to its copy stays its own.
	m.IDs[0], m.Labels["gpt-6-astra"] = "x", "y"
	if again := CodexModels(); again.IDs[0] != "gpt-6.1-sol" || again.Labels["gpt-6-astra"] != "GPT-6-Astra" {
		t.Fatalf("the memo changed: %+v", again)
	}
}

func TestCodexModelsWithoutAUsableCache(t *testing.T) {
	for name, cache := range map[string]string{
		"missing":       "",
		"not json":      "{models: [",
		"another shape": `{"models":{"gpt-6":{"visibility":"list"}}}`,
		"a list":        `[{"slug":"gpt-6","visibility":"list"}]`,
		"no visibility": `{"models":[{"slug":"gpt-6"},{"slug":"gpt-7","visibility":"none"}]}`,
		"odd entries":   `{"models":[1,"gpt-6",{"slug":7,"visibility":"list"},{"slug":"","visibility":"list"}]}`,
	} {
		t.Run(name, func(t *testing.T) {
			codexHomeWith(t, cache, "")
			if m := CodexModels(); len(m.IDs) != 0 || m.Labels != nil || m.Default != "" {
				t.Fatalf("models = %+v, want none", m)
			}
		})
	}
	// One odd entry leaves the others.
	codexHomeWith(t, `{"models":[42,{"slug":"gpt-6","visibility":"list"}]}`, "")
	if m := CodexModels(); !slices.Equal(m.IDs, []string{"gpt-6"}) {
		t.Fatalf("ids = %v", m.IDs)
	}
}

func TestCodexDefaultModel(t *testing.T) {
	for config, want := range map[string]string{
		`model = "gpt-6-astra"`:                          "gpt-6-astra",
		"model='gpt-6.1-sol'  # literal":                 "gpt-6.1-sol",
		`"model" = "gpt-\"q\""`:                          `gpt-"q"`,
		"model_provider = \"openai\"\n":                  "",
		"[profiles.x]\nmodel = \"gpt-6-luna\"\n":         "",
		"model = 5\n":                                    "",
		"model_reasoning_effort = \"high\"\nmodel=\"a\"": "a",
	} {
		codexHomeWith(t, "", config)
		if got := CodexModels().Default; got != want {
			t.Errorf("%q: default = %q, want %q", config, got, want)
		}
	}
}

func TestCodexModelsReadAgainWhenChanged(t *testing.T) {
	dir := codexHomeWith(t, `{"models":[{"slug":"gpt-6","visibility":"list"}]}`, `model = "gpt-6"`)
	if m := CodexModels(); !slices.Equal(m.IDs, []string{"gpt-6"}) || m.Default != "gpt-6" {
		t.Fatalf("models = %+v", m)
	}
	cache, config := filepath.Join(dir, "models_cache.json"), filepath.Join(dir, "config.toml")
	if err := os.WriteFile(cache, []byte(`{"models":[{"slug":"gpt-7","visibility":"list"}]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config, []byte(`model = "gpt-7"`), 0o600); err != nil {
		t.Fatal(err)
	}
	later := time.Now().Add(time.Minute)
	for _, p := range []string{cache, config} {
		if err := os.Chtimes(p, later, later); err != nil {
			t.Fatal(err)
		}
	}
	if m := CodexModels(); !slices.Equal(m.IDs, []string{"gpt-7"}) || m.Default != "gpt-7" {
		t.Fatalf("after the change: %+v", m)
	}
	os.Remove(cache)
	if m := CodexModels(); len(m.IDs) != 0 || m.Default != "gpt-7" {
		t.Fatalf("after the cache went: %+v", m)
	}
}

func TestCanvasNowOffersCodexModels(t *testing.T) {
	codexHomeWith(t, codexCache, `model = "gpt-6-astra"`)
	codex, _ := Lookup("codex")
	c := codex.Headless.CanvasNow()
	if !slices.Equal(c.Models, []string{"gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-5.6-terra"}) ||
		c.ModelLabels["gpt-6-astra"] != "GPT-6-Astra" || c.DefaultModel != "gpt-6-astra" {
		t.Fatalf("codex canvas = %+v", c)
	}
	// Its other capabilities stay; the catalog's entry is left as it was.
	if len(c.Effort) != 4 || !c.Images || codex.Headless.Canvas.Models != nil {
		t.Fatalf("codex canvas = %+v", c)
	}
	// An agent with a static list keeps it, with no labels or default.
	claude, _ := Lookup("claude")
	if cc := claude.Headless.CanvasNow(); !slices.Equal(cc.Models, []string{"opus", "sonnet", "haiku"}) ||
		cc.ModelLabels != nil || cc.DefaultModel != "" {
		t.Fatalf("claude canvas = %+v", cc)
	}
	// No cache: only the default, as before.
	codexHomeWith(t, "", "")
	if c := codex.Headless.CanvasNow(); len(c.Models) != 0 || c.DefaultModel != "" {
		t.Fatalf("codex canvas without a cache = %+v", c)
	}
}
