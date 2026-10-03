package shellapp

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/appsvc"
	"github.com/nats-io/nats.go"
)

// Exercise the declared generic session contract over the actual authenticated
// App service transport and real shell processes. This is owner RPC, not proof
// of the separate consumer grant or automatic platform lease coordinator.
func TestManagedSessionsOverAuthenticatedAppBus(t *testing.T) {
	bin, err := exec.LookPath("nats-server")
	if err != nil {
		t.Skip("nats-server required for live App bus fixture")
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	cmd := exec.Command(bin, "-a", "127.0.0.1", "-p", strconv.Itoa(port), "-js", "-sd", t.TempDir(), "--user", "owner", "--pass", "session-test-only")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	url := fmt.Sprintf("nats://127.0.0.1:%d", port)
	var nc *nats.Conn
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		nc, err = nats.Connect(url, nats.UserInfo("owner", "session-test-only"), nats.NoReconnect(), nats.Timeout(time.Second))
		if err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	if unexpected, err := nats.Connect(url, nats.NoReconnect(), nats.Timeout(time.Second)); err == nil {
		unexpected.Close()
		t.Fatal("unauthenticated bus connection accepted")
	}
	a := NewApp(t.TempDir())
	defer a.Close()
	tools, err := Tools(a)
	if err != nil {
		t.Fatal(err)
	}
	svc := appsvc.New(nc, "session-contract-fixture", "shell", "test", "test", "")
	for _, tool := range tools {
		svc.Register(tool)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := svc.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer svc.Stop(context.Background())
	if err := nc.FlushTimeout(time.Second); err != nil {
		t.Fatal(err)
	}
	call := func(method string, args map[string]any) map[string]any {
		t.Helper()
		body, _ := json.Marshal(map[string]any{"method": method, "parameters": args})
		message, err := nc.Request("pantheon.service.session-contract-fixture", body, 5*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		var result map[string]any
		if err := json.Unmarshal(message.Data, &result); err != nil || result["error"] != nil {
			t.Fatal(result, err)
		}
		return result["result"].(map[string]any)
	}
	acquire := func(owner, lease string) map[string]any {
		return call("resource_session_acquire", map[string]any{"owner_ref": owner, "lease_id": lease, "kind": "shell", "ttl_seconds": 300})
	}
	one, two := acquire("agent-one", strings.Repeat("a", 64)), acquire("app-resource-two", strings.Repeat("b", 64))
	if one["state"] != "active" || two["state"] != "active" || one["session_id"] == two["session_id"] {
		t.Fatal(one, two)
	}
	if retry := acquire("agent-one", strings.Repeat("a", 64)); retry["session_id"] != one["session_id"] {
		t.Fatal("wire retry recreated shell", retry)
	}
	call("run_command", map[string]any{"shell_id": one["session_id"], "command": "export WIRE_OWNER=one; cd /", "timeout": 3})
	output := call("run_command", map[string]any{"shell_id": two["session_id"], "command": "echo ${WIRE_OWNER:-isolated}", "timeout": 3})
	if !strings.Contains(output["output"].(string), "isolated") {
		t.Fatal(output)
	}
	ref := map[string]any{"owner_ref": one["owner_ref"], "lease_id": one["lease_id"], "ttl_seconds": 900}
	if renewed := call("resource_session_renew", ref); renewed["session_id"] != one["session_id"] || renewed["state"] != "active" {
		t.Fatal(renewed)
	}
	if released := call("resource_session_release", ref); released["state"] != "released" {
		t.Fatal(released)
	}
	if retry := acquire("agent-one", strings.Repeat("a", 64)); retry["state"] != "released" {
		t.Fatal("released request resurrected", retry)
	}
	if result := call("run_command", map[string]any{"shell_id": one["session_id"], "command": "echo forbidden"}); result["success"] != false {
		t.Fatal(result)
	}
	if result := call("run_command", map[string]any{"shell_id": two["session_id"], "command": "echo survives", "timeout": 3}); !strings.Contains(result["output"].(string), "survives") {
		t.Fatal("releasing one owner killed another", result)
	}
}
