package lifecycle

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// This builds the shipping entrypoint and its opt-in artifact, rather than a
// stand-in HTTP fixture. Fleet then installs, probes, invokes and stops it via
// the same lifecycle Manager used by native and job-scoped nodes.
func TestManagedShellNativeLifecycle(t *testing.T) {
	if testing.Short() {
		t.Skip("builds and runs native Shell App")
	}
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("managed Shell platform unavailable")
	}
	goBinary, err := exec.LookPath("go")
	if err != nil {
		t.Fatal(err)
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal(err)
	}
	_, source, _, _ := runtime.Caller(0)
	root := filepath.Clean(filepath.Join(filepath.Dir(source), "../../.."))
	packageDir := filepath.Join(t.TempDir(), "shell-package")
	buildCtx, buildCancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer buildCancel()
	build := exec.CommandContext(buildCtx, python, filepath.Join(root, "apps/shell/build_managed.py"),
		"--output", packageDir, "--os", runtime.GOOS, "--arch", runtime.GOARCH, "--go", goBinary)
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build Shell App: %v\n%s", err, out)
	}
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	entries, err := os.ReadDir(packageDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			t.Fatal(err)
		}
		body, err := os.ReadFile(filepath.Join(packageDir, entry.Name()))
		if err != nil {
			t.Fatal(err)
		}
		if err := tw.WriteHeader(&tar.Header{Name: entry.Name(), Mode: int64(info.Mode().Perm()), Size: int64(len(body))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(archive.Bytes())
	digest := hex.EncodeToString(sum[:])
	m, err := Open(t.TempDir(), "owner", "node", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	// Even a failed assertion cleans up only this test's owned processes.
	defer func() {
		for _, in := range m.Snapshot().Instances {
			for _, resource := range in.Resources {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				_ = (NativeDriver{}).Stop(ctx, Component{StopSeconds: 4}, resource)
				cancel()
			}
		}
	}()
	for offset := 0; offset < archive.Len(); {
		end := min(offset+MaxChunk, archive.Len())
		next, err := m.Stage(digest, int64(offset), archive.Bytes()[offset:end])
		if err != nil {
			t.Fatal(err)
		}
		offset = int(next)
	}
	operation := func(id, action, scope string, generation uint64) Operation {
		t.Helper()
		if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Scope: scope, Digest: digest, Generation: generation}); err != nil {
			t.Fatal(err)
		}
		deadline := time.Now().Add(20 * time.Second)
		for time.Now().Before(deadline) {
			op := m.Snapshot().Operations[id]
			if op != nil && op.State != "queued" && op.State != "running" {
				return *op
			}
			time.Sleep(10 * time.Millisecond)
		}
		t.Fatal("lifecycle operation timed out")
		return Operation{}
	}
	for _, scope := range []string{"first", "second"} {
		if op := operation("start-"+scope, "start", scope, 0); op.State != "succeeded" {
			t.Fatal(op)
		}
	}
	first := m.Snapshot().Instances[m.instanceID(digest, "first")]
	second := m.Snapshot().Instances[m.instanceID(digest, "second")]
	if first.Resources[0].Endpoints["http"] == second.Resources[0].Endpoints["http"] {
		t.Fatal("deployments share endpoint")
	}
	// No browser or anonymous local client can invoke or drain this provider.
	res, err := http.Get(first.Resources[0].Endpoints["http"] + "/health")
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 403 {
		t.Fatal("provider control endpoint is public")
	}
	call := func(in *Instance, method string, args map[string]any) map[string]any {
		t.Helper()
		payload, _ := json.Marshal(map[string]any{"method": method, "args": args, "timeout_s": 4})
		result, err := m.Invoke(context.Background(), "shell", in.ID, digest, in.Generation, payload, 5)
		if err != nil {
			t.Fatal(err)
		}
		raw := result.(map[string]any)["response"].(json.RawMessage)
		var response struct {
			Success bool           `json:"success"`
			Result  map[string]any `json:"result"`
		}
		if err := json.Unmarshal(raw, &response); err != nil || !response.Success {
			t.Fatalf("RPC failed: %s, %v", raw, err)
		}
		return response.Result
	}
	lease := strings.Repeat("c", 64)
	args := map[string]any{"owner_ref": "agent-one", "lease_id": lease, "kind": "shell", "ttl_seconds": 90}
	session := call(first, "resource_session_acquire", args)
	if again := call(first, "resource_session_acquire", args); again["session_id"] != session["session_id"] {
		t.Fatal("session acquisition replayed")
	}
	call(first, "resource_session_renew", map[string]any{"owner_ref": "agent-one", "lease_id": lease, "ttl_seconds": 120})
	output := call(first, "run_command_in_shell", map[string]any{"shell_id": session["session_id"], "command": "export APP_TEST_VALUE=first; printf 'saved' > retained.txt; printf '%s:%s\\n' \"${PANTHEON_APP_RPC_TOKEN-unset}\" \"${PANTHEON_APP_CONFIG-unset}\"", "timeout": 2})
	if output["success"] != true || output["output"] != "unset:unset\n" {
		t.Fatal("control credentials leaked or command failed", output)
	}
	other := call(second, "new_shell", nil)
	output = call(second, "run_command_in_shell", map[string]any{"shell_id": other["shell_id"], "command": "printf '%s\\n' \"${APP_TEST_VALUE-unset}\"", "timeout": 2})
	if output["output"] != "unset\n" {
		t.Fatal("state leaked across deployments", output)
	}
	// The HTTP observer ends while the command continues. Normal stop is
	// blocked, and the original generation remains callable to drain output.
	output = call(first, "run_command_in_shell", map[string]any{"shell_id": session["session_id"], "command": "sleep 2; echo finished", "timeout": 1})
	if output["status"] != "timeout" {
		t.Fatal(output)
	}
	if op := operation("blocked-stop", "stop", "first", first.Generation); op.State != "failed" {
		t.Fatal("stop discarded pending command", op)
	}
	newWork, _ := json.Marshal(map[string]any{"method": "run_command_in_shell", "args": map[string]any{
		"shell_id": session["session_id"], "command": "echo unexpected > late-command.txt", "timeout": 1}})
	rejected, err := m.Invoke(context.Background(), "shell", first.ID, digest, first.Generation, newWork, 2)
	if err != nil {
		t.Fatal("original generation became unreachable during drain", err)
	}
	var denial struct {
		Success bool   `json:"success"`
		Error   string `json:"error"`
	}
	if err := json.Unmarshal(rejected.(map[string]any)["response"].(json.RawMessage), &denial); err != nil || denial.Success || !strings.Contains(denial.Error, "stopping") {
		t.Fatal("new command admitted while stopping", denial, err)
	}
	output = call(first, "get_shell_output", map[string]any{"shell_id": session["session_id"], "timeout": 1})
	if !strings.Contains(output["output"].(string), "finished") {
		t.Fatal("lost pending output", output)
	}
	if receipt := call(first, "resource_session_release", map[string]any{"owner_ref": "agent-one", "lease_id": lease}); receipt["state"] != "released" {
		t.Fatal(receipt)
	}
	if receipt := call(first, "resource_session_get", map[string]any{"owner_ref": "agent-one", "lease_id": lease}); receipt["state"] != "released" {
		t.Fatal("released session changed state", receipt)
	}
	if op := operation("stop-first", "stop", "first", first.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if _, err := m.Invoke(context.Background(), "shell", first.ID, digest, first.Generation, json.RawMessage(`{"method":"_ping"}`), 2); err == nil {
		t.Fatal("stopped generation accepted call")
	}
	output = call(second, "run_command_in_shell", map[string]any{"shell_id": other["shell_id"], "command": "echo still-alive", "timeout": 2})
	if !strings.Contains(output["output"].(string), "still-alive") {
		t.Fatal("stopping sibling lost session", output)
	}
	if op := operation("stop-second", "stop", "second", second.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	// Stopping retains the ordinary instance data directory.
	saved, err := os.ReadFile(filepath.Join(m.paths(digest, "first").Data, "workspace/retained.txt"))
	if err != nil || string(saved) != "saved" {
		t.Fatal("instance data lost", err)
	}
	if _, err := os.Stat(filepath.Join(m.paths(digest, "first").Data, "workspace/late-command.txt")); !os.IsNotExist(err) {
		t.Fatal("rejected command executed", err)
	}
	// The artifact carries matching manifests and needs no interpreter at runtime.
	manifest, err := m.InstalledManifest(digest)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(manifest)
	if !bytes.Contains(encoded, []byte("resource-session@1")) {
		t.Fatal("missing resource-session interface")
	}
	if !bytes.Contains(encoded, []byte(`"execution":`)) {
		t.Fatal("manifest does not declare managed execution")
	}
}
