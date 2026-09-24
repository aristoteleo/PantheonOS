//go:build linux

package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/groupnetwork"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

const groupEchoApp = `import hmac,os,signal,sys
from http.server import HTTPServer,BaseHTTPRequestHandler
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  ok=hmac.compare_digest(self.headers.get('X-Pantheon-App-Token',''),os.environ['PANTHEON_APP_RPC_TOKEN'])
  body=os.environ['PANTHEON_NODE_ID'].encode() if ok else b'Unauthorized'
  self.send_response(200 if ok else 401);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
HTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
`

func TestGroupContainerNativeLifecycle(t *testing.T) {
	if os.Getenv("PANTHEON_GROUP_LIFECYCLE_TEST") != "isolated-container" {
		t.Skip("requires disposable Linux controller and cached Docker image")
	}
	if _, err := os.Stat("/.dockerenv"); err != nil {
		t.Fatal(err)
	}
	root, image := os.Getenv("PANTHEON_GROUP_TEST_ROOT"), os.Getenv("PANTHEON_GROUP_TEST_IMAGE")
	if !filepath.IsAbs(root) || image == "" {
		t.Fatal("missing isolated test inputs")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	if out, err := exec.CommandContext(ctx, "ip", "address", "add", "10.250.123.1/32", "dev", "lo").CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	fixture, _, fixtureDigest, key, ca, caPEM := groupCredentialFixture(t)
	original, err := readGroupManifest(fixture.paths(fixtureDigest, "group").Package)
	if err != nil {
		t.Fatal(err)
	}
	fixture.Close()
	managers := make([]*Manager, 2)
	digests := make([]string, 2)
	manifests := make([]groupcredentials.Manifest, 2)
	endpoints := make([]groupcredentials.OverlayEndpoint, 2)
	owned := []string{}
	t.Cleanup(func() {
		for _, m := range managers {
			if m != nil {
				m.Close()
			}
		}
	})
	for rank := range managers {
		manifest := *original
		manifest.Rank = &rank
		manifests[rank] = manifest
		node := manifest.Topology.Members[rank].Node
		nodeRoot := filepath.Join(root, fmt.Sprintf("node%d", rank))
		driver := NativeDriver{Engine: &ContainerEngine{Root: filepath.Join(nodeRoot, "dependencies/docker")}}
		m, e := Open(nodeRoot, manifest.Topology.Owner, node, proto.Capability{OS: "linux", Arch: runtime.GOARCH, Caps: []string{"proc", "model-group-private-network"}}, driver)
		if e != nil {
			t.Fatal(e)
		}
		managers[rank] = m
		m.SetResourceSampler(resourceInventory)
		def := Definition{Protocol: 1, AppID: "model-service", Version: "network-acceptance", Requires: Requirements{OS: []string{"linux"}, Arch: []string{runtime.GOARCH}, Caps: []string{"model-group-private-network"}}, Dependencies: Dependencies{ContainerEngine: &EngineDependency{Provider: "docker", Provision: "never"}}, Components: []Component{{
			Name: "backend", Runtime: "container", Image: image, Argv: []string{"python3", "/fleet/package/app.py"}, GroupPeer: true, GroupNetwork: true, RunAsOwner: true, Resources: &ResourceRequest{MemoryBytes: 128 << 20}, ReadOnlyMounts: map[string]string{"package": "/fleet/package"}, Ports: map[string]int{"http": 30123}, StopSeconds: 5,
			Readiness: Probe{Argv: []string{"python3", "-c", `import os,urllib.request;urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'],headers={'X-Pantheon-App-Token':os.environ['PANTHEON_APP_RPC_TOKEN']}),timeout=1).read()`}, TimeoutSeconds: 10}}}}
		body, _ := json.Marshal(manifest)
		archive, digest := bundle(t, def, map[string]string{"group-peer.json": string(body), "app.py": groupEchoApp})
		digests[rank] = digest
		if _, e = m.Stage(digest, 0, archive); e != nil {
			t.Fatal(e)
		}
		if op := submit(t, m, digest, "install", "install", "group", 0); op.State != "succeeded" {
			t.Fatal(op)
		}
		if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
			t.Fatal(op)
		}
		id := m.instanceID(digest, "group")
		enrollment, err := m.GroupPeer(id, digest, 1, "", "", false)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = m.GroupPeer(id, digest, 1, signGroupEnrollment(t, enrollment, key, ca), caPEM, true); err != nil {
			t.Fatal(err)
		}
		// Network absence must fail before the prepared generation is consumed.
		if op := commitPrepared(t, m, digest, "unadmitted", "prepare", 1); op.State != "failed" || m.Snapshot().Instances[id].State != "prepared" {
			t.Fatal("unadmitted launch", op)
		}
		status, err := m.GroupOverlay("prepare", OverlayRequest{Manifest: body, Address: fmt.Sprintf("10.250.123.1:%d", 55200+rank)})
		if err != nil {
			t.Fatal(err)
		}
		endpoints[rank] = *status.Endpoint
		owned = append(owned, fmt.Sprintf("pa-%s-2-backend", id))
		encoded, _ := json.Marshal(owned)
		if err = os.WriteFile(filepath.Join(root, "owned-containers.json"), encoded, 0600); err != nil {
			t.Fatal(err)
		}
	}
	urls := make([]string, 2)
	for rank, m := range managers {
		manifest := manifests[rank]
		if _, err := m.GroupOverlay("pin", OverlayRequest{Group: manifest.Topology.Group, TopologyHash: manifest.Fingerprint(), Endpoints: endpoints}); err != nil {
			t.Fatal(err)
		}
		if op := commitPrepared(t, m, digests[rank], "start", "prepare", 1); op.State != "succeeded" {
			t.Fatalf("start: %+v", op)
		}
		in := m.Snapshot().Instances[m.instanceID(digests[rank], "group")]
		urls[rank] = in.Resources[0].Endpoints["http"]
		if in.Generation != 2 || in.State != "ready" || m.Snapshot().Protocol != Protocol {
			t.Fatal("incorrect ready generation", in)
		}
		raw, e := os.ReadFile(filepath.Join(m.root, "ledger.json"))
		var persisted Ledger
		if e != nil || json.Unmarshal(raw, &persisted) != nil || persisted.Protocol != 4 {
			t.Fatal("missing on-disk downgrade fence", e)
		}
		info, err := m.driver.(NativeDriver).inspectContainer(ctx, in.Resources[0])
		if err != nil || isolatedContainer(info) != nil {
			t.Fatal("container escaped admission", err)
		}
	}
	check := func(rank int) {
		t.Helper()
		m := managers[rank]
		in := m.Snapshot().Instances[m.instanceID(digests[rank], "group")]
		req, _ := http.NewRequestWithContext(ctx, "GET", urls[rank], nil)
		req.Header.Set("X-Pantheon-App-Token", m.rpcCredential(in.ID, in.Digest, 2))
		client := &http.Client{Timeout: 3 * time.Second}
		response, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		body, err := io.ReadAll(response.Body)
		if err != nil || response.StatusCode != 200 || string(body) != m.node {
			t.Fatal("private loopback ingress", string(body), err)
		}
	}
	check(0)
	check(1)
	// Close/reopen the actual Runner. Preserve engines, restore only original ingress.
	first := managers[0]
	driver := first.driver.(NativeDriver)
	root0, owner, node, caps := first.root, first.owner, first.node, first.caps
	before := first.Snapshot().Instances[first.instanceID(digests[0], "group")]
	first.Close()
	if alive, err := driver.Alive(ctx, before.Resources[0]); err != nil || !alive {
		t.Fatal("Runner close stopped the consumer-independent engine", err)
	}
	reopened, err := Open(root0, owner, node, caps, NativeDriver{Engine: driver.Engine})
	if err != nil {
		t.Fatal(err)
	}
	managers[0] = reopened
	reopened.SetResourceSampler(resourceInventory)
	if op := submit(t, reopened, digests[0], "reconcile", "reconcile", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if err = reopened.driver.Probe(ctx, reopened.boundComponent(reopened.Snapshot().Installations[digests[0]].Definition.Components[0], before), reopened.paths(digests[0], "group"), before.Resources[0]); err == nil {
		t.Fatal("adopted missing ingress after Runner restart")
	}
	// A conflicting listener is retained; recovery must not change the URL or
	// restart the engine to work around it.
	conflict, err := net.Listen("tcp4", strings.TrimPrefix(urls[0], "http://"))
	if err != nil {
		t.Fatal(err)
	}
	if op := submit(t, reopened, digests[0], "recover-conflict", "recover", "group", 2); op.State != "failed" {
		conflict.Close()
		t.Fatal("recovery stole another listener", op)
	}
	conflict.Close()
	for attempt := 0; attempt < 2; attempt++ {
		if op := submit(t, reopened, digests[0], fmt.Sprintf("recover-%d", attempt), "recover", "group", 2); op.State != "succeeded" {
			t.Fatal("original ingress recovery", op)
		}
		check(0)
		current := reopened.Snapshot().Instances[before.ID]
		if current.State != "ready" || current.Generation != before.Generation ||
			!reflect.DeepEqual(current.Resources, before.Resources) || !reflect.DeepEqual(current.Reservations, before.Reservations) {
			t.Fatal("recovery changed original identity or resources", current)
		}
		actual, e := reopened.driver.(NativeDriver).inspectContainer(ctx, current.Resources[0])
		if e != nil || actual.ID != before.Resources[0].ContainerID || actual.State.StartedAt != before.Resources[0].ContainerStartedAt {
			t.Fatal("recovery restarted original engine", e)
		}
	}
	// A replaced peer route must not be republished as the old model. This
	// changes only this test's owned namespace; stop must still clean it safely.
	store0 := overlayStore(filepath.Join(reopened.root, "group-overlays"), reopened.owner, reopened.node)
	network0, _, e := store0.Network(manifests[0])
	if e != nil {
		t.Fatal(e)
	}
	reopened.Close()
	if out, e := exec.CommandContext(ctx, "ip", "netns", "exec", network0.Namespace,
		"wg", "set", "wg0", "peer", endpoints[1].PublicKey, "allowed-ips", "0.0.0.0/0").CombinedOutput(); e != nil {
		t.Fatal("test route mutation", string(out), e)
	}
	reopened, err = Open(root0, owner, node, caps, NativeDriver{Engine: driver.Engine})
	if err != nil {
		t.Fatal(err)
	}
	managers[0] = reopened
	reopened.SetResourceSampler(resourceInventory)
	if op := submit(t, reopened, digests[0], "recover-mutated", "recover", "group", 2); op.State != "failed" || !strings.Contains(op.Error, "allowed-ips changed") {
		t.Fatal("recovery accepted replaced peer routes", op)
	}
	current := reopened.Snapshot().Instances[before.ID]
	if current.State == "ready" || !reflect.DeepEqual(current.Reservations, before.Reservations) {
		t.Fatal("failed recovery published or released the original engine", current)
	}
	if op := submit(t, reopened, digests[0], "reconcile-mutated", "reconcile", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, reopened, digests[0], "stop", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	check(1)
	second := managers[1]
	in := second.Snapshot().Instances[second.instanceID(digests[1], "group")]
	if err = second.driver.Stop(ctx, second.Snapshot().Installations[digests[1]].Definition.Components[0], in.Resources[0]); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, second, digests[1], "dead-reconcile", "reconcile", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	for rank, m := range managers {
		in := m.Snapshot().Instances[m.instanceID(digests[rank], "group")]
		if in.State != "stopped" || len(in.Resources) != 0 || len(in.Reservations) != 0 {
			t.Fatal("incomplete lifecycle cleanup", in)
		}
		s := overlayStore(filepath.Join(m.root, "group-overlays"), m.owner, m.node)
		state, _, e := s.Network(manifests[rank])
		if e != nil || state.Stage != "closed" {
			t.Fatal("network not closed", e)
		}
		view, e := (groupnetwork.Commands{}).Observe(ctx, *state)
		if e != nil || view.Identity != "" || view.Host != nil {
			t.Fatal("kernel resources survived", e)
		}
	}
	t.Log("real NativeDriver/Manager: prepared admission, isolated Docker start, authenticated loopback ingress, Runner restart, occupied-port refusal, same-generation recovery without engine restart, changed-peer rejection, consumer preservation, stop and dead-engine reconciliation passed")
}
