package join

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"os"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
)

// TestE2EJoinRefresh drives the real client against a running Controller:
// join (with a node pubkey) → refresh (proof-of-possession) → a stolen-token
// attempt must fail. Set FLEET_E2E_CONTROLLER=http://host:port to run.
func TestE2EJoinRefresh(t *testing.T) {
	url := os.Getenv("FLEET_E2E_CONTROLLER")
	if url == "" {
		t.Skip("set FLEET_E2E_CONTROLLER to run the join/refresh e2e test")
	}
	key := os.Getenv("FLEET_E2E_KEY")
	if key == "" {
		key = "e2e-test-key"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	nk, err := node.LoadOrCreateKey(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pub := node.PubB64(nk)

	asg, err := Join(ctx, url, key, pub)
	if err != nil {
		t.Fatalf("join: %v", err)
	}
	if asg.RefreshToken == "" {
		t.Fatal("join returned no refresh token")
	}
	if asg.Creds == "" {
		t.Fatal("join returned no creds")
	}
	t.Logf("join ok: fleet=%s creds=%dB refresh_token=%dB", asg.FleetID, len(asg.Creds), len(asg.RefreshToken))

	// Refresh with a valid proof-of-possession → fresh creds.
	ts := time.Now().Unix()
	sig := node.Sign(nk, token.PoPChallenge(pub, asg.FleetID, ts))
	out, err := Refresh(ctx, url, proto.TokenRequest{RefreshToken: asg.RefreshToken, TS: ts, Sig: sig})
	if err != nil {
		t.Fatalf("refresh: %v", err)
	}
	if out.Creds == "" {
		t.Fatal("refresh returned no creds")
	}
	if out.Creds == asg.Creds {
		t.Fatal("refresh returned identical creds (expected a fresh mint)")
	}
	t.Logf("refresh ok: fresh creds=%dB", len(out.Creds))

	// The crux: a stolen refresh token is useless without the node key.
	_, wrong, _ := ed25519.GenerateKey(nil)
	badSig := base64.StdEncoding.EncodeToString(ed25519.Sign(wrong, []byte(token.PoPChallenge(pub, asg.FleetID, ts))))
	if _, err := Refresh(ctx, url, proto.TokenRequest{RefreshToken: asg.RefreshToken, TS: ts, Sig: badSig}); err == nil {
		t.Fatal("refresh with a wrong-key signature must fail (stolen refresh token must be useless)")
	}
	t.Log("stolen refresh token (wrong key) correctly rejected")
}
