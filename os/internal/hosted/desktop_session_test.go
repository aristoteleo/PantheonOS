package hosted

// The desktop session reducer, with the cases of tests/test_desktop_session.py
// (the Python reducer it ports). Persistence cases run against the bucket in
// desktopstate_test.go.

import (
	"encoding/json"
	"errors"
	"reflect"
	"testing"
)

func intentOK(t *testing.T, s *deskSession, kind string, args map[string]any) ([]map[string]any, map[string]any) {
	t.Helper()
	// Arguments arrive as JSON.
	ops, result, err := s.apply(kind, cloneJSON(args))
	if err != nil {
		t.Fatalf("%s %v: %v", kind, args, err)
	}
	return ops, result
}

func opened(t *testing.T, s *deskSession, args map[string]any) string {
	t.Helper()
	_, result := intentOK(t, s, "open", args)
	return result["window_id"].(string)
}

func wint(v any) int64 {
	n, _ := pyInt(v)
	return n
}

func TestOpenMintsAnIDAndBumpsSeq(t *testing.T) {
	s := newDeskSession()
	ops, result := intentOK(t, s, "open", map[string]any{"app_id": "files", "title": "Files"})
	if result["window_id"] != "win-1" || result["reused"] != false {
		t.Fatalf("result %v", result)
	}
	if len(ops) != 1 || ops[0]["op"] != "upsert" || s.Seq != 1 || ops[0]["seq"] != int64(1) {
		t.Fatalf("ops %v seq %d", ops, s.Seq)
	}
}

func TestIDsAreTheDesktopsSoTwoViewportsCannotDisagree(t *testing.T) {
	s := newDeskSession()
	if opened(t, s, map[string]any{"app_id": "files"}) == opened(t, s, map[string]any{"app_id": "terminal"}) {
		t.Fatal("two windows share an id")
	}
}

func TestOpenWithAWindowIDReusesIt(t *testing.T) {
	s := newDeskSession()
	id := opened(t, s, map[string]any{"app_id": "vitessce"})
	ops, result := intentOK(t, s, "open", map[string]any{"app_id": "vitessce", "window_id": id, "path": "/w/other.h5ad"})
	if !reflect.DeepEqual(result, map[string]any{"window_id": id, "reused": true}) || len(s.Windows) != 1 {
		t.Fatalf("%v %d", result, len(s.Windows))
	}
	if ops[0]["window"].(map[string]any)["path"] != "/w/other.h5ad" {
		t.Fatalf("path %v", ops[0])
	}
}

func TestMoveAndResizeClampToTheFloor(t *testing.T) {
	s := newDeskSession()
	id := opened(t, s, map[string]any{"app_id": "files"})
	intentOK(t, s, "move", map[string]any{"window_id": id, "x": -50, "y": -10})
	intentOK(t, s, "resize", map[string]any{"window_id": id, "width": 10, "height": 10})
	w := s.Windows[id]
	if wint(w["x"]) != 0 || wint(w["y"]) != 0 || wint(w["width"]) != 320 || wint(w["height"]) != 180 {
		t.Fatalf("%v", w)
	}
}

func TestCloseRemovesAndReports(t *testing.T) {
	s := newDeskSession()
	id := opened(t, s, map[string]any{"app_id": "files"})
	ops, result := intentOK(t, s, "close", map[string]any{"window_id": id})
	if result["closed"] != true || len(s.Windows) != 0 ||
		!reflect.DeepEqual(ops, []map[string]any{{"op": "remove", "id": id, "seq": s.Seq}}) {
		t.Fatalf("%v %v", ops, result)
	}
}

func TestClosingWhatIsGoneChangesNothing(t *testing.T) {
	s := newDeskSession()
	ops, result := intentOK(t, s, "close", map[string]any{"window_id": "win-404"})
	if result["closed"] != false || len(ops) != 0 || s.Seq != 0 {
		t.Fatalf("%v %v %d", ops, result, s.Seq)
	}
}

func TestRetiringASpaceCarriesItsWindowsDown(t *testing.T) {
	s := newDeskSession()
	intentOK(t, s, "spaces", map[string]any{"count": 3})
	id := opened(t, s, map[string]any{"app_id": "files", "space": 3})
	if wint(s.Windows[id]["space"]) != 3 {
		t.Fatalf("%v", s.Windows[id])
	}
	intentOK(t, s, "spaces", map[string]any{"count": 1})
	if wint(s.Windows[id]["space"]) != 1 {
		t.Fatalf("%v", s.Windows[id])
	}
}

func nominal(w, h int64, locked bool) map[string]any {
	return map[string]any{"nominal_w": w, "nominal_h": h, "nominal_locked": locked}
}

// The desktop is as large as the largest screen looking at it: a narrow
// panel attaching does not shrink the page's desktop.
func TestNominalSizeOnlyGrows(t *testing.T) {
	s := newDeskSession()
	if _, r := intentOK(t, s, "nominal", map[string]any{"w": 1700, "h": 1000}); !reflect.DeepEqual(r, nominal(1700, 1000, false)) {
		t.Fatalf("%v", r)
	}
	ops, r := intentOK(t, s, "nominal", map[string]any{"w": 660, "h": 900})
	if !reflect.DeepEqual(r, nominal(1700, 1000, false)) || len(ops) != 0 {
		t.Fatalf("%v %v", ops, r)
	}
}

// A person choosing in Settings is not a viewport proposing.
func TestNominalSetMayShrink(t *testing.T) {
	s := newDeskSession()
	intentOK(t, s, "nominal", map[string]any{"w": 1920, "h": 1080})
	ops, r := intentOK(t, s, "nominal", map[string]any{"w": 1280, "h": 800, "mode": "set"})
	if !reflect.DeepEqual(r, nominal(1280, 800, true)) {
		t.Fatalf("%v", r)
	}
	delete(ops[0], "seq")
	if !reflect.DeepEqual(ops, []map[string]any{{"op": "meta", "nominal_w": int64(1280), "nominal_h": int64(800), "nominal_locked": true}}) {
		t.Fatalf("%v", ops)
	}
}

// The lock: a proposal from a larger screen must not undo a choice.
func TestAChoiceSurvivesTheNextViewport(t *testing.T) {
	s := newDeskSession()
	intentOK(t, s, "nominal", map[string]any{"w": 1280, "h": 800, "mode": "set"})
	ops, r := intentOK(t, s, "nominal", map[string]any{"w": 2048, "h": 1035})
	if r["nominal_w"] != int64(1280) || r["nominal_h"] != int64(800) || len(ops) != 0 {
		t.Fatalf("%v %v", ops, r)
	}
	if _, r = intentOK(t, s, "nominal", map[string]any{"w": 2048, "h": 1035, "mode": "auto"}); !reflect.DeepEqual(r, nominal(2048, 1035, false)) {
		t.Fatalf("%v", r)
	}
	if _, r = intentOK(t, s, "nominal", map[string]any{"w": 2560, "h": 1440}); r["nominal_w"] != int64(2560) {
		t.Fatalf("%v", r)
	}
}

func TestNominalSetIsFlooredAndIdempotent(t *testing.T) {
	s := newDeskSession()
	if _, r := intentOK(t, s, "nominal", map[string]any{"w": 10, "h": 10, "mode": "set"}); !reflect.DeepEqual(r, nominal(640, 480, true)) {
		t.Fatalf("%v", r)
	}
	if ops, _ := intentOK(t, s, "nominal", map[string]any{"w": 640, "h": 480, "mode": "set"}); len(ops) != 0 {
		t.Fatalf("%v", ops)
	}
}

func TestUnknownIntentAndMissingWindowAreRefused(t *testing.T) {
	s := newDeskSession()
	var refused *intentError
	if _, _, err := s.apply("teleport", map[string]any{}); !errors.As(err, &refused) {
		t.Fatalf("%v", err)
	}
	if _, _, err := s.apply("move", map[string]any{"window_id": "win-404", "x": 1.0, "y": 1.0}); !errors.As(err, &refused) {
		t.Fatalf("%v", err)
	}
	if s.Seq != 0 {
		t.Fatal("a refused intent changed the desktop")
	}
}

// An open-ended merge would let a stale client write geometry back.
func TestSetPatchesOnlyNamedKeys(t *testing.T) {
	s := newDeskSession()
	id := opened(t, s, map[string]any{"app_id": "files"})
	intentOK(t, s, "set", map[string]any{"window_id": id, "patch": map[string]any{
		"title": "renamed", "minimized": true, "x": 9999, "nonsense": 1}})
	w := s.Windows[id]
	if w["title"] != "renamed" || w["minimized"] != true || wint(w["x"]) == 9999 {
		t.Fatalf("%v", w)
	}
	if _, ok := w["nonsense"]; ok {
		t.Fatalf("%v", w)
	}
}

// Full screen is two intents (mint the space, move the window onto it); a
// `set` that overtakes its `spaces` must still land the window there.
func TestMaximisingDoesNotStrandAWindowWhenSetOvertakesSpaces(t *testing.T) {
	s := newDeskSession()
	opened(t, s, map[string]any{"app_id": "terminal", "title": "Terminal"})
	intentOK(t, s, "set", map[string]any{"window_id": "win-1", "patch": map[string]any{"fullscreen": true, "space": 2}})
	if wint(s.Windows["win-1"]["space"]) != 2 || s.Spaces < 2 {
		t.Fatalf("demoted: %v spaces %d", s.Windows["win-1"], s.Spaces)
	}
	intentOK(t, s, "spaces", map[string]any{"count": 2})
	if wint(s.Windows["win-1"]["space"]) != 2 || s.Spaces != 2 {
		t.Fatalf("%v spaces %d", s.Windows["win-1"], s.Spaces)
	}
}

func TestAWindowCanNeverBeSentPastTheLastSpace(t *testing.T) {
	s := newDeskSession()
	opened(t, s, map[string]any{"app_id": "files", "title": "Files"})
	intentOK(t, s, "set", map[string]any{"window_id": "win-1", "patch": map[string]any{"space": 99}})
	if s.Spaces > 6 || wint(s.Windows["win-1"]["space"]) != s.Spaces {
		t.Fatalf("%v spaces %d", s.Windows["win-1"], s.Spaces)
	}
}

func TestBindBrowserCommitsOnlyAgainstTheExpectedPage(t *testing.T) {
	s := newDeskSession()
	id := opened(t, s, map[string]any{"app_id": "browser", "args": map[string]any{"page_id": "p1"}})
	if _, _, err := s.apply("bind_browser", map[string]any{"window_id": id, "expected_page_id": "other",
		"page_id": "p2", "operation_id": "op"}); err == nil {
		t.Fatal("a changed binding was overwritten")
	}
	_, r := intentOK(t, s, "bind_browser", map[string]any{"window_id": id, "expected_page_id": "p1",
		"page_id": "p2", "operation_id": "op", "url": "https://example.com"})
	if r["binding"].(map[string]any)["page_id"] != "p2" || browserPageReference(s.Windows[id]) != "p2" {
		t.Fatalf("%v %v", r, s.Windows[id])
	}
	if _, _, err := s.apply("bind_browser", map[string]any{"window_id": opened(t, s, map[string]any{"app_id": "files"}),
		"expected_page_id": "", "page_id": "p", "operation_id": "o"}); err == nil {
		t.Fatal("bound a page to a window that is not a Browser")
	}
}

// A record round-trips with its ids; a window opened after it never reuses one.
func TestARecordIsAdoptedWithItsIDs(t *testing.T) {
	record := map[string]any{}
	_ = json.Unmarshal([]byte(`{"v": 2, "boot": "b1", "seq": 7, "nominal_w": 1500, "nominal_h": 900,
		"nominal_locked": true, "spaces": 2, "top_z": 14, "next_id": 3, "windows": [
		{"id": "win-1", "app_id": "files", "title": "Files", "x": 10, "z": 12},
		{"id": "win-2", "app_id": "vitessce", "title": "pbmc", "x": 300, "y": 90, "z": 14},
		{"app_id": "terminal"}]}`), &record)
	s, err := fromRecord(record, "b2")
	if err != nil {
		t.Fatal(err)
	}
	if s.Seq != 7 || s.NominalW != 1500 || !s.NominalLocked || s.Spaces != 2 || len(s.Windows) != 2 {
		t.Fatalf("%+v", s.deskMeta)
	}
	if id := opened(t, s, map[string]any{"app_id": "terminal"}); id == "win-1" || id == "win-2" {
		t.Fatalf("reused %s", id)
	}
}

// Agent views and native dialogs live for one container: a record written in
// another lifetime comes back without them.
func TestContainerBoundWindowsOnlySurviveTheirLifetime(t *testing.T) {
	record := map[string]any{}
	_ = json.Unmarshal([]byte(`{"v": 2, "boot": "old", "windows": [
		{"id": "win-1", "app_id": "files"}, {"id": "win-2", "app_id": "agent-view"},
		{"id": "win-3", "app_id": "xwindow", "args": {"wid": 4194308}}, {"id": "win-4", "app_id": "browser"}]}`), &record)
	same, _ := fromRecord(record, "old")
	other, _ := fromRecord(record, "new")
	if len(same.Windows) != 4 || len(other.Windows) != 2 || other.Windows["win-4"] == nil {
		t.Fatalf("same %d other %v", len(same.Windows), other.Windows)
	}
	ops := same.dropEphemeral()
	if len(ops) != 2 || len(same.Windows) != 2 {
		t.Fatalf("%v", ops)
	}
}

// Upgrading must not throw away the layout the browser's v1 record had.
func TestAV1BrowserRecordIsAdoptedNotDropped(t *testing.T) {
	record := map[string]any{}
	_ = json.Unmarshal([]byte(`{"v": 1, "spaces": 2, "windows": [
		{"appId": "files", "title": "Files", "x": 10, "y": 20, "width": 700, "height": 500, "space": 2},
		{"appId": "agent-view", "title": "ephemeral"},
		{"appId": "xwindow", "title": "old dialog", "args": {"wid": 4194308}}]}`), &record)
	s, err := fromRecord(record, "")
	if err != nil || len(s.Windows) != 1 || s.Spaces != 2 {
		t.Fatalf("%v %v", s, err)
	}
	for _, w := range s.Windows {
		if wint(w["x"]) != 10 || wint(w["y"]) != 20 || wint(w["space"]) != 2 {
			t.Fatalf("%v", w)
		}
	}
}
