package hosted

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/aristoteleo/pantheonos/os/internal/auth"
	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

const otherFleet = "f_fedcba9876543210"

// jetStream runs an in-process broker with JetStream and returns its URL.
func jetStream(t *testing.T) string {
	t.Helper()
	s, err := server.NewServer(&server.Options{Host: "127.0.0.1", Port: -1, JetStream: true,
		StoreDir: t.TempDir(), NoLog: true, NoSigs: true})
	if err != nil {
		t.Fatal(err)
	}
	go s.Start()
	if !s.ReadyForConnections(10 * time.Second) {
		t.Fatal("broker did not start")
	}
	t.Cleanup(s.Shutdown)
	return s.ClientURL()
}

func dial(t *testing.T, url string, opts ...nats.Option) *nats.Conn {
	t.Helper()
	nc, err := nats.Connect(url, opts...)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(nc.Close)
	return nc
}

func desktopOn(t *testing.T, url string) *DesktopState {
	t.Helper()
	nc := dial(t, url)
	return &DesktopState{Backups: filepath.Join(t.TempDir(), "backups"), Connect: func() (*nats.Conn, error) { return nc, nil }}
}

// desktopHost serves desktop-state for fleetID and otherFleet, as the
// controller does for every owner.
func desktopHost(t *testing.T, d *DesktopState) (*Host, apptransport.Binding, apptransport.Binding) {
	t.Helper()
	store := storeWith(t, map[string]deployments.AppSpec{"desktop-state": {Hosted: "desktop-state", Scope: "desktop-state", Intent: deployments.Running}})
	if _, err := store.Put(otherFleet, "general-team", 0, deployments.Spec{Release: deployments.Release{URL: "https://example.test/r.tar.gz", SHA256: releaseOK},
		Apps: map[string]deployments.AppSpec{"desktop-state": {Hosted: "desktop-state", Scope: "desktop-state", Intent: deployments.Running}}}); err != nil {
		t.Fatal(err)
	}
	h := NewHost(store)
	h.Register("desktop-state", d)
	mine, _ := Binding(fleetID, "general-team", "desktop-state", "desktop-state")
	theirs, _ := Binding(otherFleet, "general-team", "desktop-state", "desktop-state")
	return h, mine, theirs
}

func call(t *testing.T, h *Host, b apptransport.Binding, method string, args any) map[string]any {
	t.Helper()
	status, out := rpc(t, h, b, method, args)
	if status != http.StatusOK || out["success"] != true {
		t.Fatalf("%s %v: %d %v", method, args, status, out)
	}
	result, _ := out["result"].(map[string]any)
	return result
}

func refused(t *testing.T, h *Host, b apptransport.Binding, method string, args any) string {
	t.Helper()
	status, out := rpc(t, h, b, method, args)
	if out["success"] != false {
		t.Fatalf("%s %v was accepted: %d %v", method, args, status, out)
	}
	return fmt.Sprint(out["error"])
}

func windowsOf(result map[string]any) map[string]any {
	return result["session"].(map[string]any)["windows"].(map[string]any)
}

// B5: a desktop is the caller's Fleet's, from the binding it was admitted at;
// no argument names another.
func TestDesktopStateIsScopedToTheCallersFleet(t *testing.T) {
	url := jetStream(t)
	h, mine, theirs := desktopHost(t, desktopOn(t, url))
	opened := call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "files"}})
	call(t, h, mine, "settings_put", map[string]any{"settings": map[string]any{"wallpaper": "dunes"}})
	call(t, h, mine, "appearance_put", map[string]any{"preference": "dark"})

	other := call(t, h, theirs, "get", map[string]any{})
	if len(windowsOf(other)) != 0 || other["settings"] != nil || other["appearance"] != nil {
		t.Fatalf("another Fleet read this desktop: %v", other)
	}
	if !strings.HasPrefix(fmt.Sprint(other["subject"]), "fleet."+otherFleet+".apps.desktop-state.") {
		t.Fatalf("subject %v", other["subject"])
	}
	// Writing someone else's window: there is no such window in the caller's desktop.
	if closed := call(t, h, theirs, "intent", map[string]any{"kind": "close", "args": map[string]any{"window_id": opened["window_id"]}}); closed["closed"] != false {
		t.Fatalf("closed another Fleet's window: %v", closed)
	}
	refused(t, h, theirs, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": opened["window_id"], "x": 1}})
	for _, args := range []map[string]any{{"fleet": fleetID}, {"user": "someone"}, {"scope": fleetID + ".owner"}} {
		refused(t, h, theirs, "get", args)
	}
	mineNow := call(t, h, mine, "get", map[string]any{})
	if len(windowsOf(mineNow)) != 1 || mineNow["appearance"].(map[string]any)["preference"] != "dark" {
		t.Fatalf("the owner's desktop changed: %v", mineNow)
	}

	// Nor can another Fleet's bus credential name the bucket.
	kv, err := jetstream.New(dial(t, url))
	if err != nil {
		t.Fatal(err)
	}
	bucket, _ := kv.KeyValue(context.Background(), DesktopBucket)
	if _, err := bucket.Get(context.Background(), fleetID+".owner.meta"); err != nil {
		t.Fatalf("the keys are not where the doc says: %v", err)
	}
}

// B2: two viewports' moves of two different windows both stick, and a third
// viewport hears every change.
func TestConcurrentIntentsAllLandAndAreAnnounced(t *testing.T) {
	url := jetStream(t)
	// Two service instances on one bucket: their writes meet only at the
	// revision checks, as two controller processes would.
	a, b := desktopOn(t, url), desktopOn(t, url)
	ha, mine, _ := desktopHost(t, a)
	hb, _, _ := desktopHost(t, b)
	first := call(t, ha, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "files"}})["window_id"]
	second := call(t, hb, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "terminal"}})["window_id"]
	if first == second {
		t.Fatal("two services minted one id")
	}
	viewer := dial(t, url)
	events := make(chan map[string]any, 256)
	sub, err := viewer.Subscribe("fleet."+fleetID+".apps.desktop-state.owner", func(m *nats.Msg) {
		var e map[string]any
		_ = json.Unmarshal(m.Data, &e)
		events <- e
	})
	if err != nil {
		t.Fatal(err)
	}
	defer sub.Unsubscribe()
	_ = viewer.Flush()

	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(2)
		go func(i int) {
			defer wg.Done()
			call(t, ha, mine, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": first, "x": 100 + i, "y": 10}})
		}(i)
		go func(i int) {
			defer wg.Done()
			call(t, hb, mine, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": second, "x": 500 + i, "y": 20}})
		}(i)
	}
	wg.Wait()
	// Read through a third service: each one's cache only knows its own writes.
	hc, _, _ := desktopHost(t, desktopOn(t, url))
	final := call(t, hc, mine, "get", map[string]any{})
	session := final["session"].(map[string]any)
	windows := windowsOf(final)
	if wint(windows[first.(string)].(map[string]any)["y"]) != 10 || wint(windows[second.(string)].(map[string]any)["y"]) != 20 {
		t.Fatalf("a move was lost: %v", windows)
	}
	if wint(session["seq"]) != 22 {
		t.Fatalf("seq %v after 22 intents", session["seq"])
	}
	seen := map[int64]bool{}
	for deadline := time.After(settle); len(seen) < 20; {
		select {
		case e := <-events:
			if e["type"] != "desktop.delta" {
				t.Fatalf("event %v", e)
			}
			seen[wint(e["seq"])] = true
		case <-deadline:
			t.Fatalf("heard %d of 20 changes", len(seen))
		}
	}
}

// B6: a window over 64 KB, a 201st window and settings over 256 KB are
// refused with errors that say why.
func TestDesktopLimitsAreEnforced(t *testing.T) {
	h, mine, _ := desktopHost(t, desktopOn(t, jetStream(t)))
	big := strings.Repeat("x", MaxDesktopWindow)
	reason := refused(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes", "args": map[string]any{"text": big}}})
	if !strings.Contains(reason, "limited to 64 KB") {
		t.Fatalf("reason %q", reason)
	}
	id := call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes"}})["window_id"]
	reason = refused(t, h, mine, "intent", map[string]any{"kind": "set", "args": map[string]any{"window_id": id, "patch": map[string]any{"args": map[string]any{"text": big}}}})
	if !strings.Contains(reason, "limited to 64 KB") {
		t.Fatalf("reason %q", reason)
	}
	if got := call(t, h, mine, "get", map[string]any{}); len(windowsOf(got)) != 1 || wint(got["session"].(map[string]any)["seq"]) != 1 {
		t.Fatalf("a refused intent was stored: %v", got)
	}
	for i := 1; i < MaxDesktopWindows; i++ {
		call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes"}})
	}
	if reason := refused(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes"}}); !strings.Contains(reason, "at most 200 windows") {
		t.Fatalf("reason %q", reason)
	}
	// Reusing a window is not another window.
	call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes", "window_id": id}})
	reason = refused(t, h, mine, "settings_put", map[string]any{"settings": map[string]any{"blob": strings.Repeat("y", MaxDesktopSettings)}})
	if !strings.Contains(reason, "limit is 256 KB") {
		t.Fatalf("reason %q", reason)
	}
	refused(t, h, mine, "appearance_put", map[string]any{"preference": "neon"})
}

func TestSettingsMergeAndAStaleRevisionIsNotApplied(t *testing.T) {
	h, mine, _ := desktopHost(t, desktopOn(t, jetStream(t)))
	put := call(t, h, mine, "settings_put", map[string]any{"settings": map[string]any{"dock": []string{"files"}, "wallpaper": "dunes"}})
	call(t, h, mine, "settings_put", map[string]any{"patch": map[string]any{"wallpaper": "sea", "clock": "24h", "dock": nil}})
	got := call(t, h, mine, "get", map[string]any{})
	settings := got["settings"].(map[string]any)
	if settings["wallpaper"] != "sea" || settings["clock"] != "24h" || settings["dock"] != nil {
		t.Fatalf("%v", settings)
	}
	stale := call(t, h, mine, "settings_put", map[string]any{"settings": map[string]any{"wallpaper": "old"}, "revision": put["revision"]})
	if stale["conflict"] != true || stale["settings"].(map[string]any)["wallpaper"] != "sea" {
		t.Fatalf("a stale write was applied: %v", stale)
	}
	fresh := call(t, h, mine, "settings_put", map[string]any{"settings": map[string]any{"wallpaper": "new"}, "revision": got["settings_revision"]})
	if fresh["conflict"] != nil || fresh["settings"].(map[string]any)["wallpaper"] != "new" {
		t.Fatalf("%v", fresh)
	}
	refused(t, h, mine, "settings_put", map[string]any{})
	refused(t, h, mine, "settings_put", map[string]any{"settings": []string{"x"}})
}

// B4: Workspace files are adopted once, while the user's keys are absent;
// later the bucket wins.
func TestImportAdoptsWorkspaceFilesOnlyWhileAbsent(t *testing.T) {
	h, mine, _ := desktopHost(t, desktopOn(t, jetStream(t)))
	record := map[string]any{}
	_ = json.Unmarshal([]byte(`{"v": 2, "boot": "life-1", "seq": 4, "nominal_w": 1600, "nominal_h": 1000,
		"nominal_locked": true, "spaces": 2, "top_z": 13, "next_id": 4, "windows": [
		{"id": "win-1", "app_id": "files", "title": "Files", "x": 40, "y": 50, "z": 12, "space": 2},
		{"id": "win-3", "app_id": "agent-view", "title": "view", "z": 13}]}`), &record)
	out := call(t, h, mine, "import", map[string]any{"session": record, "settings": map[string]any{"wallpaper": "dunes"},
		"appearance": map[string]any{"preference": "light"}, "boot": "life-1"})
	if !jsonEqual(out["imported"], map[string]any{"session": true, "settings": true, "appearance": true}) {
		t.Fatalf("%v", out)
	}
	got := call(t, h, mine, "get", map[string]any{})
	session := got["session"].(map[string]any)
	if wint(session["nominal_w"]) != 1600 || session["nominal_locked"] != true || wint(session["spaces"]) != 2 || len(windowsOf(got)) != 2 {
		t.Fatalf("%v", session)
	}
	if got["settings"].(map[string]any)["wallpaper"] != "dunes" || got["appearance"].(map[string]any)["preference"] != "light" {
		t.Fatalf("%v", got)
	}
	if id := call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "terminal"}})["window_id"]; id == "win-1" || id == "win-3" {
		t.Fatalf("an imported id was minted again: %v", id)
	}
	// A second import (another tab, a restarted Desktop App) changes nothing.
	again := call(t, h, mine, "import", map[string]any{"session": map[string]any{"v": 2, "windows": []any{}},
		"settings": map[string]any{"wallpaper": "plain"}, "appearance": map[string]any{"preference": "dark"}, "boot": "life-1"})
	if !jsonEqual(again["imported"], map[string]any{"session": false, "settings": false, "appearance": false}) {
		t.Fatalf("%v", again)
	}
	if got := call(t, h, mine, "get", map[string]any{}); len(windowsOf(got)) != 3 || got["settings"].(map[string]any)["wallpaper"] != "dunes" {
		t.Fatalf("an import overwrote the bucket: %v", got)
	}
	// A new Workspace container claims the desktop: its agent view is gone.
	call(t, h, mine, "import", map[string]any{"boot": "life-2"})
	windows := windowsOf(call(t, h, mine, "get", map[string]any{}))
	if _, ok := windows["win-3"]; ok || len(windows) != 2 {
		t.Fatalf("a container-bound window outlived its container: %v", windows)
	}
}

func jsonEqual(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return string(x) == string(y)
}

// B7: a day's snapshot restored into an empty bucket brings the desktop back;
// only seven days are kept.
func TestSnapshotRestoresIntoAnEmptyBucket(t *testing.T) {
	d := desktopOn(t, jetStream(t))
	h, mine, theirs := desktopHost(t, d)
	call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "files", "title": "Files"}})
	closed := call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes"}})["window_id"]
	call(t, h, mine, "intent", map[string]any{"kind": "close", "args": map[string]any{"window_id": closed}})
	call(t, h, mine, "intent", map[string]any{"kind": "nominal", "args": map[string]any{"w": 1500, "h": 950, "mode": "set"}})
	call(t, h, mine, "settings_put", map[string]any{"settings": map[string]any{"wallpaper": "dunes"}})
	call(t, h, theirs, "appearance_put", map[string]any{"preference": "dark"})
	before, other := call(t, h, mine, "get", map[string]any{}), call(t, h, theirs, "get", map[string]any{})

	day := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	path, n, err := d.Snapshot(context.Background(), day)
	// meta, the open window and the settings of one desktop; the other's appearance.
	if err != nil || n != 4 || filepath.Base(path) != "desktop-2026-11-02.json" {
		t.Fatalf("%s %d %v", path, n, err)
	}
	if info, err := os.Stat(path); err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("%v %v", info, err)
	}

	empty := desktopOn(t, jetStream(t))
	restored, kept, err := empty.Restore(context.Background(), path)
	if err != nil || restored != 4 || kept != 0 {
		t.Fatalf("%d %d %v", restored, kept, err)
	}
	h2, mine2, theirs2 := desktopHost(t, empty)
	after := call(t, h2, mine2, "get", map[string]any{})
	if !jsonEqual(after["session"], before["session"]) || !jsonEqual(after["settings"], before["settings"]) ||
		!jsonEqual(call(t, h2, theirs2, "get", map[string]any{})["appearance"], other["appearance"]) {
		t.Fatalf("restored %v, was %v", after, before)
	}
	// Restoring again keeps what the bucket has.
	if restored, kept, _ := empty.Restore(context.Background(), path); restored != 0 || kept != 4 {
		t.Fatalf("%d %d", restored, kept)
	}

	for i := 1; i <= 9; i++ {
		if _, _, err := d.Snapshot(context.Background(), day.AddDate(0, 0, i)); err != nil {
			t.Fatal(err)
		}
	}
	files, _ := filepath.Glob(filepath.Join(d.Backups, "desktop-*.json"))
	if len(files) != KeepDesktopSnapshots || filepath.Base(files[0]) != "desktop-2026-11-05.json" {
		t.Fatalf("kept %v", files)
	}
}

// The service's own bus credential reaches the bucket and the owners' App
// namespaces; a Fleet's credentials cannot name the bucket.
func TestOnlyTheDesktopStateCredentialReachesTheBucket(t *testing.T) {
	root := t.TempDir()
	authority, err := auth.Bootstrap(filepath.Join(root, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := l.Addr().String()
	l.Close()
	config := filepath.Join(root, "nats.conf")
	if err := os.WriteFile(config, []byte(authority.ServerConfig(address, filepath.Join(root, "js"))), 0o600); err != nil {
		t.Fatal(err)
	}
	opts, err := server.ProcessConfigFile(config)
	if err != nil {
		t.Fatal(err)
	}
	opts.NoLog, opts.NoSigs = true, true
	s, err := server.NewServer(opts)
	if err != nil {
		t.Fatal(err)
	}
	go s.Start()
	if !s.ReadyForConnections(10 * time.Second) {
		t.Fatal("broker did not start")
	}
	t.Cleanup(s.Shutdown)

	service, err := authority.MintDesktopState(time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	nc := dial(t, "nats://"+address, nats.UserCredentialBytes(service), nats.CustomInboxPrefix(auth.DesktopStateInbox))
	d := &DesktopState{Connect: func() (*nats.Conn, error) { return nc, nil }}
	h, mine, _ := desktopHost(t, d)
	owner, err := authority.MintFleetApps(fleetID, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	browser := dial(t, "nats://"+address, nats.UserJWTAndSeed(owner.JWT, owner.Seed), nats.CustomInboxPrefix(owner.InboxPrefix))
	heard := make(chan *nats.Msg, 4)
	if _, err := browser.ChanSubscribe("fleet."+fleetID+".apps.desktop-state.owner", heard); err != nil {
		t.Fatal(err)
	}
	_ = browser.Flush()
	call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "files"}})
	select {
	case <-heard:
	case <-time.After(settle):
		t.Fatal("the owner's browser did not hear the change")
	}

	for name, creds := range map[string]func() ([]byte, error){
		"fleet user": func() ([]byte, error) { return authority.MintFleetUser(fleetID) },
		"node":       func() ([]byte, error) { return authority.MintFleetNode(fleetID, "n_1") },
	} {
		raw, err := creds()
		if err != nil {
			t.Fatal(err)
		}
		other := dial(t, "nats://"+address, nats.UserCredentialBytes(raw), nats.CustomInboxPrefix("_INBOX_"+fleetID))
		js, _ := jetstream.New(other)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		if _, err := js.KeyValue(ctx, DesktopBucket); err == nil {
			t.Fatalf("a %s credential opened the bucket", name)
		}
		cancel()
	}
}

// The service is the bucket's only writer: a desktop is read once and then
// served from memory, read again after a conflict, a refusal or idleness.
func TestADesktopIsReadOnceThenReadAgainOnlyWhenItMayHaveChanged(t *testing.T) {
	url := jetStream(t)
	d := desktopOn(t, url)
	clock := time.Now()
	d.now = func() time.Time { return clock }
	h, mine, _ := desktopHost(t, d)
	id := call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "files"}})["window_id"]
	for i := 0; i < 20; i++ {
		call(t, h, mine, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": id, "x": i, "y": i}})
	}
	call(t, h, mine, "settings_put", map[string]any{"patch": map[string]any{"wallpaper": "dunes"}})
	call(t, h, mine, "appearance_put", map[string]any{"preference": "dark"})
	if got := call(t, h, mine, "get", map[string]any{}); wint(windowsOf(got)[id.(string)].(map[string]any)["x"]) != 19 ||
		got["settings"].(map[string]any)["wallpaper"] != "dunes" || got["appearance"].(map[string]any)["preference"] != "dark" {
		t.Fatalf("%v", got)
	}
	if n := d.loads.Load(); n != 1 {
		t.Fatalf("%d reads (watchers) for one desktop", n)
	}

	// Another writer: the next write conflicts, which reads the desktop again
	// and applies the intent on what that writer left.
	other := desktopOn(t, url)
	ho, _, _ := desktopHost(t, other)
	call(t, ho, mine, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": id, "x": 700, "y": 70}})
	call(t, h, mine, "intent", map[string]any{"kind": "resize", "args": map[string]any{"window_id": id, "width": 900, "height": 600}})
	if n := d.loads.Load(); n != 2 {
		t.Fatalf("a conflict read the desktop %d times", n-1)
	}
	fresh, _, _ := desktopHost(t, desktopOn(t, url))
	w := windowsOf(call(t, fresh, mine, "get", map[string]any{}))[id.(string)].(map[string]any)
	if wint(w["x"]) != 700 || wint(w["y"]) != 70 || wint(w["width"]) != 900 {
		t.Fatalf("the other writer's move or this resize was lost: %v", w)
	}

	// A refused intent may have touched the cached copy: it is dropped.
	refused(t, h, mine, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": id, "x": "left"}})
	call(t, h, mine, "get", map[string]any{})
	if n := d.loads.Load(); n != 3 {
		t.Fatalf("after a refusal: %d reads", n)
	}
	// Idle desktops are dropped.
	call(t, h, mine, "get", map[string]any{})
	clock = clock.Add(desktopIdle + time.Minute)
	call(t, h, mine, "get", map[string]any{})
	if n := d.loads.Load(); n != 4 {
		t.Fatalf("an idle desktop stayed cached: %d reads", n)
	}
}

// A desktop's window records and meta stay within 448 KB, so the Desktop
// App's dependency call can always read it; shrinking it is always allowed.
func TestADesktopIsLimitedInTotalSize(t *testing.T) {
	h, mine, _ := desktopHost(t, desktopOn(t, jetStream(t)))
	payload := strings.Repeat("z", 30<<10)
	var ids []any
	var reason string
	for i := 0; i < 20; i++ {
		status, out := rpc(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes", "args": map[string]any{"text": payload}}})
		if out["success"] != true {
			reason = fmt.Sprint(out["error"])
			break
		}
		if status != http.StatusOK {
			t.Fatal(status)
		}
		ids = append(ids, out["result"].(map[string]any)["window_id"])
	}
	if len(ids) != 14 || !strings.Contains(reason, "a desktop is limited to 448 KB") {
		t.Fatalf("%d windows, reason %q", len(ids), reason)
	}
	// Moving and closing still work at the limit.
	call(t, h, mine, "intent", map[string]any{"kind": "move", "args": map[string]any{"window_id": ids[0], "x": 5, "y": 5}})
	call(t, h, mine, "intent", map[string]any{"kind": "close", "args": map[string]any{"window_id": ids[0]}})
	call(t, h, mine, "intent", map[string]any{"kind": "open", "args": map[string]any{"app_id": "notes", "args": map[string]any{"text": payload}}})

	// An import over the limit keeps the windows on top and reports the rest.
	h2, mine2, _ := desktopHost(t, desktopOn(t, jetStream(t)))
	var windows []any
	for i := 1; i <= 20; i++ {
		windows = append(windows, map[string]any{"id": fmt.Sprintf("win-%d", i), "app_id": "notes", "z": i, "args": map[string]any{"text": payload}})
	}
	out := call(t, h2, mine2, "import", map[string]any{"session": map[string]any{"v": 2, "windows": windows}})
	if dropped := out["dropped"].([]any); len(dropped) != 6 || dropped[0] != "win-6" {
		t.Fatalf("dropped %v", dropped)
	}
	if got := windowsOf(call(t, h2, mine2, "get", map[string]any{})); len(got) != 14 || got["win-20"] == nil {
		t.Fatalf("%d windows", len(got))
	}
}
