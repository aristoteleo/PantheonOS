package hosted

// Per-App state (docs/apps/standalone-apps.md, "Per-App state"): an App's
// durable state, one key per App beside the desktop, so a desktop window and
// an App page of the same App show the same projects and canvases, and
// closing a window loses none of it. Window args keep only the view.
//
//	<fid>.owner.app.<app key>   {…}   ≤ MaxAppState, CAS on revision
//
// The service does not read the state: it is the App's business. It keeps the
// size limit, the revision check and the change event, which carries the App
// and its new revision only (viewers read the state again with app_state_get).

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/nats-io/nats.go/jetstream"
)

// MaxAppState bounds one App's state.
const MaxAppState = 256 << 10

// appStateRE is the App ids with a state key: a built-in id, or a packaged
// App's pkg:<id>.
var appStateRE = regexp.MustCompile(`^(pkg:)?[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`)

// appStateKey is the key part of an App's state: built-in ids never hold a
// dot, so pkg:<id> (a colon is not a key character) cannot meet one.
func appStateKey(appID string) (string, error) {
	if !appStateRE.MatchString(appID) {
		return "", &ArgumentError{"invalid App id"}
	}
	return "app." + strings.Replace(appID, ":", ".", 1), nil
}

// appState reads one App's state: {app_id, revision, state}, with revision 0
// and state null while the App has none.
func (d *DesktopState) appState(ctx context.Context, s desktopScope, appID string) (map[string]any, error) {
	part, err := appStateKey(appID)
	if err != nil {
		return nil, err
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	state, rev, err := readAppState(ctx, kv, s.key(part))
	if err != nil {
		return nil, err
	}
	return map[string]any{"app_id": appID, "revision": rev, "state": rawOrNil(state)}, nil
}

func readAppState(ctx context.Context, kv jetstream.KeyValue, key string) (json.RawMessage, uint64, error) {
	entry, err := kv.Get(ctx, key)
	if errors.Is(err, jetstream.ErrKeyNotFound) {
		return nil, 0, nil
	}
	if err != nil {
		return nil, 0, err
	}
	return append(json.RawMessage(nil), entry.Value()...), entry.Revision(), nil
}

// appStatePut replaces an App's state (state) or merges top-level keys into it
// (patch; a null value removes the key). With revision (0: the App has no
// state yet), a stale write is not applied and the reply carries the current
// state instead: {conflict, revision, state}.
func (d *DesktopState) appStatePut(ctx context.Context, s desktopScope, appID string, state json.RawMessage,
	patch map[string]json.RawMessage, revision *uint64) (map[string]any, error) {
	part, err := appStateKey(appID)
	if err != nil {
		return nil, err
	}
	if (len(state) == 0 || string(state) == "null") == (patch == nil) {
		return nil, &ArgumentError{"app_state_put takes state or patch"}
	}
	var whole map[string]json.RawMessage
	if patch == nil && (json.Unmarshal(state, &whole) != nil || whole == nil) {
		return nil, &ArgumentError{"state must be an object"}
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	l := d.lock(s)
	l.Lock()
	defer l.Unlock()
	key := s.key(part)
	for attempt := 0; ; attempt++ {
		current, rev, err := readAppState(ctx, kv, key)
		if err != nil {
			return nil, err
		}
		if revision != nil && *revision != rev {
			return map[string]any{"conflict": true, "app_id": appID, "revision": rev, "state": rawOrNil(current)}, nil
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
			return nil, &ArgumentError{"state cannot be stored"}
		}
		if len(raw) > MaxAppState {
			return nil, &ArgumentError{fmt.Sprintf("the state of %s would be %d KB; an App's state is limited to %d KB "+
				"(keep large shared state on its node)", appID, (len(raw)+1023)/1024, MaxAppState>>10)}
		}
		written, err := put(ctx, kv, key, raw, rev)
		if err != nil {
			if conflict(err) && revision == nil && attempt < desktopRetries {
				continue
			}
			if conflict(err) && revision != nil {
				current, rev, err := readAppState(ctx, kv, key)
				if err != nil {
					return nil, err
				}
				return map[string]any{"conflict": true, "app_id": appID, "revision": rev, "state": rawOrNil(current)}, nil
			}
			return nil, err
		}
		d.publish(s, map[string]any{"type": "desktop.app_state", "app_id": appID, "revision": written})
		return map[string]any{"app_id": appID, "revision": written, "state": json.RawMessage(raw)}, nil
	}
}
