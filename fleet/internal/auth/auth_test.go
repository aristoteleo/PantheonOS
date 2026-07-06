package auth

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// natsServerBin returns the nats-server binary to test against, or "" to skip.
func natsServerBin() string {
	if p := os.Getenv("FLEET_TEST_NATS_SERVER"); p != "" {
		return p
	}
	if p, err := exec.LookPath("nats-server"); err == nil {
		return p
	}
	return ""
}

// TestAuthorityIsolation is the core security proof: a credential minted for one
// fleet can use its own KV bucket and subjects, but is denied access to another
// fleet's bucket and subjects by the server.
func TestAuthorityIsolation(t *testing.T) {
	bin := natsServerBin()
	if bin == "" {
		t.Skip("set FLEET_TEST_NATS_SERVER (or put nats-server on PATH) to run the auth integration test")
	}
	dir := t.TempDir()

	auth, err := Bootstrap(filepath.Join(dir, "state"))
	if err != nil {
		t.Fatal(err)
	}
	const port = "14533"
	cfgPath := filepath.Join(dir, "nats.conf")
	cfg := auth.ServerConfig("0.0.0.0:"+port, filepath.Join(dir, "js"))
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}

	srv := exec.Command(bin, "-c", cfgPath)
	srv.Stderr, srv.Stdout = os.Stderr, os.Stdout
	if err := srv.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = srv.Process.Kill() }()

	url := "nats://127.0.0.1:" + port
	writeCreds := func(fid string) string {
		creds, err := auth.MintFleetUser(fid)
		if err != nil {
			t.Fatal(err)
		}
		p := filepath.Join(dir, "fleet-"+fid+".creds")
		if err := os.WriteFile(p, creds, 0o600); err != nil {
			t.Fatal(err)
		}
		return p
	}
	credsA := writeCreds("aaaa")
	credsB := writeCreds("bbbb") // minted so the account exists; used only for the positive B-side check

	// Wait for the server to accept an authenticated connection. Creds are scoped
	// to a per-fleet inbox (_INBOX_<fid>.>), so connections must use that prefix.
	var ncA *nats.Conn
	for i := 0; i < 50; i++ {
		if ncA, err = nats.Connect(url, nats.UserCredentials(credsA), nats.CustomInboxPrefix("_INBOX_aaaa")); err == nil {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if ncA == nil {
		t.Fatalf("could not connect with fleet-aaaa creds: %v", err)
	}
	defer ncA.Drain()

	// Each op gets a fresh context: a denied JS request never gets a reply and
	// burns its whole timeout, so a shared ctx would expire for later ops.
	freshCtx := func(d time.Duration) context.Context {
		c, cc := context.WithTimeout(context.Background(), d)
		t.Cleanup(cc)
		return c
	}
	jsA, err := jetstream.New(ncA)
	if err != nil {
		t.Fatal(err)
	}

	// Positive: A can create + use its OWN bucket.
	kvA, err := jsA.CreateOrUpdateKeyValue(freshCtx(5*time.Second), jetstream.KeyValueConfig{Bucket: "FLEET_aaaa_NODES"})
	if err != nil {
		t.Fatalf("A creating its own bucket: %v", err)
	}
	if _, err := kvA.Put(freshCtx(5*time.Second), "n1", []byte("hello")); err != nil {
		t.Fatalf("A Put: %v", err)
	}
	e, err := kvA.Get(freshCtx(5*time.Second), "n1")
	if err != nil || string(e.Value()) != "hello" {
		t.Fatalf("A Get: %v val=%q", err, e)
	}
	// Keys() drives an ordered push consumer with flow control — the riskiest
	// permission set (CONSUMER.CREATE + $JS.FC.*); the Registry listing needs it.
	keys, err := kvA.Keys(freshCtx(5 * time.Second))
	if err != nil {
		t.Fatalf("A Keys (ordered-consumer/flow-control perms): %v", err)
	}
	if len(keys) != 1 || keys[0] != "n1" {
		t.Fatalf("A Keys = %v, want [n1]", keys)
	}

	// NEGATIVE (the point): A must NOT be able to touch B's bucket. The denied
	// request times out, so use a short context.
	if _, err := jsA.CreateOrUpdateKeyValue(freshCtx(2*time.Second), jetstream.KeyValueConfig{Bucket: "FLEET_bbbb_NODES"}); err == nil {
		t.Fatal("SECURITY: fleet-aaaa was able to create fleet-bbbb's bucket")
	} else {
		t.Logf("denied (expected): A creating B's bucket -> %v", err)
	}

	// NEGATIVE: A publishing to B's core subjects must raise a permissions error.
	permErr := make(chan error, 4)
	ncErrH, err := nats.Connect(url, nats.UserCredentials(credsA), nats.CustomInboxPrefix("_INBOX_aaaa"),
		nats.ErrorHandler(func(_ *nats.Conn, _ *nats.Subscription, e error) {
			if strings.Contains(strings.ToLower(e.Error()), "permission") {
				select {
				case permErr <- e:
				default:
				}
			}
		}))
	if err != nil {
		t.Fatal(err)
	}
	defer ncErrH.Drain()
	_ = ncErrH.Publish("fleet.bbbb.node.x.cmd", []byte("intrusion"))
	_ = ncErrH.Flush()
	select {
	case e := <-permErr:
		t.Logf("denied (expected): A publishing to fleet.bbbb -> %v", e)
	case <-time.After(2 * time.Second):
		t.Fatal("SECURITY: no permissions violation when fleet-aaaa published to fleet.bbbb")
	}

	// Sanity: A publishing to its OWN subject is fine (no error fires).
	_ = ncErrH.Publish("fleet.aaaa.node.x.cmd", []byte("ok"))
	_ = ncErrH.Flush()
	select {
	case e := <-permErr:
		t.Fatalf("A wrongly denied on its OWN subject: %v", e)
	case <-time.After(500 * time.Millisecond):
	}

	// And B's creds can use B's bucket (proves per-fleet minting works both ways).
	ncB, err := nats.Connect(url, nats.UserCredentials(credsB), nats.CustomInboxPrefix("_INBOX_bbbb"))
	if err != nil {
		t.Fatal(err)
	}
	defer ncB.Drain()
	jsB, _ := jetstream.New(ncB)
	if _, err := jsB.CreateOrUpdateKeyValue(freshCtx(5*time.Second), jetstream.KeyValueConfig{Bucket: "FLEET_bbbb_NODES"}); err != nil {
		t.Fatalf("B creating its own bucket: %v", err)
	}
}

// TestNodeScopeIsolation is the P1 proof: a narrow MintFleetNode credential can
// serve its OWN cmd subject, report transfer progress, write ONLY its own registry
// record and read peers' — but the server DENIES it commanding another node,
// eavesdropping on another's cmd subject, overwriting another's record, or purging
// the bucket. So a compromised Node cannot move laterally to the rest of the fleet.
func TestNodeScopeIsolation(t *testing.T) {
	bin := natsServerBin()
	if bin == "" {
		t.Skip("set FLEET_TEST_NATS_SERVER (or put nats-server on PATH) to run the node-scope test")
	}
	dir := t.TempDir()
	authority, err := Bootstrap(filepath.Join(dir, "state"))
	if err != nil {
		t.Fatal(err)
	}
	const port = "14534"
	cfgPath := filepath.Join(dir, "nats.conf")
	if err := os.WriteFile(cfgPath, []byte(authority.ServerConfig("0.0.0.0:"+port, filepath.Join(dir, "js"))), 0o600); err != nil {
		t.Fatal(err)
	}
	srv := exec.Command(bin, "-c", cfgPath)
	srv.Stderr, srv.Stdout = os.Stderr, os.Stdout
	if err := srv.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = srv.Process.Kill() }()

	url := "nats://127.0.0.1:" + port
	const fid, nodeA, nodeB = "cccc", "nodeA", "nodeB"
	writeCreds := func(name string, creds []byte, err error) string {
		if err != nil {
			t.Fatal(err)
		}
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, creds, 0o600); err != nil {
			t.Fatal(err)
		}
		return p
	}
	ac, aerr := authority.MintFleetUser(fid)
	agentCreds := writeCreds("agent.creds", ac, aerr)
	nc, nerr := authority.MintFleetNode(fid, nodeA)
	nodeCreds := writeCreds("node.creds", nc, nerr)

	freshCtx := func(d time.Duration) context.Context {
		c, cc := context.WithTimeout(context.Background(), d)
		t.Cleanup(cc)
		return c
	}

	// Broad agent connects first: it creates the bucket + seeds a peer (nodeB)
	// record, giving the narrow node something to read and something to try (and be
	// denied) to overwrite.
	var ncAgent *nats.Conn
	for i := 0; i < 50; i++ {
		if ncAgent, err = nats.Connect(url, nats.UserCredentials(agentCreds), nats.CustomInboxPrefix("_INBOX_"+fid)); err == nil {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if ncAgent == nil {
		t.Fatalf("agent connect: %v", err)
	}
	defer ncAgent.Drain()
	jsAgent, _ := jetstream.New(ncAgent)
	kvAgent, err := jsAgent.CreateOrUpdateKeyValue(freshCtx(5*time.Second), jetstream.KeyValueConfig{Bucket: "FLEET_" + fid + "_NODES"})
	if err != nil {
		t.Fatalf("agent create bucket: %v", err)
	}
	if _, err := kvAgent.Put(freshCtx(5*time.Second), nodeB, []byte("peerB")); err != nil {
		t.Fatalf("seed nodeB record: %v", err)
	}

	// Connect as the NARROW node; capture async permission violations.
	permErr := make(chan error, 8)
	ncNode, err := nats.Connect(url, nats.UserCredentials(nodeCreds), nats.CustomInboxPrefix("_INBOX_"+fid),
		nats.ErrorHandler(func(_ *nats.Conn, _ *nats.Subscription, e error) {
			if strings.Contains(strings.ToLower(e.Error()), "permission") {
				select {
				case permErr <- e:
				default:
				}
			}
		}))
	if err != nil {
		t.Fatal(err)
	}
	defer ncNode.Drain()
	drain := func() {
		for {
			select {
			case <-permErr:
			default:
				return
			}
		}
	}
	expectDenied := func(label string, act func()) {
		drain()
		act()
		_ = ncNode.Flush()
		select {
		case e := <-permErr:
			t.Logf("denied (expected): %s -> %v", label, e)
		case <-time.After(2 * time.Second):
			t.Fatalf("SECURITY: %s was NOT denied", label)
		}
	}
	expectAllowed := func(label string, act func()) {
		drain()
		act()
		_ = ncNode.Flush()
		select {
		case e := <-permErr:
			t.Fatalf("%s wrongly denied: %v", label, e)
		case <-time.After(500 * time.Millisecond):
		}
	}

	// POSITIVE — the node's legitimate operations.
	if _, err := ncNode.SubscribeSync("fleet." + fid + ".node." + nodeA + ".cmd"); err != nil {
		t.Fatalf("node sub OWN cmd: %v", err)
	}
	_ = ncNode.Flush()
	expectAllowed("node pub OWN transfer progress", func() {
		_ = ncNode.Publish("fleet."+fid+".transfer.t1.progress", []byte("p"))
	})
	jsNode, _ := jetstream.New(ncNode)
	kvNode, err := jsNode.CreateOrUpdateKeyValue(freshCtx(5*time.Second), jetstream.KeyValueConfig{Bucket: "FLEET_" + fid + "_NODES"})
	if err != nil {
		t.Fatalf("node open registry (CreateOrUpdate, like registry.Open): %v", err)
	}
	if _, err := kvNode.Put(freshCtx(5*time.Second), nodeA, []byte("meA")); err != nil {
		t.Fatalf("node write OWN registry key: %v", err)
	}
	if e, err := kvNode.Get(freshCtx(5*time.Second), nodeB); err != nil || string(e.Value()) != "peerB" {
		t.Fatalf("node read peer record (needed for transfers): %v", err)
	}

	// NEGATIVE — lateral moves the server must deny.
	expectDenied("node commands a PEER (fleet."+fid+".node."+nodeB+".cmd)", func() {
		_ = ncNode.Publish("fleet."+fid+".node."+nodeB+".cmd", []byte("pwn"))
	})
	expectDenied("node overwrites a PEER's registry record", func() {
		_ = ncNode.Publish("$KV.FLEET_"+fid+"_NODES."+nodeB, []byte("tamper"))
	})
	expectDenied("node purges the registry bucket", func() {
		_ = ncNode.Publish("$JS.API.STREAM.PURGE.KV_FLEET_"+fid+"_NODES", []byte("{}"))
	})
	// A denied SUB also raises the async permission error.
	drain()
	_, _ = ncNode.Subscribe("fleet."+fid+".node."+nodeB+".cmd", func(*nats.Msg) {})
	_ = ncNode.Flush()
	select {
	case e := <-permErr:
		t.Logf("denied (expected): node eavesdrops on peer cmd -> %v", e)
	case <-time.After(2 * time.Second):
		t.Fatal("SECURITY: node was NOT denied subscribing to a peer's cmd subject")
	}
}
