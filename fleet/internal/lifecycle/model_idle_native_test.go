package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Real shipped connector, real separately supervised processes and actual
// socket closure/rebinding. The engine fixture has no weights and cannot make
// inference claims; opt-in native engine acceptance covers model generation.
func TestModelIdleNativeCoordinator(t *testing.T) {
	testModelIdleNative(t, "", "")
}

func TestLiveModelIdleOllama(t *testing.T)  { testLiveModelIdle(t, "ollama-0.34.2-darwin") }
func TestLiveModelIdleLLMster(t *testing.T) { testLiveModelIdle(t, "llmster-0.0.25-1-darwin-arm64") }
func testLiveModelIdle(t *testing.T, recipe string) {
	cache := os.Getenv("FLEET_TEST_IDLE_CACHE")
	if cache == "" || runtime.GOOS != "darwin" || runtime.GOARCH != "arm64" {
		t.Skip("opt-in isolated Mac idle coordinator acceptance")
	}
	if os.Getenv("FLEET_TEST_MODEL_SOURCE") == "" || os.Getenv("FLEET_TEST_MODEL_BLOBS") == "" {
		t.Fatal("verified model cache is required")
	}
	testModelIdleNative(t, cache, recipe)
}

func testModelIdleNative(t *testing.T, cache, recipe string) {
	t.Helper()
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	if _, err := exec.LookPath(python); err != nil {
		t.Skip("Python required")
	}
	ownedRoot, err := os.MkdirTemp("", "fleet-idle-"+t.Name()+"-")
	if err != nil {
		t.Fatal(err)
	}
	m, err := Open(ownedRoot, "native-idle-owner", "native-idle-node", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, NativeDriver{})
	if err != nil {
		os.RemoveAll(ownedRoot)
		t.Fatal(err)
	}
	t.Cleanup(func() {
		m.Close()
		snapshot := m.Snapshot()
		clean := true
		for _, in := range snapshot.Instances {
			for _, r := range in.Resources {
				component := Component{Name: r.Component, StopSeconds: 10}
				if install := snapshot.Installations[in.Digest]; install != nil {
					for _, c := range install.Definition.Components {
						if c.Name == r.Component {
							component = c
						}
					}
				}
				if err := (NativeDriver{}).Stop(context.Background(), component, r); err != nil {
					clean = false
					t.Error(err)
				}
			}
		}
		if clean {
			if err := os.RemoveAll(ownedRoot); err != nil {
				t.Error(err)
			}
		} else {
			t.Logf("owned test state retained for process recovery: %s", ownedRoot)
		}
	})
	m.SetResourceSampler(resourceInventory)
	if cache != "" {
		m.SetResourceSampler(node.DetectResources)
	}
	root := filepath.Join("..", "..", "..", "apps", "model-service")
	b, err := os.ReadFile(filepath.Join(root, "fleet."+runtime.GOOS+"-"+runtime.GOARCH+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var connector Definition
	if json.Unmarshal(b, &connector) != nil {
		t.Fatal("invalid connector manifest")
	}
	files := map[string]string{}
	modules, err := filepath.Glob(filepath.Join(root, "*.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range append(modules, filepath.Join(root, "engines.json")) {
		b, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		files[filepath.Base(path)] = string(b)
	}
	start := func(def Definition, files map[string]string, scope string) ModelIdleBinding {
		t.Helper()
		payload, digest := bundle(t, def, files)
		if _, err := m.Stage(digest, 0, payload); err != nil {
			t.Fatal(err)
		}
		id := "start-" + scope
		if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: "start", Digest: digest, Scope: scope}); err != nil {
			t.Fatal(err)
		}
		deadline := time.Now().Add(40 * time.Second)
		for time.Now().Before(deadline) {
			op := m.Snapshot().Operations[id]
			if op.State == "succeeded" {
				break
			}
			if op.State != "queued" && op.State != "running" {
				t.Fatal(op.Error)
			}
			time.Sleep(50 * time.Millisecond)
		}
		if m.Snapshot().Operations[id].State != "succeeded" {
			t.Fatal("native startup timed out")
		}
		in := m.Snapshot().Instances[m.instanceID(digest, scope)]
		return ModelIdleBinding{in.ID, in.Digest, in.Generation}
	}
	c := start(connector, files, "model-native")
	managed := ModelIdleManaged{RecipeID: "ollama-0.34.2-darwin", ContextLength: 2048, Parallel: 1, LoadPolicy: "on_demand", Scope: "engine-native", MemoryBytes: 1 << 30}
	engine := Definition{Protocol: 1, AppID: "model-service", Version: "idle-fixture", Components: []Component{{Name: "backend", Runtime: "process", Argv: []string{python, "${PACKAGE}/engine.py"}, Ports: map[string]int{"http": 0}, StopSeconds: 3, Resources: &ResourceRequest{MemoryBytes: 1 << 30}, Readiness: Probe{Argv: []string{python, "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP']+'/api/ps',timeout=1).read()"}, TimeoutSeconds: 3}}}}
	engineFiles := map[string]string{"engine.py": `import json,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'models':[],'data':[]}).encode())
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
`}
	engineName := "ollama"
	if cache != "" {
		managed.RecipeID, managed.MemoryBytes = recipe, 1536<<20
		if strings.HasPrefix(recipe, "llmster-") {
			engineName = "lmstudio"
		}
		ownedCache := filepath.Join(m.root, "cache", "model-service")
		prepare := exec.Command(python, "-c", `import sys,threading,os,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from engines import EngineCache,recipe
from artifacts import ArtifactCache,file_lock,atomic_json
EngineCache(sys.argv[2],ArtifactCache(Path(sys.argv[3])/'blobs'),file_lock,atomic_json,'engine-native').fetch(recipe(sys.argv[4])['source'],threading.Event(),lambda *args:None)
artifact=json.loads(Path(os.environ['FLEET_TEST_MODEL_SOURCE']).read_text())
original=Path(os.environ['FLEET_TEST_MODEL_BLOBS'])/artifact['sha256']
ArtifactCache.verify(original,artifact['sha256'],artifact['size'],threading.Event())
target=Path(sys.argv[2])/'blobs'/artifact['sha256']
target.parent.mkdir(parents=True,exist_ok=True)
if not target.exists(): os.link(original,target)
`, root, ownedCache, cache, recipe)
		if output, err := prepare.CombinedOutput(); err != nil {
			t.Fatalf("isolated engine preparation: %v %s", err, output)
		}
		for _, name := range []string{"engines.py", "engines.json", "managed_engine.py", "llmster_runtime.py"} {
			engineFiles[name] = files[name]
		}
		engine.Components[0].Argv = []string{python, "${PACKAGE}/managed_engine.py", "start"}
		engine.Components[0].Readiness = Probe{Argv: []string{python, "${PACKAGE}/managed_engine.py", "ready"}, TimeoutSeconds: 30}
		engine.Components[0].StopSeconds = 10
		engine.Components[0].Resources = &ResourceRequest{MemoryBytes: managed.MemoryBytes, Devices: []DeviceBudget{{ID: "apple-metal", Backend: "metal", MemoryBytes: 1 << 30}}}
	}
	// The immutable engine file contains launch options, not connector binding.
	config, _ := json.Marshal(map[string]any{"recipe_id": managed.RecipeID, "context_length": managed.ContextLength, "parallel": managed.Parallel, "keep_alive_seconds": managed.KeepAliveSeconds, "load_policy": managed.LoadPolicy})
	engineFiles["engine-config.json"] = string(config)
	e := start(engine, engineFiles, "engine-native")
	endpoint, err := m.Service(e.InstanceID, e.Revision, e.Generation, "backend", "http")
	if err != nil {
		t.Fatal(err)
	}
	args := modelConfigArgs(ModelIdleConfig{Engine: engineName, Endpoint: endpoint + "/v1", Managed: managed})
	configured, err := m.modelRPC(context.Background(), c, "configure", args)
	if err != nil {
		t.Fatal(err)
	}
	verifyModel := func(phase string) {
		t.Helper()
		if cache == "" {
			return
		}
		url, err := m.Service(c.InstanceID, c.Revision, c.Generation, "backend", "http")
		if err != nil {
			t.Fatal(err)
		}
		token, err := m.RPCCredential(c.InstanceID, c.Revision, c.Generation)
		if err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
		defer cancel()
		command := exec.CommandContext(ctx, python, filepath.Join("..", "..", "scripts", "verify-node-idle-model.py"), url, phase)
		command.Env = append(os.Environ(), "FLEET_IDLE_TEST_RPC_TOKEN="+token)
		output, err := command.CombinedOutput()
		t.Logf("%s model check: %s", phase, output)
		if err != nil {
			t.Fatal("native model inference failed", err)
		}
	}
	verifyModel("before")
	p, err := m.RegisterModelIdle(context.Background(), ModelIdleRegistration{ID: "native", Connector: c, Engine: e, ConfigRevision: configured.ConfigRevision, IdleSeconds: 1})
	if err != nil {
		t.Fatal(err)
	}
	// Remove only the caller's grace interval; the real connector's idle clock
	// and the node observer still decide when fencing/stop can occur.
	idleTestExpire(m, p.ID)
	m.notifyModelIdle()
	deadline := time.Now().Add(9 * time.Second)
	for time.Now().Before(deadline) {
		p, err = m.ModelIdleStatus(p.ID)
		if err != nil {
			t.Fatal(err)
		}
		if p.State == "sleeping" {
			break
		}
		if p.State == "recovery_required" {
			t.Fatal(p)
		}
		time.Sleep(50 * time.Millisecond)
	}
	if p.State != "sleeping" {
		t.Fatal("unattended node did not stop engine", p)
	}
	client := http.Client{Timeout: 300 * time.Millisecond}
	if res, err := client.Get(endpoint + "/api/ps"); err == nil {
		res.Body.Close()
		t.Fatal("engine socket remained open after sleep")
	}
	if in := m.Snapshot().Instances[e.InstanceID]; len(in.Resources) != 0 || len(in.Reservations) != 0 {
		t.Fatal("resource release not confirmed", in)
	}
	if _, err = m.WakeModelIdle(p.ID, p.Revision); err != nil {
		t.Fatal(err)
	}
	// No driveModelIdle test helper here: only the live node observer performs
	// the persisted wake, verifies readiness and resumes the connector.
	deadline = time.Now().Add(40 * time.Second)
	for time.Now().Before(deadline) {
		p, err = m.ModelIdleStatus(p.ID)
		if err != nil {
			t.Fatal(err)
		}
		if p.State == "active" {
			break
		}
		if p.State == "recovery_required" {
			t.Fatal(p)
		}
		time.Sleep(25 * time.Millisecond)
	}
	if p.State != "active" || p.Engine.Generation != e.Generation+2 || p.Connector != c {
		t.Fatal("wake did not preserve exact ownership", p)
	}
	status, err := m.modelRPC(context.Background(), c, "status", nil)
	if err != nil || !status.Accepting || status.EngineIdle.Fenced || status.ConfigRevision != p.ConfigRevision {
		t.Fatal("connector did not acknowledge new binding", status, err)
	}
	newEndpoint, err := m.Service(e.InstanceID, e.Revision, p.Engine.Generation, "backend", "http")
	if err != nil {
		t.Fatal(err)
	}
	if newEndpoint+"/v1" != p.Configuration.Endpoint {
		t.Fatal("wake used an old port")
	}
	verifyModel("after")
	for _, binding := range []ModelIdleBinding{c, p.Engine} {
		in := m.Snapshot().Instances[binding.InstanceID]
		op := submit(t, m, binding.Revision, fmt.Sprintf("cleanup-%s", in.Scope), "stop", in.Scope, in.Generation)
		if op.State != "succeeded" {
			t.Fatal(op)
		}
	}
	if _, err = m.WakeModelIdle(p.ID, p.Revision); err == nil {
		t.Fatal("Stop allowed stale automatic wake")
	}
	t.Logf("autonomous idle stop and request wake passed; engine generations %d -> %d, connector generation %d retained", e.Generation, p.Engine.Generation, c.Generation)
}
