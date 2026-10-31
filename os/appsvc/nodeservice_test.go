package appsvc

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
)

// A node service's id is the one the controller has always given it, so a
// node can address another node's service.
func TestNodeServiceID(t *testing.T) {
	sum := sha256.Sum256([]byte("n2"))
	scope := "node" + hex.EncodeToString(sum[:])[:16] + "-app"
	id := sha256.Sum256([]byte("f_1-app-coding-agents-" + scope))
	if NodeScope("n2") != scope || NodeServiceID("f_1", "coding-agents", "n2") != hex.EncodeToString(id[:]) {
		t.Fatalf("scope %s id %s", NodeScope("n2"), NodeServiceID("f_1", "coding-agents", "n2"))
	}
}
