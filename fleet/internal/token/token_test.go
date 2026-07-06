package token

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
	"time"
)

func TestRefreshTokenAndPoP(t *testing.T) {
	cpub, cpriv, _ := ed25519.GenerateKey(nil) // controller signing key
	npub, npriv, _ := ed25519.GenerateKey(nil) // node key (never leaves the node)
	nodePub := base64.StdEncoding.EncodeToString(npub)

	p := Payload{FleetID: "f_test", NodePub: nodePub, Exp: time.Now().Add(time.Hour).Unix()}
	tok, err := Sign(cpriv, p)
	if err != nil {
		t.Fatal(err)
	}

	got, err := Verify(cpub, tok)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if got.NodePub != nodePub || got.FleetID != "f_test" {
		t.Fatal("payload mismatch")
	}

	sign := func(k ed25519.PrivateKey, ts int64) string {
		return base64.StdEncoding.EncodeToString(ed25519.Sign(k, []byte(PoPChallenge(nodePub, "f_test", ts))))
	}

	ts := time.Now().Unix()
	if err := VerifyPoP(got, ts, sign(npriv, ts)); err != nil {
		t.Fatalf("PoP should pass for the real node key: %v", err)
	}

	// The crux: a stolen refresh token is useless without the node key.
	_, wrong, _ := ed25519.GenerateKey(nil)
	if err := VerifyPoP(got, ts, sign(wrong, ts)); err == nil {
		t.Fatal("PoP must fail with a different key")
	}

	// Tampered token, stale challenge, and expired token must all be rejected.
	if _, err := Verify(cpub, tok+"x"); err == nil {
		t.Fatal("tampered token must fail")
	}
	if err := VerifyPoP(got, ts-3600, sign(npriv, ts-3600)); err == nil {
		t.Fatal("stale challenge must fail")
	}
	pe := Payload{FleetID: "f", NodePub: nodePub, Exp: time.Now().Add(-time.Hour).Unix()}
	toke, _ := Sign(cpriv, pe)
	if _, err := Verify(cpub, toke); err == nil {
		t.Fatal("expired token must fail")
	}
}
