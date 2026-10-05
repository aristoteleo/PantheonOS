package appdirect

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

func scopedRequest(f *fixture) Request {
	q := f.request()
	q.Dependency = &Dependency{ID: strings.Repeat("a", 64), Proof: strings.Repeat("b", 64),
		Consumer: apptransport.InstanceIdentity{Fleet: q.Fleet, Node: "consumer-node", Instance: "consumer-app", Revision: strings.Repeat("c", 64), Generation: 1},
		HTTP: apptransport.HTTPDependency{Credential: q.Credential,
			Rules:   []apptransport.HTTPRule{{Method: "POST", Path: "/v1/chat/completions"}},
			Headers: map[string]string{"X-Model-Config": "original"}}}
	return q
}

func TestDependencyGrantRequiresVerifierAndFreezesScope(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	q := scopedRequest(f)
	if _, err := f.server.Issue(q); !errors.Is(err, ErrInvalidGrant) {
		t.Fatalf("dependency without verifier was accepted: %v", err)
	}
	f.server.SetDependencyCheck(func(context.Context, Request) error { return nil })
	g, err := f.server.Issue(q)
	if err != nil {
		t.Fatal(err)
	}
	q.Dependency.HTTP.Rules[0].Path = "/rpc"
	q.Dependency.HTTP.Headers["X-Model-Config"] = "replacement"
	q.Dependency.Consumer.Generation++
	issued, ok := f.server.consume(g.Token, q.Peer)
	if !ok || issued.Dependency.Consumer.Generation != 1 || issued.Dependency.HTTP.Rules[0].Path != "/v1/chat/completions" || issued.Dependency.HTTP.Headers["X-Model-Config"] != "original" {
		t.Fatal("caller changed issued dependency scope")
	}
}

func TestDependencyRevokedBeforeQUICAcknowledgement(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	f.server.SetDependencyCheck(func(context.Context, Request) error { return ErrGrantRejected })
	g, err := f.server.Issue(scopedRequest(f))
	if err != nil {
		t.Fatal(err)
	}
	if conn, err := Dial(f.ctx, f.client, g); err == nil {
		conn.Close()
		t.Fatal("revoked dependency opened direct transport")
	}
	if f.calls.Load() != 0 {
		t.Fatal("revoked dependency reached provider")
	}
}

func TestDirectDependencyUsesPrivateControllerTLSAndStillRevokes(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("model-output"))
	}))
	q := scopedRequest(f)
	var revoked atomic.Bool
	controller := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var received Request
		if r.URL.Path != "/apps/dependencies/check" || r.Method != "POST" ||
			json.NewDecoder(r.Body).Decode(&received) != nil || received.Dependency == nil ||
			received.Dependency.Proof != q.Dependency.Proof || received.Dependency.Consumer != q.Dependency.Consumer {
			t.Error("wrong Controller check or identity")
			w.WriteHeader(400)
			return
		}
		if revoked.Load() {
			w.WriteHeader(410)
			return
		}
		w.WriteHeader(204)
	}))
	defer controller.Close()
	defaultCheck, err := ControllerCheck(f.ctx, controller.URL)
	if err != nil {
		t.Fatal(err)
	}
	if defaultCheck(f.ctx, q) == nil {
		t.Fatal("untrusted Controller accepted")
	}
	roots := x509.NewCertPool()
	roots.AddCert(controller.Certificate())
	check, err := ControllerCheckWithTLS(f.ctx, controller.URL, &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12})
	if err != nil {
		t.Fatal(err)
	}
	f.server.SetDependencyCheck(check)
	grant, err := f.server.Issue(q)
	if err != nil {
		t.Fatal(err)
	}
	conn := f.dial(t, grant)
	req, _ := http.NewRequest("POST", "http://app.test/v1/chat/completions", strings.NewReader(`{}`))
	response, body := exchange(t, conn, req)
	if response.StatusCode != 200 || body != "model-output" {
		t.Fatal(response.StatusCode, body)
	}
	conn.Close()
	revoked.Store(true)
	grant, err = f.server.Issue(q)
	if err != nil {
		t.Fatal(err)
	}
	if conn, err := Dial(f.ctx, f.client, grant); err == nil {
		conn.Close()
		t.Fatal("revoked private Controller grant accepted")
	}
	if f.calls.Load() != 1 {
		t.Fatal("revoked inference reached model")
	}
}
