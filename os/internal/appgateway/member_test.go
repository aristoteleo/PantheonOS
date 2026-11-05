package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// A space grant (docs/apps/team-chat.md, "Phase 3 as built") reaches only an
// owner's hosted team-chat App, only its RPC, with the person's identity, and
// ends when its grant id is revoked.
func TestMemberGrantReachesOnlyTeamChatRPC(t *testing.T) {
	const serviceToken = "controller-service-token-is-not-an-app-token"
	g, err := New("apps.test", serviceToken, []string{"https://atrium.test"},
		func(context.Context, Binding, string, string) error { return nil }, func(context.Context, Binding) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	var got *MemberGrant
	calls := 0
	g.SetHosted("controller", func(w http.ResponseWriter, r *http.Request, b Binding, m *MemberGrant) {
		calls++
		got = m
		_ = json.NewEncoder(w).Encode(map[string]any{"success": true, "path": r.URL.Path})
	})
	g.SetMemberCheck(func(b Binding) bool { return b.Instance == "hteamchat" })
	mux := http.NewServeMux()
	g.Register(mux)
	server := httptest.NewServer(g.Handler(mux))
	defer server.Close()
	do := func(method, path, host string, body []byte, headers http.Header) (*http.Response, string) {
		t.Helper()
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(body))
		req.Host = host
		req.Header = headers
		res, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(res.Body)
		return res, string(raw)
	}
	b := Binding{Fleet: "f_owner", Node: "controller", Instance: "hteamchat", Revision: strings.Repeat("a", 64), Generation: 1, Component: "backend", Port: "http"}
	member := MemberGrant{GrantID: "tcm-0123456789abcdef", Space: "f_owner", UserID: "u-alice", Username: "alice", Name: "Alice",
		FleetID: "f_alice", Role: "member", Joined: time.Now().Unix()}
	attach := func(binding Binding, m MemberGrant, workload bool) (int, string) {
		t.Helper()
		q := AttachRequest{Binding: binding, Credential: strings.Repeat("v", 40), Expires: time.Now().Add(time.Hour).Unix(), Member: &m, Workload: workload}
		if !workload {
			q.UIOrigin = "https://atrium.test"
		}
		body, _ := json.Marshal(q)
		res, raw := do("POST", "/apps/connect", "controller.test", body, http.Header{"Authorization": {"Bearer " + serviceToken}})
		return res.StatusCode, raw
	}
	// Refused: another App, another space, a role a grant cannot carry, a workload.
	other := b
	other.Instance = "hdesktop"
	wrongSpace := member
	wrongSpace.Space = "f_someone"
	asOwner := member
	asOwner.Role = "owner"
	onNode := b
	onNode.Node = "n_mac"
	for name, c := range map[string]struct {
		b        Binding
		m        MemberGrant
		workload bool
	}{"another App": {other, member, false}, "another space": {b, wrongSpace, false}, "owner role": {b, asOwner, false},
		"workload": {b, member, true}, "a node's App": {onNode, member, false}} {
		if status, raw := attach(c.b, c.m, c.workload); status == 200 {
			t.Fatal(name, "accepted", raw)
		}
	}
	status, raw := attach(b, member, false)
	var grant struct{ Ticket string }
	if status != 200 || json.Unmarshal([]byte(raw), &grant) != nil {
		t.Fatal("attach", status, raw)
	}
	host := Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test")
	ticket, _ := json.Marshal(map[string]string{"ticket": grant.Ticket})
	res, _ := do("POST", "/__fleet/connect", host, ticket, http.Header{"Origin": {"https://atrium.test"}})
	if res.StatusCode != 204 {
		t.Fatal("connect", res.Status)
	}
	cookie := res.Header.Get("Set-Cookie")
	cookie = cookie[:strings.Index(cookie, ";")]
	app := func(method, path string) int {
		t.Helper()
		res, _ := do(method, path, host, []byte(`{"method":"space_get","args":{}}`),
			http.Header{"Cookie": {cookie}, "Origin": {"https://atrium.test"}, "Content-Type": {"application/json"}})
		return res.StatusCode
	}
	if code := app("POST", "/rpc"); code != 200 || got == nil || got.UserID != "u-alice" || got.FleetID != "f_alice" || got.Role != "member" {
		t.Fatal("rpc", code, got)
	}
	if code := app("GET", "/__fleet/status"); code != 200 {
		t.Fatal("status", code)
	}
	before := calls
	for _, c := range [][2]string{{"GET", "/"}, {"GET", "/app-host.html"}, {"POST", "/_fleet/fs"}, {"GET", "/__fleet/model-media"}, {"PUT", "/rpc"}} {
		if code := app(c[0], c[1]); code != 403 {
			t.Fatal("not for a member", c, code)
		}
	}
	if calls != before {
		t.Fatal("a refused request reached the App")
	}
	// Removing the person drops the grant at once.
	if n := g.RevokeShare(member.GrantID); n != 1 {
		t.Fatal("revoked", n)
	}
	if code := app("POST", "/rpc"); code != 401 {
		t.Fatal("after revoke", code)
	}
}
