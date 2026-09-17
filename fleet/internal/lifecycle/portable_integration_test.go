package lifecycle

// Optional integration against a real App artifact, isolated from any running
// Fleet. Build the artifact with pantheon.apps.portable.execution_package and
// set FLEET_TEST_PORTABLE_ARTIFACT to its tar path.
import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestPortableSpatialLifecycle(t *testing.T) {
	artifact := os.Getenv("FLEET_TEST_PORTABLE_ARTIFACT")
	if artifact == "" {
		t.Skip("set FLEET_TEST_PORTABLE_ARTIFACT to run native Spatial 3D integration")
	}
	payload, err := os.ReadFile(artifact)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	digest := hex.EncodeToString(sum[:])
	m, err := Open(t.TempDir(), "integration", "isolated-mac", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	for offset := 0; offset < len(payload); {
		end := min(offset+MaxChunk, len(payload))
		if _, err = m.Stage(digest, int64(offset), payload[offset:end]); err != nil {
			t.Fatal(err)
		}
		offset = end
	}
	run := func(action string, generation uint64) {
		id := action + time.Now().Format("150405000000000")
		if _, err = m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "app", Generation: generation}); err != nil {
			t.Fatal(err)
		}
		until := time.Now().Add(10 * time.Minute)
		for time.Now().Before(until) {
			op := m.Snapshot().Operations[id]
			if op.State == "succeeded" {
				return
			}
			if op.State != "queued" && op.State != "running" {
				t.Fatalf("%s: %s", action, op.Error)
			}
			time.Sleep(100 * time.Millisecond)
		}
		t.Fatalf("%s timeout", action)
	}
	run("install", 0)
	run("start", 0)
	id := m.instanceID(digest, "app")
	in := m.Snapshot().Instances[id]
	defer func() {
		if current := m.Snapshot().Instances[id]; current != nil && current.State == "ready" {
			run("stop", current.Generation)
		}
	}()
	endpoint, err := m.Service(id, digest, in.Generation, "backend", "http")
	if err != nil {
		t.Fatal(err)
	}
	client := http.Client{Timeout: 90 * time.Second}
	response, err := client.Post(endpoint+"/rpc", "application/json", bytes.NewBufferString(`{"method":"load_dataset","args":{"id":"synthetic"},"timeout_s":80}`))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	response.Body.Close()
	var answer struct {
		Success bool   `json:"success"`
		Error   string `json:"error"`
		Result  struct {
			Config struct {
				URL   string `json:"url"`
				Title string `json:"title"`
			} `json:"config"`
		} `json:"result"`
	}
	if json.Unmarshal(body, &answer) != nil || !answer.Success || !strings.Contains(answer.Result.Config.Title, "2,400") {
		t.Fatalf("RPC failed: %s", body)
	}
	response, err = client.Get(endpoint + answer.Result.Config.URL + "/_spatial.json")
	if err != nil {
		t.Fatal(err)
	}
	body, _ = io.ReadAll(response.Body)
	response.Body.Close()
	if response.StatusCode != 200 || !json.Valid(body) {
		t.Fatalf("Zarr metadata unavailable: %s", body)
	}
	t.Logf("Spatial 3D generated 2,400 cells on %s/%s; PID %d; metadata %s", runtime.GOOS, runtime.GOARCH, in.Resources[0].PID, body)
	marker := filepath.Join(m.paths(digest, "app").Data, "retained.txt")
	if err = os.WriteFile(marker, []byte("preserved"), 0600); err != nil {
		t.Fatal(err)
	}
	run("stop", in.Generation)
	if _, err = m.Service(id, digest, in.Generation, "backend", "http"); err == nil {
		t.Fatal("stopped service still routable")
	}
	run("uninstall", m.Snapshot().Instances[id].Generation)
	if b, err := os.ReadFile(marker); err != nil || string(b) != "preserved" {
		t.Fatal("App data was not retained", err)
	}
}
