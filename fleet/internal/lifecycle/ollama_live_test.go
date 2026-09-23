package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Opt in only with an already verified public engine archive cache. Uses its
// own Fleet root, port, HOME and empty model store. No user's Ollama is stopped.
func TestLivePreparedOllamaRestart(t *testing.T) {
	testPreparedEngineRestart(t, os.Getenv("FLEET_TEST_OLLAMA_CACHE"), "ollama-0.34.2-darwin", "/api/tags", "/api/version")
}

func TestLivePreparedLLMsterRestart(t *testing.T) {
	testPreparedEngineRestart(t, os.Getenv("FLEET_TEST_LLMSTER_CACHE"), "llmster-0.0.25-1-darwin-arm64", "/v1/models", "/v1/models")
}

func testPreparedEngineRestart(t *testing.T, cache, recipeID, modelsPath, healthPath string) {
	t.Helper()
	if cache == "" || runtime.GOOS != "darwin" || runtime.GOARCH != "arm64" {
		t.Skip("isolated macOS engine acceptance only")
	}

	source, err := filepath.Abs(filepath.Join("..", "..", "..", "apps", "model-service"))
	if err != nil {
		t.Fatal(err)
	}
	inv := node.DetectResources()
	m, err := Open(t.TempDir(), "managed-model-test", "isolated-mac", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}, Resources: &inv}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer func() { m.Close() }()
	m.SetResourceSampler(node.DetectResources)
	// Always clean up every exact owned process, including a failed readiness
	// attempt. Manager.Close stops reconciliation; it deliberately keeps apps.
	defer func() {
		for _, in := range m.Snapshot().Instances {
			for _, resource := range in.Resources {
				if err := (NativeDriver{}).Stop(context.Background(), Component{Name: resource.Component, StopSeconds: 10}, resource); err != nil {
					t.Error("test process cleanup:", err)
				}
			}
		}
	}()
	ownedCache := filepath.Join(m.root, "cache", "model-service")
	prepare := exec.Command("python3", "-c", `import sys,threading
sys.path.insert(0,sys.argv[1])
from engines import EngineCache,recipe
from artifacts import ArtifactCache,file_lock,atomic_json
EngineCache(sys.argv[2],ArtifactCache(sys.argv[3]),file_lock,atomic_json,'engine-acceptance').fetch(recipe(sys.argv[4])['source'],threading.Event(),lambda *args:None)
`, source, ownedCache, filepath.Join(cache, "blobs"), recipeID)
	if output, err := prepare.CombinedOutput(); err != nil {
		t.Fatalf("prepare: %v: %s", err, output)
	}
	policy := os.Getenv("FLEET_TEST_MODEL_POLICY")
	ttl := 300
	if policy != "" && policy != "warm" && policy != "resident" && policy != "on_demand" {
		t.Fatal("unsupported model lifetime acceptance policy")
	}
	if policy == "warm" {
		ttl = 3
	} else if policy != "" {
		ttl = 0
	}
	configuration := map[string]any{"recipe_id": recipeID, "context_length": 4096, "parallel": 1, "keep_alive_seconds": ttl}
	if policy != "" {
		configuration["load_policy"] = policy
	}
	configBytes, err := json.Marshal(configuration)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string]string{"engine-config.json": string(configBytes)}
	for _, name := range []string{"engines.py", "engines.json", "managed_engine.py", "llmster_runtime.py"} {
		b, err := os.ReadFile(filepath.Join(source, name))
		if err != nil {
			t.Fatal(err)
		}
		files[name] = string(b)
	}
	def := Definition{Protocol: 1, AppID: "model-service", Version: "0.1.0", Requires: Requirements{OS: []string{"darwin"}, Arch: []string{"arm64"}, Caps: []string{"proc"}}, Components: []Component{{
		Name: "backend", Runtime: "process", Argv: []string{"python3", "${PACKAGE}/managed_engine.py", "start"}, Ports: map[string]int{"http": 0}, StopSeconds: 10,
		Readiness: Probe{Argv: []string{"python3", "${PACKAGE}/managed_engine.py", "ready"}, TimeoutSeconds: 20},
		Resources: &ResourceRequest{MemoryBytes: 2 << 30, Devices: []DeviceBudget{{ID: "apple-metal", Backend: "metal", MemoryBytes: 1 << 30}}},
	}}}
	if recipeID == "llmster-0.0.25-1-darwin-arm64" && os.Getenv("FLEET_TEST_MODEL_SOURCE") == "" {
		// This acceptance starts an empty daemon with JIT disabled; it does
		// not budget or load model weights. Inference gets a separate budget.
		def.Components[0].Resources.MemoryBytes = 1 << 30
		def.Components[0].Resources.Devices[0].MemoryBytes = 256 << 20
	}
	if os.Getenv("FLEET_TEST_MODEL_SOURCE") != "" {
		// Qwen 0.5B Q4 with a fixed 4K context and one concurrent call.
		// Fleet still enforces the ordinary host safety margin; no test override.
		def.Components[0].Resources.MemoryBytes = 1536 << 20
	}
	payload, digest := bundle(t, def, files)
	if _, err := stageModelArtifact(m, digest, payload); err != nil {
		t.Fatal(err)
	}
	scope := "engine-acceptance"
	id := m.instanceID(digest, scope)
	run := func(action string) {
		t.Helper()
		var gen uint64
		if in := m.Snapshot().Instances[id]; in != nil {
			gen = in.Generation
		}
		request := Request{Protocol: 1, OperationID: fmt.Sprintf("%s-%d", action, time.Now().UnixNano()), Action: action, Digest: digest, Scope: scope, Generation: gen}
		if _, err := m.Submit(request); err != nil {
			t.Fatal(err)
		}
		for deadline := time.Now().Add(40 * time.Second); time.Now().Before(deadline); {
			op := m.Snapshot().Operations[request.OperationID]
			if op.State == "succeeded" {
				return
			}
			if op.State != "running" && op.State != "queued" {
				filepath.WalkDir(m.root, func(path string, d os.DirEntry, e error) error {
					if e == nil && !d.IsDir() && filepath.Ext(path) == ".log" {
						if b, e := os.ReadFile(path); e == nil {
							t.Logf("%s: %s", filepath.Base(path), b)
						}
					}
					return nil
				})
				t.Fatalf("%s failed: %s", action, op.Error)
			}
			time.Sleep(100 * time.Millisecond)
		}
		t.Fatal("managed engine lifecycle deadline exceeded")
	}
	run("install")
	defer func() {
		if in := m.Snapshot().Instances[id]; in != nil && in.State == "ready" {
			run("stop")
		}
	}()
	client := &http.Client{Timeout: 3 * time.Second}
	modelSource := os.Getenv("FLEET_TEST_MODEL_SOURCE")
	modelAcceptance := modelSource != ""
	for attempt := 0; attempt < 2; attempt++ {
		started := time.Now()
		run("start")
		t.Logf("prepared engine start %d: %s", attempt, time.Since(started))
		if recipeID == "llmster-0.0.25-1-darwin-arm64" {
			// Validate settings AFTER the real daemon parsed them. A schema
			// error silently replaces the file with vendor defaults.
			verify := exec.Command("python3", "-c", `import json,sys
from pathlib import Path
p=Path(sys.argv[1])/'models/lmstudio/engine-acceptance/.lmstudio/settings.json'
s=json.loads(p.read_text())
assert s['autoLoadBundledLLM'] is False and s['enableLocalService'] is False
assert s['developer']['autoUpdateExtensionPacks'] is False
t=s['developer']['jitModelTTL'];ttl=int(sys.argv[2])
assert t['ttlSeconds'] > 0 and t['enabled'] == (ttl > 0)
`, ownedCache, fmt.Sprint(ttl))
			if output, err := verify.CombinedOutput(); err != nil {
				t.Fatalf("llmster discarded owned settings: %v: %s", err, output)
			}
		}
		in := m.Snapshot().Instances[id]
		endpoint, err := m.Service(id, digest, in.Generation, "backend", "http")
		if err != nil {
			t.Fatal(err)
		}
		response, err := client.Get(endpoint + modelsPath)
		if err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != http.StatusOK {
			response.Body.Close()
			t.Fatal("owned engine model catalog unavailable", response.StatusCode)
		}
		var models struct {
			Models []any
			Data   []any
		}
		err = json.NewDecoder(response.Body).Decode(&models)
		response.Body.Close()
		if err != nil || (!modelAcceptance || attempt == 0) && (len(models.Models) != 0 || len(models.Data) != 0) {
			t.Fatal("owned engine should have an isolated empty model store", err, models)
		}
		if modelAcceptance {
			script := filepath.Join(source, "..", "..", "fleet", "scripts", "verify-managed-model.py")
			args := []string{script, "--source", source, "--cache", ownedCache, "--endpoint", endpoint,
				"--recipe", recipeID, "--artifact", modelSource, "--blob-cache", os.Getenv("FLEET_TEST_MODEL_BLOBS"),
				"--memory-bytes", fmt.Sprint(def.Components[0].Resources.MemoryBytes)}
			if policy != "" {
				args = append(args, "--load-policy", policy)
			}
			if attempt > 0 {
				args = append(args, "--restart")
			}
			ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
			check := exec.CommandContext(ctx, "python3", args...)
			output, err := check.CombinedOutput()
			cancel()
			t.Logf("managed model check: %s", output)
			if err != nil {
				t.Fatal("managed model acceptance:", err)
			}
		}
		// Simulate a runner restart while the exact engine survives, then a
		// machine-style restart where that owned process has already exited.
		before := m.Snapshot().Instances[id]
		if attempt == 1 {
			if err := (NativeDriver{}).Stop(context.Background(), def.Components[0], before.Resources[0]); err != nil {
				t.Fatal(err)
			}
		}
		if err := m.Close(); err != nil {
			t.Fatal(err)
		}
		m, err = Open(m.root, "managed-model-test", "isolated-mac", m.caps, NativeDriver{})
		if err != nil {
			t.Fatal(err)
		}
		m.SetResourceSampler(node.DetectResources)
		recoveredAt := time.Now()
		run("recover")
		after := m.Snapshot().Instances[id]
		if attempt == 0 {
			if after.State != "ready" || after.Generation != before.Generation || !reflect.DeepEqual(after.Resources, before.Resources) || !reflect.DeepEqual(after.Reservations, before.Reservations) {
				t.Fatal("recovery changed a surviving owned engine", after)
			}
		} else {
			if after.State != "stopped" || len(after.Reservations) != 0 || after.Generation != before.Generation+1 {
				t.Fatal(after)
			}
			run("start")
			after = m.Snapshot().Instances[id]
			endpoint, err = m.Service(id, digest, after.Generation, "backend", "http")
			if err != nil {
				t.Fatal(err)
			}
		}
		response, err = client.Get(endpoint + modelsPath)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusOK {
			t.Fatal("recovered engine unhealthy", response.StatusCode)
		}
		t.Logf("recovery %d completed in %s (generation %d -> %d)", attempt, time.Since(recoveredAt), before.Generation, after.Generation)
		run("stop")
		if len(m.Snapshot().Instances[id].Reservations) != 0 {
			t.Fatal("engine memory reservation survived shutdown")
		}
		if response, err := client.Get(endpoint + healthPath); err == nil {
			response.Body.Close()
			t.Fatal("owned engine is still serving after stop")
		}
	}
	verify := exec.Command("python3", "-c", `import sys
sys.path.insert(0,sys.argv[1])
from engines import installed,recipe
assert installed(sys.argv[2],recipe(sys.argv[3]))
`, source, ownedCache, recipeID)
	if output, err := verify.CombinedOutput(); err != nil {
		t.Fatalf("shared recipe cache was mutated by an engine: %v: %s", err, output)
	}
	t.Log("engine restarted from prepared cache; owned port closed and reservation released after each stop")
}
