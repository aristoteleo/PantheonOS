package codingagents

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestOriginOfResolvedPath(t *testing.T) {
	cases := map[string]Origin{
		// npm's global prefix in ~/.local (npm config prefix), nvm, Homebrew's node.
		"/Users/u/.local/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js": {Via: "npm", Package: "@earendil-works/pi-coding-agent", Prefix: "/Users/u/.local"},
		"/home/u/.nvm/versions/node/v22.1.0/lib/node_modules/opencode-ai/bin/opencode":        {Via: "npm", Package: "opencode-ai", Prefix: "/home/u/.nvm/versions/node/v22.1.0"},
		"/workspace/.local/lib/node_modules/@openai/codex/bin/codex.js":                       {Via: "npm", Package: "@openai/codex", Prefix: "/workspace/.local"},
		"/home/u/.bun/install/global/node_modules/@qwen-code/qwen-code/cli.js":                {Via: "bun", Package: "@qwen-code/qwen-code", Prefix: "/home/u/.bun"},
		"/opt/homebrew/Cellar/gemini-cli/0.66.0/libexec/bin/gemini":                           {Via: "brew", Package: "gemini-cli", Prefix: "/opt/homebrew"},
		"/opt/homebrew/Caskroom/codex/0.166.0/codex-aarch64-apple-darwin":                     {Via: "brew-cask", Package: "codex", Prefix: "/opt/homebrew"},
		// Vendors' own installers: their updater.
		"/Users/u/.local/share/claude/versions/2.1.316":                               {},
		"/Users/u/.local/share/cursor-agent/versions/2026.10.22-27dcf5f/cursor-agent": {},
		"/Users/u/.codex/packages/standalone/current/bin/codex":                       {},
	}
	for path, want := range cases {
		if got := originOfResolved(path); got != want {
			t.Errorf("%s: %+v, want %+v", path, got, want)
		}
	}
}

func TestVersionComparison(t *testing.T) {
	newer := []struct{ latest, installed string }{
		{"1.5.0", "1.4.0"},
		{"2.1.316", "2.1.300 (Claude Code)"},
		{"0.166.0", "codex-cli 0.99.1"},
		{"1.10.0", "1.9.9"},
		{"1.0.0", "1.0.0-beta.2"},
		{"2026.10.29-abc1234", "2026.10.22-27dcf5f"},
	}
	for _, c := range newer {
		if !newerVersion(c.latest, c.installed) {
			t.Errorf("%s should be newer than %s", c.latest, c.installed)
		}
	}
	notNewer := []struct{ latest, installed string }{
		{"1.4.0", "1.4.0"},
		{"1.4.0", "1.5.0"},
		{"0.74.2", "1.4.0"},
		// Cursor's builds of one day differ only by their hash.
		{"2026.10.22-27dcf5f", "2026.10.22-0000000"},
		{"", "1.0.0"},
		{"1.0.0", "unknown"},
	}
	for _, c := range notNewer {
		if newerVersion(c.latest, c.installed) {
			t.Errorf("%s should not be newer than %s", c.latest, c.installed)
		}
	}
}

func TestEnginesRange(t *testing.T) {
	cases := []struct {
		v, rng    string
		ok, known bool
	}{
		{"v20.19.0", ">=22.19.0", false, true},
		{"v22.19.0", ">=22.19.0", true, true},
		{"v24.1.0", ">=22.19.0", true, true},
		{"v20.18.0", "^20.17.0 || >=22.9.0", true, true},
		{"v21.0.0", "^20.17.0 || >=22.9.0", false, true},
		{"v18.0.0", ">= 18", true, true},
		{"v18.0.0", ">=16 <20", true, true},
		{"v20.0.0", "20.x", true, true},
		{"v20.0.0", "node-gyp-ish nonsense", false, false},
	}
	for _, c := range cases {
		ok, known := satisfies(c.v, c.rng)
		if ok != c.ok || known != c.known {
			t.Errorf("satisfies(%s, %q) = %v,%v want %v,%v", c.v, c.rng, ok, known, c.ok, c.known)
		}
	}
}

// resetLatest gives a test an empty lookup cache and a clock of its own.
func resetLatest(t *testing.T) *time.Time {
	t.Helper()
	t.Setenv("PANTHEON_NO_UPDATE_CHECK", "")
	now := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	latestMu.Lock()
	latestCache = map[string]*latestEntry{}
	latestNow = func() time.Time { return now }
	latestMu.Unlock()
	t.Cleanup(func() {
		latestMu.Lock()
		latestCache, latestNow = map[string]*latestEntry{}, time.Now
		latestMu.Unlock()
	})
	return &now
}

// settle waits until key has no lookup in flight.
func settle(t *testing.T, key string) {
	t.Helper()
	for range 200 {
		latestMu.Lock()
		busy := latestCache[key] != nil && latestCache[key].busy != nil
		latestMu.Unlock()
		if !busy {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("lookup never ended")
}

func TestLatestIsCachedAndRateLimited(t *testing.T) {
	now := resetLatest(t)
	var calls atomic.Int32
	fail := atomic.Bool{}
	fetch := func(context.Context) (release, error) {
		calls.Add(1)
		if fail.Load() {
			return release{}, errors.New("offline")
		}
		return release{Version: fmt.Sprintf("1.%d.0", calls.Load())}, nil
	}
	rel, ok := latestRelease("k", fetch, time.Second)
	if !ok || rel.Version != "1.1.0" {
		t.Fatalf("first lookup: %+v %v", rel, ok)
	}
	for range 5 {
		if rel, _ := latestRelease("k", fetch, time.Second); rel.Version != "1.1.0" {
			t.Fatalf("cached: %+v", rel)
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("asked the source %d times within its TTL", calls.Load())
	}
	// Due again, but offline: the old answer stays, and the source is not
	// asked again before the retry interval.
	*now = now.Add(latestTTL)
	fail.Store(true)
	if rel, ok := latestRelease("k", fetch, time.Second); !ok || rel.Version != "1.1.0" {
		t.Fatalf("offline: %+v %v", rel, ok)
	}
	settle(t, "k")
	latestRelease("k", fetch, time.Second)
	settle(t, "k")
	if calls.Load() != 2 {
		t.Fatalf("a failed source asked again at once (%d calls)", calls.Load())
	}
	*now = now.Add(latestRetry)
	fail.Store(false)
	latestRelease("k", fetch, time.Second)
	settle(t, "k")
	if rel, _ := latestRelease("k", fetch, time.Second); rel.Version != "1.3.0" {
		t.Fatalf("after the retry interval: %+v (%d calls)", rel, calls.Load())
	}
}

func TestLatestNeverBlocksDetection(t *testing.T) {
	resetLatest(t)
	release1 := make(chan struct{})
	defer close(release1)
	slow := func(ctx context.Context) (release, error) {
		select {
		case <-release1:
		case <-ctx.Done():
		}
		return release{}, errors.New("slow")
	}
	start := time.Now()
	if _, ok := latestRelease("slow", slow, 50*time.Millisecond); ok {
		t.Fatal("a source that has not answered reported a version")
	}
	// A second detection does not wait on the lookup still in flight... it
	// waits at most its own limit, and starts no second lookup.
	latestRelease("slow", slow, 0)
	if d := time.Since(start); d > time.Second {
		t.Fatalf("detection waited %v", d)
	}
	t.Setenv("PANTHEON_NO_UPDATE_CHECK", "1")
	if _, ok := latestRelease("off", func(context.Context) (release, error) {
		t.Fatal("looked up with update checks off")
		return release{}, nil
	}, time.Second); ok {
		t.Fatal("update checks off")
	}
}

// fakeRegistry serves npm's /<pkg>/latest and Homebrew's API.
func fakeRegistry(t *testing.T, versions map[string]string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.EscapedPath(), "/")
		body, ok := versions[path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		fmt.Fprint(w, body)
	}))
	t.Cleanup(srv.Close)
	t.Setenv("npm_config_registry", srv.URL)
	old := brewAPI
	brewAPI = srv.URL
	t.Cleanup(func() { brewAPI = old })
	return srv
}

func writeExec(t *testing.T, path, script string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+script+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
}

func TestPlanUpdateByInstallLocation(t *testing.T) {
	resetLatest(t)
	fakeRegistry(t, map[string]string{
		"@earendil-works%2Fpi-coding-agent/latest": `{"version":"1.5.0","engines":{"node":">=22.19.0"}}`,
		"opencode-ai/latest":                       `{"version":"1.19.5"}`,
		"formula/gemini-cli.json":                  `{"versions":{"stable":"0.66.0"}}`,
		"cask/codex.json":                          `{"version":"0.166.0,abc"}`,
	})
	prefix := t.TempDir()
	writeExec(t, filepath.Join(prefix, "bin", "npm"), "exit 0")
	writeExec(t, filepath.Join(prefix, "bin", "node"), "echo v22.20.0")
	pi := Found{ID: "pi", Label: "Pi", Path: filepath.Join(prefix, "bin", "pi")}

	// npm at a prefix of its own (~/.local): that prefix's npm, at that prefix.
	p := planUpdate(pi, "1.4.0", Origin{Via: "npm", Package: "@earendil-works/pi-coding-agent", Prefix: prefix}, time.Second)
	want := []string{filepath.Join(prefix, "bin", "npm"), "install", "-g", "--prefix", prefix, "@earendil-works/pi-coding-agent@latest"}
	if fmt.Sprint(p.Argv) != fmt.Sprint(want) || p.Latest != "1.5.0" || !p.Newer || p.Note != "" {
		t.Fatalf("npm: %+v", p)
	}
	if p := planUpdate(pi, "1.5.0", Origin{Via: "npm", Package: "@earendil-works/pi-coding-agent", Prefix: prefix}, time.Second); p.Newer {
		t.Fatalf("up to date: %+v", p)
	}

	// An npm prefix without its own npm: the npm on PATH, still at that prefix.
	bare := t.TempDir()
	p = planUpdate(Found{ID: "opencode", Label: "OpenCode"}, "1.19.0", Origin{Via: "npm", Package: "opencode-ai", Prefix: bare}, time.Second)
	if fmt.Sprint(p.Argv) != fmt.Sprint([]string{"npm", "install", "-g", "--prefix", bare, "opencode-ai@latest"}) || !p.Newer {
		t.Fatalf("npm on PATH: %+v", p)
	}

	// The newest Pi needs a newer Node.js than the one at the prefix (a
	// Workspace on Node 20): no update offered, and the note says why.
	old := t.TempDir()
	writeExec(t, filepath.Join(old, "bin", "node"), "echo v20.19.0")
	p = planUpdate(pi, "0.74.2", Origin{Via: "npm", Package: "@earendil-works/pi-coding-agent", Prefix: old}, time.Second)
	if p.Argv != nil || p.Newer || !strings.Contains(p.Note, "needs Node.js >=22.19.0") || !strings.Contains(p.Note, "v20.19.0") {
		t.Fatalf("old Node.js: %+v", p)
	}

	// Homebrew: brew upgrade, a cask with --cask; the version from its API.
	p = planUpdate(Found{ID: "gemini", Label: "Gemini CLI"}, "0.60.0", Origin{Via: "brew", Package: "gemini-cli", Prefix: "/opt/homebrew"}, time.Second)
	if fmt.Sprint(p.Argv) != "[/opt/homebrew/bin/brew upgrade gemini-cli]" || p.Latest != "0.66.0" || !p.Newer {
		t.Fatalf("brew: %+v", p)
	}
	p = planUpdate(Found{ID: "codex", Label: "Codex"}, "codex-cli 0.166.0", Origin{Via: "brew-cask", Package: "codex", Prefix: "/opt/homebrew"}, time.Second)
	if fmt.Sprint(p.Argv) != "[/opt/homebrew/bin/brew upgrade --cask codex]" || p.Latest != "0.166.0" || p.Newer {
		t.Fatalf("cask: %+v", p)
	}

	// The vendor's own install: its updater, and its own feed.
	feeds := releaseSources
	releaseSources = map[string]releaseSource{"pi": {Feed: func(context.Context) (string, error) { return "1.5.0", nil }}}
	t.Cleanup(func() { releaseSources = feeds })
	p = planUpdate(Found{ID: "pi", Label: "Pi", Path: "/home/u/.pi/agent/bin/pi"}, "1.4.0", Origin{}, time.Second)
	if fmt.Sprint(p.Argv) != "[/home/u/.pi/agent/bin/pi update]" || !p.Newer {
		t.Fatalf("vendor: %+v", p)
	}
	// An agent with no updater and no package manager: nothing to offer.
	if p := planUpdate(Found{ID: "goose", Label: "Goose", Path: "/usr/local/bin/goose"}, "1.0", Origin{}, time.Second); p.Argv != nil || p.Newer {
		t.Fatalf("goose: %+v", p)
	}
	// The Pantheon Agent updates with its node.
	if p := PlanUpdate(Found{ID: PantheonAgent, Path: "/usr/local/bin/pantheon"}, "0.9"); p.Argv != nil {
		t.Fatalf("pantheon: %+v", p)
	}
}
