package controller

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

// One machine joined account A's fleet; account B's installer on the same
// machine presented the same node id and key for fleet B (2026-11-03). The
// second fleet must get its own identity, never the first fleet's.
func TestJoinRefusesNodeIdentityOfAnotherFleet(t *testing.T) {
	dir := t.TempDir()
	nodePubs := loadNodePubs(filepath.Join(dir, "nodepubs.json"))
	nodeFleets := loadNodePubs(filepath.Join(dir, "nodefleets.json"))
	nodePubs.record("n_mac", "pub-mac")
	nodeFleets.record("n_mac", "f_a")

	cases := []struct {
		name, nodeID, nodePub, fleet string
		refused                      bool
	}{
		{"same fleet re-join keeps its node", "n_mac", "pub-mac", "f_a", false},
		{"node id of another fleet", "n_mac", "pub-mac", "f_b", true},
		{"key of another fleet's node under a new id", "n_new", "pub-mac", "f_b", true},
		{"same key, new id, same fleet", "n_new", "pub-mac", "f_a", false},
		{"a separate identity for the second fleet", "n_b", "pub-b", "f_b", false},
		{"unknown node (never joined since bindings were kept)", "n_old", "pub-old", "f_b", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := otherFleetNode(nodeFleets, nodePubs, tc.nodeID, tc.nodePub, tc.fleet); got != tc.refused {
				t.Fatalf("otherFleetNode = %v, want %v", got, tc.refused)
			}
			rec := httptest.NewRecorder()
			req := proto.JoinRequest{NodeID: tc.nodeID, NodePub: tc.nodePub}
			if got := refuseOtherFleetNode(rec, nodeFleets, nodePubs, req, tc.fleet); got != tc.refused {
				t.Fatalf("refuseOtherFleetNode = %v, want %v", got, tc.refused)
			}
			if !tc.refused {
				return
			}
			if rec.Code != http.StatusConflict {
				t.Fatalf("status = %d, want 409", rec.Code)
			}
			var body proto.JoinConflict
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			// The refusal names the caller's own fleet, never the other one.
			if body.Error != proto.JoinConflictNodeInOtherFleet || body.FleetID != tc.fleet {
				t.Fatalf("body = %+v", body)
			}
		})
	}
}
