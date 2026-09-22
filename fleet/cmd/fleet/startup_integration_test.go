package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Observe the first registry event, not a later heartbeat that can conceal a
// startup race. Consumers must be able to use lifecycle RPC immediately.
func TestFirstNodeAdvertisementIsReady(t *testing.T) {
	const fid, nid = "startup", "new-node"
	dir := t.TempDir()
	a, err := auth.Bootstrap(filepath.Join(dir, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	url := recoveryServer(t, a)
	observerCreds, err := a.MintFleetUser(fid)
	if err != nil {
		t.Fatal(err)
	}
	nodeCreds, err := a.MintFleetNode(fid, nid)
	if err != nil {
		t.Fatal(err)
	}
	creds := filepath.Join(dir, "observer.creds")
	if err := writePrivateFile(creds, observerCreds); err != nil {
		t.Fatal(err)
	}
	var nc *nats.Conn
	waitRecovery(t, "NATS did not start", func() bool {
		nc, err = nats.Connect(url, nats.UserCredentials(creds), nats.CustomInboxPrefix("_INBOX_"+fid))
		return err == nil
	})
	defer nc.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if _, err := registry.Open(ctx, nc, fid, nid, 30*time.Second); err != nil {
		t.Fatal(err)
	}
	js, err := jetstream.New(nc)
	if err != nil {
		t.Fatal(err)
	}
	kv, err := js.KeyValue(ctx, proto.RegistryBucket(fid))
	if err != nil {
		t.Fatal(err)
	}
	watch, err := kv.Watch(ctx, nid, jetstream.UpdatesOnly())
	if err != nil {
		t.Fatal(err)
	}
	defer watch.Stop()
	controller := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/join" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(proto.JoinResponse{FleetID: fid, NatsURL: url, Creds: string(nodeCreds)})
	}))
	defer controller.Close()
	state := filepath.Join(dir, "node")
	if err := os.Mkdir(state, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, "node_id"), []byte(nid), 0600); err != nil {
		t.Fatal(err)
	}
	args, _ := json.Marshal([]string{"--controller", controller.URL, "--join-token", "test-only",
		"--state-dir", state, "--workdir", dir, "--no-dataplane", "--no-files", "--no-capture-setup"})
	child := exec.Command(os.Args[0], "-test.run=^TestNodeStartupChild$")
	child.Env = append(os.Environ(), "FLEET_TEST_STARTUP_ARGS="+string(args))
	log, err := os.Create(filepath.Join(dir, "runner.log"))
	if err != nil {
		t.Fatal(err)
	}
	child.Stdout, child.Stderr = log, log
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = child.Process.Kill(); _ = child.Wait(); _ = log.Close() })
	select {
	case entry := <-watch.Updates():
		if entry == nil {
			t.Fatal("no first registry event")
		}
		var record proto.Node
		if err := json.Unmarshal(entry.Value(), &record); err != nil {
			t.Fatal(err)
		}
		for _, capability := range []string{"app-lifecycle", "app-rpc-auth", "app-resources", "app-services", "app-recovery"} {
			if record.Capability.Runtimes[capability] != "1" {
				t.Fatalf("first advertisement missing %s: %+v", capability, record.Capability.Runtimes)
			}
		}
	case <-ctx.Done():
		output, _ := os.ReadFile(filepath.Join(dir, "runner.log"))
		t.Fatalf("node did not advertise: %s", output)
	}
	msg, err := nc.Request(proto.SubjNodeCmd(fid, nid), []byte(`{"type":"app_lifecycle","protocol":1,"method":"status"}`), 2*time.Second)
	if err != nil {
		t.Fatalf("advertised node cannot serve commands: %v", err)
	}
	var reply map[string]any
	if err := json.Unmarshal(msg.Data, &reply); err != nil {
		t.Fatal(err)
	}
	if reply["instances"] == nil || reply["error"] != nil {
		t.Fatalf("lifecycle unavailable: %s", msg.Data)
	}
}

func TestNodeStartupChild(t *testing.T) {
	raw := os.Getenv("FLEET_TEST_STARTUP_ARGS")
	if raw == "" {
		return
	}
	var args []string
	if err := json.Unmarshal([]byte(raw), &args); err != nil {
		t.Fatal(err)
	}
	cmdUp(args)
}
