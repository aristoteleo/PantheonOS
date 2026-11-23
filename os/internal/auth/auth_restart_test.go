package auth

import (
	"strings"
	"testing"
	"time"
)

// A restarted controller must emit the same operator: nats-server refuses to
// reload a config whose operator changed, and a revoked node is kicked only by
// that reload.
func TestOperatorJWTSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	a, err := Bootstrap(dir)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(1100 * time.Millisecond) // a new JWT would carry a new issue time
	b, err := Bootstrap(dir)
	if err != nil {
		t.Fatal(err)
	}
	if a.opJWT != b.opJWT {
		t.Fatal("the operator JWT changed across a restart")
	}
	op := func(cfg string) string {
		for _, l := range strings.Split(cfg, "\n") {
			if strings.HasPrefix(l, "operator:") {
				return l
			}
		}
		return ""
	}
	before := op(a.ServerConfig("127.0.0.1:4222", dir))
	if err := b.RevokeUser("UABC"); err != nil {
		t.Fatal(err)
	}
	if after := op(b.ServerConfig("127.0.0.1:4222", dir)); after != before || after == "" {
		t.Fatal("a revocation changed the operator line")
	}
}
