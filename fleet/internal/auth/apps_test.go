package auth

import (
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/jwt/v2"
)

func TestAppsCredentialReachesOnlyTheAppNamespace(t *testing.T) {
	authority, err := Bootstrap(filepath.Join(t.TempDir(), "state"))
	if err != nil {
		t.Fatal(err)
	}
	a, err := authority.MintFleetApps("f_1", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := authority.MintFleetApps("f_1", time.Hour)
	claims, err := jwt.DecodeUserClaims(a.JWT)
	if err != nil {
		t.Fatal(err)
	}
	if a.SubjectPrefix != "fleet.f_1.apps" || !strings.HasPrefix(a.InboxPrefix, "_INBOX_f_1.apps.") || a.InboxPrefix == b.InboxPrefix {
		t.Fatalf("prefixes: %+v %+v", a, b)
	}
	pub, sub := claims.Permissions.Pub.Allow, claims.Permissions.Sub.Allow
	if !slices.Equal(pub, jwt.StringList{"fleet.f_1.apps.>", "_INBOX_f_1.apps.>"}) {
		t.Fatalf("publish: %v", pub)
	}
	if !slices.Equal(sub, jwt.StringList{"fleet.f_1.apps.>", a.InboxPrefix + ".>"}) {
		t.Fatalf("subscribe (own replies only): %v", sub)
	}
	for _, forbidden := range []string{"fleet.f_1.>", "fleet.f_1.node.", "$KV.", "_INBOX_f_1.>"} {
		for _, s := range append(append([]string{}, pub...), sub...) {
			if s == forbidden || strings.HasPrefix(s, forbidden) && forbidden != "_INBOX_f_1.>" {
				t.Fatalf("credential reaches %s via %s", forbidden, s)
			}
		}
	}
	if claims.Expires > time.Now().Add(time.Hour+time.Minute).Unix() {
		t.Fatal("expiry follows the requested lifetime")
	}
}
