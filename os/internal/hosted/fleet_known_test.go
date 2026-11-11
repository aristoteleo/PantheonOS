package hosted

import (
	"testing"
	"time"
)

// Combined check 1, C2: an owner's computer that leaves the fleet is listed
// offline, not dropped; platform nodes are not remembered; a week later it goes.
func TestAComputerThatLeftIsListedOffline(t *testing.T) {
	f := &Fleet{}
	now := time.Unix(1_800_000_000, 0)
	mac := map[string]any{"node_id": "n_mac", "name": "mac.local", "kind": "machine", "last_seen": now.Format(time.RFC3339Nano),
		"capability": map[string]any{"os": "darwin", "caps": []any{"proc", "fs:local"}},
		"state":      map[string]any{"status": "online", "instances": []any{map[string]any{"app_id": "pty", "health": "healthy"}}}}
	ws := map[string]any{"node_id": "n_ws", "kind": "sandbox", "capability": map[string]any{"caps": []any{"fs:workspace"}},
		"state": map[string]any{"status": "online"}, "last_seen": now.Format(time.RFC3339Nano)}
	pod := map[string]any{"node_id": "n_pod", "kind": "pod", "state": map[string]any{"status": "online"}, "last_seen": now.Format(time.RFC3339Nano)}
	if got := f.listed("f1", []map[string]any{mac, ws, pod}, now); len(got) != 3 {
		t.Fatalf("live: %d records", len(got))
	}
	got := f.listed("f1", nil, now.Add(time.Minute))
	if len(got) != 1 || got[0]["node_id"] != "n_mac" {
		t.Fatalf("after the nodes left: %v", got)
	}
	inv := inventory(got, now.Add(time.Minute))
	n := inv["nodes"].([]map[string]any)[0]
	if n["status"] != "offline" || n["name"] != "mac.local" || n["has_pty"] != false || len(inv["instances"].([]map[string]any)) != 0 {
		t.Fatalf("listed as %v / instances %v", n, inv["instances"])
	}
	if mac["state"].(map[string]any)["status"] != "online" {
		t.Fatal("the remembered record itself was changed")
	}
	if got := f.listed("f2", nil, now); len(got) != 0 {
		t.Fatalf("another fleet sees %v", got)
	}
	if got := f.listed("f1", nil, now.Add(KnownNodeDays*24*time.Hour+time.Hour)); len(got) != 0 {
		t.Fatalf("still listed after %d days: %v", KnownNodeDays, got)
	}
}
