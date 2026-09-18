package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

func recoveryServer(t *testing.T, a *auth.Authority) string {
	t.Helper()
	bin := os.Getenv("FLEET_TEST_NATS_SERVER")
	if bin == "" {
		bin, _ = exec.LookPath("nats-server")
	}
	if bin == "" {
		t.Skip("set FLEET_TEST_NATS_SERVER to run reconnect integration test")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := listener.Addr().String()
	_ = listener.Close()
	dir := t.TempDir()
	cfg := filepath.Join(dir, "nats.conf")
	if err := os.WriteFile(cfg, []byte(a.ServerConfig(addr, filepath.Join(dir, "js"))), 0600); err != nil {
		t.Fatal(err)
	}
	log, err := os.Create(filepath.Join(dir, "nats.log"))
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(bin, "-c", cfg)
	cmd.Stdout, cmd.Stderr = log, log
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait(); _ = log.Close() })
	return "nats://" + addr
}

func waitRecovery(t *testing.T, message string, ready func() bool) {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		if ready() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal(message)
}

// Reproduces the laptop outage: access JWT expires while /token is unreachable,
// more than 60 auth reconnect attempts fail, then the controller recovers. The
// SAME runner connection must resume commands and reappear in the registry.
func TestExpiredCredentialsRecoverAfterControllerOutage(t *testing.T) {
	const fid, nid = "recovery", "laptop"
	dir := t.TempDir()
	a, err := auth.Bootstrap(filepath.Join(dir, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	url := recoveryServer(t, a)
	longCreds, err := a.MintFleetNode(fid, nid)
	if err != nil {
		t.Fatal(err)
	}
	observerCreds, err := a.MintFleetUser(fid)
	if err != nil {
		t.Fatal(err)
	}
	previous := auth.AccessTTL
	auth.AccessTTL = 3 * time.Second
	shortCreds, err := a.MintFleetNode(fid, nid)
	auth.AccessTTL = previous
	if err != nil {
		t.Fatal(err)
	}
	credsPath := filepath.Join(dir, "fleet.creds")
	if err := writePrivateFile(credsPath, shortCreds); err != nil {
		t.Fatal(err)
	}
	observerPath := filepath.Join(dir, "observer.creds")
	if err := writePrivateFile(observerPath, observerCreds); err != nil {
		t.Fatal(err)
	}
	var observer *nats.Conn
	waitRecovery(t, "server failed to start", func() bool {
		observer, err = nats.Connect(url, nats.UserCredentials(observerPath), nats.CustomInboxPrefix("_INBOX_"+fid))
		return err == nil
	})
	defer observer.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var available atomic.Bool
	controller := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !available.Load() {
			http.Error(w, "network unavailable", http.StatusServiceUnavailable)
			return
		}
		_ = json.NewEncoder(w).Encode(proto.TokenResponse{Creds: string(longCreds), RefreshToken: "rotated"})
	}))
	defer controller.Close()
	kick := make(chan struct{}, 1)
	opts := fleetNATSOptions(fleetRecoveryOptions(ctx, cancel, kick, true))
	opts = append(opts, nats.UserCredentials(credsPath), nats.CustomInboxPrefix("_INBOX_"+fid),
		nats.ReconnectWait(10*time.Millisecond), nats.ReconnectJitter(0, 0))
	nc, err := nats.Connect(url, opts...)
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	reg, err := registry.Open(ctx, nc, fid, nid, 300*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	rec := proto.Node{NodeID: nid, Name: "test laptop", State: proto.State{Status: proto.StatusOnline}}
	r := runner.New(nc, fid, nid, reg, nil, &rec)
	_, err = r.Serve()
	if err != nil {
		t.Fatal(err)
	}
	if err := nc.Flush(); err != nil {
		t.Fatal(err)
	}
	heartbeatDone := make(chan struct{})
	go func() { defer close(heartbeatDone); r.Heartbeat(ctx, 50*time.Millisecond) }()
	defer func() { cancel(); <-heartbeatDone }()
	key, err := node.LoadOrCreateKey(dir)
	if err != nil {
		t.Fatal(err)
	}
	state := fleetState{ControllerURL: controller.URL, FleetID: fid, NatsURL: url, RefreshToken: "original"}
	refreshDone := make(chan struct{})
	go func() {
		defer close(refreshDone)
		refreshCredsLoop(ctx, cancel, kick, controller.URL, fid, state.RefreshToken, node.PubB64(key), key, credsPath, dir, state, func() {
			if nc.IsReconnecting() {
				_ = nc.ForceReconnect()
			}
		})
	}()
	defer func() { cancel(); <-refreshDone }()
	readReg, err := registry.Open(ctx, observer, fid, nid, 300*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	waitRecovery(t, "initial heartbeat missing", func() bool { _, err := readReg.Get(ctx, nid); return err == nil })
	waitRecovery(t, "did not exercise repeated expired-auth reconnects", func() bool { return nc.Stats().Reconnects > 65 })
	if nc.IsClosed() || ctx.Err() != nil {
		t.Fatal("runner gave up during outage")
	}
	waitRecovery(t, "offline node did not expire", func() bool { _, err := readReg.Get(ctx, nid); return err != nil })
	available.Store(true)
	waitRecovery(t, "same connection did not recover", nc.IsConnected)
	waitRecovery(t, "heartbeat did not re-register same node", func() bool { n, err := readReg.Get(ctx, nid); return err == nil && n.NodeID == nid })
	msg, err := observer.Request(proto.SubjNodeCmd(fid, nid), []byte(`{"type":"ping"}`), time.Second)
	if err != nil {
		t.Fatalf("restored command subscription: %v", err)
	}
	if string(msg.Data) != fmt.Sprintf(`{"pong":%q}`, nid) {
		t.Fatalf("reply=%s", msg.Data)
	}
	saved, found, err := loadFleetState(dir)
	if err != nil || !found || saved.RefreshToken != "rotated" {
		t.Fatalf("rotated login not persisted: %v", err)
	}
	t.Logf("same node/connection recovered after %d failed reconnects; heartbeat and command subscription restored", nc.Stats().Reconnects)
}
