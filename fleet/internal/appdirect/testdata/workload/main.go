// A loopback-only test control plane for the Python/Fleet integration suite.
// It is not built into Fleet, and uses only synthetic credentials and bindings.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	var input struct{ Endpoint string }
	if json.NewDecoder(os.Stdin).Decode(&input) != nil || !strings.HasPrefix(input.Endpoint, "http://127.0.0.1:") {
		panic("expected loopback test backend")
	}
	p, err := dataplane.NewAppClient(ctx)
	if err != nil {
		panic(err)
	}
	defer p.Close()
	binding := apptransport.Binding{Fleet: "fleet-test", Node: "mac-node", Instance: "instance", Revision: strings.Repeat("a", 64), Generation: 2, Component: "backend", Port: "http"}
	var uses, grants atomic.Int32
	var current atomic.Bool
	current.Store(true)
	s := appdirect.New(ctx, p, func(b apptransport.Binding) (string, func(), error) {
		if b != binding || !current.Load() {
			return "", nil, fmt.Errorf("stale")
		}
		uses.Add(1)
		return input.Endpoint, func() { uses.Add(-1) }, nil
	}, func() bool { return true })
	mux := http.NewServeMux()
	mux.HandleFunc("/grant", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Peer string `json:"peer_id"`
		}
		if json.NewDecoder(io.LimitReader(r.Body, 8192)).Decode(&body) != nil {
			http.Error(w, "invalid test input", 400)
			return
		}
		g, err := s.Issue(appdirect.Request{Binding: binding, Peer: body.Peer, Credential: strings.Repeat("test-credential-", 4), Expires: time.Now().Add(time.Minute).Unix()})
		if err != nil {
			http.Error(w, "grant unavailable", 409)
			return
		}
		grants.Add(1)
		switch r.URL.Query().Get("mode") {
		case "token":
			g.Token = strings.Repeat("0", 64)
		case "stale":
			current.Store(false)
		case "malformed":
			g.Addresses = []string{"/invalid/p2p/" + g.Peer}
		case "unreachable":
			g.Addresses = []string{"/ip4/127.0.0.1/udp/1/quic-v1/p2p/" + g.Peer}
		case "relay-only":
			g.Addresses = []string{"/ip4/127.0.0.1/udp/1/quic-v1/p2p/" + g.Peer + "/p2p-circuit/p2p/" + g.Peer}
		}
		out := map[string]any{}
		encoded, _ := json.Marshal(g)
		_ = json.Unmarshal(encoded, &out)
		out["binding"] = binding
		_ = json.NewEncoder(w).Encode(out)
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"uses": uses.Load(), "grants": grants.Load()})
	})
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		panic(err)
	}
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 2 * time.Second}
	defer server.Close()
	go func() { _ = server.Serve(l) }()
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"control": "http://" + l.Addr().String()})
	go func() { _, _ = io.Copy(io.Discard, os.Stdin); cancel() }()
	<-ctx.Done()
}
