package hosted

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
)

func listen(t *testing.T, url string) chan map[string]any {
	t.Helper()
	viewer := dial(t, url)
	events := make(chan map[string]any, 64)
	if _, err := viewer.Subscribe("fleet."+fleetID+".apps.desktop-state.owner", func(m *nats.Msg) {
		var e map[string]any
		_ = json.Unmarshal(m.Data, &e)
		events <- e
	}); err != nil {
		t.Fatal(err)
	}
	_ = viewer.Flush()
	return events
}

func next(t *testing.T, events chan map[string]any) map[string]any {
	t.Helper()
	select {
	case e := <-events:
		return e
	case <-time.After(settle):
		t.Fatal("no event")
		return nil
	}
}

func appEntry(id string, extra map[string]any) map[string]any {
	entry := map[string]any{"id": id, "name": strings.ToUpper(id), "manifest": map[string]any{"id": id, "version": "1.0.0"},
		"placement": map[string]any{"node_id": "mac", "policy": "on_demand"}}
	for k, v := range extra {
		entry[k] = v
	}
	return entry
}

// C1/C3: the index the Desktop App writes is what every desktop reads, and a
// change is announced to the others.
func TestTheAppIndexIsReadWithTheDesktopAndAnnounced(t *testing.T) {
	url := jetStream(t)
	h, mine, theirs := desktopHost(t, desktopOn(t, url))
	empty := call(t, h, mine, "get", map[string]any{})
	if empty["apps"] != nil || wint(empty["apps_revision"]) != 0 {
		t.Fatalf("a new desktop has an index: %v", empty)
	}
	events := listen(t, url)
	apps := []any{appEntry("notes", map[string]any{"icon": "data:image/svg+xml;base64,PHN2Zy8+"})}
	put := call(t, h, mine, "apps_put", map[string]any{"apps": apps})
	if wint(put["revision"]) == 0 || len(put["missing_icons"].([]any)) != 0 {
		t.Fatalf("apps_put %v", put)
	}
	if e := next(t, events); e["type"] != "desktop.apps" || wint(e["revision"]) != wint(put["revision"]) || !jsonEqual(e["apps"], apps) {
		t.Fatalf("event %v", e)
	}
	// Read by a fresh service: it is in the bucket, not only in a cache.
	h2, _, _ := desktopHost(t, desktopOn(t, url))
	got := call(t, h2, mine, "get", map[string]any{})
	if !jsonEqual(got["apps"], apps) || wint(got["apps_revision"]) != wint(put["revision"]) {
		t.Fatalf("get %v", got)
	}
	// The same index again (every Desktop App start) writes and announces nothing.
	again := call(t, h, mine, "apps_put", map[string]any{"apps": apps})
	if wint(again["revision"]) != wint(put["revision"]) {
		t.Fatalf("an unchanged index was written: %v", again)
	}
	select {
	case e := <-events:
		t.Fatalf("announced an unchanged index: %v", e)
	case <-time.After(200 * time.Millisecond):
	}
	// A stale revision is not applied.
	stale := call(t, h, mine, "apps_put", map[string]any{"apps": []any{}, "revision": 999})
	if stale["conflict"] != true || wint(stale["revision"]) != wint(put["revision"]) {
		t.Fatalf("stale write %v", stale)
	}
	if other := call(t, h, theirs, "get", map[string]any{}); other["apps"] != nil {
		t.Fatalf("another Fleet reads this index: %v", other)
	}
}

// Icons too large for the index are stored by hash, sent only when missing,
// and deleted with the App's entry.
func TestLargeIconsAreStoredBesideTheIndex(t *testing.T) {
	url := jetStream(t)
	h, mine, theirs := desktopHost(t, desktopOn(t, url))
	icon := "data:image/png;base64," + strings.Repeat("A", 40<<10)
	hash := iconHash(icon)
	put := call(t, h, mine, "apps_put", map[string]any{"apps": []any{appEntry("viewer", map[string]any{"icon_hash": hash})}})
	if missing := put["missing_icons"].([]any); len(missing) != 1 || missing[0] != "viewer" {
		t.Fatalf("missing %v", put)
	}
	if got := call(t, h, mine, "apps_icon", map[string]any{"app_id": "viewer"}); got["icon"] != nil {
		t.Fatalf("icon before it was sent: %v", got)
	}
	if reason := refused(t, h, mine, "apps_icon_put", map[string]any{"app_id": "viewer", "icon": icon + "B"}); !strings.Contains(reason, "names no such icon") {
		t.Fatalf("an icon the index does not name: %s", reason)
	}
	refused(t, h, mine, "apps_icon_put", map[string]any{"app_id": "viewer", "icon": "javascript:alert(1)"})
	refused(t, h, theirs, "apps_icon_put", map[string]any{"app_id": "viewer", "icon": icon})
	events := listen(t, url)
	if stored := call(t, h, mine, "apps_icon_put", map[string]any{"app_id": "viewer", "icon": icon}); stored["hash"] != hash {
		t.Fatalf("stored %v", stored)
	}
	if e := next(t, events); e["type"] != "desktop.icon" || e["app_id"] != "viewer" || e["hash"] != hash {
		t.Fatalf("event %v", e)
	}
	h2, _, _ := desktopHost(t, desktopOn(t, url))
	if got := call(t, h2, mine, "apps_icon", map[string]any{"app_id": "viewer"}); got["icon"] != icon || got["hash"] != hash {
		t.Fatalf("apps_icon %v", got["hash"])
	}
	if again := call(t, h2, mine, "apps_put", map[string]any{"apps": []any{appEntry("viewer", map[string]any{"icon_hash": hash})}}); len(again["missing_icons"].([]any)) != 0 {
		t.Fatalf("a stored icon is reported missing: %v", again)
	}
	call(t, h2, mine, "apps_put", map[string]any{"apps": []any{}})
	if got := call(t, h, mine, "apps_icon", map[string]any{"app_id": "viewer"}); got["icon"] != nil {
		t.Fatal("the icon of an uninstalled App is kept")
	}
}

func TestTheAppIndexIsChecked(t *testing.T) {
	h, mine, _ := desktopHost(t, desktopOn(t, jetStream(t)))
	for args, want := range map[string]string{
		`{"apps": {"id": "a"}}`:                                                         "list of App entries",
		`{"apps": [{"name": "no id"}]}`:                                                 "needs an id",
		`{"apps": [{"id": "a.b"}]}`:                                                     "needs an id",
		`{"apps": [{"id": "a"}, {"id": "a"}]}`:                                          "listed twice",
		`{"apps": [{"id": "a", "icon_hash": "zz"}]}`:                                    "icon_hash",
		`{"apps": [], "fleet": "f_0123456789abcdef"}`:                                   "invalid arguments",
		`{"apps": [{"id": "a", "pad": "` + strings.Repeat("x", MaxDesktopApps) + `"}]}`: "the limit is 128 KB",
	} {
		var decoded map[string]any
		if err := json.Unmarshal([]byte(args), &decoded); err != nil {
			t.Fatal(err)
		}
		if reason := refused(t, h, mine, "apps_put", decoded); !strings.Contains(reason, want) {
			t.Fatalf("%.60s: %s", args, reason)
		}
	}
}
