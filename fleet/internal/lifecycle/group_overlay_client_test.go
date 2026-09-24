package lifecycle

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Opt-in cross-language production client/store test. No kernel interfaces,
// containers, model downloads, GPU allocations or enrolled Fleet nodes.
func TestPythonGroupOverlayClient(t *testing.T) {
	python := os.Getenv("PANTHEON_TEST_PYTHON")
	if python == "" {
		t.Skip("set PANTHEON_TEST_PYTHON for Python/Go overlay boundary")
	}
	managers := map[string]*Manager{}
	for _, node := range []string{"node-0", "node-1"} {
		m, err := Open(t.TempDir(), "f_"+strings.Repeat("a", 16), node, proto.Capability{}, NativeDriver{})
		if err != nil {
			t.Fatal(err)
		}
		managers[node] = m
		t.Cleanup(func() { _ = m.Close() })
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		m := managers[strings.TrimPrefix(r.URL.Path, "/")]
		if m == nil {
			http.NotFound(w, r)
			return
		}
		var q struct {
			Method  string         `json:"method"`
			Overlay OverlayRequest `json:"group_overlay"`
		}
		d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32768))
		d.DisallowUnknownFields()
		if err := d.Decode(&q); err != nil {
			t.Error(err)
			http.Error(w, "invalid request", 400)
			return
		}
		result, err := m.GroupOverlay(strings.TrimPrefix(q.Method, "group_overlay_"), q.Overlay)
		w.Header().Set("Content-Type", "application/json")
		if err != nil {
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = json.NewEncoder(w).Encode(result)
	}))
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, filepath.Join(root, "tests", "harness_group_overlay.py"), server.URL)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "PYTHONPATH="+root)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("Python overlay boundary: %v\n%s", err, out)
	}
	t.Log(string(out))
}
