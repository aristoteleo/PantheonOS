package deployments

// Durable desired state. Like the dependency grant journal, this is a
// single-controller, local-filesystem store: one private directory, an
// exclusive writer lock, one file per deployment written atomically (temp
// file, fsync, rename, directory fsync). Corruption is fatal at open, never
// an empty fallback.

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	ErrNotFound = errors.New("deployment not found")
	ErrConflict = errors.New("deployment changed; reload before saving")
	ErrLimit    = errors.New("too many deployments for this fleet")
)

// Store keeps every fleet's deployments in memory, backed by the directory.
type Store struct {
	mu      sync.Mutex
	root    *os.Root
	lock    *os.File
	items   map[string]Deployment // key: fleet + "/" + name
	failed  bool
	changes chan string // fleet/name keys, for the reconciler; never blocks writers
}

func key(fleet, name string) string { return fleet + "/" + name }

// Open loads the store, creating its private directory if needed.
func Open(path string) (*Store, error) {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return nil, fmt.Errorf("deployment store unavailable")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || !private(info) {
		return nil, fmt.Errorf("deployment store must be a private directory")
	}
	lock, err := lockStore(filepath.Join(path, "writer.lock"))
	if err != nil {
		return nil, fmt.Errorf("deployment store already owned or unavailable")
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		lock.Close()
		return nil, fmt.Errorf("deployment store unavailable")
	}
	s := &Store{root: root, lock: lock, items: map[string]Deployment{}, changes: make(chan string, 1024)}
	fail := func(why string) (*Store, error) {
		root.Close()
		lock.Close()
		return nil, fmt.Errorf("deployment store invalid (%s); recovery required", why)
	}
	fleets, err := os.ReadDir(path)
	if err != nil {
		return fail("unreadable")
	}
	for _, fleetEntry := range fleets {
		fleet := fleetEntry.Name()
		if fleet == "writer.lock" || strings.HasPrefix(fleet, ".") {
			continue
		}
		if !fleetEntry.IsDir() || !fleetRE.MatchString(fleet) {
			return fail("unexpected entry " + fleet)
		}
		files, err := os.ReadDir(filepath.Join(path, fleet))
		if err != nil {
			return fail("unreadable fleet " + fleet)
		}
		count := 0
		for _, f := range files {
			name := f.Name()
			if strings.HasPrefix(name, ".") {
				continue // an interrupted write's temp file; never renamed, never trusted
			}
			if filepath.Ext(name) != ".json" || !nameRE.MatchString(strings.TrimSuffix(name, ".json")) {
				return fail("unexpected file " + fleet + "/" + name)
			}
			count++
			if count > MaxPerFleet {
				return fail("too many deployments in " + fleet)
			}
			d, err := s.read(fleet + "/" + name)
			if err != nil || d.Fleet != fleet || d.Name+".json" != name {
				return fail("record " + fleet + "/" + name)
			}
			s.items[key(fleet, d.Name)] = d
		}
	}
	return s, nil
}

func (s *Store) read(rel string) (Deployment, error) {
	var d Deployment
	stat, err := s.root.Lstat(rel)
	if err != nil || !stat.Mode().IsRegular() || !private(stat) {
		return d, fmt.Errorf("not a private file")
	}
	f, err := s.root.Open(rel)
	if err != nil {
		return d, err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 2*MaxSpecBytes+1))
	if err != nil || len(raw) > 2*MaxSpecBytes {
		return d, fmt.Errorf("record too large")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&d); err != nil {
		return d, err
	}
	if d.Protocol != Protocol || !nameRE.MatchString(d.Name) || !fleetRE.MatchString(d.Fleet) || d.Revision < 1 {
		return d, fmt.Errorf("invalid record")
	}
	return d, d.Spec.Validate()
}

// write persists d. Caller holds s.mu. A failure poisons further writes until
// the controller restarts and reloads (the rename may or may not have landed).
func (s *Store) write(d Deployment) error {
	if s.failed {
		return fmt.Errorf("deployment store failed earlier; restart the controller")
	}
	raw, err := json.MarshalIndent(d, "", " ")
	if err != nil {
		return err
	}
	if err := s.root.Mkdir(d.Fleet, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
		return fmt.Errorf("deployment store write failed")
	}
	tmp := filepath.Join(d.Fleet, "."+d.Name+"-"+nonce())
	f, err := s.root.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err == nil {
		defer s.root.Remove(tmp)
		_, err = f.Write(raw)
		if err == nil {
			err = f.Sync()
		}
		if closeErr := f.Close(); err == nil {
			err = closeErr
		}
		if err == nil {
			err = s.root.Rename(tmp, filepath.Join(d.Fleet, d.Name+".json"))
		}
		if err == nil {
			err = syncDir(s.root, d.Fleet)
		}
	}
	if err != nil {
		s.failed = true
		return fmt.Errorf("deployment store write failed; recovery required")
	}
	return nil
}

func syncDir(root *os.Root, rel string) error {
	dir, err := root.Open(rel)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

func (s *Store) notify(k string) {
	select {
	case s.changes <- k:
	default: // the reconciler also rescans periodically; never block a writer
	}
}

// Changes delivers fleet/name keys of written deployments.
func (s *Store) Changes() <-chan string { return s.changes }

func (s *Store) Get(fleet, name string) (Deployment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	d, ok := s.items[key(fleet, name)]
	if !ok {
		return Deployment{}, ErrNotFound
	}
	return clone(d), nil
}

// List returns a fleet's deployments ordered by name.
func (s *Store) List(fleet string) []Deployment {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Deployment
	for _, d := range s.items {
		if d.Fleet == fleet {
			out = append(out, clone(d))
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// All returns every deployment (for the reconciler's periodic scan).
func (s *Store) All() []Deployment {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Deployment, 0, len(s.items))
	for _, d := range s.items {
		out = append(out, clone(d))
	}
	sort.Slice(out, func(i, j int) bool { return key(out[i].Fleet, out[i].Name) < key(out[j].Fleet, out[j].Name) })
	return out
}

// Put creates (revision 0) or replaces (current revision) a deployment's spec.
// Status is kept: where Apps run is observed state, not the owner's to reset.
func (s *Store) Put(fleet, name string, revision int64, spec Spec) (Deployment, error) {
	if !fleetRE.MatchString(fleet) || !nameRE.MatchString(name) {
		return Deployment{}, fmt.Errorf("invalid fleet or deployment name")
	}
	if err := spec.Validate(); err != nil {
		return Deployment{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, exists := s.items[key(fleet, name)]
	if (exists && current.Revision != revision) || (!exists && revision != 0) {
		return Deployment{}, ErrConflict
	}
	if !exists {
		count := 0
		for _, d := range s.items {
			if d.Fleet == fleet {
				count++
			}
		}
		if count >= MaxPerFleet {
			return Deployment{}, ErrLimit
		}
	}
	next := Deployment{Protocol: Protocol, Fleet: fleet, Name: name, Revision: revision + 1,
		Updated: time.Now().Unix(), Spec: clone(spec), Status: current.Status}
	if err := s.write(next); err != nil {
		return Deployment{}, err
	}
	s.items[key(fleet, name)] = next
	s.notify(key(fleet, name))
	return next, nil
}

// Patch applies a narrow owner edit to one App (intent, placement, config)
// under the same revision check as Put.
func (s *Store) Patch(fleet, name string, revision int64, app string, edit func(*AppSpec) error) (Deployment, error) {
	s.mu.Lock()
	current, ok := s.items[key(fleet, name)]
	s.mu.Unlock()
	if !ok {
		return Deployment{}, ErrNotFound
	}
	spec := cloneSpec(current.Spec)
	a, ok := spec.Apps[app]
	if !ok {
		return Deployment{}, ErrNotFound
	}
	if err := edit(&a); err != nil {
		return Deployment{}, err
	}
	spec.Apps[app] = a
	return s.Put(fleet, name, revision, spec)
}

// SetStatus records the reconciler's observation. It never changes the spec or
// its revision, so it cannot race an owner write into a lost update.
func (s *Store) SetStatus(fleet, name string, specRevision int64, status Status) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	current, ok := s.items[key(fleet, name)]
	if !ok {
		return ErrNotFound
	}
	if current.Revision != specRevision {
		return ErrConflict // the owner changed the spec meanwhile; observe again
	}
	current.Status = clone(status)
	if err := s.write(current); err != nil {
		return err
	}
	s.items[key(fleet, name)] = current
	return nil
}

// Delete removes a deployment record. Stopping its Apps is the reconciler's
// job before calling this (see the API's DELETE semantics).
func (s *Store) Delete(fleet, name string, revision int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	current, ok := s.items[key(fleet, name)]
	if !ok {
		return ErrNotFound
	}
	if current.Revision != revision {
		return ErrConflict
	}
	if s.failed {
		return fmt.Errorf("deployment store failed earlier; restart the controller")
	}
	if err := s.root.Remove(filepath.Join(fleet, name+".json")); err != nil {
		s.failed = true
		return fmt.Errorf("deployment store write failed; recovery required")
	}
	if err := syncDir(s.root, fleet); err != nil {
		s.failed = true
		return fmt.Errorf("deployment store write failed; recovery required")
	}
	delete(s.items, key(fleet, name))
	s.notify(key(fleet, name))
	return nil
}

func (s *Store) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.failed = true
	a, b := s.root.Close(), s.lock.Close()
	if a != nil {
		return a
	}
	return b
}

func cloneSpec(spec Spec) Spec { return clone(spec) }

// clone deep-copies through JSON: callers own what they get, and the store
// owns what it keeps (specs and statuses hold maps).
func clone[T any](v T) T {
	raw, _ := json.Marshal(v)
	var out T
	_ = json.Unmarshal(raw, &out)
	return out
}

func nonce() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
