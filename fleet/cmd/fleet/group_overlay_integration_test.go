package main

import (
	"encoding/json"
	"fmt"
	"reflect"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

// Reuses the actual owner-authenticated two-Runner recovery fixture. No kernel
// networking or engine is started by enrollment/pinning. Caller restarts rank0
// and the owner connection before invoking the returned verification.
func prepareOverlayRPCRecovery(t *testing.T, topology groupcredentials.Topology, ca string, rpc func(int, string, map[string]any) map[string]json.RawMessage) func() {
	t.Helper()
	call := func(rank int, action string, q lifecycle.OverlayRequest) groupcredentials.OverlayStatus {
		t.Helper()
		out := rpc(rank, "group_overlay_"+action, map[string]any{"group_overlay": q})
		if out["error"] != nil {
			t.Fatal(string(out["error"]))
		}
		if out["private_key"] != nil || out["manifest"] != nil {
			t.Fatal("node-private material exposed")
		}
		var status groupcredentials.OverlayStatus
		if err := json.Unmarshal(mustJSON(out), &status); err != nil {
			t.Fatal(err)
		}
		return status
	}
	manifests := make([]groupcredentials.Manifest, 2)
	prepares := make([]lifecycle.OverlayRequest, 2)
	endpoints := make([]groupcredentials.OverlayEndpoint, 2)
	for rank := 0; rank < 2; rank++ {
		manifests[rank] = groupcredentials.Manifest{Protocol: 1, Rank: &rank, Topology: topology, CAHash: ca}
		prepares[rank] = lifecycle.OverlayRequest{Manifest: mustJSON(manifests[rank]), Address: fmt.Sprintf("192.168.50.%d:51890", rank+10)}
		status := call(rank, "prepare", prepares[rank])
		if status.State != "prepared" || status.Endpoint == nil {
			t.Fatal("missing original node endpoint")
		}
		endpoints[rank] = *status.Endpoint
	}
	if out := rpc(1, "group_overlay_prepare", map[string]any{"group_overlay": prepares[0]}); out["error"] == nil {
		t.Fatal("foreign rank accepted")
	}
	q := lifecycle.OverlayRequest{Group: topology.Group, TopologyHash: manifests[0].Fingerprint(), Endpoints: endpoints}
	original := make([]groupcredentials.OverlayStatus, 2)
	for rank := 0; rank < 2; rank++ {
		original[rank] = call(rank, "pin", q)
		if original[rank].State != "pinned" {
			t.Fatal("not pinned")
		}
	}
	// An explicit stop reaching a node before its preparation is durable too.
	tombstone := topology
	tombstone.Group = "overlay-cancelled-before-prepare"
	rank := 0
	late := groupcredentials.Manifest{Protocol: 1, Rank: &rank, Topology: tombstone, CAHash: ca}
	closedQ := lifecycle.OverlayRequest{Group: tombstone.Group, TopologyHash: late.Fingerprint()}
	closed := call(0, "close", closedQ)
	if closed.State != "closed" || closed.Endpoint != nil {
		t.Fatal("invalid early close fence")
	}
	return func() {
		for rank := 0; rank < 2; rank++ {
			if got := call(rank, "prepare", prepares[rank]); !reflect.DeepEqual(got, original[rank]) {
				t.Fatal("restart changed enrollment")
			}
			if got := call(rank, "pin", q); !reflect.DeepEqual(got, original[rank]) {
				t.Fatal("restart changed roster")
			}
			statusQ := q
			statusQ.Endpoints = nil
			if got := call(rank, "status", statusQ); !reflect.DeepEqual(got, original[rank]) {
				t.Fatal("status changed roster")
			}
			if got := call(rank, "close", statusQ); got.State != "closed" {
				t.Fatal("close not durable")
			}
			if out := rpc(rank, "group_overlay_pin", map[string]any{"group_overlay": q}); out["error"] == nil {
				t.Fatal("late pin accepted")
			}
			if got := call(rank, "prepare", prepares[rank]); got.State != "closed" {
				t.Fatal("late prepare reopened group")
			}
		}
		lateQ := lifecycle.OverlayRequest{Manifest: mustJSON(late), Address: "192.168.50.10:51890"}
		if got := call(0, "prepare", lateQ); !reflect.DeepEqual(got, closed) {
			t.Fatal("restart lost early close fence")
		}
	}
}
