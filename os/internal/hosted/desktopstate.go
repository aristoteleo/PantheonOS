package hosted

// desktop-state: the user's desktop session, desktop settings and appearance,
// kept by the controller in one JetStream KV bucket so no node has to be up to
// draw a desktop (docs/os/desktop-state.md). The service is the bucket's only
// client; browsers and the Workspace's Desktop App reach it as a hosted App.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"log"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// DesktopBucket holds every Fleet's desktops, keyed <fid>.<uid>.<part>:
// meta, win.<window id>, settings, appearance.
const DesktopBucket = "DESKTOP"

// Limits the service enforces: a window's large shared state belongs on its
// node, not in the user's desktop. MaxDesktopTotal (every window record plus
// the meta key) keeps a whole desktop within what one dependency call returns
// (512 KiB) to the Workspace's Desktop App.
const (
	MaxDesktopWindow   = 64 << 10
	MaxDesktopWindows  = 200
	MaxDesktopTotal    = 448 << 10
	MaxDesktopSettings = 256 << 10
	desktopRetries     = 8
)

// The service is the bucket's only writer, so it keeps the desktops it used
// recently instead of reading one per call (each read is a JetStream
// consumer). A desktop untouched for desktopIdle is dropped, and at most
// desktopCached are kept.
const (
	desktopIdle   = 30 * time.Minute
	desktopCached = 4096
)

// desktopOwner is the user segment of a desktop key. A Fleet belongs to one
// Hub user, and every caller a hosted service admits acts for that owner: the
// owner's browser (an App connection the Hub grants the owner only), or an App
// of the owner's deployment through a dependency grant. Visitors of a shared
// App never reach a hosted service (appgateway's share resolver refuses
// them). A Fleet with several users would carry the authenticated user on
// the Call and use it here.
const desktopOwner = "owner"

var (
	desktopFleetRE  = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	desktopWindowRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
)

// DesktopState is the desktop-state hosted service.
type DesktopState struct {
	// Connect opens the service's own bus connection (on first use, so the
	// controller does not wait for its broker to start).
	Connect func() (*nats.Conn, error)
	// Backups is the directory daily snapshots go to; "" takes none.
	Backups string

	mu    sync.Mutex
	nc    *nats.Conn
	kv    jetstream.KeyValue
	locks [64]sync.Mutex

	cacheMu sync.Mutex
	cache   map[desktopScope]*deskCached
	swept   time.Time
	// loads counts desktops read from the bucket (one watcher each).
	loads atomic.Int64
	// now is the clock (tests replace it).
	now func() time.Time
}

type deskCached struct {
	st   *deskStored
	used time.Time
}

// desktopScope is one caller's desktop: the Fleet comes from the hosted
// binding the call was admitted at (never from its arguments), the user from
// who may call a hosted App at all (see desktopOwner).
type desktopScope struct{ fleet, user string }

func scopeOf(c *Call) (desktopScope, error) {
	if !desktopFleetRE.MatchString(c.Fleet) {
		return desktopScope{}, fmt.Errorf("invalid Fleet")
	}
	return desktopScope{c.Fleet, desktopOwner}, nil
}

func (s desktopScope) key(part string) string { return s.fleet + "." + s.user + "." + part }

// Subject is where the service announces changes to this desktop: inside the
// owner's App namespace, which the browser's bus credential subscribes to.
func (s desktopScope) Subject() string {
	return fmt.Sprintf("fleet.%s.apps.desktop-state.%s", s.fleet, s.user)
}

// lock serializes one desktop's writes in this process; revision checks keep
// any other writer from being overwritten.
func (d *DesktopState) lock(s desktopScope) *sync.Mutex {
	h := fnv.New32a()
	_, _ = h.Write([]byte(s.fleet + "." + s.user))
	return &d.locks[h.Sum32()%uint32(len(d.locks))]
}

// bucket opens (creating once) the DESKTOP bucket.
func (d *DesktopState) bucket(ctx context.Context) (jetstream.KeyValue, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.kv != nil {
		return d.kv, nil
	}
	if d.nc == nil {
		if d.Connect == nil {
			return nil, fmt.Errorf("desktop state is not connected")
		}
		nc, err := d.Connect()
		if err != nil {
			return nil, fmt.Errorf("desktop state bus: %w", err)
		}
		d.nc = nc
	}
	js, err := jetstream.New(d.nc)
	if err != nil {
		return nil, err
	}
	kv, err := js.CreateOrUpdateKeyValue(ctx, jetstream.KeyValueConfig{Bucket: DesktopBucket,
		Description: "Desktop sessions, settings and appearance (docs/os/desktop-state.md)", History: 1})
	if err != nil {
		return nil, fmt.Errorf("desktop state bucket: %w", err)
	}
	d.kv = kv
	return kv, nil
}

func (d *DesktopState) publish(s desktopScope, event map[string]any) {
	d.mu.Lock()
	nc := d.nc
	d.mu.Unlock()
	raw, err := json.Marshal(event)
	if nc == nil || err != nil {
		return
	}
	// A missed notification costs a viewport a re-read, never state.
	if err := nc.Publish(s.Subject(), raw); err != nil {
		log.Printf("[desktop-state] %s notify: %v", s.fleet, err)
	}
}

func conflict(err error) bool { return errors.Is(err, jetstream.ErrKeyExists) }

// ── stored desktop ─────────────────────────────────────────────────────────

// deskStored is one desktop as the bucket has it: values, the revision each
// was read or written at, and the size of each window record.
type deskStored struct {
	session       *deskSession
	metaRev       uint64
	metaSize      int
	winRev        map[string]uint64
	winSize       map[string]int
	settings      json.RawMessage
	settingsRev   uint64
	appearance    json.RawMessage
	appearanceRev uint64
}

// total is the size of the desktop's window records and meta key.
func (st *deskStored) total() int {
	n := st.metaSize
	for _, size := range st.winSize {
		n += size
	}
	return n
}

func (d *DesktopState) clock() time.Time {
	if d.now != nil {
		return d.now()
	}
	return time.Now()
}

// stored is the caller's desktop: the cached one, or read from the bucket.
// The caller holds the desktop's lock and either commits every change it
// makes to it or forgets it.
func (d *DesktopState) stored(ctx context.Context, kv jetstream.KeyValue, s desktopScope) (*deskStored, error) {
	now := d.clock()
	d.cacheMu.Lock()
	d.sweep(now)
	entry := d.cache[s]
	if entry != nil {
		entry.used = now
	}
	d.cacheMu.Unlock()
	if entry != nil {
		return entry.st, nil
	}
	st, err := d.load(ctx, kv, s)
	if err != nil {
		return nil, err
	}
	d.cacheMu.Lock()
	if d.cache == nil {
		d.cache = map[desktopScope]*deskCached{}
	}
	d.cache[s] = &deskCached{st, now}
	for len(d.cache) > desktopCached {
		var oldest desktopScope
		var at time.Time
		for scope, e := range d.cache {
			if at.IsZero() || e.used.Before(at) {
				oldest, at = scope, e.used
			}
		}
		delete(d.cache, oldest)
	}
	d.cacheMu.Unlock()
	return st, nil
}

// sweep drops desktops idle for desktopIdle, at most once a minute. The
// caller holds cacheMu.
func (d *DesktopState) sweep(now time.Time) {
	if now.Sub(d.swept) < time.Minute {
		return
	}
	d.swept = now
	for scope, e := range d.cache {
		if now.Sub(e.used) > desktopIdle {
			delete(d.cache, scope)
		}
	}
}

// forget drops a desktop from the cache: a failed or refused change may have
// left it different from the bucket, and the next call reads it again.
func (d *DesktopState) forget(s desktopScope) {
	d.cacheMu.Lock()
	delete(d.cache, s)
	d.cacheMu.Unlock()
}

func (d *DesktopState) forgetAll() {
	d.cacheMu.Lock()
	d.cache = nil
	d.cacheMu.Unlock()
}

// load reads every key of one desktop at their current revisions.
func (d *DesktopState) load(ctx context.Context, kv jetstream.KeyValue, s desktopScope) (*deskStored, error) {
	d.loads.Add(1)
	st := &deskStored{session: newDeskSession(), winRev: map[string]uint64{}, winSize: map[string]int{}}
	watcher, err := kv.WatchFiltered(ctx, []string{s.key(">")}, jetstream.IgnoreDeletes())
	if err != nil {
		return nil, err
	}
	defer watcher.Stop()
	prefix := s.key("")
	for {
		var entry jetstream.KeyValueEntry
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case entry = <-watcher.Updates():
		}
		if entry == nil {
			break
		}
		part := strings.TrimPrefix(entry.Key(), prefix)
		switch {
		case part == "meta":
			meta := newDeskMeta()
			if err := json.Unmarshal(entry.Value(), &meta); err != nil {
				return nil, fmt.Errorf("desktop record %s is unreadable", entry.Key())
			}
			st.session.deskMeta, st.metaRev, st.metaSize = meta, entry.Revision(), len(entry.Value())
		case strings.HasPrefix(part, "win."):
			var w map[string]any
			if err := json.Unmarshal(entry.Value(), &w); err != nil || w == nil {
				return nil, fmt.Errorf("desktop record %s is unreadable", entry.Key())
			}
			id := strings.TrimPrefix(part, "win.")
			st.session.Windows[id], st.winRev[id], st.winSize[id] = w, entry.Revision(), len(entry.Value())
		case part == "settings":
			st.settings, st.settingsRev = append(json.RawMessage(nil), entry.Value()...), entry.Revision()
		case part == "appearance":
			st.appearance, st.appearanceRev = append(json.RawMessage(nil), entry.Value()...), entry.Revision()
		}
	}
	return st, nil
}

// put writes value at key if its revision is still rev (0: the key is absent).
func put(ctx context.Context, kv jetstream.KeyValue, key string, value []byte, rev uint64) (uint64, error) {
	if rev == 0 {
		return kv.Create(ctx, key, value)
	}
	return kv.Update(ctx, key, value, rev)
}

// commit writes what an intent changed: windows first, then the meta key
// (whose seq says the change happened). Each write is checked against the
// revision it was computed from, and st follows the new revisions.
func (d *DesktopState) commit(ctx context.Context, kv jetstream.KeyValue, s desktopScope, st *deskStored, ops []map[string]any, encoded map[string][]byte) error {
	done := map[string]bool{}
	for _, op := range ops {
		id, _ := op["id"].(string)
		if id == "" || done[id] {
			continue
		}
		done[id] = true
		switch op["op"] {
		case "upsert":
			rev, err := put(ctx, kv, s.key("win."+id), encoded[id], st.winRev[id])
			if err != nil {
				return err
			}
			st.winRev[id], st.winSize[id] = rev, len(encoded[id])
		case "remove":
			if rev := st.winRev[id]; rev != 0 {
				if err := kv.Delete(ctx, s.key("win."+id), jetstream.LastRevision(rev)); err != nil {
					return err
				}
			}
			delete(st.winRev, id)
			delete(st.winSize, id)
		}
	}
	meta, err := json.Marshal(st.session.deskMeta)
	if err != nil {
		return err
	}
	rev, err := put(ctx, kv, s.key("meta"), meta, st.metaRev)
	if err != nil {
		return err
	}
	st.metaRev, st.metaSize = rev, len(meta)
	return nil
}

// encodeWindows encodes the windows an intent upserts, refusing records over
// the limit.
func encodeWindows(s *deskSession, ops []map[string]any) (map[string][]byte, error) {
	out := map[string][]byte{}
	for _, op := range ops {
		id, _ := op["id"].(string)
		if op["op"] != "upsert" || out[id] != nil {
			continue
		}
		raw, err := encodeWindow(id, s.Windows[id])
		if err != nil {
			return nil, err
		}
		out[id] = raw
	}
	return out, nil
}

func encodeWindow(id string, w map[string]any) ([]byte, error) {
	raw, err := json.Marshal(w)
	if err != nil {
		return nil, &ArgumentError{fmt.Sprintf("window %s cannot be stored: %v", id, err)}
	}
	if len(raw) > MaxDesktopWindow {
		return nil, &ArgumentError{fmt.Sprintf("window %s is %d KB; a desktop window record is limited to %d KB "+
			"(keep an App's large shared state on its node)", id, (len(raw)+1023)/1024, MaxDesktopWindow>>10)}
	}
	return raw, nil
}

// checkTotal refuses a change that grows the desktop past MaxDesktopTotal
// (one that shrinks it is always allowed).
func checkTotal(st *deskStored, encoded map[string][]byte) error {
	meta, _ := json.Marshal(st.session.deskMeta)
	total := len(meta)
	for id := range st.session.Windows {
		if raw, ok := encoded[id]; ok {
			total += len(raw)
		} else {
			total += st.winSize[id]
		}
	}
	if total > MaxDesktopTotal && total > st.total() {
		return &ArgumentError{fmt.Sprintf("this desktop's windows would take %d KB; a desktop is limited to %d KB "+
			"(close a window, or keep an App's large shared state on its node)", (total+1023)/1024, MaxDesktopTotal>>10)}
	}
	return nil
}

// ── tools ──────────────────────────────────────────────────────────────────

func (d *DesktopState) Call(ctx context.Context, c *Call) (any, error) {
	s, err := scopeOf(c)
	if err != nil {
		return nil, err
	}
	switch c.Method {
	case "get":
		if err := c.Decode(&struct{}{}); err != nil {
			return nil, err
		}
		return d.get(ctx, s)
	case "intent":
		var a struct {
			Kind string         `json:"kind"`
			Args map[string]any `json:"args"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return d.intent(ctx, s, a.Kind, a.Args)
	case "settings_put":
		var a struct {
			Settings json.RawMessage            `json:"settings"`
			Patch    map[string]json.RawMessage `json:"patch"`
			Revision *uint64                    `json:"revision"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return d.settingsPut(ctx, s, a.Settings, a.Patch, a.Revision)
	case "appearance_put":
		var a struct {
			Preference string `json:"preference"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return d.appearancePut(ctx, s, a.Preference)
	case "import":
		var a struct {
			Session    map[string]any  `json:"session"`
			Settings   json.RawMessage `json:"settings"`
			Appearance *struct {
				Preference string `json:"preference"`
			} `json:"appearance"`
			Boot string `json:"boot"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		preference := ""
		if a.Appearance != nil {
			preference = a.Appearance.Preference
		}
		return d.importState(ctx, s, a.Session, a.Settings, preference, a.Boot)
	}
	return nil, &ArgumentError{fmt.Sprintf("unknown method %s", c.Method)}
}

func (d *DesktopState) get(ctx context.Context, s desktopScope) (json.RawMessage, error) {
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	l := d.lock(s)
	l.Lock()
	defer l.Unlock()
	st, err := d.stored(ctx, kv, s)
	if err != nil {
		return nil, err
	}
	return replyJSON(map[string]any{
		"session": st.session.snapshot(), "settings": rawOrNil(st.settings), "settings_revision": st.settingsRev,
		"appearance": rawOrNil(st.appearance), "subject": s.Subject(), "initialized": st.metaRev != 0,
	})
}

// replyJSON is a reply encoded while the desktop's lock is held: it shares
// window records with the cached desktop, which the next intent changes.
func replyJSON(reply map[string]any) (json.RawMessage, error) {
	raw, err := json.Marshal(reply)
	if err != nil {
		return nil, err
	}
	return raw, nil
}

func rawOrNil(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	return raw
}

func (d *DesktopState) intent(ctx context.Context, s desktopScope, kind string, args map[string]any) (json.RawMessage, error) {
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	l := d.lock(s)
	l.Lock()
	defer l.Unlock()
	for attempt := 0; ; attempt++ {
		st, err := d.stored(ctx, kv, s)
		if err != nil {
			return nil, err
		}
		before := len(st.session.Windows)
		// The reducer applies the intent to the cached desktop in place: from
		// here every path either commits it or forgets it. It may keep
		// references into args, so a retry starts from its own copy.
		ops, result, err := st.session.apply(kind, cloneJSON(args))
		if err != nil {
			d.forget(s)
			return nil, &ArgumentError{err.Error()}
		}
		reply := map[string]any{"seq": st.session.Seq, "ops": ops}
		for k, v := range result {
			reply[k] = v
		}
		if len(ops) == 0 {
			return replyJSON(reply)
		}
		if len(st.session.Windows) > before && len(st.session.Windows) > MaxDesktopWindows {
			d.forget(s)
			return nil, &ArgumentError{fmt.Sprintf("a desktop holds at most %d windows; close one first", MaxDesktopWindows)}
		}
		encoded, err := encodeWindows(st.session, ops)
		if err == nil {
			err = checkTotal(st, encoded)
		}
		if err != nil {
			d.forget(s)
			return nil, err
		}
		if err := d.commit(ctx, kv, s, st, ops, encoded); err != nil {
			d.forget(s)
			if conflict(err) && attempt < desktopRetries {
				continue // another writer moved on: read it and apply again
			}
			return nil, err
		}
		d.publish(s, map[string]any{"type": "desktop.delta", "seq": st.session.Seq, "ops": ops})
		return replyJSON(reply)
	}
}

func cloneJSON(m map[string]any) map[string]any {
	if m == nil {
		return nil
	}
	raw, err := json.Marshal(m)
	if err != nil {
		return m
	}
	var out map[string]any
	if json.Unmarshal(raw, &out) != nil {
		return m
	}
	return out
}

// settingsPut replaces the desktop settings (settings) or merges top-level
// keys into them (patch; a null value removes the key). With revision, a
// stale write is not applied: the reply carries the current settings instead.
func (d *DesktopState) settingsPut(ctx context.Context, s desktopScope, settings json.RawMessage,
	patch map[string]json.RawMessage, revision *uint64) (map[string]any, error) {
	if (len(settings) == 0 || string(settings) == "null") == (patch == nil) {
		return nil, &ArgumentError{"settings_put takes settings or patch"}
	}
	var whole map[string]json.RawMessage
	if patch == nil && (json.Unmarshal(settings, &whole) != nil || whole == nil) {
		return nil, &ArgumentError{"settings must be an object"}
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	l := d.lock(s)
	l.Lock()
	defer l.Unlock()
	key := s.key("settings")
	for attempt := 0; ; attempt++ {
		st, err := d.stored(ctx, kv, s)
		if err != nil {
			return nil, err
		}
		current, rev := st.settings, st.settingsRev
		if revision != nil && *revision != rev {
			return map[string]any{"conflict": true, "revision": rev, "settings": rawOrNil(current)}, nil
		}
		next := whole
		if patch != nil {
			next = map[string]json.RawMessage{}
			if len(current) > 0 {
				_ = json.Unmarshal(current, &next)
			}
			for k, v := range patch {
				if string(v) == "null" {
					delete(next, k)
				} else {
					next[k] = v
				}
			}
		}
		raw, err := json.Marshal(next)
		if err != nil {
			return nil, &ArgumentError{"settings cannot be stored"}
		}
		if len(raw) > MaxDesktopSettings {
			return nil, &ArgumentError{fmt.Sprintf("desktop settings are %d KB; the limit is %d KB",
				(len(raw)+1023)/1024, MaxDesktopSettings>>10)}
		}
		written, err := put(ctx, kv, key, raw, rev)
		if err != nil {
			d.forget(s)
			if conflict(err) && revision == nil && attempt < desktopRetries {
				continue
			}
			if conflict(err) && revision != nil {
				if st, err = d.stored(ctx, kv, s); err != nil {
					return nil, err
				}
				return map[string]any{"conflict": true, "revision": st.settingsRev, "settings": rawOrNil(st.settings)}, nil
			}
			return nil, err
		}
		st.settings, st.settingsRev = raw, written
		d.publish(s, map[string]any{"type": "desktop.settings", "revision": written, "settings": json.RawMessage(raw)})
		return map[string]any{"revision": written, "settings": json.RawMessage(raw)}, nil
	}
}

func validPreference(p string) bool { return p == "light" || p == "dark" || p == "system" }

func (d *DesktopState) appearancePut(ctx context.Context, s desktopScope, preference string) (map[string]any, error) {
	if !validPreference(preference) {
		return nil, &ArgumentError{"preference is light, dark or system"}
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	l := d.lock(s)
	l.Lock()
	defer l.Unlock()
	raw, _ := json.Marshal(map[string]string{"preference": preference})
	for attempt := 0; ; attempt++ {
		st, err := d.stored(ctx, kv, s)
		if err != nil {
			return nil, err
		}
		written, err := put(ctx, kv, s.key("appearance"), raw, st.appearanceRev)
		if err != nil {
			d.forget(s)
			if conflict(err) && attempt < desktopRetries {
				continue
			}
			return nil, err
		}
		st.appearance, st.appearanceRev = raw, written
		d.publish(s, map[string]any{"type": "desktop.appearance", "preference": preference})
		return map[string]any{"preference": preference}, nil
	}
}

// importState adopts a user's Workspace files, each part only while its keys
// are absent: the session record (.pantheon/desktop.json), the desktop
// settings (.pantheon/atrium/desktop.json) and the appearance. boot is the
// importing Workspace container's lifetime: when it differs from the one that
// last claimed the desktop, windows of container-bound Apps are dropped.
func (d *DesktopState) importState(ctx context.Context, s desktopScope, record map[string]any, settings json.RawMessage,
	preference, boot string) (map[string]any, error) {
	if preference != "" && !validPreference(preference) {
		return nil, &ArgumentError{"preference is light, dark or system"}
	}
	var settingsRaw []byte
	if len(settings) > 0 && string(settings) != "null" {
		var whole map[string]json.RawMessage
		if json.Unmarshal(settings, &whole) != nil || whole == nil {
			return nil, &ArgumentError{"settings must be an object"}
		}
		settingsRaw, _ = json.Marshal(whole)
		if len(settingsRaw) > MaxDesktopSettings {
			return nil, &ArgumentError{fmt.Sprintf("desktop settings are over %d KB", MaxDesktopSettings>>10)}
		}
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	l := d.lock(s)
	l.Lock()
	defer l.Unlock()
	imported := map[string]any{"session": false, "settings": false, "appearance": false}
	dropped := []string{}
	// An import (once per Desktop App start) reads the bucket afresh and
	// leaves the next call to read what it wrote.
	defer d.forget(s)
	for attempt := 0; ; attempt++ {
		d.forget(s)
		st, err := d.stored(ctx, kv, s)
		if err != nil {
			return nil, err
		}
		var delta []map[string]any
		switch {
		case st.metaRev == 0 && record != nil:
			if dropped, err = d.importSession(ctx, kv, s, st, record, boot); err != nil {
				if conflict(err) && attempt < desktopRetries {
					continue
				}
				return nil, err
			}
			imported["session"] = true
		case st.metaRev != 0 && boot != "" && st.session.Boot != boot:
			delta = st.session.dropEphemeral()
			if len(delta) > 0 {
				st.session.Seq++
				for _, op := range delta {
					op["seq"] = st.session.Seq
				}
			}
			st.session.Boot = boot
			if err := d.commit(ctx, kv, s, st, delta, nil); err != nil {
				if conflict(err) && attempt < desktopRetries {
					continue
				}
				return nil, err
			}
		}
		if st.settingsRev == 0 && settingsRaw != nil {
			if _, err := kv.Create(ctx, s.key("settings"), settingsRaw); err != nil && !conflict(err) {
				return nil, err
			} else if err == nil {
				imported["settings"] = true
			}
		}
		if st.appearanceRev == 0 && preference != "" {
			raw, _ := json.Marshal(map[string]string{"preference": preference})
			if _, err := kv.Create(ctx, s.key("appearance"), raw); err != nil && !conflict(err) {
				return nil, err
			} else if err == nil {
				imported["appearance"] = true
			}
		}
		if imported["session"] == true || imported["settings"] == true || imported["appearance"] == true {
			d.publish(s, map[string]any{"type": "desktop.reset"})
		} else if len(delta) > 0 {
			d.publish(s, map[string]any{"type": "desktop.delta", "seq": st.session.Seq, "ops": delta})
		}
		return map[string]any{"imported": imported, "dropped": dropped}, nil
	}
}

// importSession stores a Workspace session record as this desktop, dropping
// windows the limits refuse (reported, not fatal: the rest of the desktop
// still comes back).
func (d *DesktopState) importSession(ctx context.Context, kv jetstream.KeyValue, s desktopScope, st *deskStored,
	record map[string]any, boot string) ([]string, error) {
	session, err := fromRecord(record, boot)
	if err != nil {
		return nil, &ArgumentError{"session record: " + err.Error()}
	}
	session.Boot = boot
	dropped := []string{}
	encoded := map[string][]byte{}
	meta, _ := json.Marshal(session.deskMeta)
	total := len(meta)
	ids := sortedIDs(session.Windows)
	// Over the count or the size, keep the windows on top.
	sort.SliceStable(ids, func(i, j int) bool {
		zi, _ := intOr(session.Windows[ids[i]]["z"], 0)
		zj, _ := intOr(session.Windows[ids[j]]["z"], 0)
		return zi > zj
	})
	for _, id := range ids {
		raw, err := encodeWindow(id, session.Windows[id])
		if err != nil || !desktopWindowRE.MatchString(id) || len(encoded) >= MaxDesktopWindows || total+len(raw) > MaxDesktopTotal {
			dropped = append(dropped, id)
			delete(session.Windows, id)
			continue
		}
		encoded[id] = raw
		total += len(raw)
	}
	// Window keys without a meta key are left from an import that did not
	// finish: the meta key is what makes a session exist.
	for id, rev := range st.winRev {
		if _, keep := encoded[id]; !keep {
			if err := kv.Delete(ctx, s.key("win."+id), jetstream.LastRevision(rev)); err != nil {
				return nil, err
			}
		}
	}
	for _, id := range sortedIDs(session.Windows) {
		if _, err := put(ctx, kv, s.key("win."+id), encoded[id], st.winRev[id]); err != nil {
			return nil, err
		}
	}
	if _, err := kv.Create(ctx, s.key("meta"), meta); err != nil {
		return nil, err
	}
	return dropped, nil
}

func sortedIDs(windows map[string]map[string]any) []string {
	ids := make([]string, 0, len(windows))
	for id := range windows {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
