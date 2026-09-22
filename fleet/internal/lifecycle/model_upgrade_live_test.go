package lifecycle

// The coordinator runs in Python; every lifecycle/RPC call below uses a real
// isolated Fleet supervisor and native process. No existing node is contacted.
import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func TestLiveManagedEngineUpgrade(t *testing.T) {
	cache := os.Getenv("FLEET_TEST_UPGRADE_CACHE")
	if cache == "" || runtime.GOOS != "darwin" || runtime.GOARCH != "arm64" {
		t.Skip("opt-in isolated Mac engine version upgrade acceptance")
	}
	inv := node.DetectResources()
	m, err := Open(t.TempDir(), "upgrade-test", "isolated-mac", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}, Resources: &inv}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	m.SetResourceSampler(node.DetectResources)
	defer m.Close()
	defer func() {
		for _, in := range m.Snapshot().Instances {
			for _, resource := range in.Resources {
				if err := (NativeDriver{}).Stop(context.Background(), Component{Name: resource.Component, StopSeconds: 10}, resource); err != nil {
					t.Error(err)
				}
			}
		}
	}()
	client := &http.Client{Timeout: 30 * time.Second}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			Action  string  `json:"action"`
			Digest  string  `json:"digest"`
			Data    []byte  `json:"data"`
			Request Request `json:"request"`
			Binding struct {
				InstanceID string `json:"instance_id"`
				Revision   string `json:"revision"`
				Generation uint64 `json:"generation"`
			} `json:"binding"`
			Method string         `json:"method"`
			Args   map[string]any `json:"args"`
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 48<<20)).Decode(&request); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		var result any
		var err error
		b := request.Binding
		switch request.Action {
		case "status":
			result = m.Snapshot()
		case "stage":
			result, err = m.Stage(request.Digest, 0, request.Data)
		case "submit":
			result, err = m.Submit(request.Request)
		case "usage":
			err = m.SetKeepAlive(b.InstanceID, b.Revision, b.Generation, true)
		case "rpc":
			var endpoint, token string
			endpoint, err = m.Service(b.InstanceID, b.Revision, b.Generation, "backend", "http")
			if err == nil {
				token, err = m.RPCCredential(b.InstanceID, b.Revision, b.Generation)
			}
			if err == nil {
				data, _ := json.Marshal(map[string]any{"method": request.Method, "args": request.Args})
				req, _ := http.NewRequest("POST", endpoint+"/rpc", bytes.NewReader(data))
				req.Header.Set("X-Fleet-RPC-Token", token)
				req.Header.Set("Content-Type", "application/json")
				var res *http.Response
				res, err = client.Do(req)
				if err == nil {
					defer res.Body.Close()
					if res.StatusCode != 200 {
						body, _ := io.ReadAll(io.LimitReader(res.Body, 4096))
						err = fmt.Errorf("RPC %s HTTP %d: %s", request.Method, res.StatusCode, body)
					} else {
						err = json.NewDecoder(res.Body).Decode(&result)
					}
				}
			}
		default:
			err = fmt.Errorf("unknown test action")
		}
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"result": result})
	}))
	defer server.Close()
	repo, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	python := os.Getenv("FLEET_TEST_PYTHON")
	if python == "" {
		python = "python3"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	command := exec.CommandContext(ctx, python, filepath.Join(repo, "fleet", "scripts", "verify-engine-upgrade.py"), server.URL, m.root, cache)
	command.Dir = repo
	command.Env = append(os.Environ(), "PYTHONPATH="+repo)
	output, err := command.CombinedOutput()
	t.Log(string(output))
	if err != nil {
		t.Fatal("engine upgrade acceptance:", err)
	}
	for _, in := range m.Snapshot().Instances {
		if in.State != "stopped" || len(in.Resources) != 0 || len(in.Reservations) != 0 {
			t.Fatal("owned instance was not cleaned up", in.ID, in.State)
		}
	}
}
