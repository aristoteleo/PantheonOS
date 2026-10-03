package main

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

// Test-only privileged provider composition. Its private owner control fixture
// stands in for PlatformService's lifecycle/authority objects; this is NOT a new
// production credential storage or deployment path. The consumer is a separate
// managed native process with only a scoped gateway credential. RPC crosses the
// real TLS gateway, authenticated NATS, and node lifecycle invocation twice:
// allocation provider first, then the dynamically granted tool provider.
func testRemoteDependencyBinding(t *testing.T, root, controlURL, controlKey string, gateway *httptest.Server, manager *lifecycle.Manager, consumer *lifecycle.Instance, sdk map[string][]byte, recipe map[string]any) func() {
	t.Helper()
	files := map[string][]byte{}
	for name, data := range sdk {
		if len(name) > 9 && name[:9] == "pantheon/" {
			files[name] = data
		}
	}
	files["app.json"] = []byte(`{"apiVersion":2,"id":"dependency-broker","version":"1.0.0","provides":{"interfaces":[{"name":"dependency-binding","version":1,"tools":["bind_dependencies"]}],"tools":[{"name":"bind_dependencies","params":[{"name":"policy_id"},{"name":"owner_ref"},{"name":"operation_id"},{"name":"aliases"}]}]}}`)
	definition := lifecycle.Definition{Protocol: 1, AppID: "dependency-broker", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend", Runtime: "process", Argv: []string{"python3", "${PACKAGE}/broker.py", "${DATA}"}, Ports: map[string]int{"http": 0}, StopSeconds: 1,
		Configuration: &lifecycle.ConfigDeclaration{Values: map[string]lifecycle.ConfigField{"policies": {Required: true}}},
		Readiness:     lifecycle.Probe{Argv: []string{"python3", "-c", `import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'],timeout=1)`}, TimeoutSeconds: 5}}}}
	files["fleet.json"], _ = json.Marshal(definition)
	files["broker.py"] = []byte(`
import asyncio, hmac, json, os, sys, threading, urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pantheon.apps.dependency_binding_service import DependencyBindingService
from pantheon.apps.live_dependencies import LiveDependencyOwner
from pantheon.apps.resource_sessions import ResourceSessionOwner
from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.runtime_config import load_runtime_configuration
data=Path(sys.argv[1])
config=json.loads((data/'trusted-owner-fixture.json').read_text())
base,key=config['endpoint'],config['key']
def post(path,body,method='POST'):
 request=urllib.request.Request(base+path,method=method,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=15) as response:
  return None if response.status==204 else json.load(response)
class Wire(FleetLifecycle):
 async def _request(self,node,method,**args):
  result=await asyncio.to_thread(post,'/node/'+node,dict(type='app_lifecycle',protocol=1,method=method,**args))
  if result.get('error'): raise RuntimeError('fixture lifecycle unavailable')
  return result
class Authority:
 async def issue(self,body): return await asyncio.to_thread(post,'/grant',body)
wire=Wire(None)
owner=LiveDependencyOwner(wire,data/'attempts',ResourceSessionOwner(wire,data/'sessions'),Authority())
service=DependencyBindingService(owner,policies=json.loads(load_runtime_configuration(required=True).values['policies']))
loop=asyncio.new_event_loop()
threading.Thread(target=loop.run_forever,daemon=True).start()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'ready')
 def do_POST(self):
  if self.path!='/rpc' or not hmac.compare_digest(self.headers.get('X-Fleet-RPC-Token',''),os.environ['PANTHEON_APP_RPC_TOKEN']):
   self.send_response(403); self.end_headers(); return
  try:
   payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
   assert payload['method']=='bind_dependencies'
   result=asyncio.run_coroutine_threadsafe(service.bind_dependencies(**payload['args']),loop).result(20)
   response=dict(success=True,result=result)
  except Exception: response=dict(success=False,error='binding unavailable')
  raw=json.dumps(response).encode()
  self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
`)
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	for name, data := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0400, Size: int64(len(data))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(archive.Bytes())
	digest := hex.EncodeToString(sum[:])
	if _, err := manager.Stage(digest, 0, archive.Bytes()); err != nil {
		t.Fatal(err)
	}
	run := func(q lifecycle.Request) {
		t.Helper()
		if _, err := manager.Submit(q); err != nil {
			t.Fatal(err)
		}
		for end := time.Now().Add(10 * time.Second); time.Now().Before(end); {
			op := manager.Snapshot().Operations[q.OperationID]
			if op.State == "succeeded" {
				return
			}
			if op.State != "running" && op.State != "queued" {
				t.Fatal(op.Error)
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatal("binding provider lifecycle timed out")
	}
	run(lifecycle.Request{Protocol: 1, OperationID: "broker-install", Action: "install", Digest: digest, Scope: "app"})
	run(lifecycle.Request{Protocol: 1, OperationID: "broker-prepare", Action: "prepare_start", Digest: digest, Scope: "app"})
	var broker *lifecycle.Instance
	for _, in := range manager.Snapshot().Instances {
		if in.Digest == digest {
			broker = in
		}
	}
	if broker == nil {
		t.Fatal("binding provider was not installed")
	}
	identity := map[string]any{"node_id": "consumer-node", "instance_id": consumer.ID, "revision": consumer.Digest, "generation": consumer.Generation}
	original := recipe["bindings"].(map[string]any)["provider"].(map[string]any)
	binding := map[string]any{}
	for k, v := range original {
		if k != "component" {
			binding[k] = v
		}
	}
	policies, _ := json.Marshal(map[string]any{"approved": map[string]any{"consumer": identity, "bindings": map[string]any{"provider": binding}}})
	policyDocument, _ := json.Marshal(string(policies))
	cfg := lifecycle.AppConfiguration{Preparation: broker.StartPreparationID, Components: map[string]lifecycle.ComponentConfig{"backend": {Values: map[string]json.RawMessage{"policies": policyDocument}}}}
	if err := manager.ConfigureApp(broker.ID, broker.Digest, broker.Generation, cfg); err != nil {
		t.Fatal(err)
	}
	private := map[string]any{"endpoint": controlURL, "key": controlKey}
	data := filepath.Join(root, "consumer-node", "data", broker.ID)
	if err := os.MkdirAll(data, 0700); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(private)
	if err := os.WriteFile(filepath.Join(data, "trusted-owner-fixture.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	run(lifecycle.Request{Protocol: 1, OperationID: "broker-start", Action: "start", Digest: digest, Scope: "app", Generation: broker.Generation, StartPreparationID: broker.StartPreparationID})
	broker = manager.Snapshot().Instances[broker.ID]
	// Issue only the allocation method; the gateway owns the policy selector.
	grant := map[string]any{"operation_id": "native-allocator", "consumer": identity,
		"provider": map[string]any{"node_id": "consumer-node", "instance_id": broker.ID, "revision": digest, "generation": broker.Generation, "component": "backend", "port": "http"},
		"app_id":   "dependency-broker", "ttl_seconds": 900, "timeout_seconds": 5,
		"methods": map[string]any{"bind_dependencies": map[string]any{"arguments": []string{"owner_ref", "operation_id", "aliases"}, "bound": map[string]string{"policy_id": "approved"}}}}
	// The authenticated fixture management bridge is the same one used by the
	// existing prepared-configuration test; no management key reaches consumer.
	response := dependencyFixtureRequest(t, controlURL, controlKey, grant)
	consumerData := filepath.Join(root, "consumer-node", "data", consumer.ID)
	if err := os.WriteFile(filepath.Join(consumerData, "invoke-binding.tmp"), response, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(consumerData, "invoke-binding.tmp"), filepath.Join(consumerData, "invoke-binding")); err != nil {
		t.Fatal(err)
	}
	for end := time.Now().Add(15 * time.Second); ; {
		if result, err := os.ReadFile(filepath.Join(consumerData, "remote-binding-result.json")); err == nil && bytes.Contains(result, []byte("remote-native-member")) {
			break
		}
		if time.Now().After(end) {
			t.Fatal("native consumer did not acquire and invoke scoped dependency")
		}
		time.Sleep(20 * time.Millisecond)
	}
	entries, _ := filepath.Glob(filepath.Join(data, "attempts", "*.json"))
	if len(entries) != 1 {
		t.Fatal("replay or denied request allocated another binding", len(entries))
	}
	if receipt, _ := os.ReadFile(entries[0]); bytes.Contains(receipt, []byte("access_token")) || bytes.Contains(receipt, []byte(controlKey)) {
		t.Fatal("owner receipt leaked credential")
	}
	return func() {
		// Caller is now stopped; no method reaches the otherwise-live broker.
		assertDependencyFixtureDenied(t, gateway, response)
		if now, _ := filepath.Glob(filepath.Join(data, "attempts", "*.json")); len(now) != 1 {
			t.Fatal("stopped consumer allocated a binding")
		}
		run(lifecycle.Request{Protocol: 1, OperationID: "broker-stop", Action: "stop", Digest: digest, Scope: "app", Generation: broker.Generation})
		for _, res := range broker.Resources {
			if alive, err := (lifecycle.NativeDriver{}).Alive(context.Background(), res); err != nil || alive {
				t.Fatal("binding provider survived stop", err)
			}
		}
	}
}

func dependencyFixtureRequest(t *testing.T, base, key string, grant map[string]any) []byte {
	t.Helper()
	body, _ := json.Marshal(grant)
	request, err := http.NewRequest("POST", base+"/grant", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer "+key)
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 10 * time.Second}).Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 65536))
	if err != nil || response.StatusCode != 200 {
		t.Fatal("allocation credential failed", response.StatusCode, err)
	}
	return raw
}

func assertDependencyFixtureDenied(t *testing.T, gateway *httptest.Server, raw []byte) {
	t.Helper()
	var grant struct {
		Endpoint string `json:"endpoint"`
		Token    string `json:"access_token"`
	}
	if err := json.Unmarshal(raw, &grant); err != nil {
		t.Fatal(err)
	}
	endpoint, err := url.Parse(grant.Endpoint)
	if err != nil {
		t.Fatal(err)
	}
	request, _ := http.NewRequest("POST", gateway.URL+"/rpc", bytes.NewBufferString(`{"method":"bind_dependencies","args":{"owner_ref":"stopped","operation_id":"stopped","aliases":["provider"]}}`))
	request.Host = endpoint.Host
	request.Header.Set("Authorization", "Bearer "+grant.Token)
	request.Header.Set("Content-Type", "application/json")
	response, err := gateway.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 409 {
		t.Fatal("stopped consumer reached allocation provider", response.StatusCode)
	}
}
