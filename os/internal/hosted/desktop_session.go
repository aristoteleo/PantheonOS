package hosted

// The desktop session reducer: the intents apps/desktop/desktop_session.py
// applies to one user's window document, ported rule for rule (its tests are
// ported in desktop_session_test.go). It is a pure function of the document;
// desktopstate.go reads the document from the DESKTOP bucket, applies one
// intent here and writes back what changed.

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// Container-bound windows: agent-view URLs point at the Workspace's tunnels
// and xwindow args at its native display. They are shared while that
// container lives and dropped when another one claims the desktop.
var ephemeralApps = map[string]bool{"agent-view": true, "xwindow": true}

const maxSpaces = 6

// deskMeta is everything about a desktop that is not one window.
type deskMeta struct {
	Seq           int64  `json:"seq"`
	NominalW      int64  `json:"nominal_w"`
	NominalH      int64  `json:"nominal_h"`
	NominalLocked bool   `json:"nominal_locked"`
	Spaces        int64  `json:"spaces"`
	TopZ          int64  `json:"top_z"`
	NextID        int64  `json:"next_id"`
	Boot          string `json:"boot,omitempty"`
}

func newDeskMeta() deskMeta {
	return deskMeta{NominalW: 1280, NominalH: 800, Spaces: 1, TopZ: 10, NextID: 1}
}

// deskSession is one user's window document.
type deskSession struct {
	deskMeta
	Windows map[string]map[string]any
}

func newDeskSession() *deskSession {
	return &deskSession{deskMeta: newDeskMeta(), Windows: map[string]map[string]any{}}
}

// snapshot is the document as viewports read it (desktop_session_get).
func (s *deskSession) snapshot() map[string]any {
	windows := map[string]any{}
	for id, w := range s.Windows {
		windows[id] = w
	}
	return map[string]any{"seq": s.Seq, "nominal_w": s.NominalW, "nominal_h": s.NominalH,
		"nominal_locked": s.NominalLocked, "spaces": s.Spaces, "top_z": s.TopZ, "windows": windows}
}

// mint returns an unused window id. The counter lives in the meta key, so
// two viewports can never mint the same id.
func (s *deskSession) mint() string {
	for {
		id := fmt.Sprintf("win-%d", s.NextID)
		s.NextID++
		if _, taken := s.Windows[id]; !taken {
			return id
		}
	}
}

func (s *deskSession) bumpZ() int64 {
	s.TopZ++
	return s.TopZ
}

// intentError is a refused intent: the Python reducer's KeyError (missing
// window) or ValueError (bad arguments).
type intentError struct{ msg string }

func (e *intentError) Error() string { return e.msg }

func refuse(format string, args ...any) error { return &intentError{fmt.Sprintf(format, args...)} }

// ── Python value semantics ─────────────────────────────────────────────────

// pyTruthy is Python's bool(v) for decoded JSON.
func pyTruthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case float64:
		return t != 0
	case int:
		return t != 0
	case int64:
		return t != 0
	case string:
		return t != ""
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

// pyInt is Python's int(v): numbers truncate, numeric strings parse.
func pyInt(v any) (int64, error) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case float64:
		if math.IsNaN(t) || math.IsInf(t, 0) {
			return 0, refuse("invalid number %v", t)
		}
		return int64(t), nil
	case int:
		return int64(t), nil
	case int64:
		return t, nil
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(t), 10, 64)
		if err != nil {
			return 0, refuse("invalid literal for int(): %q", t)
		}
		return n, nil
	}
	return 0, refuse("invalid number %v", v)
}

// intOr is int(v or fallback).
func intOr(v any, fallback int64) (int64, error) {
	if !pyTruthy(v) {
		return fallback, nil
	}
	return pyInt(v)
}

// intUnlessNone is int(v if v is not None else fallback).
func intUnlessNone(v any, fallback int64) (int64, error) {
	if v == nil {
		return fallback, nil
	}
	return pyInt(v)
}

// pyStr is Python's str(v) for decoded JSON.
func pyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case string:
		return t
	case bool:
		if t {
			return "True"
		}
		return "False"
	case float64:
		if t == math.Trunc(t) && math.Abs(t) < 1e15 {
			return strconv.FormatInt(int64(t), 10)
		}
		return strconv.FormatFloat(t, 'g', -1, 64)
	case int:
		return strconv.Itoa(t)
	case int64:
		return strconv.FormatInt(t, 10)
	}
	raw, _ := json.Marshal(v)
	return string(raw)
}

// strOr is str(v or fallback).
func strOr(v any, fallback string) string {
	if !pyTruthy(v) {
		return fallback
	}
	return pyStr(v)
}

func object(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

func maxInt(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

// browserPageReference is the authoritative native page identity of a
// Browser window, with old window records supported.
func browserPageReference(window map[string]any) string {
	args := object(window["args"])
	binding := object(args["browser_binding"])
	if _, ok := binding["page_id"]; ok {
		return strOr(binding["page_id"], "")
	}
	shared := object(object(args["shared"])["v"])
	if _, ok := shared["page"]; ok {
		return strOr(shared["page"], "")
	}
	if pyTruthy(args["page_id"]) {
		return pyStr(args["page_id"])
	}
	pages, _ := shared["pages"].([]any)
	active, ok := shared["active"]
	if !ok {
		active = 0.0
	}
	if n, isNum := active.(float64); isNum && n == math.Trunc(n) && n >= 0 && int(n) < len(pages) {
		if p := pages[int(n)]; pyTruthy(p) {
			return pyStr(p)
		}
	}
	for _, p := range pages {
		if pyTruthy(p) {
			return pyStr(p)
		}
	}
	return ""
}

// ── windows ────────────────────────────────────────────────────────────────

type windowSpec struct {
	appID, title, path, openedBy string
	args                         any
	x, y, width, height, space   any
	minimized, maximized         any
	fullscreen                   any
	z                            int64
}

func newWindow(w windowSpec) (map[string]any, error) {
	var (
		n   [5]int64
		err error
	)
	for i, v := range []struct {
		value    any
		fallback int64
	}{{w.x, 80}, {w.y, 64}, {w.width, 720}, {w.height, 460}, {w.space, 1}} {
		if n[i], err = intUnlessNone(v.value, v.fallback); err != nil {
			return nil, err
		}
	}
	args := w.args
	if args == nil {
		args = map[string]any{}
	}
	return map[string]any{
		"app_id": w.appID, "title": w.title, "path": w.path, "args": args,
		"x": n[0], "y": n[1], "width": n[2], "height": n[3], "space": n[4], "z": w.z,
		"minimized": pyTruthy(w.minimized), "maximized": pyTruthy(w.maximized), "fullscreen": pyTruthy(w.fullscreen),
		"status": "opening", "opened_by": w.openedBy, "updated_seq": int64(0),
	}, nil
}

// ── intents ────────────────────────────────────────────────────────────────

// apply runs one intent. Every change bumps seq; the ops are the delta
// viewports apply and the result goes back to the caller.
func (s *deskSession) apply(kind string, a map[string]any) ([]map[string]any, map[string]any, error) {
	if a == nil {
		a = map[string]any{}
	}
	handlers := map[string]func(map[string]any) ([]map[string]any, map[string]any, error){
		"open": s.doOpen, "close": s.doClose, "bind_browser": s.doBindBrowser, "move": s.doMove,
		"resize": s.doResize, "raise": s.doRaise, "set": s.doSet, "spaces": s.doSpaces, "nominal": s.doNominal,
	}
	handler, ok := handlers[kind]
	if !ok {
		return nil, nil, refuse("unknown desktop intent '%s'", kind)
	}
	ops, result, err := handler(a)
	if err != nil {
		return nil, nil, err
	}
	if len(ops) > 0 {
		s.Seq++
		for _, op := range ops {
			op["seq"] = s.Seq
		}
	}
	if ops == nil {
		ops = []map[string]any{}
	}
	return ops, result, nil
}

func (s *deskSession) touch(id string) (map[string]any, error) {
	w, ok := s.Windows[id]
	if !ok {
		return nil, refuse("no window '%s'", id)
	}
	w["updated_seq"] = s.Seq + 1
	return w, nil
}

func (s *deskSession) upsert(id string) map[string]any {
	return map[string]any{"op": "upsert", "id": id, "window": s.Windows[id]}
}

func (s *deskSession) doOpen(a map[string]any) ([]map[string]any, map[string]any, error) {
	appID := strOr(a["app_id"], "")
	if appID == "" {
		return nil, nil, refuse("open needs an app_id")
	}
	id := strOr(a["window_id"], "")
	if w, ok := s.Windows[id]; id != "" && ok {
		// Reuse: showing a different file in a window the user already has
		// beats piling up another one.
		if pyTruthy(a["args"]) {
			w["args"] = a["args"]
		}
		if pyTruthy(a["path"]) {
			w["path"] = a["path"]
		}
		if pyTruthy(a["title"]) {
			w["title"] = a["title"]
		}
		w["status"] = "opening"
		w["z"] = s.bumpZ()
		w["minimized"] = false
		w["updated_seq"] = s.Seq + 1
		return []map[string]any{s.upsert(id)}, map[string]any{"window_id": id, "reused": true}, nil
	}
	// Cascade, so a second window of the same app does not hide the first.
	offset := int64(len(s.Windows)%8) * 28
	x, ok := a["x"]
	if !ok {
		x = float64(80 + offset)
	}
	y, ok := a["y"]
	if !ok {
		y = float64(64 + offset)
	}
	space, err := intOr(a["space"], 1)
	if err != nil {
		return nil, nil, err
	}
	z := s.bumpZ()
	id = s.mint()
	w, err := newWindow(windowSpec{appID: appID, args: orEmptyArgs(a["args"]), title: strOr(a["title"], appID),
		z: z, path: strOr(a["path"], ""), x: x, y: y, width: a["width"], height: a["height"],
		space: float64(minInt(s.Spaces, maxInt(1, space))), openedBy: strOr(a["opened_by"], "")})
	if err != nil {
		return nil, nil, err
	}
	w["updated_seq"] = s.Seq + 1
	s.Windows[id] = w
	return []map[string]any{s.upsert(id)}, map[string]any{"window_id": id, "reused": false}, nil
}

// orEmptyArgs is `a.get("args") or {}`.
func orEmptyArgs(v any) any {
	if !pyTruthy(v) {
		return map[string]any{}
	}
	return v
}

func (s *deskSession) doClose(a map[string]any) ([]map[string]any, map[string]any, error) {
	id := strOr(a["window_id"], "")
	if _, ok := s.Windows[id]; !ok {
		return nil, map[string]any{"closed": false}, nil
	}
	delete(s.Windows, id)
	return []map[string]any{{"op": "remove", "id": id}}, map[string]any{"closed": true}, nil
}

// doBindBrowser commits a created page only while its original window and
// binding exist: the creator owns only the new page, and a failure never
// reopens a closed window or overwrites another operation.
func (s *deskSession) doBindBrowser(a map[string]any) ([]map[string]any, map[string]any, error) {
	id := strOr(a["window_id"], "")
	window, ok := s.Windows[id]
	if !ok || window["app_id"] != "browser" {
		return nil, nil, refuse("No such desktop Browser window: %s", id)
	}
	if browserPageReference(window) != strOr(a["expected_page_id"], "") {
		return nil, nil, refuse("The Browser page binding changed while a page was opening")
	}
	page, operation := strOr(a["page_id"], ""), strOr(a["operation_id"], "")
	if page == "" || operation == "" {
		return nil, nil, refuse("A Browser binding requires page_id and operation_id")
	}
	binding := map[string]any{"page_id": page, "operation_id": operation, "revision": s.Seq + 1}
	args := map[string]any{}
	for k, v := range object(window["args"]) {
		args[k] = v
	}
	args["browser_binding"], args["page_id"] = binding, page
	args["shared"] = map[string]any{"by": "browser", "v": map[string]any{"page": page, "url": strOr(a["url"], "")}}
	w, _ := s.touch(id)
	w["args"] = args
	return []map[string]any{s.upsert(id)}, map[string]any{"binding": binding}, nil
}

func (s *deskSession) doMove(a map[string]any) ([]map[string]any, map[string]any, error) {
	id := strOr(a["window_id"], "")
	w, err := s.touch(id)
	if err != nil {
		return nil, nil, err
	}
	x, err := intOr(a["x"], 0)
	if err != nil {
		return nil, nil, err
	}
	y, err := intOr(a["y"], 0)
	if err != nil {
		return nil, nil, err
	}
	w["x"], w["y"] = maxInt(0, x), maxInt(0, y)
	return []map[string]any{s.upsert(id)}, map[string]any{}, nil
}

func (s *deskSession) doResize(a map[string]any) ([]map[string]any, map[string]any, error) {
	id := strOr(a["window_id"], "")
	w, err := s.touch(id)
	if err != nil {
		return nil, nil, err
	}
	width, err := intOr(a["width"], 320)
	if err != nil {
		return nil, nil, err
	}
	height, err := intOr(a["height"], 180)
	if err != nil {
		return nil, nil, err
	}
	w["width"], w["height"] = maxInt(320, width), maxInt(180, height)
	return []map[string]any{s.upsert(id)}, map[string]any{}, nil
}

func (s *deskSession) doRaise(a map[string]any) ([]map[string]any, map[string]any, error) {
	id := strOr(a["window_id"], "")
	w, err := s.touch(id)
	if err != nil {
		return nil, nil, err
	}
	w["z"] = s.bumpZ()
	w["minimized"] = false
	return []map[string]any{s.upsert(id), {"op": "meta", "top_z": s.TopZ}}, map[string]any{}, nil
}

// doSet patches the flags a window carries. Only these keys, by name: an
// open-ended merge would let a stale client write geometry back.
func (s *deskSession) doSet(a map[string]any) ([]map[string]any, map[string]any, error) {
	id := strOr(a["window_id"], "")
	w, err := s.touch(id)
	if err != nil {
		return nil, nil, err
	}
	patch := map[string]any{}
	if pyTruthy(a["patch"]) {
		var ok bool
		if patch, ok = a["patch"].(map[string]any); !ok {
			return nil, nil, refuse("set needs a patch object")
		}
	}
	for _, key := range []string{"title", "minimized", "maximized", "fullscreen", "status", "path"} {
		if v, ok := patch[key]; ok {
			w[key] = v
		}
	}
	if v, ok := patch["space"]; ok {
		// GROW to fit rather than clamp down: full screen mints a space and
		// moves a window onto it as two intents, and a `set` that overtakes
		// its `spaces` must not demote the window to a space nobody is
		// looking at. A count that trails a window is the count that is wrong.
		want, err := intOr(v, 1)
		if err != nil {
			return nil, nil, err
		}
		want = maxInt(1, want)
		if want > s.Spaces {
			s.Spaces = minInt(maxSpaces, want)
		}
		w["space"] = minInt(s.Spaces, want)
	}
	if args, ok := patch["args"].(map[string]any); ok {
		merged := map[string]any{}
		for k, v := range object(w["args"]) {
			merged[k] = v
		}
		for k, v := range args {
			merged[k] = v
		}
		w["args"] = merged
	}
	return []map[string]any{s.upsert(id)}, map[string]any{}, nil
}

func (s *deskSession) doSpaces(a map[string]any) ([]map[string]any, map[string]any, error) {
	count, err := intOr(a["count"], 1)
	if err != nil {
		return nil, nil, err
	}
	n := maxInt(1, minInt(maxSpaces, count))
	if n == s.Spaces {
		return nil, map[string]any{"spaces": n}, nil
	}
	var ops []map[string]any
	if n < s.Spaces {
		// Windows on a retired space fall into the one below it, as they do
		// when a space is closed by hand.
		for _, id := range sortedIDs(s.Windows) {
			w := s.Windows[id]
			if space, _ := intOr(w["space"], 0); space > n {
				w["space"] = n
				w["updated_seq"] = s.Seq + 1
				ops = append(ops, s.upsert(id))
			}
		}
	}
	s.Spaces = n
	ops = append(ops, map[string]any{"op": "meta", "spaces": n})
	return ops, map[string]any{"spaces": n}, nil
}

// doNominal sets the desktop's resolution. `propose` (default) is a viewport
// reporting its size: grow-only, ignored once locked. `set` is a person
// choosing in Settings: exact, may shrink, and locks. `auto` unlocks and
// adopts the proposing viewport's size.
func (s *deskSession) doNominal(a map[string]any) ([]map[string]any, map[string]any, error) {
	rw, err := intOr(a["w"], 0)
	if err != nil {
		return nil, nil, err
	}
	rh, err := intOr(a["h"], 0)
	if err != nil {
		return nil, nil, err
	}
	w, h := maxInt(640, rw), maxInt(480, rh)
	mode := strOr(a["mode"], "propose")
	before := [3]any{s.NominalW, s.NominalH, s.NominalLocked}
	switch {
	case mode == "set":
		s.NominalW, s.NominalH, s.NominalLocked = w, h, true
	case mode == "auto":
		s.NominalLocked = false
		s.NominalW, s.NominalH = w, h
	case s.NominalLocked:
		// A viewport's opinion after someone has decided: not an error, just
		// no longer something that changes anything.
	case w > s.NominalW || h > s.NominalH:
		s.NominalW, s.NominalH = maxInt(s.NominalW, w), maxInt(s.NominalH, h)
	}
	state := map[string]any{"nominal_w": s.NominalW, "nominal_h": s.NominalH, "nominal_locked": s.NominalLocked}
	if before == [3]any{s.NominalW, s.NominalH, s.NominalLocked} {
		return nil, state, nil
	}
	op := map[string]any{"op": "meta"}
	for k, v := range state {
		op[k] = v
	}
	return []map[string]any{op}, state, nil
}

// ── records ────────────────────────────────────────────────────────────────

// fromRecord adopts a Workspace record (.pantheon/desktop.json): v2 is the
// Desktop App's own document, v1 the browser's older list of windows. Windows
// of container-bound Apps are kept only when the record was written in the
// importing container's lifetime (boot).
func fromRecord(data map[string]any, boot string) (*deskSession, error) {
	s := newDeskSession()
	if data == nil {
		return s, nil
	}
	spaces, err := intOr(data["spaces"], 1)
	if err != nil {
		return nil, err
	}
	s.Spaces = maxInt(1, minInt(maxSpaces, spaces))
	windows, _ := data["windows"].([]any)
	if v, _ := data["v"].(float64); v == 2 {
		fields := []struct {
			into     *int64
			fallback int64
		}{{&s.NominalW, 1280}, {&s.NominalH, 800}, {&s.NextID, 1}, {&s.Seq, 0}}
		for i, key := range []string{"nominal_w", "nominal_h", "next_id", "seq"} {
			if *fields[i].into, err = intOr(data[key], fields[i].fallback); err != nil {
				return nil, err
			}
		}
		s.NominalLocked = pyTruthy(data["nominal_locked"])
		s.NextID, s.Seq = maxInt(1, s.NextID), maxInt(0, s.Seq)
		topZ, err := intOr(data["top_z"], 0)
		if err != nil {
			return nil, err
		}
		s.TopZ = maxInt(s.TopZ, topZ)
		sameLife := boot != "" && data["boot"] == boot
		for _, item := range windows {
			w := object(item)
			id := strOr(w["id"], "")
			if id == "" {
				continue
			}
			if !sameLife && ephemeralApps[pyStr(w["app_id"])] {
				continue
			}
			s.Windows[id] = w
			z, _ := intOr(w["z"], 0)
			s.TopZ = maxInt(s.TopZ, z)
		}
		return s, nil
	}
	// v1: the browser's own record, a list of windows with no ids. Adopt it
	// rather than dropping the user's layout at upgrade.
	for _, item := range windows {
		w := object(item)
		appID := strOr(w["appId"], "")
		if appID == "" || ephemeralApps[appID] {
			continue
		}
		id := s.mint()
		window, err := newWindow(windowSpec{appID: appID, args: orEmptyArgs(w["args"]), title: strOr(w["title"], appID),
			x: w["x"], y: w["y"], width: w["width"], height: w["height"], space: w["space"],
			minimized: w["minimized"], maximized: w["maximized"], fullscreen: w["fullscreen"], z: s.bumpZ()})
		if err != nil {
			return nil, err
		}
		s.Windows[id] = window
	}
	return s, nil
}

// dropEphemeral removes the windows of container-bound Apps (a new container
// claimed the desktop), returning the delta.
func (s *deskSession) dropEphemeral() []map[string]any {
	var ops []map[string]any
	for _, id := range sortedIDs(s.Windows) {
		if ephemeralApps[pyStr(s.Windows[id]["app_id"])] {
			delete(s.Windows, id)
			ops = append(ops, map[string]any{"op": "remove", "id": id})
		}
	}
	return ops
}
