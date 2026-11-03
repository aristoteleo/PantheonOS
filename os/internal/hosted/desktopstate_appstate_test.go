package hosted

import (
	"strings"
	"sync"
	"testing"
)

// An App's state is read and written by its id, merged by patch, checked by
// revision, announced, kept per Fleet, and apart from the desktop's session.
func TestAppStateIsKeptPerAppAndChecked(t *testing.T) {
	url := jetStream(t)
	h, mine, theirs := desktopHost(t, desktopOn(t, url))
	empty := call(t, h, mine, "app_state_get", map[string]any{"app_id": "coding-agents"})
	if empty["state"] != nil || wint(empty["revision"]) != 0 || empty["app_id"] != "coding-agents" {
		t.Fatalf("a new App has state: %v", empty)
	}
	events := listen(t, url)

	// The first writer of an absent key (migration) writes at revision 0.
	first := call(t, h, mine, "app_state_put", map[string]any{"app_id": "coding-agents",
		"state": map[string]any{"tabs": []any{"a"}}, "revision": 0})
	rev := wint(first["revision"])
	if rev == 0 || first["conflict"] != nil {
		t.Fatalf("first put %v", first)
	}
	if e := next(t, events); e["type"] != "desktop.app_state" || e["app_id"] != "coding-agents" || wint(e["revision"]) != rev {
		t.Fatalf("event %v", e)
	}
	// A second migration racing it sees the conflict, with the state to merge into.
	late := call(t, h, mine, "app_state_put", map[string]any{"app_id": "coding-agents",
		"state": map[string]any{"tabs": []any{"b"}}, "revision": 0})
	if late["conflict"] != true || wint(late["revision"]) != rev || !jsonEqual(late["state"], map[string]any{"tabs": []any{"a"}}) {
		t.Fatalf("stale put %v", late)
	}
	// A patch merges top-level keys; null removes one.
	patched := call(t, h, mine, "app_state_put", map[string]any{"app_id": "coding-agents",
		"patch": map[string]any{"page": map[string]any{"panel": map[string]any{"open": true}}}, "revision": rev})
	want := map[string]any{"tabs": []any{"a"}, "page": map[string]any{"panel": map[string]any{"open": true}}}
	if !jsonEqual(patched["state"], want) {
		t.Fatalf("patch %v", patched)
	}
	removed := call(t, h, mine, "app_state_put", map[string]any{"app_id": "coding-agents", "patch": map[string]any{"tabs": nil}})
	if !jsonEqual(removed["state"], map[string]any{"page": map[string]any{"panel": map[string]any{"open": true}}}) {
		t.Fatalf("null did not remove: %v", removed)
	}

	// Packaged Apps have their own keys; Apps do not see each other's.
	call(t, h, mine, "app_state_put", map[string]any{"app_id": "pkg:text-viewer", "state": map[string]any{"x": 1}})
	if got := call(t, h, mine, "app_state_get", map[string]any{"app_id": "pkg:text-viewer"}); !jsonEqual(got["state"], map[string]any{"x": 1}) {
		t.Fatalf("pkg state %v", got)
	}
	if got := call(t, h, mine, "app_state_get", map[string]any{"app_id": "text-viewer"}); got["state"] != nil {
		t.Fatalf("a built-in id reads a packaged App's state: %v", got)
	}
	// Another Fleet reads none of it; the session knows nothing of it.
	if got := call(t, h, theirs, "app_state_get", map[string]any{"app_id": "coding-agents"}); got["state"] != nil {
		t.Fatalf("another Fleet reads this state: %v", got)
	}
	if desk := call(t, h, mine, "get", map[string]any{}); len(windowsOf(desk)) != 0 || desk["initialized"] != false {
		t.Fatalf("per-App state touched the session: %v", desk)
	}
	// Read by a fresh service: it is in the bucket.
	h2, _, _ := desktopHost(t, desktopOn(t, url))
	if got := call(t, h2, mine, "app_state_get", map[string]any{"app_id": "coding-agents"}); wint(got["revision"]) != wint(removed["revision"]) {
		t.Fatalf("fresh read %v", got)
	}
}

func TestAppStateIsCheckedAndLimited(t *testing.T) {
	url := jetStream(t)
	h, mine, _ := desktopHost(t, desktopOn(t, url))
	for _, args := range []map[string]any{
		{"app_id": "../x", "state": map[string]any{}},
		{"app_id": "a.b", "state": map[string]any{}},
		{"app_id": "pkg:", "state": map[string]any{}},
		{"app_id": "coding-agents"},
		{"app_id": "coding-agents", "state": map[string]any{}, "patch": map[string]any{}},
		{"app_id": "coding-agents", "state": []any{1}},
	} {
		refused(t, h, mine, "app_state_put", args)
	}
	big := strings.Repeat("x", MaxAppState)
	if msg := refused(t, h, mine, "app_state_put", map[string]any{"app_id": "agent-canvas", "state": map[string]any{"v": big}}); !strings.Contains(msg, "limited") {
		t.Fatalf("over the limit: %s", msg)
	}
}

// Concurrent patches without a revision all land (the service retries a
// conflict); with one, exactly one of several racing writers wins.
func TestConcurrentAppStateWrites(t *testing.T) {
	url := jetStream(t)
	h, mine, _ := desktopHost(t, desktopOn(t, url))
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			call(t, h, mine, "app_state_put", map[string]any{"app_id": "agent-canvas", "patch": map[string]any{string(rune('a' + i)): i}})
		}(i)
	}
	wg.Wait()
	got := call(t, h, mine, "app_state_get", map[string]any{"app_id": "agent-canvas"})
	if len(got["state"].(map[string]any)) != 16 {
		t.Fatalf("lost patches: %v", got)
	}
	rev := wint(got["revision"])
	wins := make(chan bool, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			out := call(t, h, mine, "app_state_put", map[string]any{"app_id": "agent-canvas", "state": map[string]any{"w": i}, "revision": rev})
			wins <- out["conflict"] == nil
		}(i)
	}
	wg.Wait()
	close(wins)
	n := 0
	for w := range wins {
		if w {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("%d writers won at one revision", n)
	}
}
