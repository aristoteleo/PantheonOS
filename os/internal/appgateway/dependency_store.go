package appgateway

// Durable owner-issued dependency credentials. This is a single-controller,
// local-filesystem journal; replicas require external fencing and shared storage.
// The consumer has no access to this directory or the grant management routes.
import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

var grantHex = regexp.MustCompile(`^[a-f0-9]{64}$`)

// Journal bounds. Records are keyed by operation, so the journal grows with
// every grant a deployment's Apps ever asked for; expired records are pruned
// (see dependencyTombstoneRetention) and each owner (Fleet) has its own share,
// so one owner filling its share never stops admission for the others. The
// overall bound only limits memory and load time. Variables for tests.
var (
	maxDependencyRecords         = 65536
	maxDependencyRecordsPerFleet = 2048
	maxActiveDependencyGrants    = 4096
	maxActiveGrantsPerFleet      = 256
)

// dependencyTombstoneRetention is how long a record is kept after its grant
// expired or was revoked. Such a record (a tombstone: request and token
// dropped) exists only so that a retry of the same operation is answered 410
// instead of minting a fresh credential. Every caller retries an operation
// only within a short, bounded window:
//   - the allocator (hosted.Allocator) keeps a grant it still wants alive by
//     renewing it 5 minutes before its 15 minute expiry, retries a binding
//     only while its consumer calls bind_dependencies again (60 s client
//     timeout, at most two tries per call), and re-runs maintenance every
//     30 s with a 2 minute deadline. Its own binding journal records every
//     delivered grant and refuses a re-minted one ("cannot be replaced or
//     revived") without consulting this journal;
//   - the reconciler retries one start preparation until its grants expire
//     (grantTTL, 15 minutes), then prepares a new start; the node accepts a
//     preparation's configuration once;
//   - model access derives operation ids from 30 s windows.
// The longest retry that relies on this journal alone is therefore one grant
// lifetime plus the allocator's maintenance deadline, about 17 minutes. A day
// is a margin of more than 80 times, and it also covers a controller that was
// down for most of a day (nothing retries while it is down; after that the
// consumer check still requires the exact consumer generation to be live).
const dependencyTombstoneRetention = 24 * time.Hour

// dependencyPruneInterval bounds how often writes prune when not at capacity.
const dependencyPruneInterval = 10 * time.Minute

type dependencyRecord struct {
	Protocol int                `json:"protocol"`
	Domain   string             `json:"domain"`
	Key      string             `json:"key"`
	Policy   string             `json:"policy"`
	Token    string             `json:"token,omitempty"`
	ID       string             `json:"grant_id"`
	Fleet    string             `json:"fleet_id"`
	Expires  int64              `json:"expires"`
	Request  *DependencyRequest `json:"request,omitempty"`
}

type dependencyStore struct {
	root    *os.Root
	lock    *os.File
	records map[string]dependencyRecord
	fleets  map[string]int // records per owner
	pruned  time.Time      // last prune
	now     func() time.Time
}

func dependencyPolicy(q DependencyRequest) string {
	q.Expires = 0 // A retry must not implicitly renew the original grant.
	if q.HTTP != nil {
		permission := *q.HTTP
		permission.Credential = "" // Freshly signed credentials do not expand this pinned policy.
		q.HTTP = &permission
	}
	raw, _ := json.Marshal(q)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}
func dependencyOperation(q DependencyRequest) string {
	raw, _ := json.Marshal(q.Consumer)
	sum := sha256.Sum256(append(append(raw, 0), []byte(q.Operation)...))
	return hex.EncodeToString(sum[:])
}

// OpenDependencyStore must run after SetDependencyDispatch and before serving.
// Corruption, changed domain or concurrent ownership is fatal, never an empty
// fallback. A revoked/expired operation keeps a tombstone and cannot be reissued.
func (g *Gateway) OpenDependencyStore(path string) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.dependencyStore != nil || len(g.dependencies) != 0 || g.consumerCheck == nil {
		return fmt.Errorf("initialize dependency persistence before serving")
	}
	if err := makeDependencyDirectory(path); err != nil {
		return fmt.Errorf("dependency journal unavailable")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || !privateDependencyFile(info) {
		return fmt.Errorf("dependency journal must be private")
	}
	lock, err := lockDependencyStore(filepath.Join(path, "writer.lock"))
	if err != nil {
		return fmt.Errorf("dependency journal already owned or unavailable")
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		lock.Close()
		return fmt.Errorf("dependency journal unavailable")
	}
	s := &dependencyStore{root: root, lock: lock, records: map[string]dependencyRecord{}, fleets: map[string]int{}, now: g.dependencyNow}
	if s.now == nil {
		s.now = time.Now
	}
	fail := func() error {
		root.Close()
		lock.Close()
		return fmt.Errorf("dependency journal invalid; recovery required")
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		return fail()
	}
	active := map[string]*dependencyGrant{}
	identities := map[string]bool{}
	for _, entry := range entries {
		name := entry.Name()
		if filepath.Ext(name) != ".json" {
			continue
		}
		key := name[:len(name)-5]
		// Load a journal over its bound (left by an older controller or a
		// lowered bound) and prune it below, rather than refusing to start.
		if !grantHex.MatchString(key) || len(s.records) >= 4*maxDependencyRecords {
			return fail()
		}
		stat, e := root.Lstat(name)
		if e != nil || !stat.Mode().IsRegular() || !privateDependencyFile(stat) {
			return fail()
		}
		f, e := root.Open(name)
		if e != nil {
			return fail()
		}
		raw, e := io.ReadAll(io.LimitReader(f, 128*1024+1))
		f.Close()
		if e != nil || len(raw) > 128*1024 || uniqueJSON(raw) != nil {
			return fail()
		}
		var v dependencyRecord
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&v) != nil || v.Protocol != 1 || v.Domain != g.domain || v.Key != key || !grantHex.MatchString(v.Policy) || !grantHex.MatchString(v.ID) || v.Fleet == "" || v.Expires <= 0 {
			return fail()
		}
		if identities[v.ID] {
			return fail()
		}
		identities[v.ID] = true
		if v.Request != nil {
			q := *v.Request
			// Historical timestamps are validated structurally, without reviving
			// expired authorization. Request lifetime changes only through PATCH.
			q.Expires = time.Now().Add(time.Minute).Unix()
			sum := sha256.Sum256([]byte(v.Token))
			expected := v.ID
			if q.Operation != "" {
				expected = dependencyOperation(q)
			}
			if !q.valid() || !grantHex.MatchString(v.Token) || hex.EncodeToString(sum[:]) != v.ID || expected != key || v.Policy != dependencyPolicy(q) || q.Consumer.Fleet != v.Fleet || v.Request.Expires != v.Expires {
				return fail()
			}
			if v.Expires > time.Now().Unix() {
				if v.Expires > time.Now().Add(15*time.Minute).Unix() || len(active) >= maxActiveDependencyGrants {
					return fail()
				}
				active[v.Token] = &dependencyGrant{*v.Request, v.ID}
			}
		} else if v.Token != "" {
			return fail()
		}
		s.records[key] = v
		s.fleets[v.Fleet]++
	}
	// Validate the complete journal before pruning or compacting. Records
	// expired longer than the retention can no longer matter; drop them first.
	s.prune(s.now().Add(-dependencyTombstoneRetention).Unix(), "")
	if len(s.records) > maxDependencyRecords {
		log.Printf("[appgateway] DEPENDENCY JOURNAL OVER BOUND after pruning: %d records (bound %d); new grants are refused until records expire", len(s.records), maxDependencyRecords)
	}
	// Keep recent operation tombstones so retries cannot resurrect an expired
	// authorization.
	for _, record := range s.records {
		if record.Request != nil && record.Expires <= time.Now().Unix() {
			record.Request, record.Token = nil, ""
			if writeDependencyRecord(s, record) != nil {
				return fail()
			}
		}
	}
	g.dependencyStore = s
	g.dependencies = active
	return nil
}

func (g *Gateway) CloseDependencyStore() error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.dependencyStore == nil {
		return nil
	}
	// Stop dependency admission before surrendering the local writer lock.
	g.dependencyStoreFailed = true
	for _, flights := range g.dependencyHTTP {
		for flight := range flights {
			flight.cancel()
		}
	}
	s := g.dependencyStore
	g.dependencyStore = nil
	a, b := s.root.Close(), s.lock.Close()
	if a != nil {
		return a
	}
	return b
}

// Caller holds g.mu. Failure poisons admission until the process reloads the
// journal, including the ambiguous rename-success/directory-sync-failure case.
func (g *Gateway) saveDependency(v dependencyRecord) error {
	if g.dependencyStore == nil {
		return nil
	}
	if err := writeDependencyRecord(g.dependencyStore, v); err != nil {
		g.dependencyStoreFailed = true
		return err
	}
	return nil
}

func writeDependencyRecord(s *dependencyStore, v dependencyRecord) error {
	raw, err := json.Marshal(v)
	if err != nil || len(raw) > 128*1024 {
		return fmt.Errorf("dependency record exceeds limit")
	}
	tmp := ".grant-" + nonce()
	f, err := s.root.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err == nil {
		defer s.root.Remove(tmp)
		_, err = f.Write(raw)
		if err == nil {
			err = f.Sync()
		}
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
		if err == nil {
			err = s.root.Rename(tmp, v.Key+".json")
		}
		if err == nil {
			var dir *os.File
			dir, err = s.root.Open(".")
			if err == nil {
				err = dir.Sync()
				dir.Close()
			}
		}
	}
	if err != nil {
		return fmt.Errorf("dependency journal write failed; recovery required")
	}
	if old, exists := s.records[v.Key]; !exists {
		s.fleets[v.Fleet]++
	} else if old.Fleet != v.Fleet {
		s.fleets[old.Fleet]--
		s.fleets[v.Fleet]++
	}
	s.records[v.Key] = v
	return nil
}

// prune deletes records whose grant expired at or before cutoff (Unix
// seconds), of one owner or (fleet "") of all, and returns how many. Removing
// a file is crash-safe in either order: a record that survives a crash is a
// still-valid tombstone, one that is gone was already past mattering. A failed
// removal keeps the record and is retried by a later prune; it never poisons
// admission. Live (unexpired) records are never pruned.
func (s *dependencyStore) prune(cutoff int64, fleet string) int {
	removed := 0
	for key, v := range s.records {
		if v.Expires > cutoff || fleet != "" && v.Fleet != fleet {
			continue
		}
		if err := s.root.Remove(key + ".json"); err != nil && !os.IsNotExist(err) {
			continue
		}
		delete(s.records, key)
		if s.fleets[v.Fleet]--; s.fleets[v.Fleet] <= 0 {
			delete(s.fleets, v.Fleet)
		}
		removed++
	}
	if removed > 0 {
		if dir, err := s.root.Open("."); err == nil {
			_ = dir.Sync()
			dir.Close()
		}
	}
	s.pruned = s.now()
	return removed
}

// admit makes room for one new record of fleet: it prunes expired records
// past the retention every dependencyPruneInterval, and at once when the
// owner's share or the journal is full. A full share refuses that owner only;
// both cases are logged, since they mean grants are not expiring as designed.
func (s *dependencyStore) admit(fleet string) error {
	now := s.now()
	full := func() bool {
		return s.fleets[fleet] >= maxDependencyRecordsPerFleet || len(s.records) >= maxDependencyRecords
	}
	if full() || now.Sub(s.pruned) >= dependencyPruneInterval {
		if n := s.prune(now.Add(-dependencyTombstoneRetention).Unix(), ""); n > 0 {
			log.Printf("[appgateway] dependency journal: pruned %d expired records; %d remain", n, len(s.records))
		}
	}
	if s.fleets[fleet] >= maxDependencyRecordsPerFleet {
		log.Printf("[appgateway] DEPENDENCY JOURNAL FULL for owner %s: %d unexpired records (share %d); refusing its new grants", fleet, s.fleets[fleet], maxDependencyRecordsPerFleet)
		return fmt.Errorf("dependency journal capacity reached for this owner")
	}
	if len(s.records) >= maxDependencyRecords {
		log.Printf("[appgateway] DEPENDENCY JOURNAL FULL: %d records (bound %d); refusing new grants", len(s.records), maxDependencyRecords)
		return fmt.Errorf("dependency journal capacity reached")
	}
	return nil
}

func (g *Gateway) persistGrant(q DependencyRequest, key, id string) error {
	if g.dependencyStore == nil {
		return nil
	}
	recordKey := id
	if q.Operation != "" {
		recordKey = dependencyOperation(q)
	}
	if _, exists := g.dependencyStore.records[recordKey]; !exists {
		if err := g.dependencyStore.admit(q.Consumer.Fleet); err != nil {
			return dependencyError(503, err.Error())
		}
	}
	return g.saveDependency(dependencyRecord{Protocol: 1, Domain: g.domain, Key: recordKey,
		Policy: dependencyPolicy(q), Token: key, ID: id, Fleet: q.Consumer.Fleet, Expires: q.Expires, Request: &q})
}

func (g *Gateway) persistRevocation(fleet, id string) error {
	if g.dependencyStore == nil {
		return nil
	}
	for _, v := range g.dependencyStore.records {
		if v.ID == id && v.Fleet == fleet && v.Request != nil {
			v.Request, v.Token = nil, ""
			return g.saveDependency(v)
		}
	}
	return nil
}

// Sync every newly created directory's parent before issuing a credential. A
// record fsync alone cannot make a newly created journal path crash-durable.
func makeDependencyDirectory(path string) error {
	path = filepath.Clean(path)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		parent := filepath.Dir(path)
		if err := makeDependencyDirectory(parent); err != nil {
			return err
		}
		if err := os.Mkdir(path, 0700); err != nil && !os.IsExist(err) {
			return err
		}
	} else if err != nil {
		return err
	}
	parent, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer parent.Close()
	return parent.Sync()
}
