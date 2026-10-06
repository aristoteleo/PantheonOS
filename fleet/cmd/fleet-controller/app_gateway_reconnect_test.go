package main

import (
	"context"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// The node remains subscribed to a real authenticated broker while only the
// gateway loses its transport. A timed-out invocation must never be replayed
// when that transport returns; successful new calls must still work.
func TestAppGatewayReconnectDoesNotReplayExpiredRequests(t *testing.T) {
	binary, err := exec.LookPath("nats-server")
	if err != nil {
		t.Skip("nats-server required")
	}
	root := t.TempDir()
	authority, err := auth.Bootstrap(filepath.Join(root, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := l.Addr().String()
	l.Close()
	config := filepath.Join(root, "nats.conf")
	if err := os.WriteFile(config, []byte(authority.ServerConfig(address, filepath.Join(root, "js"))), 0600); err != nil {
		t.Fatal(err)
	}
	process := exec.Command(binary, "-c", config)
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { process.Process.Kill(); process.Wait() }()
	const owner = "f_0123456789abcdef"
	creds, err := authority.MintFleetNode(owner, "provider-node")
	if err != nil {
		t.Fatal(err)
	}
	var node *nats.Conn
	until := func(what string, ready func() bool) {
		t.Helper()
		for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
			if ready() {
				return
			}
			time.Sleep(10 * time.Millisecond)
		}
		t.Fatal("timed out: " + what)
	}
	until("broker startup", func() bool {
		node, err = nats.Connect("nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix("_INBOX_"+owner))
		return err == nil
	})
	defer node.Close()
	var mu sync.Mutex
	var delivered []string
	_, err = node.Subscribe(proto.SubjNodeCmd(owner, "provider-node"), func(m *nats.Msg) {
		mu.Lock()
		delivered = append(delivered, string(m.Data))
		mu.Unlock()
		_ = m.Respond([]byte(`{"ok":true}`))
	})
	if err != nil {
		t.Fatal(err)
	}
	if err = node.Flush(); err != nil {
		t.Fatal(err)
	}

	proxy, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	var transportMu sync.Mutex
	paused := false
	connections := map[net.Conn]bool{}
	var copies sync.WaitGroup
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		for {
			incoming, err := proxy.Accept()
			if err != nil {
				return
			}
			transportMu.Lock()
			if paused {
				transportMu.Unlock()
				incoming.Close()
				continue
			}
			upstream, err := net.DialTimeout("tcp", address, time.Second)
			if err != nil {
				transportMu.Unlock()
				incoming.Close()
				continue
			}
			connections[incoming] = true
			connections[upstream] = true
			copies.Add(1)
			transportMu.Unlock()
			go func() {
				defer copies.Done()
				done := make(chan struct{})
				go func() { io.Copy(upstream, incoming); upstream.Close(); close(done) }()
				io.Copy(incoming, upstream)
				incoming.Close()
				<-done
				transportMu.Lock()
				delete(connections, incoming)
				delete(connections, upstream)
				transportMu.Unlock()
			}()
		}
	}()
	defer func() {
		proxy.Close()
		<-stopped
		transportMu.Lock()
		for c := range connections {
			c.Close()
		}
		transportMu.Unlock()
		copies.Wait()
	}()
	gateway, err := connectAppGateway(authority, owner, "nats://"+proxy.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer gateway.Close()
	request := func(value string, timeout time.Duration) error {
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		_, err := gateway.RequestWithContext(ctx, proto.SubjNodeCmd(owner, "provider-node"), []byte(value))
		return err
	}
	if err = request("before-outage", time.Second); err != nil {
		t.Fatal(err)
	}
	transportMu.Lock()
	paused = true
	for c := range connections {
		c.Close()
	}
	transportMu.Unlock()
	until("gateway reconnecting", func() bool { return gateway.IsReconnecting() })
	if err = request("expired-during-outage", 100*time.Millisecond); err == nil {
		t.Fatal("disconnected request succeeded")
	}
	transportMu.Lock()
	paused = false
	transportMu.Unlock()
	until("gateway reconnected", func() bool { return gateway.IsConnected() })
	// A reply from this same subscription is a barrier after any queued request.
	if err = request("after-reconnect", time.Second); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(delivered) != 2 || delivered[0] != "before-outage" || delivered[1] != "after-reconnect" {
		t.Fatalf("node received abandoned request after reconnect: %v", delivered)
	}
}
