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
