package lifecycle

import (
	"fmt"
	"testing"
	"time"
)

// The ledger travels whole in every status reply: settled operations are
// kept for a week, at most finishedOperationsKept; unsettled ones stay.
func TestFinishedOperationsArePrunedUnfinishedKept(t *testing.T) {
	m, _, _ := setup(t)
	now := time.Now().UTC()
	m.mu.Lock()
	for i := 0; i < finishedOperationsKept+50; i++ {
		m.ledger.Operations[fmt.Sprintf("recent-%d", i)] = &Operation{State: "succeeded", UpdatedAt: now.Add(-time.Duration(i) * time.Minute)}
	}
	m.ledger.Operations["old-failed"] = &Operation{State: "failed", UpdatedAt: now.Add(-8 * 24 * time.Hour)}
	m.ledger.Operations["old-running"] = &Operation{State: "running", UpdatedAt: now.Add(-30 * 24 * time.Hour)}
	m.ledger.Operations["queued"] = &Operation{State: "queued", UpdatedAt: now.Add(-30 * 24 * time.Hour)}
	m.ledger.Operations["interrupted"] = &Operation{State: "unknown"}
	m.pruneOperationsLocked(now)
	ops := m.ledger.Operations
	m.mu.Unlock()
	if len(ops) != finishedOperationsKept+3 {
		t.Fatalf("kept %d operations", len(ops))
	}
	for _, id := range []string{"recent-0", fmt.Sprintf("recent-%d", finishedOperationsKept-1), "old-running", "queued", "interrupted"} {
		if ops[id] == nil {
			t.Fatalf("%s must stay", id)
		}
	}
	for _, id := range []string{fmt.Sprintf("recent-%d", finishedOperationsKept), "old-failed"} {
		if ops[id] != nil {
			t.Fatalf("%s must go", id)
		}
	}
}
