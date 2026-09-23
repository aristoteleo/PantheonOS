package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Opt-in Python/Go boundary acceptance; creates only isolated local CPU fixtures.
// No Fleet enrollment, installed node, model download or external service.
func TestPythonGroupCoordinatorNativeProcesses(t *testing.T) {
	python := os.Getenv("PANTHEON_TEST_PYTHON")
	if python == "" {
		t.Skip("set PANTHEON_TEST_PYTHON for cross-language group acceptance")
	}
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("POSIX subprocess fixture")
	}
	for _, scenario := range []string{"lost-reply", "failed-start", "failed-admission",
		"lost-before-prepare", "lost-before-start", "lost-before-stop"} {
		t.Run(scenario, func(t *testing.T) {
			managers := map[string]*Manager{}
			var targets []map[string]any
			for _, node := range []string{"node-a", "node-b"} {
				m, err := Open(t.TempDir(), "test-owner", node, proto.Capability{}, NativeDriver{})
				if err != nil {
					t.Fatal(err)
				}
				managers[node] = m
				m.SetResourceSampler(resourceInventory)
				probe := "exit 0"
				budget := uint64(4 << 30)
				if node == "node-b" && scenario == "failed-start" {
					probe = "exit 1"
				}
				if node == "node-b" && scenario == "failed-admission" {
					budget = 16 << 30
				}
				def := Definition{Protocol: 1, AppID: "group-fixture", Version: "1", Components: []Component{{
					Name: "backend", Runtime: "process", Argv: []string{"sh", "-c", "exec sleep 60"},
					Resources: &ResourceRequest{MemoryBytes: budget},
					Readiness: Probe{Argv: []string{"sh", "-c", probe}, TimeoutSeconds: 2}, StopSeconds: 2,
				}}}
				t.Cleanup(func() {
					for _, in := range m.Snapshot().Instances {
						for _, r := range in.Resources {
							_ = m.driver.Stop(context.Background(), def.Components[0], r)
						}
					}
					_ = m.Close()
				})
				b, digest := bundle(t, def, nil)
				if _, err := m.Stage(digest, 0, b); err != nil {
					t.Fatal(err)
				}
				if op := submit(t, m, digest, "install", "install", "engine-group", 0); op.State != "succeeded" {
					t.Fatal(op)
				}
				targets = append(targets, map[string]any{"node_id": node, "digest": digest, "scope": "engine-group", "generation": 0})
			}
			var mu sync.Mutex
			seen := map[string]Resource{}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				m := managers[strings.TrimPrefix(r.URL.Path, "/")]
				if m == nil {
					http.NotFound(w, r)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				if r.Method == "GET" {
					state := m.Snapshot()
					mu.Lock()
					for _, in := range state.Instances {
						for _, resource := range in.Resources {
							// Startup briefly publishes an ID-only placeholder before
							// NativeDriver returns its actual PID/birth identity. It
							// is not an OS process handle and cannot be passed to Alive.
							if resource.PID > 0 {
								seen[fmt.Sprint(resource.PID)] = resource
							}
						}
					}
					mu.Unlock()
					_ = json.NewEncoder(w).Encode(state)
					return
				}
				var request Request
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					http.Error(w, err.Error(), 400)
					return
				}
				var op Operation
				var err error
				if r.Method == "PUT" {
					op, err = m.FenceStart(request)
				} else {
					op, err = m.Submit(request)
				}
				if err != nil {
					http.Error(w, err.Error(), 409)
					return
				}
				_ = json.NewEncoder(w).Encode(op)
			}))
			defer server.Close()
			payload, _ := json.Marshal(targets)
			root, _ := filepath.Abs("../../..")
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, python, filepath.Join(root, "tests", "harness_model_groups.py"),
				server.URL, string(payload), filepath.Join(t.TempDir(), "groups.db"), scenario)
			cmd.Dir = root
			cmd.Env = append(os.Environ(), "PYTHONPATH="+root)
			out, err := cmd.CombinedOutput()
			if err != nil {
				t.Fatalf("coordinator failed: %v\n%s", err, out)
			}
			t.Log(string(out))
			for _, m := range managers {
				for _, in := range m.Snapshot().Instances {
					if in.State != "stopped" || len(in.Resources) != 0 || len(in.Reservations) != 0 {
						t.Fatal("group left owned resources", in)
					}
				}
			}
			mu.Lock()
			defer mu.Unlock()
			if (scenario == "lost-reply" || scenario == "lost-before-stop") && len(seen) != 2 {
				t.Fatal("expected two actual native processes", seen)
			}
			if (scenario == "lost-before-prepare" || scenario == "lost-before-start") && len(seen) != 0 {
				t.Fatal("fenced startup created an actual process", seen)
			}
			for _, resource := range seen {
				if alive, err := (NativeDriver{}).Alive(context.Background(), resource); err != nil || alive {
					t.Fatal("native process survived group stop", resource, err)
				}
			}
		})
	}
}
