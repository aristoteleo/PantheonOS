package lifecycle

// Opt-in real inference on an isolated NVIDIA test node. The test image owns
// its engine installation and pinned weights; Fleet owns both child processes.
import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func TestLiveSGLangManagedInference(t *testing.T) {
	if os.Getenv("FLEET_TEST_SGLANG") != "1" {
		t.Skip("isolated GPU acceptance only")
	}
	inv := node.DetectResources()
	if len(inv.Accelerators) != 1 || inv.Accelerators[0].Backend != "cuda" {
		t.Fatal("expected one NVIDIA test GPU", inv)
	}
	if inv.Accelerators[0].Memory.AvailableBytes == nil {
		t.Fatal("initial GPU memory unavailable")
	}
	// total-free includes driver-reserved memory. Compare with this node's
	// measured baseline, and separately require no new compute clients remain.
	baseline := inv.Accelerators[0].Memory.TotalBytes - *inv.Accelerators[0].Memory.AvailableBytes
	computeClients := func() string {
		t.Helper()
		out, err := exec.Command("nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory", "--format=csv,noheader,nounits").CombinedOutput()
		if err != nil {
			t.Fatalf("GPU process inventory: %v: %s", err, out)
		}
		return strings.TrimSpace(string(out))
	}
	initialClients := computeClients()
	t.Logf("GPU baseline: %.1f MiB; compute clients: %q", float64(baseline)/(1<<20), initialClients)
	var source struct {
		SHA256 string `json:"sha256"`
	}
	sourceBytes, err := os.ReadFile("/opt/model-snapshot.json")
	if err != nil || json.Unmarshal(sourceBytes, &source) != nil || !digestRE.MatchString(source.SHA256) {
		t.Fatal("verified snapshot missing", err)
	}
	modelID := "fleet-snapshot-" + source.SHA256
	config := map[string]any{"recipe_id": "sglang-0.5.20-linux-amd64", "model_artifact_sha256": source.SHA256,
		"context_length": 4096, "parallel": 2, "keep_alive_seconds": 0,
		"resources": map[string]any{"memory_bytes": 10 << 30, "devices": []DeviceBudget{{ID: inv.Accelerators[0].ID, Backend: "cuda", MemoryBytes: 16 << 30, Exclusive: true}}}}
	configBytes, _ := json.Marshal(config)
	def := Definition{Protocol: 1, AppID: "model-service", Version: "0.1.0", Requires: Requirements{OS: []string{"linux"}, Arch: []string{"amd64"}, Caps: []string{"proc"}}, Components: []Component{
		{Name: "engine", Runtime: "process", Ports: map[string]int{"http": 0}, StopSeconds: 20,
			Argv:      []string{"python3", "${PACKAGE}/sglang_runtime.py", "start"},
			Env:       map[string]string{"HOME": "${DATA}", "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "SGLANG_DISABLE_UPDATE_CHECK": "1"},
			Readiness: Probe{Argv: []string{"python3", "${PACKAGE}/sglang_runtime.py", "ready"}, TimeoutSeconds: 360},
			Resources: &ResourceRequest{MemoryBytes: 10 << 30, Devices: []DeviceBudget{{ID: inv.Accelerators[0].ID, Backend: "cuda", MemoryBytes: 16 << 30, Exclusive: true}}}},
		{Name: "backend", Runtime: "process", DependsOn: []string{"engine"}, Ports: map[string]int{"http": 0}, StopSeconds: 10,
			Argv:      []string{"python3", "${PACKAGE}/server.py", "start", "--data", "${DATA}"},
			Readiness: Probe{Argv: []string{"python3", "${PACKAGE}/server.py", "ready", "--data", "${DATA}"}, TimeoutSeconds: 15}},
	}, Hooks: map[string]Hook{"before_stop": {Argv: []string{"python3", "${PACKAGE}/server.py", "drain", "--data", "${DATA}"}, TimeoutSeconds: 10}}}
	files := map[string]string{"engine-config.json": string(configBytes)}
	connectorFiles, err := os.ReadDir("/opt/connector")
	if err != nil {
		t.Fatal(err)
	}
	// Keep the live fixture's dynamic Python modules aligned with the shipped
	// connector, including admission/idle/preload helpers added after this test.
	for _, entry := range connectorFiles {
		name := entry.Name()
		if entry.IsDir() || (!strings.HasSuffix(name, ".py") && name != "engines.json") {
			continue
		}
		b, err := os.ReadFile(filepath.Join("/opt/connector", name))
		if err != nil {
			t.Fatal(err)
		}
		files[name] = string(b)
	}
	payload, digest := bundle(t, def, files)
	m, err := Open(t.TempDir(), "gpu-acceptance", "isolated-modal-gpu", proto.Capability{OS: "linux", Arch: "amd64", Caps: []string{"proc"}, Resources: &inv}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	m.SetResourceSampler(node.DetectResources)
	cacheDir := filepath.Join(m.root, "cache", "model-service", "snapshots", source.SHA256)
	if err := os.MkdirAll(cacheDir, 0700); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir("/fleet/weights")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if err := os.Link(filepath.Join("/fleet/weights", entry.Name()), filepath.Join(cacheDir, entry.Name())); err != nil {
			t.Fatal(err)
		}
	}
	t.Cleanup(func() { m.Close() })
	if _, err = stageModelArtifact(m, digest, payload); err != nil {
		t.Fatal(err)
	}
	scope, id := "model-gpu", m.instanceID(digest, "model-gpu")
	run := func(action string) {
		t.Helper()
		generation := uint64(0)
		if in := m.Snapshot().Instances[id]; in != nil {
			generation = in.Generation
		}
		req := Request{Protocol: 1, OperationID: fmt.Sprintf("%s-%d", action, time.Now().UnixNano()), Action: action, Digest: digest, Scope: scope, Generation: generation}
		if _, err := m.Submit(req); err != nil {
			t.Fatal(err)
		}
		until := time.Now().Add(390 * time.Second)
		for time.Now().Before(until) {
			op := m.Snapshot().Operations[req.OperationID]
			if op.State == "succeeded" {
				return
			}
			if op.State != "queued" && op.State != "running" {
				filepath.WalkDir(m.root, func(p string, d os.DirEntry, e error) error {
					if e == nil && !d.IsDir() && strings.HasSuffix(p, ".log") {
						b, _ := os.ReadFile(p)
						if len(b) > 16000 {
							b = b[len(b)-16000:]
						}
						t.Logf("%s: %s", filepath.Base(p), b)
					}
					return nil
				})
				t.Fatalf("%s: %s", action, op.Error)
			}
			time.Sleep(250 * time.Millisecond)
		}
		t.Fatal("lifecycle deadline exceeded")
	}
	start := time.Now()
	run("install")
	run("start")
	t.Logf("Fleet install + GPU engine + connector ready: %s", time.Since(start))
	t.Cleanup(func() {
		if in := m.Snapshot().Instances[id]; in != nil && in.State == "ready" {
			run("stop")
		}
	})
	in := m.Snapshot().Instances[id]
	if len(in.Reservations) != 1 || len(in.Resources) != 2 {
		t.Fatal("engine ownership/budget not recorded", in)
	}
	backend, err := m.Service(id, digest, in.Generation, "backend", "http")
	if err != nil {
		t.Fatal(err)
	}
	engine, err := m.Service(id, digest, in.Generation, "engine", "http")
	if err != nil {
		t.Fatal(err)
	}
	token, err := m.RPCCredential(id, digest, in.Generation)
	if err != nil {
		t.Fatal(err)
	}
	client := http.Client{Timeout: 90 * time.Second}
	post := func(path string, value any, headers map[string]string) *http.Response {
		t.Helper()
		data, _ := json.Marshal(value)
		req, _ := http.NewRequest("POST", backend+path, bytes.NewReader(data))
		req.Header.Set("Content-Type", "application/json")
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		res, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		if res.StatusCode != 200 {
			body, _ := io.ReadAll(res.Body)
			res.Body.Close()
			t.Fatalf("HTTP %d: %s", res.StatusCode, body)
		}
		return res
	}
	res := post("/rpc", map[string]any{"method": "configure", "args": map[string]any{"config": map[string]string{"engine": "sglang", "endpoint": engine + "/v1"},
		"managed": map[string]any{"scope": "engine-gpu", "recipe_id": config["recipe_id"], "context_length": 4096, "parallel": 2,
			"keep_alive_seconds": 0, "memory_bytes": 10 << 30, "model_artifact_sha256": source.SHA256}}}, map[string]string{"X-Fleet-RPC-Token": token})
	var configuration map[string]string
	json.NewDecoder(res.Body).Decode(&configuration)
	res.Body.Close()
	for attempt := 0; attempt < 3; attempt++ {
		started := time.Now()
		res = post("/v1/chat/completions", map[string]any{"model": modelID, "stream": true, "max_tokens": 64, "temperature": 0,
			"messages": []map[string]string{{"role": "user", "content": "What is 2 + 2? Answer briefly."}}}, map[string]string{"X-Model-Request": fmt.Sprintf("gpu-smoke-%d", attempt), "X-Model-Config": configuration["config_revision"]})
		scanner := bufio.NewScanner(res.Body)
		var content string
		var first time.Duration
		done := false
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
			if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk); err != nil {
				t.Fatal(err)
			}
			for _, c := range chunk.Choices {
				content += c.Delta.Content
				if c.Delta.Content != "" && first == 0 {
					first = time.Since(started)
				}
			}
		}
		res.Body.Close()
		if !done || !strings.Contains(content, "4") {
			t.Fatalf("invalid model response: %q", content)
		}
		t.Logf("inference %d first-content=%s total=%s content=%q", attempt, first, time.Since(started), content)
	}
	loaded := node.DetectResources()
	if len(loaded.Accelerators) != 1 || loaded.Accelerators[0].Memory.AvailableBytes == nil {
		t.Fatal("GPU usage unavailable")
	}
	used := loaded.Accelerators[0].Memory.TotalBytes - *loaded.Accelerators[0].Memory.AvailableBytes
	if used < 1<<30 {
		t.Fatal("no GPU model allocation observed", used)
	}
	t.Logf("loaded GPU memory: %.2f GiB; declared reservation: 16 GiB", float64(used)/(1<<30))
	res = post("/v1/chat/completions", map[string]any{"model": modelID, "stream": true, "max_tokens": 1024, "temperature": 0,
		"messages": []map[string]string{{"role": "user", "content": "Count from 1 to 1000, one number on each line. Do not stop early."}}}, map[string]string{"X-Model-Request": "cancel-gpu-smoke", "X-Model-Config": configuration["config_revision"]})
	reader := bufio.NewReader(res.Body)
	if _, err := reader.ReadString('\n'); err != nil {
		t.Fatal(err)
	}
	cancelStarted := time.Now()
	cancelResponse := post("/cancel", map[string]string{"request_id": "cancel-gpu-smoke"}, nil)
	var cancelled struct{ Cancelled bool }
	json.NewDecoder(cancelResponse.Body).Decode(&cancelled)
	cancelResponse.Body.Close()
	if !cancelled.Cancelled {
		t.Fatal("no active GPU inference to cancel")
	}
	io.Copy(io.Discard, res.Body)
	res.Body.Close()
	var active float64 = 1
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		status := post("/rpc", map[string]any{"method": "status", "args": map[string]any{}}, map[string]string{"X-Fleet-RPC-Token": token})
		var body map[string]any
		json.NewDecoder(status.Body).Decode(&body)
		status.Body.Close()
		active, _ = body["active_calls"].(float64)
		if active == 0 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if active != 0 {
		t.Fatal("cancelled request still occupies connector slot")
	}
	t.Logf("cancel to connector release: %s", time.Since(cancelStarted))
	run("stop")
	if len(m.Snapshot().Instances[id].Reservations) != 0 {
		t.Fatal("GPU lease retained after successful stop")
	}
	if res, err := client.Get(engine + "/health"); err == nil {
		res.Body.Close()
		t.Fatal("owned engine still serves after stop")
	}
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
		stopped := node.DetectResources()
		if len(stopped.Accelerators) == 1 && stopped.Accelerators[0].Memory.AvailableBytes != nil {
			used = stopped.Accelerators[0].Memory.TotalBytes - *stopped.Accelerators[0].Memory.AvailableBytes
			if used <= baseline+(32<<20) && computeClients() == initialClients {
				break
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	if used > baseline+(32<<20) || computeClients() != initialClients {
		t.Fatalf("GPU allocation survived process shutdown: %d bytes (baseline %d); compute clients: %q", used, baseline, computeClients())
	}
	t.Logf("GPU memory after stop: %.1f MiB", float64(used)/(1<<20))
	t.Log("owned engine stopped; GPU reservation released")
}
