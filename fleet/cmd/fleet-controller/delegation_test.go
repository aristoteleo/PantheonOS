package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
	"github.com/nats-io/jwt/v2"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDelegationRequiresBoundParentProofAndKeepsNarrowScope(t *testing.T) {
	authority, err := auth.Bootstrap(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	controllerPub, controllerPriv, _ := ed25519.GenerateKey(rand.Reader)
	parentPub, parentPriv, _ := ed25519.GenerateKey(rand.Reader)
	p := token.Payload{FleetID: "f_one", NodeID: "mac", NodePub: base64.StdEncoding.EncodeToString(parentPub), Exp: time.Now().Add(time.Hour).Unix()}
	refresh, _ := token.Sign(controllerPriv, p)
	rev := loadRevoked(t.TempDir())
	handler := delegateHandler(authority, controllerPub, rev)
	in := proto.DelegateRequest{RefreshToken: refresh, Allocation: strings.Repeat("a", 32), TS: time.Now().Unix()}
	in.Sig = base64.StdEncoding.EncodeToString(ed25519.Sign(parentPriv, []byte(proto.DelegateChallenge(p.NodePub, p.FleetID, in.Allocation, in.TS))))
	call := func(req proto.DelegateRequest) *httptest.ResponseRecorder {
		b, _ := json.Marshal(req)
		w := httptest.NewRecorder()
		handler(w, httptest.NewRequest("POST", "/delegate", bytes.NewReader(b)))
		return w
	}
	w := call(in)
	if w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	var grant proto.DelegateResponse
	json.Unmarshal(w.Body.Bytes(), &grant)
	if grant.NodeID != proto.DelegatedNodeID(p.FleetID, p.NodeID, in.Allocation) {
		t.Fatal(grant.NodeID)
	}
	encoded, _ := jwt.ParseDecoratedJWT([]byte(grant.Creds))
	claims, err := jwt.DecodeUserClaims(encoded)
	if err != nil {
		t.Fatal(err)
	}
	want := proto.SubjNodeCmd(p.FleetID, grant.NodeID)
	found := false
	for _, subject := range claims.Permissions.Sub.Allow {
		if subject == want {
			found = true
		}
		if strings.Contains(subject, ".node.") && subject != want {
			t.Fatalf("broad node access: %s", subject)
		}
	}
	if !found {
		t.Fatal("missing delegated command permission")
	}
	for _, mutate := range []func(*proto.DelegateRequest){
		func(r *proto.DelegateRequest) { r.Allocation = strings.Repeat("b", 32) },
		func(r *proto.DelegateRequest) { r.TS -= 1000 },
		func(r *proto.DelegateRequest) {
			r.Sig = base64.StdEncoding.EncodeToString(ed25519.Sign(parentPriv, []byte(token.PoPChallenge(p.NodePub, p.FleetID, r.TS))))
		},
	} {
		bad := in
		mutate(&bad)
		if call(bad).Code != 401 {
			t.Fatal("accepted altered delegation")
		}
	}
	rev.revoke(p.NodePub)
	if call(in).Code != 401 {
		t.Fatal("revoked connector delegated a node")
	}
	if proto.DelegatedNodeID("f_other", p.NodeID, in.Allocation) == grant.NodeID {
		t.Fatal("identity crosses fleets")
	}
}
