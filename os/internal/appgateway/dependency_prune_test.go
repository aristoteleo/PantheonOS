//go:build !windows

package appgateway

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// tombstone writes an expired record of fleet, as a revoked or expired grant
// leaves it, directly into an open journal.
func tombstone(t *testing.T, g *Gateway, fleet string, n int, expired time.Time) string {
	t.Helper()
	hash := func(s string) string { sum := sha256.Sum256([]byte(s)); return hex.EncodeToString(sum[:]) }
	key := hash(fmt.Sprint("key", fleet, n, expired.UnixNano()))
	g.mu.Lock()
	defer g.mu.Unlock()
	if err := writeDependencyRecord(g.dependencyStore, dependencyRecord{Protocol: 1, Domain: g.domain, Key: key,
		Policy: hash("policy"), ID: hash(key), Fleet: fleet, Expires: expired.Unix()}); err != nil {
		t.Fatal(err)
	}
	return key
}

func onFleet(q DependencyRequest, fleet, operation string) DependencyRequest {
	q.Consumer.Fleet, q.Provider.Fleet, q.Operation = fleet, fleet, operation
	return q
}

func exists(root, key string) bool {
	_, err := os.Stat(filepath.Join(root, key+".json"))
	return err == nil
}

// Loading drops records expired longer than the retention; a recent tombstone
// still refuses its operation after a restart.
func TestDependencyJournalPrunesOldTombstonesAtLoad(t *testing.T) {
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	issued := persistedIssue(t, h, q)
	revoke := map[string]any{"fleet_id": "owner", "grant_id": issued["grant_id"]}
	if r := persistenceCall(h, "DELETE", "control.test", "/apps/dependencies", persistenceControl, revoke); r.Code != 204 {
		t.Fatal(r.Code)
	}
	old := tombstone(t, g, "owner", 1, time.Now().Add(-dependencyTombstoneRetention-time.Hour))
	recent := tombstone(t, g, "owner", 2, time.Now().Add(-dependencyTombstoneRetention+time.Hour))
	g.CloseDependencyStore()
	g, h = persistedGateway(t, root)
	if exists(root, old) || !exists(root, recent) || !exists(root, dependencyOperation(q)) {
		t.Fatal("load pruned the wrong records")
	}
	if g.dependencyStore.fleets["owner"] != 2 {
		t.Fatal("owner count", g.dependencyStore.fleets)
	}
	if r := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, q); r.Code != 410 {
		t.Fatal("revoked operation revived after pruning", r.Code)
	}
}

// An owner whose share is full is refused alone; old tombstones are pruned to
// make room before refusing, and the journal is never poisoned.
func TestDependencyJournalFullOwnerDoesNotBlockOthers(t *testing.T) {
	defer func(v int) { maxDependencyRecordsPerFleet = v }(maxDependencyRecordsPerFleet)
	maxDependencyRecordsPerFleet = 3
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	for i := range 3 {
		persistedIssue(t, h, onFleet(q, "owner", fmt.Sprint("op-", i)))
	}
	full := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, onFleet(q, "owner", "op-3"))
	if full.Code != 503 || !strings.Contains(full.Body.String(), "for this owner") {
		t.Fatal("full owner admitted", full.Code, full.Body.String())
	}
	persistedIssue(t, h, onFleet(q, "other", "op-0"))
	if g.dependencyStoreFailed {
		t.Fatal("capacity poisoned the journal")
	}
	// Replays of existing operations still answer.
	persistedIssue(t, h, onFleet(q, "owner", "op-1"))

	// A share full of old tombstones is pruned at write time.
	root2 := filepath.Join(t.TempDir(), "journal")
	g2, h2 := persistedGateway(t, root2)
	var old []string
	for i := range 3 {
		old = append(old, tombstone(t, g2, "owner", i, time.Now().Add(-dependencyTombstoneRetention-time.Minute)))
	}
	recent := tombstone(t, g2, "other", 9, time.Now().Add(-time.Minute))
	persistedIssue(t, h2, onFleet(q, "owner", "op-new"))
	for _, key := range old {
		if exists(root2, key) {
			t.Fatal("old tombstone kept at capacity")
		}
	}
	if !exists(root2, recent) || g2.dependencyStore.fleets["owner"] != 1 {
		t.Fatal("prune removed a recent tombstone or miscounted", g2.dependencyStore.fleets)
	}
}

// Writes prune past-retention records every dependencyPruneInterval even
// when the journal is far from full; unexpired grants are never pruned.
func TestDependencyJournalPrunesPeriodically(t *testing.T) {
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	live := persistedIssue(t, h, onFleet(q, "owner", "live"))
	soon := tombstone(t, g, "owner", 1, time.Now().Add(-dependencyTombstoneRetention+5*time.Minute))
	clock := time.Now()
	g.mu.Lock()
	g.dependencyStore.now = func() time.Time { return clock }
	g.mu.Unlock()
	persistedIssue(t, h, onFleet(q, "owner", "a"))
	if !exists(root, soon) {
		t.Fatal("pruned before the retention passed")
	}
	clock = clock.Add(dependencyPruneInterval + time.Minute)
	persistedIssue(t, h, onFleet(q, "owner", "b"))
	if exists(root, soon) {
		t.Fatal("periodic prune did not run")
	}
	if replay := persistedIssue(t, h, onFleet(q, "owner", "live")); replay["access_token"] != live["access_token"] {
		t.Fatal("live grant lost")
	}
}

// Live grants are bounded per owner too.
func TestDependencyLiveGrantsBoundedPerOwner(t *testing.T) {
	defer func(v int) { maxActiveGrantsPerFleet = v }(maxActiveGrantsPerFleet)
	maxActiveGrantsPerFleet = 2
	_, h := persistedGateway(t, filepath.Join(t.TempDir(), "journal"))
	q := persistenceRequest()
	persistedIssue(t, h, onFleet(q, "owner", "a"))
	persistedIssue(t, h, onFleet(q, "owner", "b"))
	if r := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, onFleet(q, "owner", "c")); r.Code != 503 {
		t.Fatal("owner exceeded its live grants", r.Code)
	}
	persistedIssue(t, h, onFleet(q, "other", "a"))
}
