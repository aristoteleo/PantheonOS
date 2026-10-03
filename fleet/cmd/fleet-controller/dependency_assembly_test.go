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
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// Actual Python assembly -> authenticated NATS -> prepared native consumer ->
// Python SDK -> TLS gateway -> provider process. Local management endpoints
// substitute for Hub's authentication wrapper (tested in Hub). Only fixture
// DNS/port and CA trust differ on the consumer's actual TLS connection.
func testPreparedDependencyAssembly(t *testing.T, root, owner, address string, authority *auth.Authority, gateway *appgateway.Gateway, controlKey string, manager *lifecycle.Manager, provider *lifecycle.Instance) {
	t.Helper()
	creds, err := authority.MintFleetUser(owner)
	if err != nil {
		t.Fatal(err)
	}
	nc, err := nats.Connect("nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix("_INBOX_"+owner))
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	mux := http.NewServeMux()
	gateway.Register(mux)
	tlsServer := httptest.NewTLSServer(gateway.Handler(mux))
	defer tlsServer.Close()
	_, port, err := net.SplitHostPort(strings.TrimPrefix(tlsServer.URL, "https://"))
	if err != nil {
		t.Fatal(err)
	}
	management := http.NewServeMux()
	management.HandleFunc("/node/", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+controlKey {
			w.WriteHeader(403)
			return
		}
		raw, _ := io.ReadAll(io.LimitReader(r.Body, 128<<10))
		node := strings.TrimPrefix(r.URL.Path, "/node/")
		result, err := nc.Request(proto.SubjNodeCmd(owner, node), raw, 10*time.Second)
		if err != nil {
			w.WriteHeader(503)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(result.Data)
	})
	management.HandleFunc("/grant", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+controlKey {
			w.WriteHeader(403)
			return
		}
		var body map[string]any
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			w.WriteHeader(400)
			return
		}
		var consumer, provider map[string]any
		if r.Method == "POST" {
			consumer = body["consumer"].(map[string]any)
			provider = body["provider"].(map[string]any)
			consumer["fleet_id"] = owner
			provider["fleet_id"] = owner
		} else {
			body["fleet_id"] = owner
		}
		if r.Method != "DELETE" {
			body["expires"] = time.Now().Unix() + int64(body["ttl_seconds"].(float64))
			delete(body, "ttl_seconds")
		}
		raw, _ := json.Marshal(body)
		request := httptest.NewRequest(r.Method, "http://controller.test/apps/dependencies", bytes.NewReader(raw))
		request.Header.Set("Authorization", "Bearer "+controlKey)
		record := httptest.NewRecorder()
		mux.ServeHTTP(record, request)
		if record.Code != 200 {
			w.WriteHeader(record.Code)
			return
		}
		var result map[string]any
		_ = json.Unmarshal(record.Body.Bytes(), &result)
		if r.Method == "POST" {
			result["consumer"] = consumer
			result["provider"] = provider
		}
		_ = json.NewEncoder(w).Encode(result)
	})
	control := httptest.NewServer(management)
	defer control.Close()
	files := map[string][]byte{}
	for _, name := range []string{"pantheon/__init__.py", "pantheon/apps/__init__.py", "pantheon/platform/__init__.py"} {
		files[name] = []byte("")
	}
	for _, name := range []string{"apps/runtime_config.py", "apps/dependency_client.py", "apps/lifecycle.py", "apps/dependency_assembly.py", "platform/registry_lock.py"} {
		b, err := os.ReadFile(filepath.Join("..", "..", "..", "pantheon", name))
		if err != nil {
			t.Fatal(err)
		}
		files["pantheon/"+name] = b
	}
	files["app.json"] = []byte(`{"apiVersion":2,"id":"dependency-consumer","version":"1.0.0","dependencies":{"rpc-example":{"range":"^1.0.0","uses":["echo@1"]}}}`)
	def := lifecycle.Definition{Protocol: 1, AppID: "dependency-consumer", Version: "1.0.0", Components: []lifecycle.Component{{Name: "backend", Runtime: "process", Argv: []string{"python3", "${PACKAGE}/consumer.py", "${DATA}", port}, StopSeconds: 1,
		Configuration: &lifecycle.ConfigDeclaration{Credentials: map[string]lifecycle.ConfigField{"provider": {Required: true}}},
		Readiness:     lifecycle.Probe{Argv: []string{"python3", "-c", `import pathlib,sys;assert pathlib.Path(sys.argv[1],'result.json').is_file()`, "${DATA}"}, TimeoutSeconds: 5}}}}
	files["fleet.json"], _ = json.Marshal(def)
	files["consumer.py"] = []byte(`
import json, os, socket, ssl, sys, time
from pathlib import Path
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
config=load_runtime_configuration(required=True)
assert 'FLEET_KEY' not in os.environ and 'PANTHEON_HUB_URL' not in os.environ
original=socket.getaddrinfo
def mapped(host,port,*args,**kwargs):
 if host.endswith('.apps.test'): return original('127.0.0.1',int(sys.argv[2]),*args,**kwargs)
 return original(host,port,*args,**kwargs)
socket.getaddrinfo=mapped
client=DependencyClient(config.credentials['provider'],tls_context=ssl._create_unverified_context())
Path(sys.argv[1],'loaded').write_text('loaded')
for attempt in range(100):
 try:
  response=client.invoke('echo',{'value':'native-consumer'},timeout_seconds=5)
  break
 except DependencyCallError as error:
  # Only pre-admission denial is retried, never an unknown mutation outcome.
  if error.status!=409: raise
  time.sleep(.05)
else: raise RuntimeError('consumer never became ready')
assert response['success'] and response['result']=={'value':'native-consumer','workspace_id':'project-a'}
Path(sys.argv[1],'result.json').write_text(json.dumps(response))
while True:
 if Path(sys.argv[1],'invoke-again').exists():
  response=client.invoke('echo',{'value':'after-renewal'},timeout_seconds=5)
  assert response['result']=={'value':'after-renewal','workspace_id':'project-a'}
  Path(sys.argv[1],'renewed.json').write_text(json.dumps(response))
  Path(sys.argv[1],'invoke-again').unlink()
 time.sleep(.05)
`)
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	for name, data := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0400, Size: int64(len(data))}); err != nil {
			t.Fatal(err)
		}
		_, _ = tw.Write(data)
	}
	_ = tw.Close()
	digestBytes := sha256.Sum256(archive.Bytes())
	digest := hex.EncodeToString(digestBytes[:])
	if _, err := manager.Stage(digest, 0, archive.Bytes()); err != nil {
		t.Fatal(err)
	}
	submit := func(request lifecycle.Request) {
		t.Helper()
		if _, err := manager.Submit(request); err != nil {
			t.Fatal(err)
		}
		for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
			op := manager.Snapshot().Operations[request.OperationID]
			if op.State == "succeeded" {
				return
			}
			if op.State != "queued" && op.State != "running" {
				t.Fatal(op.Error)
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatal("operation did not finish")
	}
	submit(lifecycle.Request{Protocol: 1, OperationID: "assembly-install", Action: "install", Digest: digest, Scope: "app"})
	submit(lifecycle.Request{Protocol: 1, OperationID: "assembly-prepare", Action: "prepare_start", Digest: digest, Scope: "app"})
	var consumer *lifecycle.Instance
	for _, in := range manager.Snapshot().Instances {
		if in.Digest == digest {
			consumer = in
		}
	}
	if consumer == nil {
		t.Fatal("no prepared consumer")
	}
	coordRoot := filepath.Join(root, "coordinator")
	for name, data := range files {
		if !strings.HasPrefix(name, "pantheon/") {
			continue
		}
		path := filepath.Join(coordRoot, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	recipe := map[string]any{"consumer": map[string]any{"node_id": "consumer-node", "instance_id": consumer.ID, "revision": digest, "generation": consumer.Generation}, "preparation_id": consumer.StartPreparationID, "operation_id": "assembly-start",
		"bindings": map[string]any{"provider": map[string]any{"app_id": provider.AppID, "component": "backend", "provider": map[string]any{"node_id": "provider-node", "instance_id": provider.ID, "revision": provider.Digest, "generation": provider.Generation, "component": "backend", "port": "http"}, "methods": map[string]any{"echo": map[string]any{"arguments": []string{"value"}, "bound": map[string]string{"workspace_id": "project-a"}}}}}, "components": map[string]any{"backend": map[string]any{}}}
	input, _ := json.Marshal(recipe)
	if err := os.WriteFile(filepath.Join(coordRoot, "recipe.json"), input, 0600); err != nil {
		t.Fatal(err)
	}
	source := `
import asyncio, json, sys, urllib.request, time
from pathlib import Path
from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.dependency_assembly import DependencyStarter
base,key=sys.argv[1:3]
def post(path,body,method='POST'):
 request=urllib.request.Request(base+path,method=method,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=15) as response:
  return None if response.status==204 else json.load(response)
class Wire(FleetLifecycle):
 async def _request(self,node,method,**data):
  result=await asyncio.to_thread(post,'/node/'+node,dict(type='app_lifecycle',protocol=1,method=method,**data))
  if result.get('error'):raise RuntimeError(result['error'])
  return result
class Authority:
 async def issue(self,body):return await asyncio.to_thread(post,'/grant',body)
 async def renew(self,grant_id,ttl_seconds):return await asyncio.to_thread(post,'/grant',dict(grant_id=grant_id,ttl_seconds=ttl_seconds),'PATCH')
 async def revoke(self,grant_id):return await asyncio.to_thread(post,'/grant',dict(grant_id=grant_id),'DELETE')
async def main():
 recipe=json.loads(Path('recipe.json').read_text())
 starter=DependencyStarter(Wire(None),Path('attempts'),Authority())
 if len(sys.argv)>3:
  path=next(starter.root.glob('*.json'))
  record=json.loads(path.read_text())
  receipt=record['renewals']['provider']
  grant_id=receipt['grant_id']
  if sys.argv[3]=='renew':
   # Force the owner schedule due; do not alter gateway time or live grant.
   receipt['expires']=int(time.time())+100
   path.write_text(json.dumps(record))
  result=await starter.reconcile_once()
  assert result[{'renew':'renewed','revoke':'revoked'}[sys.argv[3]]]==1,result
  saved=json.loads(path.read_text())['renewals']['provider']
  assert saved['grant_id']==grant_id and 'access_token' not in path.read_text()
  if sys.argv[3]=='revoke':
   try:await Authority().renew(grant_id,900)
   except urllib.error.HTTPError as error:assert error.code==410
   else:raise AssertionError('revoked grant renewed')
  print(json.dumps(result))
  return
 result=await starter.start(**recipe)
 assert result['operation']['state'] in ('queued','running','succeeded')
 assert 'access_token' not in json.dumps(result)
 print(json.dumps({'accepted':True}))
asyncio.run(main())
`
	if err := os.WriteFile(filepath.Join(coordRoot, "start.py"), []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "python3", "start.py", control.URL, controlKey)
	cmd.Dir = coordRoot
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("owner coordinator failed: %v %s", err, output)
	}
	dataPath := filepath.Join(root, "consumer-node", "data", consumer.ID, "result.json")
	found := false
	for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline); {
		raw, err := os.ReadFile(dataPath)
		if err == nil && manager.Snapshot().Operations["assembly-start"].State == "succeeded" {
			if !bytes.Contains(raw, []byte("native-consumer")) {
				t.Fatal("wrong result")
			}
			found = true
			break
		}
		time.Sleep(30 * time.Millisecond)
	}
	if !found {
		t.Fatal("native consumer did not invoke dependency")
	}
	state := manager.Snapshot()
	running := state.Instances[consumer.ID]
	if running.State != "ready" {
		t.Fatal("consumer not ready", running.State)
	}
	public, _ := json.Marshal(state)
	if bytes.Contains(public, []byte("access_token")) || bytes.Contains(public, []byte(controlKey)) {
		t.Fatal("credential leaked")
	}
	maintain := func(mode string) {
		t.Helper()
		cmd := exec.CommandContext(ctx, "python3", "start.py", control.URL, controlKey, mode)
		cmd.Dir = coordRoot
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("owner maintenance failed: %v %s", err, output)
		}
	}
	maintain("renew")
	if err := os.WriteFile(filepath.Join(filepath.Dir(dataPath), "invoke-again"), []byte("1"), 0600); err != nil {
		t.Fatal(err)
	}
	found = false
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		if raw, err := os.ReadFile(filepath.Join(filepath.Dir(dataPath), "renewed.json")); err == nil && bytes.Contains(raw, []byte("after-renewal")) {
			found = true
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !found {
		t.Fatal("consumer could not use its unchanged credential after renewal")
	}
	submit(lifecycle.Request{Protocol: 1, OperationID: "assembly-stop", Action: "stop", Digest: digest, Scope: "app", Generation: running.Generation})
	maintain("revoke")
	for _, res := range running.Resources {
		alive, err := (lifecycle.NativeDriver{}).Alive(context.Background(), res)
		if err != nil || alive {
			t.Fatal("consumer survived stop", err)
		}
	}
}
