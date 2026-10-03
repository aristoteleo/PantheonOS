package shellapp

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/appsvc"
)

func TestManagedShellSessionsUseGenericResourceContract(t *testing.T) {
	a := NewApp(t.TempDir())
	defer a.Close()
	tools, err := Tools(a)
	if err != nil {
		t.Fatal(err)
	}
	handlers := map[string]appsvc.Handler{}
	for _, tool := range tools {
		handlers[tool.Name] = tool.Handler
	}
	control := func(method, owner, lease string) appsvc.SessionReceipt {
		t.Helper()
		result, err := handlers[method](context.Background(), map[string]any{"owner_ref": owner, "lease_id": lease, "kind": "shell", "ttl_seconds": 300})
		if err != nil {
			t.Fatal(err)
		}
		return result.(appsvc.SessionReceipt)
	}
	one := control("resource_session_acquire", "owner-a", strings.Repeat("a", 64))
	two := control("resource_session_acquire", "owner-b", strings.Repeat("b", 64))
	if one.State != "active" || two.State != "active" || one.SessionID == two.SessionID {
		t.Fatal(one, two)
	}
	a.runInShell(one.SessionID, "export OWNER_MARK=one; cd /", 5)
	if retry := control("resource_session_acquire", one.OwnerRef, one.LeaseID); retry.SessionID != one.SessionID {
		t.Fatal("retry replaced session")
	}
	if renewed := control("resource_session_renew", one.OwnerRef, one.LeaseID); renewed.State != "active" || renewed.SessionID != one.SessionID {
		t.Fatal(renewed)
	}
	first := a.runInShell(one.SessionID, "echo $OWNER_MARK; pwd", 5)
	second := a.runInShell(two.SessionID, "echo ${OWNER_MARK:-unset}; pwd", 5)
	if !strings.Contains(first["output"].(string), "one\n/\n") || !strings.Contains(second["output"].(string), "unset") {
		t.Fatal("sessions lost isolation", first, second)
	}
	a.mu.Lock()
	process := a.shells[one.SessionID]
	a.mu.Unlock()
	if released := control("resource_session_release", one.OwnerRef, one.LeaseID); released.State != "released" {
		t.Fatal(released)
	}
	select {
	case <-process.waitDone:
	default:
		t.Fatal("release returned before shell exited")
	}
	if result := a.runInShell(one.SessionID, "echo should-not-run", 5); result["success"] != false {
		t.Fatal("released session ran", result)
	}
	if got := control("resource_session_acquire", one.OwnerRef, one.LeaseID); got.State != "released" {
		t.Fatal("release intent resurrected", got)
	}
	if result := a.runInShell(two.SessionID, "echo still-alive", 5); !strings.Contains(result["output"].(string), "still-alive") {
		t.Fatal("other owner affected", result)
	}
}

func TestManagedShellLeaseExpiresWithoutOwnerActivity(t *testing.T) {
	if testing.Short() {
		t.Skip("real 30-second resource lease expiry")
	}
	a := NewApp(t.TempDir())
	defer a.Close()
	tools, err := Tools(a)
	if err != nil {
		t.Fatal(err)
	}
	var acquire appsvc.Handler
	for _, tool := range tools {
		if tool.Name == "resource_session_acquire" {
			acquire = tool.Handler
		}
	}
	start := time.Now()
	value, err := acquire(context.Background(), map[string]any{"owner_ref": "abandoned-owner", "lease_id": strings.Repeat("c", 64), "kind": "shell", "ttl_seconds": 30})
	if err != nil {
		t.Fatal(err)
	}
	receipt := value.(appsvc.SessionReceipt)
	a.mu.Lock()
	process := a.shells[receipt.SessionID]
	a.mu.Unlock()
	if process == nil {
		t.Fatal("no managed shell created")
	}
	// No calls to the registry or App until the provider's own sweeper stops it.
	select {
	case <-process.waitDone:
		if time.Since(start) < 28*time.Second {
			t.Fatal("lease ended early")
		}
	case <-time.After(40 * time.Second):
		t.Fatal("abandoned session was not reclaimed by the provider")
	}
	got, err := a.sessions.Get(receipt.OwnerRef, receipt.LeaseID)
	if err != nil || got.State != "expired" {
		t.Fatal(got, err)
	}
	t.Logf("provider reclaimed abandoned Shell after %s without owner RPCs", time.Since(start).Round(time.Millisecond))
}
