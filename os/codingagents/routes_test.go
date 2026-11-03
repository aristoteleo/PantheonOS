package codingagents

import (
	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

const testAccessKey = "pbk_access-test-0123456789"

// fakeHub serves one user's routes and their resolution to the test access key.
type fakeHub struct {
	*httptest.Server
	resolves atomic.Int32
	// plan is what resolving answers: the candidates' deployments and models.
	plan [][2]string
}

func newFakeHub(t *testing.T, plan ...[2]string) *fakeHub {
	t.Helper()
	h := &fakeHub{plan: plan}
	h.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testAccessKey {
			http.Error(w, `{"detail":"Not authenticated"}`, http.StatusUnauthorized)
			return
		}
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/model-services/routes":
			_, _ = io.WriteString(w, `{"routes":[
				{"route_id":"tier-normal","name":"Normal","requires":{"operation":"text"},"candidates":[
					{"deployment_id":"platform","model_id":"openrouter/deepseek/deepseek-v4.1-flash"},
					{"deployment_id":"d_gpu","model_id":"qwen3-coder"},
					{"deployment_id":"platform","model_id":"openrouter/z-ai/glm-5.3"}]},
				{"route_id":"voices","name":"Voices","requires":{"operation":"speech"},"candidates":[{"deployment_id":"platform","model_id":"tts-1"}]},
				{"route_id":"fast","name":"Fast","candidates":[{"deployment_id":"platform","model_id":"openrouter/openai/gpt-5.5-mini"}]}],
				"platform":{}}`)
		case r.Method == http.MethodPost && r.URL.Path == "/api/model-services/routes/tier-normal/resolve":
			h.resolves.Add(1)
			var req map[string]any
			_ = json.NewDecoder(r.Body).Decode(&req)
			if req["operation"] != "text" {
				http.Error(w, "operation", http.StatusBadRequest)
				return
			}
			var c []map[string]any
			for _, p := range h.plan {
				c = append(c, map[string]any{"deployment": map[string]any{"deployment_id": p[0]}, "model": map[string]any{"id": p[1]}})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"candidates": c, "transport": "fleet_relay",
				"excluded": []map[string]string{{"deployment_id": "platform", "model_id": "openrouter/z-ai/glm-5.2", "reason": "tools_unconfirmed"}}})
		default:
			http.Error(w, `{"detail":"Model route is not in your directory"}`, http.StatusNotFound)
		}
	}))
	t.Cleanup(h.Close)
	return h
}

// fakeModels is the platform proxy's chat completions, each model answering as given.
type fakeModels struct {
	*httptest.Server
	mu    sync.Mutex
	asked []string
}

func newFakeModels(t *testing.T, answer map[string]func(w http.ResponseWriter, body map[string]any)) *fakeModels {
	t.Helper()
	f := &fakeModels{}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer "+testPlatformKey {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		model, _ := body["model"].(string)
		f.mu.Lock()
		f.asked = append(f.asked, model)
		f.mu.Unlock()
		if a := answer[model]; a != nil {
			a(w, body)
			return
		}
		http.Error(w, `{"error":{"message":"no such model"}}`, http.StatusNotFound)
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeModels) models() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.asked)
}

// onRoutes gives this process the platform at models and the Hub at hub,
// and a gateway in front of them.
func onRoutes(t *testing.T, hub, models string) (*RouteGateway, *httptest.Server) {
	t.Helper()
	onPlatform(t, models)
	s := Settings{Access: Access{Key: testAccessKey, HubURL: hub + "/"}}
	g := newRouteGateway("", func() Settings { return s })
	srv := httptest.NewServer(g)
	g.base = srv.URL
	t.Cleanup(srv.Close)
	return g, srv
}

func ask(t *testing.T, srv *httptest.Server, token, model string, stream bool) *http.Response {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"model": model, "stream": stream, "messages": []map[string]string{{"role": "user", "content": "hi"}},
		"tools": []map[string]any{{"type": "function", "function": map[string]any{"name": "read"}}}})
	req, _ := http.NewRequest(http.MethodPost, srv.URL+"/v1/chat/completions", strings.NewReader(string(body)))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func sse(chunks ...string) func(w http.ResponseWriter, _ map[string]any) {
	return func(w http.ResponseWriter, _ map[string]any) {
		w.Header().Set("Content-Type", "text/event-stream")
		for _, c := range chunks {
			_, _ = io.WriteString(w, "data: "+c+"\n\n")
			w.(http.Flusher).Flush()
		}
	}
}

func TestRoutesListed(t *testing.T) {
	hub := newFakeHub(t)
	onPlatform(t, "https://hub.test/litellm")
	got := PlatformRoutes(Settings{Access: Access{Key: testAccessKey, HubURL: hub.URL}})
	// Text routes by name, each with its platform models; one on the user's own computers said so.
	want := []Route{{Ref: "fleet-route://fast", Name: "Fast", Models: []string{"openrouter/openai/gpt-5.5-mini"}},
		{Ref: "fleet-route://tier-normal", Name: "Normal", Models: []string{"openrouter/deepseek/deepseek-v4.1-flash", "openrouter/z-ai/glm-5.3"}, Note: RouteSkipped}}
	if b, w := mustJSON(t, got), mustJSON(t, want); b != w {
		t.Errorf("routes %s\nwant %s", b, w)
	}
	if routeName("tier-normal") != "Normal" || routeName("gone") != "gone" {
		t.Error("route names")
	}
	// Without a PantheonOS key, none.
	if r := PlatformRoutes(Settings{Access: Access{HubURL: hub.URL}}); len(r) != 0 {
		t.Errorf("no key: %v", r)
	}
	for in, want := range map[string]bool{"fleet-route://tier-normal": true, "fleet-route://": false, "fleet-route://Bad": false,
		"openrouter/x": false, "fleet-route://a/b": false} {
		if _, ok := RouteID(in); ok != want {
			t.Errorf("RouteID(%q) = %v", in, ok)
		}
	}
	if SubscriptionModel("fleet-route://tier-normal") != "" {
		t.Error("a route is no subscription model")
	}
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestRouteGatewayToken(t *testing.T) {
	hub := newFakeHub(t, [2]string{"platform", "m1"})
	models := newFakeModels(t, map[string]func(http.ResponseWriter, map[string]any){"m1": sse(`{"ok":1}`)})
	g, srv := onRoutes(t, hub.URL, models.URL)
	if resp := ask(t, srv, "", "tier-normal", true); resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("no token: %d", resp.StatusCode)
	}
	if resp := ask(t, srv, testPlatformKey, "tier-normal", true); resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("the platform key is no token: %d", resp.StatusCode)
	}
	token := g.grant("pi", "tier-normal")
	if resp := ask(t, srv, token, "other-route", true); resp.StatusCode != http.StatusForbidden {
		t.Errorf("a launch runs its own route only: %d", resp.StatusCode)
	}
	if resp := ask(t, srv, token, "fleet-route://tier-normal", true); resp.StatusCode != http.StatusOK {
		t.Errorf("by reference: %d", resp.StatusCode)
	}
	if n := len(models.models()); n != 1 {
		t.Errorf("only the granted request reached the models: %d", n)
	}
}

func TestRouteGatewayFallsBackBeforeTheFirstByte(t *testing.T) {
	hub := newFakeHub(t, [2]string{"platform", "down"}, [2]string{"d_gpu", "local-qwen"}, [2]string{"platform", "up"})
	models := newFakeModels(t, map[string]func(http.ResponseWriter, map[string]any){
		"down": func(w http.ResponseWriter, _ map[string]any) {
			http.Error(w, `{"error":{"message":"provider overloaded"}}`, http.StatusServiceUnavailable)
		},
		"up": sse(`{"choices":[{"delta":{"content":"from up"}}]}`, "[DONE]"),
	})
	g, srv := onRoutes(t, hub.URL, models.URL)
	resp := ask(t, srv, g.grant("pi", "tier-normal"), "tier-normal", true)
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(body), "from up") || resp.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("%d %q %s", resp.StatusCode, resp.Header.Get("Content-Type"), body)
	}
	// In the route's order; the model on the user's own computer skipped.
	if got := models.models(); !slices.Equal(got, []string{"down", "up"}) {
		t.Errorf("asked %v", got)
	}
	// The resolution is kept a little while.
	_ = ask(t, srv, g.grant("pi", "tier-normal"), "tier-normal", true)
	if n := hub.resolves.Load(); n != 1 {
		t.Errorf("resolved %d times", n)
	}

	// None answering: the error says why, model by model.
	hub2 := newFakeHub(t, [2]string{"platform", "down"}, [2]string{"d_gpu", "local-qwen"})
	g2, srv2 := onRoutes(t, hub2.URL, models.URL)
	resp = ask(t, srv2, g2.grant("pi", "tier-normal"), "tier-normal", true)
	body, _ = io.ReadAll(resp.Body)
	for _, want := range []string{"down: 503", "provider overloaded", "local-qwen", RouteSkipped, "glm-5.2 (tools_unconfirmed)"} {
		if resp.StatusCode != http.StatusBadGateway || !strings.Contains(string(body), want) {
			t.Errorf("%d %s lacks %q", resp.StatusCode, body, want)
		}
	}
}

func TestRouteGatewayNeverReplaysAStartedAnswer(t *testing.T) {
	hub := newFakeHub(t, [2]string{"platform", "breaks"}, [2]string{"platform", "up"})
	models := newFakeModels(t, map[string]func(http.ResponseWriter, map[string]any){
		"breaks": func(w http.ResponseWriter, _ map[string]any) {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "data: {\"partial\":1}\n\n")
			w.(http.Flusher).Flush()
			// The connection drops mid-answer.
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close()
		},
		"up": sse(`{"never":1}`),
	})
	g, srv := onRoutes(t, hub.URL, models.URL)
	resp := ask(t, srv, g.grant("opencode", "tier-normal"), "tier-normal", true)
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "partial") || strings.Contains(string(body), "never") {
		t.Errorf("body %s", body)
	}
	if got := models.models(); !slices.Equal(got, []string{"breaks"}) {
		t.Errorf("a started generation was sent again: %v", got)
	}
}

func TestRouteGatewayStreams(t *testing.T) {
	hub := newFakeHub(t, [2]string{"platform", "slow"})
	next := make(chan struct{})
	models := newFakeModels(t, map[string]func(http.ResponseWriter, map[string]any){
		"slow": func(w http.ResponseWriter, body map[string]any) {
			if body["tools"] == nil || body["stream"] != true {
				http.Error(w, "the request is passed on as it is", http.StatusBadRequest)
				return
			}
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "data: one\n\n")
			w.(http.Flusher).Flush()
			<-next // the second chunk waits until the agent has the first
			_, _ = io.WriteString(w, "data: two\n\n")
		},
	})
	g, srv := onRoutes(t, hub.URL, models.URL)
	resp := ask(t, srv, g.grant("pi", "tier-normal"), "tier-normal", true)
	lines := bufio.NewReader(resp.Body)
	first, err := lines.ReadString('\n')
	close(next)
	if err != nil || first != "data: one\n" {
		t.Fatalf("first chunk %q %v", first, err)
	}
	rest, _ := io.ReadAll(lines)
	if !strings.Contains(string(rest), "data: two") {
		t.Errorf("rest %q", rest)
	}
}

func TestRouteLaunch(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	onPlatform(t, "https://hub.test/litellm")
	s := Settings{Access: Access{Key: testAccessKey, HubURL: "https://hub.test"}}
	if _, _, _, ok, _ := RouteLaunch("pi", "openrouter/deepseek/deepseek-v4.1-flash", s); ok {
		t.Error("a model is no route")
	}
	if _, _, _, ok, err := RouteLaunch("claude", "fleet-route://tier-normal", s); !ok || err == nil {
		t.Error("Claude Code runs no route yet")
	}
	if _, _, _, ok, err := RouteLaunch("pi", "fleet-route://tier-normal", Settings{}); !ok || err == nil {
		t.Error("a route needs PantheonOS on")
	}
	args, env, model, ok, err := RouteLaunch("pi", "fleet-route://tier-normal", s)
	if !ok || err != nil || model != "tier-normal" || len(args) != 4 || args[0] != "-e" || args[2] != "--provider" || args[3] != routeProvider {
		t.Fatalf("pi: %v %v %q %v %v", args, env, model, ok, err)
	}
	ext, _ := os.ReadFile(args[1])
	line := strings.Join(append(args, env...), " ") + string(ext)
	for _, secret := range []string{testAccessKey, testPlatformKey} {
		if strings.Contains(line, secret) {
			t.Errorf("a key reaches the agent: %s", line)
		}
	}
	if !slices.Contains(env, platformKeyVar+"=") || !strings.HasPrefix(env[0], routeTokenVar+"=prt_") ||
		!strings.Contains(string(ext), `"id":"tier-normal"`) || !strings.Contains(string(ext), "process.env."+routeTokenVar) {
		t.Errorf("pi env %v ext %s", env, ext)
	}
	// OpenCode: its configuration for the process, the token named in it, not given.
	_, env, model, _, err = RouteLaunch("opencode", "fleet-route://tier-normal", s)
	var cfg string
	for _, kv := range env {
		if v, ok := strings.CutPrefix(kv, "OPENCODE_CONFIG_CONTENT="); ok {
			cfg = v
		}
	}
	if err != nil || model != "pantheon-routes/tier-normal" || !strings.Contains(cfg, `"apiKey":"{env:`+routeTokenVar+`}"`) ||
		!strings.Contains(cfg, `"model":"pantheon-routes/tier-normal"`) || strings.Contains(cfg, testPlatformKey) {
		t.Errorf("opencode %q %s %v", model, cfg, err)
	}
}
