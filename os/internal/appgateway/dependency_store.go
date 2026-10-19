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
	"os"
	"path/filepath"
	"regexp"
	"time"
)

var grantHex = regexp.MustCompile(`^[a-f0-9]{64}$`)

const maxDependencyRecords = 4096

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
	s := &dependencyStore{root: root, lock: lock, records: map[string]dependencyRecord{}}
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
		if !grantHex.MatchString(key) || len(s.records) >= maxDependencyRecords {
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
				if v.Expires > time.Now().Add(15*time.Minute).Unix() || len(active) >= 1024 {
					return fail()
				}
				active[v.Token] = &dependencyGrant{*v.Request, v.ID}
			}
		} else if v.Token != "" {
			return fail()
		}
		s.records[key] = v
	}
	// Validate the complete journal before compacting expired credentials. Keep
	// operation tombstones so retries cannot resurrect an expired authorization.
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
	s.records[v.Key] = v
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
	if _, exists := g.dependencyStore.records[recordKey]; !exists && len(g.dependencyStore.records) >= maxDependencyRecords {
		return fmt.Errorf("dependency journal capacity reached")
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
