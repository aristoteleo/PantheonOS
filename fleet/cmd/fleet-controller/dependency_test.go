package main

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// Real Controller callbacks, authenticated NATS for two independently-owned
// node Managers, and native Python App processes. No provider token is returned
// to the consumer, and the provider rejects calls without its private RPC key.
func TestDependencyRPCOverAuthenticatedNATSAndNativeApps(t *testing.T) {
	binary, err := exec.LookPath("nats-server")
	if err != nil {
		t.Skip("nats-server required")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 required")
	}
	root := t.TempDir()
	native := newAgentDeploymentFixture(t, root)
	authority, err := auth.Bootstrap(filepath.Join(root, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	listener.Close()
	config := filepath.Join(root, "nats.conf")
	if err := os.WriteFile(config, []byte(authority.ServerConfig(address, filepath.Join(root, "js"))), 0600); err != nil {
		t.Fatal(err)
	}
	process := exec.Command(binary, "-c", config)
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if process != nil {
			process.Process.Kill()
			process.Wait()
		}
	}()
	const owner = "f_0123456789abcdef"
	var nodeConnections []*nats.Conn
	connect := func(node string) *nats.Conn {
		t.Helper()
		creds, err := authority.MintFleetNode(owner, node)
		if err != nil {
			t.Fatal(err)
		}
		var nc *nats.Conn
		for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
			nc, err = nats.Connect("nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix("_INBOX_"+owner))
			if err == nil {
				t.Cleanup(nc.Close)
				nodeConnections = append(nodeConnections, nc)
				return nc
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatal(err)
		return nil
	}
	definition := lifecycle.Definition{Protocol: 1, AppID: "rpc-example", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend", Runtime: "process", Argv: []string{"python3", "${PACKAGE}/server.py"}, Ports: map[string]int{"http": 0}, StopSeconds: 1, Readiness: lifecycle.Probe{Argv: []string{"python3", "-c", `import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'],timeout=1)`}, TimeoutSeconds: 10}}}}
	manifest, _ := json.Marshal(definition)
	source := `
import json, os, hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'ready')
 def do_POST(self):
  if not hmac.compare_digest(self.headers.get('X-Fleet-RPC-Token',''),os.environ['PANTHEON_APP_RPC_TOKEN']):
   self.send_response(403);self.end_headers();return
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  reply=json.dumps({'success':True,'result':body['args'],'pid':os.getpid()}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(reply)));self.end_headers();self.wfile.write(reply)
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
`
	var archive bytes.Buffer
	tarWriter := tar.NewWriter(&archive)
	for name, data := range map[string][]byte{"fleet.json": manifest, "server.py": []byte(source), "app.json": []byte(`{"apiVersion":2,"id":"rpc-example","version":"1.0.0","provides":{"interfaces":[{"name":"echo","version":1,"tools":["echo"]}],"tools":[{"name":"echo","params":[{"name":"value"},{"name":"workspace_id"}]}]}}`)} {
		if err := tarWriter.WriteHeader(&tar.Header{Name: name, Mode: 0400, Size: int64(len(data))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tarWriter.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := tarWriter.Close(); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(archive.Bytes())
	digest := hex.EncodeToString(sum[:])
	run := func(m *lifecycle.Manager, id, action string, generation uint64) {
		t.Helper()
		if _, err := m.Submit(lifecycle.Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "app", Generation: generation}); err != nil {
			t.Fatal(err)
		}
		for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline); {
			op := m.Snapshot().Operations[id]
			if op.State == "succeeded" {
				return
			}
			if op.State != "running" && op.State != "queued" {
				t.Fatal(op.Error)
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatal("lifecycle timed out")
	}
	newNode := func(node string) (*lifecycle.Manager, *lifecycle.Instance) {
		t.Helper()
		nc := connect(node)
		// Match a normal Fleet state root so the real CLI and supervisor share
		// the same owner vault during migration acceptance.
		stateRoot := filepath.Join(root, node)
		if err := os.MkdirAll(stateRoot, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(stateRoot, "node_id"), []byte(node+"\n"), 0600); err != nil {
			t.Fatal(err)
		}
		m, err := lifecycle.Open(filepath.Join(stateRoot, "apps", owner), owner, node, proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, lifecycle.NativeDriver{Environment: native.environment()})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			for _, in := range m.Snapshot().Instances {
				for _, res := range in.Resources {
					_ = (lifecycle.NativeDriver{}).Stop(context.Background(), lifecycle.Component{StopSeconds: 1}, res)
				}
			}
			m.Close()
		})
		if _, err := m.Stage(digest, 0, archive.Bytes()); err != nil {
			t.Fatal(err)
		}
		run(m, "start", "start", 0)
		// Match Runner.handleLifecycle: invocations cannot monopolize the NATS
		// callback while the App performs a dependency RPC back to this node.
		slots := make(chan struct{}, 16)
		var calls sync.WaitGroup
		var admission sync.Mutex
		closing := false
		sub, err := nc.Subscribe(proto.SubjNodeCmd(owner, node), func(message *nats.Msg) {
			admission.Lock()
			if closing {
				admission.Unlock()
				return
			}
			calls.Add(1)
			admission.Unlock()
			if native.service(m, message, &calls) {
				return
			}
			var q lifecycle.Command
			decodeErr := lifecycle.StrictDecode(message.Data, &q)
			dispatch := func() {
				defer calls.Done()
				var out any
				err := decodeErr
				if err == nil {
					// Match Runner: InvokeHTTP validates and enforces the caller's
					// bounded timeout. An extra fixture deadline truncates legitimate
					// cold kernel startup and other long-running App operations.
					out, err = m.Dispatch(context.Background(), q)
				}
				if err != nil {
					out = map[string]string{"error": err.Error()}
				}
				raw, _ := json.Marshal(out)
				_ = message.Respond(raw)
			}
			if q.Method == "invoke" || q.Method == "check_instance" || q.Method == "app_manifest" {
				select {
				case slots <- struct{}{}:
				default:
					_ = message.Respond([]byte(`{"error":"fixture concurrency limit"}`))
					calls.Done()
					return
				}
				go func() { defer func() { <-slots }(); dispatch() }()
			} else {
				dispatch()
			}
		})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			admission.Lock()
			closing = true
			admission.Unlock()
			_ = sub.Unsubscribe()
			calls.Wait()
		})
		if err := nc.Flush(); err != nil {
			t.Fatal(err)
		}
		for _, in := range m.Snapshot().Instances {
			return m, in
		}
		t.Fatal("missing instance")
		return nil, nil
	}
	consumerManager, consumer := newNode("consumer-node")
	providerManager, provider := newNode("provider-node")
	const controllerKey = "pbk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	g, err := makeAppGateway("apps.test", controllerKey, []string{"https://atrium.test"}, authority, "nats://"+address)
	if err != nil {
		t.Fatal(err)
	}
	if err := g.OpenDependencyStore(filepath.Join(root, "gateway-dependencies")); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { g.CloseDependencyStore() })

	mux := http.NewServeMux()
	g.Register(mux)
	server := httptest.NewServer(g.Handler(mux))
	defer server.Close()
	grantRequest := appgateway.DependencyRequest{
		Consumer: apptransport.InstanceIdentity{Fleet: owner, Node: "consumer-node", Instance: consumer.ID, Revision: digest, Generation: consumer.Generation},
		Provider: appgateway.Binding{Fleet: owner, Node: "provider-node", Instance: provider.ID, Revision: digest, Generation: provider.Generation, Component: "backend", Port: "http"},
		AppID:    provider.AppID, Timeout: 5, Expires: time.Now().Add(time.Minute).Unix(), Methods: map[string]appgateway.RPCMethod{"echo": {Arguments: []string{"value"}, Bound: map[string]json.RawMessage{"workspace_id": json.RawMessage(`"workspace-a"`)}}},
	}
	body, _ := json.Marshal(grantRequest)
	do := func(path, host, token string, body []byte) (int, []byte) {
		t.Helper()
		req, _ := http.NewRequest("POST", server.URL+path, bytes.NewReader(body))
		req.Host = host
		req.Header.Set("Authorization", "Bearer "+token)
		res, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(res.Body)
		return res.StatusCode, raw
	}
	code, raw := do("/apps/dependencies", "controller.test", controllerKey, body)
	if code != 200 {
		t.Fatal(code, string(raw))
	}
	var grant struct {
		Token string `json:"access_token"`
	}
	if json.Unmarshal(raw, &grant) != nil || grant.Token == "" {
		t.Fatal("missing grant")
	}
	host := appgateway.Host(provider.ID, "backend", "http", provider.Generation, "apps.test")
	invoke := []byte(`{"method":"echo","args":{"value":"only-once"}}`)
	code, raw = do("/rpc", host, grant.Token, invoke)
	var output struct {
		Success bool              `json:"success"`
		Result  map[string]string `json:"result"`
		PID     int               `json:"pid"`
	}
	if code != 200 || json.Unmarshal(raw, &output) != nil || !output.Success || output.Result["workspace_id"] != "workspace-a" || output.Result["value"] != "only-once" || bytes.Contains(raw, []byte(controllerKey)) {
		t.Fatal(code, string(raw))
	}
	t.Run("BrokerOutageRecovery", func(t *testing.T) {
		originalPID := output.PID
		if originalPID <= 0 {
			t.Fatal("missing real provider PID")
		}
		if err := process.Process.Kill(); err != nil {
			t.Fatal(err)
		}
		_ = process.Wait()
		process = nil
		// The Apps stay alive. The gateway must not authorize calls merely
		// from its cached grant when neither node can be checked.
		code, raw := do("/rpc", host, grant.Token, invoke)
		if code == 200 || bytes.Contains(raw, []byte(controllerKey)) {
			t.Fatalf("offline call admitted or credentials exposed: %d %s", code, raw)
		}
		process = exec.Command(binary, "-c", config)
		if err := process.Start(); err != nil {
			process = nil
			t.Fatal(err)
		}
		// Retry only this idempotent fixture echo. The gateway itself never
		// replays calls; the separate transport regression checks that property.
		for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline); {
			connected := true
			for _, nc := range nodeConnections {
				connected = connected && nc.IsConnected()
			}
			if connected {
				code, raw = do("/rpc", host, grant.Token, invoke)
				if code == 200 {
					if json.Unmarshal(raw, &output) != nil || !output.Success || output.PID != originalPID || output.Result["workspace_id"] != "workspace-a" {
						t.Fatalf("recovery changed provider or binding: %s", raw)
					}
					if consumerManager.Snapshot().Instances[consumer.ID].Generation != consumer.Generation || providerManager.Snapshot().Instances[provider.ID].Generation != provider.Generation {
						t.Fatal("transport recovery restarted an App")
					}
					return
				}
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatalf("same grant did not recover: %d %s", code, raw)
	})
	testPreparedDependencyAssembly(t, root, owner, address, authority, g, controllerKey, consumerManager, provider)
	t.Run("ResourceSessionOwner", func(t *testing.T) {
		testResourceSessionOwner(t, root, owner, address, authority, consumerManager, providerManager, consumer)
	})
	// Stopping the consumer invalidates authorization despite the cached grant.
	run(consumerManager, "stop-consumer", "stop", consumer.Generation)
	if code, _ := do("/rpc", host, grant.Token, invoke); code != 409 {
		t.Fatal("stopped consumer admitted", code)
	}
	current := consumerManager.Snapshot().Instances[consumer.ID]
	run(consumerManager, "restart-consumer", "start", current.Generation)
	if code, _ := do("/rpc", host, grant.Token, invoke); code != 409 {
		t.Fatal("new consumer generation used old grant", code)
	}
	grantRequest.Consumer.Generation = consumerManager.Snapshot().Instances[consumer.ID].Generation
	body, _ = json.Marshal(grantRequest)
	code, raw = do("/apps/dependencies", "controller.test", controllerKey, body)
	if code != 200 {
		t.Fatal(code, string(raw))
	}
	_ = json.Unmarshal(raw, &grant)
	run(providerManager, "stop-provider", "stop", provider.Generation)
	if code, raw := do("/rpc", host, grant.Token, invoke); code != 502 || strings.Contains(string(raw), digest) {
		t.Fatal("provider error was forwarded or stale call allowed", code, string(raw))
	}
	// Run slow package acceptance after the short-lived grant assertions above.
	// Its candidate scopes do not depend on the stopped rpc-example processes.
	if native != nil {
		t.Run("NativeAgentDeployment", func(t *testing.T) {
			native.run(t, owner, address, authority, g, controllerKey)
		})
	}
}
