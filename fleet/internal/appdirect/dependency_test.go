package appdirect

import (
	"context"
	"errors"
	"net/http"
	"strings"
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
