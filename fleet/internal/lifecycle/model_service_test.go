package lifecycle

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Exercises the shipped connector through the actual process supervisor,
// including its readiness/drain hooks, restart and retained configuration.
// Optional real inference: FLEET_TEST_MODEL_ENDPOINT=http://127.0.0.1:11434/v1
// FLEET_TEST_MODEL=gemma4:latest. This never installs or stops the attached engine.
func TestAttachedModelServiceLifecycle(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	if _, err := exec.LookPath(python); err != nil {
		t.Skip("Python 3 is required")
	}
	root := filepath.Join("..", "..", "..", "apps", "model-service")
	definitionBytes, err := os.ReadFile(filepath.Join(root, "fleet."+runtime.GOOS+"-"+runtime.GOARCH+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var def Definition
	if err = json.Unmarshal(definitionBytes, &def); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{}
	modules, err := filepath.Glob(filepath.Join(root, "*.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range append(modules, filepath.Join(root, "engines.json")) {
		body, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		files[filepath.Base(name)] = string(body)
	}
	payload, digest := bundle(t, def, files)
	m, err := Open(t.TempDir(), "model-test-owner", "model-test-node", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Close() })
	if _, err = m.Stage(digest, 0, payload); err != nil {
		t.Fatal(err)
	}
	scope := "model-test"
	run := func(action string, generation uint64) {
		t.Helper()
		op := submit(t, m, digest, fmt.Sprintf("%s-%d", action, time.Now().UnixNano()), action, scope, generation)
		if op.State != "succeeded" {
			t.Fatalf("%s failed: %s", action, op.Error)
		}
	}
	started := time.Now()
	run("install", 0)
	run("start", 0)
	id := m.instanceID(digest, scope)
	t.Cleanup(func() {
		current := m.Snapshot().Instances[id]
		if current != nil && current.State == "ready" {
			run("stop", current.Generation)
		}
	})
	endpoint := func() string {
		in := m.Snapshot().Instances[id]
		url, err := m.Service(id, digest, in.Generation, "backend", "http")
		if err != nil {
			t.Fatal(err)
		}
		return url
	}
	client := http.Client{Timeout: 180 * time.Second}
	rpc := func(method string, args map[string]any) map[string]any {
		data, _ := json.Marshal(map[string]any{"method": method, "args": args})
		request, _ := http.NewRequest("POST", endpoint()+"/rpc", bytes.NewReader(data))
		token, err := m.RPCCredential(id, digest, m.Snapshot().Instances[id].Generation)
		if err != nil {
			t.Fatal(err)
		}
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("X-Fleet-RPC-Token", token)
		res, err := client.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		var result map[string]any
		if json.NewDecoder(res.Body).Decode(&result) != nil || res.StatusCode != 200 {
			t.Fatalf("RPC failed: %v", result)
		}
		return result
	}
	t.Logf("connector install + start: %s", time.Since(started))
	fixture := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"data":[{"id":"fixture"}]}`)
	}))
	defer fixture.Close()
	upstream := os.Getenv("FLEET_TEST_MODEL_ENDPOINT")
	if upstream == "" {
		upstream = fixture.URL + "/v1"
	}
	config := rpc("configure", map[string]any{"config": map[string]string{"engine": "ollama", "endpoint": upstream}})
	if rpc("artifacts_list", nil)["jobs"] == nil {
		t.Fatal("download store not available through authenticated owner RPC")
	}
	if _, err := os.Stat(filepath.Join(m.root, "cache", "model-service", "tasks", scope, "jobs.sqlite3")); err != nil {
		t.Fatal("download metadata was not stored in the stable app cache", err)
	}
	if rpc("discover", nil)["models"] == nil {
		t.Fatal("discovery returned no catalog")
	}
	in := m.Snapshot().Instances[id]
	if err = m.SetKeepAlive(id, digest, in.Generation, true); err != nil {
		t.Fatal(err)
	}
	run("stop", in.Generation)
	if _, err = m.Service(id, digest, in.Generation, "backend", "http"); err == nil {
		t.Fatal("stopped connector still routable")
	}
	started = time.Now()
	run("start", m.Snapshot().Instances[id].Generation)
	t.Logf("connector restart with retained configuration: %s", time.Since(started))
	if rpc("status", nil)["config_revision"] != config["config_revision"] {
		t.Fatal("configuration was not retained")
	}
	if rpc("artifacts_list", nil)["jobs"] == nil {
		t.Fatal("download store not re-opened after process restart")
	}
	// Upgrade via the real supervisor. The checkpoint includes SQLite history,
	// while downloads stay in the stable App cache rather than being copied.
	if rpc("cancel_request", map[string]any{"request_id": "before-upgrade"})["cancelled"] != true {
		t.Fatal("activity not recorded")
	}
	cachePath := filepath.Join(m.root, "cache", "model-service", "tasks", scope, "jobs.sqlite3")
	cacheBefore, err := os.Stat(cachePath)
	if err != nil {
		t.Fatal(err)
	}
	oldDigest, oldID := digest, id
	run("stop", m.Snapshot().Instances[id].Generation)
	oldGeneration := m.Snapshot().Instances[id].Generation
	def.Version = "upgrade-test"
	newPayload, newDigest := bundle(t, def, files)
	if _, err = m.Stage(newDigest, 0, newPayload); err != nil {
		t.Fatal(err)
	}
	digest = newDigest
	id = m.instanceID(digest, scope)
	run("install", 0)
	if _, err = m.Submit(Request{Protocol: 1, OperationID: "upgrade-copy", Action: "clone_data", Digest: digest, Scope: scope, DataSource: &DataSource{oldDigest, oldGeneration}}); err != nil {
		t.Fatal(err)
	}
	if op := wait(t, m, "upgrade-copy"); op.State != "succeeded" {
		t.Fatal(op.Error)
	}
	run("start", 0)
	if rpc("status", nil)["config_revision"] != config["config_revision"] {
		t.Fatal("upgrade lost configuration")
	}
	records := rpc("activity", nil)["requests"].([]any)
	if len(records) != 1 || records[0].(map[string]any)["request_id"] != "before-upgrade" {
		t.Fatal("upgrade lost request history", records)
	}
	cacheAfter, err := os.Stat(cachePath)
	if err != nil || !os.SameFile(cacheBefore, cacheAfter) {
		t.Fatal("upgrade replaced download cache", err)
	}
	if _, err = os.Stat(filepath.Join(m.root, "data", oldID, "connector.json")); err != nil {
		t.Fatal("original configuration removed", err)
	}
	t.Log("connector upgrade preserved configuration, SQLite activity and original download cache")
	if model := os.Getenv("FLEET_TEST_MODEL"); model != "" {
		body, _ := json.Marshal(map[string]any{"model": model, "stream": true, "max_tokens": 128, "messages": []map[string]string{{"role": "user", "content": "Reply with exactly: FLEET_MODEL_OK. Do not explain."}}})
		req, _ := http.NewRequest("POST", endpoint()+"/v1/chat/completions", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Model-Request", "real-model-smoke")
		req.Header.Set("X-Model-Config", config["config_revision"].(string))
		started = time.Now()
		res, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		if res.StatusCode != 200 {
			data, _ := io.ReadAll(res.Body)
			t.Fatalf("inference HTTP %d: %s", res.StatusCode, data)
		}
		scanner := bufio.NewScanner(res.Body)
		scanner.Buffer(make([]byte, 4096), 1024*1024)
		text := ""
		done := false
		first := time.Duration(0)
		for scanner.Scan() {
			line := scanner.Text()
			if line == "data: [DONE]" {
				done = true
				break
			}
			if !strings.HasPrefix(line, "data: ") {
				continue
			}
			var chunk struct {
				Choices []struct{ Delta struct{ Content string } }
			}
			if json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk) != nil {
				t.Fatal("invalid model SSE")
			}
			for _, choice := range chunk.Choices {
				text += choice.Delta.Content
				if choice.Delta.Content != "" && first == 0 {
					first = time.Since(started)
				}
			}
		}
		if !done || !strings.Contains(text, "FLEET_MODEL_OK") {
			t.Fatalf("incomplete inference: %q", text)
		}
		t.Logf("real %s result=%q, first content=%s total=%s", model, text, first, time.Since(started))
	}
	run("stop", m.Snapshot().Instances[id].Generation)
	// The fixture or real borrowed engine is independent of the connector.
	res, err := client.Get(upstream + "/models")
	if err != nil {
		t.Fatal("attached engine was stopped", err)
	}
	res.Body.Close()
}
