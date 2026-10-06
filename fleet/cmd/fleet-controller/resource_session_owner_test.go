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
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// Real Python owner journal -> owner-authenticated NATS -> two node Managers ->
// shipping native Shell binary. The loopback bridge replaces only Python's
// transport adapter, not lifecycle state, provider calls or Shell execution.
func testResourceSessionOwner(t *testing.T, root, owner, address string, authority *auth.Authority, consumerManager, providerManager *lifecycle.Manager, template *lifecycle.Instance) {
	t.Helper()
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("native Shell platform unavailable")
	}
	goBinary, err := exec.LookPath("go")
	if err != nil {
		t.Fatal(err)
	}
	_, source, _, _ := runtime.Caller(0)
	repo := filepath.Clean(filepath.Join(filepath.Dir(source), "../../.."))
	packageDir := filepath.Join(t.TempDir(), "shell")
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	build := exec.CommandContext(ctx, "python3", filepath.Join(repo, "apps/shell/build_managed.py"), "--output", packageDir,
		"--os", runtime.GOOS, "--arch", runtime.GOARCH, "--go", goBinary)
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("native Shell build: %v %s", err, output)
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
	for offset := 0; offset < archive.Len(); {
		end := min(offset+lifecycle.MaxChunk, archive.Len())
		next, err := providerManager.Stage(digest, int64(offset), archive.Bytes()[offset:end])
		if err != nil {
			t.Fatal(err)
		}
		offset = int(next)
	}
	operate := func(manager *lifecycle.Manager, digest, id, action, scope string, generation uint64, preparation ...string) *lifecycle.Instance {
		t.Helper()
		request := lifecycle.Request{Protocol: 1, OperationID: id, Action: action,
			Digest: digest, Scope: scope, Generation: generation}
		if len(preparation) != 0 {
			request.StartPreparationID = preparation[0]
		}
		_, err := manager.Submit(request)
		if err != nil {
			t.Fatal(err)
		}
		for deadline := time.Now().Add(20 * time.Second); time.Now().Before(deadline); {
			snapshot := manager.Snapshot()
			op := snapshot.Operations[id]
			if op.State == "succeeded" {
				if action == "install" {
					return nil
				}
				for _, instance := range snapshot.Instances {
					if instance.Digest == digest && instance.Scope == scope {
						return instance
					}
				}
				t.Fatal("completed operation has no instance")
			}
			if op.State != "queued" && op.State != "running" {
				t.Fatal(op.Error)
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatal("resource session lifecycle operation timed out")
		return nil
	}
	startProvider := func(id string, generation uint64) *lifecycle.Instance {
		t.Helper()
		prepared := operate(providerManager, digest, id+"-prepare", "prepare_start", "session-provider", generation)
		config := lifecycle.AppConfiguration{Preparation: prepared.StartPreparationID,
			Components: map[string]lifecycle.ComponentConfig{"backend": {Values: map[string]json.RawMessage{}}}}
		if err := providerManager.ConfigureApp(prepared.ID, digest, prepared.Generation, config); err != nil {
			t.Fatal(err)
		}
		return operate(providerManager, digest, id, "start", "session-provider", prepared.Generation, prepared.StartPreparationID)
	}
	operate(providerManager, digest, "session-provider-install", "install", "session-provider", 0)
	provider := startProvider("session-provider-start", 0)
	consumer := operate(consumerManager, template.Digest, "session-consumer-start", "start", "session-consumer", 0)
	creds, err := authority.MintFleetUser(owner)
	if err != nil {
		t.Fatal(err)
	}
	nc, err := nats.Connect("nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix("_INBOX_"+owner))
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	controlKey := strings.Repeat("owner-control-fixture", 2)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+controlKey {
			w.WriteHeader(403)
			return
		}
		node := strings.TrimPrefix(r.URL.Path, "/node/")
		if node != "consumer-node" && node != "provider-node" {
			w.WriteHeader(404)
			return
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, 128<<10))
		if err != nil {
			w.WriteHeader(400)
			return
		}
		response, err := nc.Request(proto.SubjNodeCmd(owner, node), body, 15*time.Second)
		if err != nil {
			w.WriteHeader(503)
			return
		}
		_, _ = w.Write(response.Data)
	}))
	defer control.Close()
	coordinator := filepath.Join(root, "resource-coordinator")
	for _, name := range []string{"__init__.py", "apps/__init__.py", "platform/__init__.py", "apps/lifecycle.py", "apps/dependency_assembly.py", "apps/owner_journal.py", "apps/resource_sessions.py", "platform/registry_lock.py"} {
		var body []byte
		if !strings.HasSuffix(name, "__init__.py") {
			body, err = os.ReadFile(filepath.Join(repo, "pantheon", name))
			if err != nil {
				t.Fatal(err)
			}
		}
		path := filepath.Join(coordinator, "pantheon", name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	writeRecipe := func(id string, consumer *lifecycle.Instance) {
		t.Helper()
		recipe := map[string]any{
			"consumer":  map[string]any{"node_id": "consumer-node", "instance_id": consumer.ID, "revision": consumer.Digest, "generation": consumer.Generation},
			"provider":  map[string]any{"node_id": "provider-node", "instance_id": provider.ID, "revision": digest, "generation": provider.Generation, "component": "backend", "port": "http"},
			"owner_ref": id, "operation_id": id, "app_id": "shell", "kind": "shell", "preparation_id": "",
		}
		body, _ := json.Marshal(recipe)
		if err := os.WriteFile(filepath.Join(coordinator, "recipe.json"), body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	writeRecipe("instance-one", consumer)
	program := `
import asyncio, json, sys, time, urllib.request
from pathlib import Path
from types import SimpleNamespace
from pantheon.apps.lifecycle import FleetLifecycle
import pantheon.apps.resource_sessions as session_module
from pantheon.apps.resource_sessions import ResourceSessionOwner
base,key,mode=sys.argv[1:4]
def post(node,body):
 request=urllib.request.Request(base+'/node/'+node,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key})
 with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
class Wire(FleetLifecycle):
 lose=False
 async def _request(self,node,method,**data):
  result=await asyncio.to_thread(post,node,dict(type='app_lifecycle',protocol=1,method=method,**data))
  if result.get('error'):raise RuntimeError(result['error'])
  return result
 async def resource_session(self,*args):
  result=await super().resource_session(*args)
  if self.lose and args[2]=='resource_session_acquire':
   self.lose=False
   raise TimeoutError('lost successful acquisition acknowledgement')
  return result
async def main():
 recipe=json.loads(Path('recipe.json').read_text())
 second={**recipe,'owner_ref':'instance-two','operation_id':'instance-two'}
 wire=Wire(None)
 owner=ResourceSessionOwner(wire,Path('sessions'))
 def lookup(r):return dict(consumer=r['consumer'],operation_id=r['operation_id'])
 async def command(record,text):
  b=record['recipe']['provider']
  response=await wire._request(b['node_id'],'invoke',app_id='shell',instance_id=b['instance_id'],revision=b['revision'],generation=b['generation'],timeout_seconds=5,
   payload={'method':'run_command_in_shell','args':{'shell_id':record['receipt']['session_id'],'command':text,'timeout':2},'timeout_s':4})
  result=response['response']
  assert result['success'] and result['result']['success'],result
  return result['result']['output']
 if mode=='acquire':
  wire.lose=True
  try:await owner.acquire(**recipe)
  except TimeoutError:pass
  else:raise AssertionError('missing injected ACK loss')
  assert (await owner.inspect(**lookup(recipe)))['phase']=='acquiring'
  first=await owner.acquire(**recipe)
  other=await owner.acquire(**second)
  assert first['receipt']['session_id']!=other['receipt']['session_id']
  await command(first,'export OWNER_VALUE=one; mkdir -p instance-one; cd instance-one')
  assert await command(first,'printf "%s:%s\n" "$OWNER_VALUE" "${PWD##*/}"')=='one:instance-one\n'
  assert await command(other,'printf "%s:%s\n" "${OWNER_VALUE-unset}" "${PWD##*/}"')=='unset:workspace\n'
  assert (await owner.acquire(**recipe))['receipt']['session_id']==first['receipt']['session_id']
 elif mode=='renew-release':
  first=await owner.inspect(**lookup(recipe))
  # Advance only the coordinator's renewal schedule; provider time/TTL remain
  # real. A renew cannot shorten a live lease, so do not try to force expiry.
  receipt=first['receipt']
  await asyncio.sleep(1.1)
  session_module.time=SimpleNamespace(time=lambda:time.time()+610)
  assert (await owner.reconcile_once())['renewed']==2
  session_module.time=time
  current=await owner.inspect(**lookup(recipe))
  assert current['receipt']['expires']>=int(time.time())+890
  assert current['receipt']['expires']>receipt['expires']
  assert current['receipt']['session_id']==receipt['session_id']
  assert await command(current,'echo "$OWNER_VALUE"')=='one\n'
  assert (await owner.release(**lookup(recipe)))['receipt']['state']=='released'
  assert await command(await owner.inspect(**lookup(second)),'echo sibling-alive')=='sibling-alive\n'
 elif mode=='consumer-stopped':
  assert (await owner.reconcile_once())['released']==1
  record=await owner.inspect(**lookup(second))
  remote=await wire.resource_session('shell',recipe['provider'],'resource_session_get',dict(owner_ref=second['owner_ref'],lease_id=record['lease_id']))
  assert remote['state']=='released'
  assert not any((await owner.reconcile_once()).values())
 elif mode=='acquire-replacement-check':
  assert (await owner.acquire(**recipe))['phase']=='active'
 elif mode=='provider-replaced':
  assert (await owner.reconcile_once())['lost']==1
  record=await owner.inspect(**lookup(recipe))
  assert record['phase']=='terminal' and record['reason']=='provider_unavailable'
  assert (await owner.acquire(**recipe))==record
  # Exact old generation remains invalid even if the same package restarts.
  try:await wire.resource_session('shell',recipe['provider'],'resource_session_get',dict(owner_ref=recipe['owner_ref'],lease_id=record['lease_id']))
  except RuntimeError:pass
  else:raise AssertionError('replacement accepted old generation')
 else:raise AssertionError(mode)
 print('resource-owner-ok:'+mode)
asyncio.run(main())
`
	if err := os.WriteFile(filepath.Join(coordinator, "owner.py"), []byte(program), 0600); err != nil {
		t.Fatal(err)
	}
	run := func(mode string) {
		t.Helper()
		cmd := exec.CommandContext(ctx, "python3", "owner.py", control.URL, controlKey, mode)
		cmd.Dir = coordinator
		output, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("resource session owner %s: %v\n%s", mode, err, output)
		}
		if !strings.Contains(string(output), "resource-owner-ok:"+mode) {
			t.Fatal("missing owner completion evidence", string(output))
		}
	}
	run("acquire")
	run("renew-release") // A fresh owner process resumes from the same journal.
	consumer = operate(consumerManager, consumer.Digest, "session-consumer-stop", "stop", "session-consumer", consumer.Generation)
	run("consumer-stopped")
	consumer = operate(consumerManager, consumer.Digest, "session-consumer-restart", "start", "session-consumer", consumer.Generation)
	writeRecipe("provider-replacement-check", consumer)
	run("acquire-replacement-check")
	stopped := operate(providerManager, digest, "session-provider-stop", "stop", "session-provider", provider.Generation)
	started := startProvider("session-provider-restart", stopped.Generation)
	run("provider-replaced")
	operate(providerManager, digest, "session-provider-final-stop", "stop", "session-provider", started.Generation)
	operate(consumerManager, consumer.Digest, "session-consumer-final-stop", "stop", "session-consumer", consumer.Generation)
}
