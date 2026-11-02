package hosted

// The installed-App index (docs/os/desktop-state.md, P2): what a desktop needs
// to draw its launcher, dock and icons and to route a window to its node,
// kept beside the session so none of it waits for the Workspace. The
// Workspace's Desktop App owns the App library and writes the index whenever
// it changes; browsers read it with `get` and follow `desktop.apps`.
//
// An entry is the Desktop App's business except for two fields the service
// reads: `id` (one entry per App) and `icon_hash`. A small icon rides inline
// in its entry; a larger one is stored under icon.<App id> and named by its
// hash, so the index stays small and the icon is read only by a desktop that
// draws it.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/nats-io/nats.go/jetstream"
)

var iconHashRE = regexp.MustCompile(`^[0-9a-f]{16}$`)

// iconHash names an icon in the index: the first 8 bytes of its SHA-256, hex.
func iconHash(icon string) string {
	sum := sha256.Sum256([]byte(icon))
	return hex.EncodeToString(sum[:8])
}

// appsIndex checks an index and returns it encoded, with the icon hash each
// App refers to ("" for none).
func appsIndex(apps json.RawMessage) ([]byte, map[string]string, error) {
	var entries []map[string]json.RawMessage
	if json.Unmarshal(apps, &entries) != nil || entries == nil {
		return nil, nil, &ArgumentError{"apps must be a list of App entries"}
	}
	if len(entries) > maxDesktopApps {
		return nil, nil, &ArgumentError{fmt.Sprintf("the App index lists %d Apps; the limit is %d", len(entries), maxDesktopApps)}
	}
	refs := map[string]string{}
	for _, entry := range entries {
		var id string
		if entry == nil || json.Unmarshal(entry["id"], &id) != nil || !desktopAppRE.MatchString(id) {
			return nil, nil, &ArgumentError{"every App entry needs an id (letters, digits, - and _)"}
		}
		if _, twice := refs[id]; twice {
			return nil, nil, &ArgumentError{fmt.Sprintf("App %s is listed twice", id)}
		}
		hash := ""
		if raw, ok := entry["icon_hash"]; ok && string(raw) != "null" {
			if json.Unmarshal(raw, &hash) != nil || !iconHashRE.MatchString(hash) {
				return nil, nil, &ArgumentError{fmt.Sprintf("App %s: icon_hash is 16 hex digits", id)}
			}
		}
		refs[id] = hash
	}
	raw, err := json.Marshal(entries)
	if err != nil {
		return nil, nil, &ArgumentError{"the App index cannot be stored"}
	}
	if len(raw) > MaxDesktopApps {
		return nil, nil, &ArgumentError{fmt.Sprintf("the App index is %d KB; the limit is %d KB (store large icons with apps_icon_put)",
			(len(raw)+1023)/1024, MaxDesktopApps>>10)}
	}
	return raw, refs, nil
}

// missingIcons are the Apps whose index entry names an icon the bucket does
// not hold (yet): the writer sends each with apps_icon_put.
func missingIcons(st *deskStored, refs map[string]string) []string {
	missing := []string{}
	for id, hash := range refs {
		if hash != "" && st.icons[id].hash != hash {
			missing = append(missing, id)
		}
	}
	sort.Strings(missing)
	return missing
}

// appsPut replaces the App index. An unchanged index is not written (the
// Desktop App sends it on every start); with revision, a stale write returns
// {conflict, revision}. Icons no App refers to any more are deleted.
func (d *DesktopState) appsPut(ctx context.Context, s desktopScope, apps json.RawMessage, revision *uint64) (map[string]any, error) {
	raw, refs, err := appsIndex(apps)
	if err != nil {
		return nil, err
	}
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
		if revision != nil && *revision != st.appsRev {
			return map[string]any{"conflict": true, "revision": st.appsRev}, nil
		}
		changed := !bytes.Equal(raw, st.apps)
		if changed {
			written, err := put(ctx, kv, s.key("apps"), raw, st.appsRev)
			if err != nil {
				d.forget(s)
				if conflict(err) && revision == nil && attempt < desktopRetries {
					continue
				}
				if conflict(err) && revision != nil {
					if st, err = d.stored(ctx, kv, s); err != nil {
						return nil, err
					}
					return map[string]any{"conflict": true, "revision": st.appsRev}, nil
				}
				return nil, err
			}
			st.apps, st.appsRev = raw, written
		}
		for id, icon := range st.icons {
			if refs[id] != "" {
				continue
			}
			// Housekeeping: a failure leaves an unused icon, never a wrong one.
			if err := kv.Delete(ctx, s.key("icon."+id), jetstream.LastRevision(icon.rev)); err != nil {
				d.forget(s)
				break
			}
			delete(st.icons, id)
		}
		if changed {
			d.publish(s, map[string]any{"type": "desktop.apps", "revision": st.appsRev, "apps": json.RawMessage(raw)})
		}
		return map[string]any{"revision": st.appsRev, "missing_icons": missingIcons(st, refs)}, nil
	}
}

// appsIconPut stores the icon the index names for an App by its hash; one
// the index does not name is refused, so the bucket holds no unused icons.
func (d *DesktopState) appsIconPut(ctx context.Context, s desktopScope, appID, icon string) (map[string]any, error) {
	if !desktopAppRE.MatchString(appID) {
		return nil, &ArgumentError{"invalid App id"}
	}
	if !strings.HasPrefix(icon, "data:image/") {
		return nil, &ArgumentError{"an icon is a data:image/ URI"}
	}
	raw, _ := json.Marshal(icon)
	if len(raw) > MaxDesktopIcon {
		return nil, &ArgumentError{fmt.Sprintf("the icon of %s is %d KB; the limit is %d KB", appID, (len(raw)+1023)/1024, MaxDesktopIcon>>10)}
	}
	hash := iconHash(icon)
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
		var refs map[string]string
		if len(st.apps) > 0 {
			_, refs, _ = appsIndex(st.apps)
		}
		if refs[appID] != hash {
			return nil, &ArgumentError{fmt.Sprintf("the App index names no such icon for %s; put the index first", appID)}
		}
		if st.icons[appID].hash == hash {
			return map[string]any{"hash": hash}, nil
		}
		written, err := put(ctx, kv, s.key("icon."+appID), raw, st.icons[appID].rev)
		if err != nil {
			d.forget(s)
			if conflict(err) && attempt < desktopRetries {
				continue
			}
			return nil, err
		}
		st.icons[appID] = deskIcon{hash, written}
		d.publish(s, map[string]any{"type": "desktop.icon", "app_id": appID, "hash": hash})
		return map[string]any{"hash": hash}, nil
	}
}

// appsIcon reads one stored icon: {icon, hash}, or {icon: null} without one.
func (d *DesktopState) appsIcon(ctx context.Context, s desktopScope, appID string) (map[string]any, error) {
	if !desktopAppRE.MatchString(appID) {
		return nil, &ArgumentError{"invalid App id"}
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return nil, err
	}
	entry, err := kv.Get(ctx, s.key("icon."+appID))
	if errors.Is(err, jetstream.ErrKeyNotFound) {
		return map[string]any{"icon": nil}, nil
	}
	if err != nil {
		return nil, err
	}
	var icon string
	if json.Unmarshal(entry.Value(), &icon) != nil {
		return map[string]any{"icon": nil}, nil
	}
	return map[string]any{"icon": icon, "hash": iconHash(icon)}, nil
}
