package appdirect

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
	"github.com/libp2p/go-libp2p/core/network"
)

type fixture struct {
	ctx              context.Context
	server           *Server
	client, outsider *dataplane.Plane
	binding          apptransport.Binding
	online, current  atomic.Bool
	uses             atomic.Int32
	calls            atomic.Int32
}

func setup(t *testing.T, handler http.Handler) *fixture {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	t.Cleanup(cancel)
	f := &fixture{ctx: ctx, binding: apptransport.Binding{Fleet: "fleet-a", Node: "node-a", Instance: "instance-a", Revision: strings.Repeat("a", 64), Generation: 7, Component: "model", Port: "http"}}
	f.online.Store(true)
	f.current.Store(true)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { f.calls.Add(1); handler.ServeHTTP(w, r) }))
	t.Cleanup(upstream.Close)
	planes := make([]*dataplane.Plane, 3)
	for i := range planes {
		p, err := dataplane.NewAppClient(ctx)
		if err != nil {
			t.Fatal(err)
		}
		planes[i] = p
		t.Cleanup(func() { _ = p.Close() })
	}
	f.client, f.outsider = planes[1], planes[2]
	f.server = New(ctx, planes[0], func(b apptransport.Binding) (string, func(), error) {
		if b != f.binding || !f.current.Load() {
			return "", nil, fmt.Errorf("stale generation")
		}
		f.uses.Add(1)
		return upstream.URL, func() { f.uses.Add(-1) }, nil
	}, f.online.Load)
	t.Cleanup(func() { cancel(); eventually(t, func() bool { return f.uses.Load() == 0 && len(f.server.slots) == 0 }) })
	return f
}

func eventually(t *testing.T, condition func() bool) {
	t.Helper()
	until := time.Now().Add(3 * time.Second)
	for !condition() {
		if time.Now().After(until) {
			t.Fatal("condition did not settle")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func (f *fixture) request() Request {
	return Request{Binding: f.binding, Peer: f.client.ID(), Credential: strings.Repeat("credential", 8), Expires: time.Now().Add(time.Minute).Unix()}
}
func (f *fixture) grant(t *testing.T) Grant {
	t.Helper()
	g, err := f.server.Issue(f.request())
	if err != nil {
		t.Fatal(err)
	}
	return g
}
func (f *fixture) dial(t *testing.T, g Grant) net.Conn {
	t.Helper()
	c, err := Dial(f.ctx, f.client, g)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}
func exchange(t *testing.T, conn net.Conn, req *http.Request) (*http.Response, string) {
	t.Helper()
	if err := req.Write(conn); err != nil {
		t.Fatal(err)
	}
	res, err := http.ReadResponse(bufio.NewReader(conn), req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	data, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatal(err)
	}
	return res, string(data)
}

func TestDirectHTTPIdentityAndSingleUse(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for _, key := range []string{"Authorization", "Cookie", "X-Fleet-RPC-Token", "X-Pantheon-Other", "Forwarded", "X-Forwarded-For"} {
			if r.Header.Get(key) != "" {
				t.Errorf("untrusted identity header passed: %s", key)
			}
		}
		if r.Header.Get("X-Pantheon-App-Token") != strings.Repeat("credential", 8) {
			t.Error("wrong scoped identity")
		}
		w.Header().Set("Set-Cookie", "secret=x")
		data, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			return
		}
		_, _ = w.Write(data)
	}))
	g := f.grant(t)
	if _, err := Dial(f.ctx, f.outsider, g); err == nil {
		t.Fatal("other peer used stolen grant")
	}
	conn := f.dial(t, g)
	body := strings.Repeat("binary\x00body", 10000)
	req, _ := http.NewRequest("POST", "http://untrusted.test/infer?x=1", strings.NewReader(body))
	for _, key := range []string{"Authorization", "Cookie", "X-Fleet-RPC-Token", "X-Pantheon-App-Token", "X-Pantheon-Other", "Forwarded", "X-Forwarded-For"} {
		req.Header.Set(key, "forged")
	}
	res, result := exchange(t, conn, req)
	if res.StatusCode != 200 || result != body || res.Header.Get("Set-Cookie") != "" {
		t.Fatal("direct HTTP body/identity isolation failed")
	}
	if _, err := Dial(f.ctx, f.client, g); err == nil {
		t.Fatal("grant reused")
	}
	if f.calls.Load() != 1 {
		t.Fatal("unauthorized request reached engine")
	}
}

func TestDirectRevalidatesExactGenerationForEveryRequest(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) }))
	conn := f.dial(t, f.grant(t))
	req, _ := http.NewRequest("GET", "http://app.test/route-state", nil)
	res, _ := exchange(t, conn, req)
	if res.StatusCode != 200 {
		t.Fatal(res.StatusCode)
	}
	f.current.Store(false)
	res, _ = exchange(t, conn, req)
	if res.StatusCode != 409 || f.calls.Load() != 1 {
		t.Fatal("stale generation reached engine")
	}
}

func TestDirectRejectsGrantBeforeInference(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("unexpected engine call") }))
	for _, change := range []func(*Request){
		func(q *Request) { q.Fleet = "other" }, func(q *Request) { q.Node = "other" }, func(q *Request) { q.Generation++ },
		func(q *Request) { q.Expires = time.Now().Add(-time.Second).Unix() }, func(q *Request) { q.Expires = time.Now().Add(time.Hour).Unix() },
		func(q *Request) { q.Peer = "not-a-peer" }, func(q *Request) { q.Credential = "" }, func(q *Request) { q.Port = "admin" },
	} {
		q := f.request()
		change(&q)
		if _, err := f.server.Issue(q); err == nil {
			t.Fatal("invalid grant accepted")
		}
	}
	g := f.grant(t)
	f.current.Store(false)
	if _, err := Dial(f.ctx, f.client, g); err == nil {
		t.Fatal("connected stale generation")
	}
	f.current.Store(true)
	f.online.Store(false)
	if _, err := f.server.Issue(f.request()); err == nil {
		t.Fatal("disconnected node issued grant")
	}
}

func TestDirectRefusesBrowserAndManagementHeaders(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("browser/proxy call reached engine") }))
	for _, header := range []string{"Origin", "Sec-Fetch-Site", "Upgrade"} {
		conn := f.dial(t, f.grant(t))
		req, _ := http.NewRequest("POST", "http://app.test/infer", strings.NewReader("prompt"))
		req.Header.Set(header, "untrusted")
		res, _ := exchange(t, conn, req)
		if res.StatusCode != 403 {
			t.Fatal("browser request accepted", header)
		}
		_ = conn.Close()
	}
}

func TestDirectStreamCancellationExpiryAndRevocation(t *testing.T) {
	for _, mode := range []string{"cancel", "expiry", "offline"} {
		t.Run(mode, func(t *testing.T) {
			ended := make(chan struct{})
			f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				defer close(ended)
				_, _ = io.Copy(io.Discard, r.Body)
				_, _ = w.Write([]byte("data: first\n\n"))
				w.(http.Flusher).Flush()
				<-r.Context().Done()
			}))
			q := f.request()
			if mode == "expiry" {
				q.Expires = time.Now().Add(2 * time.Second).Unix()
			}
			g, err := f.server.Issue(q)
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithCancel(f.ctx)
			defer cancel()
			conn, err := Dial(ctx, f.client, g)
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			req, _ := http.NewRequest("POST", "http://app.test/infer", strings.NewReader("prompt"))
			if err := req.Write(conn); err != nil {
				t.Fatal(err)
			}
			response, err := http.ReadResponse(bufio.NewReader(conn), req)
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			first := make([]byte, len("data: first\n\n"))
			if _, err := io.ReadFull(response.Body, first); err != nil || string(first) != "data: first\n\n" {
				t.Fatal("stream buffered", err)
			}
			if mode == "cancel" {
				cancel()
			}
			if mode == "offline" {
				f.online.Store(false)
			}
			select {
			case <-ended:
			case <-time.After(3 * time.Second):
				t.Fatal("upstream request not cancelled")
			}
			eventually(t, func() bool { return f.uses.Load() == 0 && len(f.server.slots) == 0 })
			if f.calls.Load() != 1 {
				t.Fatal("inference replayed")
			}
		})
	}
}

func TestDirectRequiresExactPeerAndNoCircuitRelay(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	g := f.grant(t)
	g.Peer = f.outsider.ID()
	if _, err := Dial(f.ctx, f.client, g); err == nil {
		t.Fatal("mixed target identity")
	}
	g = f.grant(t)
	g.Addresses = []string{"/ip4/127.0.0.1/udp/1234/quic-v1/p2p/" + f.outsider.ID() + "/p2p-circuit/p2p/" + g.Peer}
	if _, err := Dial(f.ctx, f.client, g); err == nil {
		t.Fatal("direct call accepted relay-only target")
	}
}

func TestDirectGrantConsumptionIsAtomicAndBounded(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	g := f.grant(t)
	results := make(chan bool, 16)
	for range 16 {
		go func() {
			conn, err := Dial(f.ctx, f.client, g)
			if err == nil {
				_ = conn.Close()
			}
			results <- err == nil
		}()
	}
	success := 0
	for range 16 {
		if <-results {
			success++
		}
	}
	if success != 1 {
		t.Fatalf("grant consumed %d times", success)
	}
	for range 1024 {
		f.grant(t)
	}
	if _, err := f.server.Issue(f.request()); err == nil {
		t.Fatal("unbounded pending grants")
	}
}

type limitedConn struct{ network.Conn }

func (limitedConn) Stat() network.ConnStats {
	return network.ConnStats{Stats: network.Stats{Limited: true}}
}

type limitedStream struct {
	network.Stream
	reset bool
}

func (s *limitedStream) Conn() network.Conn { return limitedConn{} }
func (s *limitedStream) Reset() error       { s.reset = true; return nil }

func TestDirectServerRejectsRelayedStreamBeforeReadingGrant(t *testing.T) {
	stream := &limitedStream{}
	// No server/stream I/O fields are initialized: rejection must happen before
	// consuming capacity, touching grant state or reading application bytes.
	(&Server{}).serve(stream)
	if !stream.reset {
		t.Fatal("relayed connection was not rejected")
	}
}
