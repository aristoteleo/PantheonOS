package groupcredentials

import (
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
)

// OverlayStore persists identity only, before any kernel effect. The lifecycle
// manager must serialize calls and hold the exclusive node-state lock. Closing
// enrollment does NOT prove that network namespaces or joined engines are gone.
type OverlayStore struct{ Root, Owner, Node string }

type OverlayStatus struct {
	Protocol     int               `json:"protocol"`
	Owner        string            `json:"owner"`
	Node         string            `json:"node_id"`
	Group        string            `json:"group_id"`
	TopologyHash string            `json:"topology_sha256"`
	State        string            `json:"state"` // prepared, pinned, closed; never ready/running
	Endpoint     *OverlayEndpoint  `json:"endpoint,omitempty"`
	Endpoints    []OverlayEndpoint `json:"endpoints,omitempty"`
}

type overlayRecord struct {
	OverlayStatus
	Manifest *Manifest `json:"manifest,omitempty"`
	Key      string    `json:"private_key,omitempty"`
}

func (s OverlayStore) directory(group, fingerprint string, create bool) (*os.Root, bool, error) {
	if !ownerRE.MatchString(s.Owner) || !nodeRE.MatchString(s.Node) || !groupRE.MatchString(group) || !digestRE.MatchString(fingerprint) {
		return nil, false, fmt.Errorf("invalid overlay enrollment identity")
	}
	// Never permit a changed plan to create a second key for the same group ID.
	return openPrivateState(s.Root, hash([]byte(s.Owner+"\x00"+s.Node+"\x00"+group)), create, 128)
}

func (s OverlayStore) validate(m Manifest) error {
	if err := validateOverlayManifest(m); err != nil {
		return err
	}
	if m.Topology.Owner != s.Owner || m.Topology.Members[*m.Rank].Node != s.Node {
		return fmt.Errorf("overlay enrollment requires this node's original rank")
	}
	return nil
}

func (s OverlayStore) read(root *os.Root, group, fingerprint string) (overlayRecord, error) {
	var r overlayRecord
	info, err := root.Lstat("overlay.json")
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 32768 {
		return r, fmt.Errorf("overlay record missing or invalid; never recreate this group")
	}
	f, err := root.Open("overlay.json")
	if err != nil {
		return r, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 32769))
	if err != nil || len(data) > 32768 {
		return r, fmt.Errorf("invalid bounded overlay record")
	}
	if decode(data, &r) != nil || r.Protocol != 1 || r.Owner != s.Owner || r.Node != s.Node ||
		r.Group != group || r.TopologyHash != fingerprint || (r.State != "prepared" && r.State != "pinned" && r.State != "closed") {
		return r, fmt.Errorf("overlay identity changed or record is corrupt")
	}
	if r.Manifest == nil {
		if r.State != "closed" || r.Key != "" || r.Endpoint != nil || r.Endpoints != nil {
			return r, fmt.Errorf("invalid overlay tombstone")
		}
		return r, nil
	}
	m := *r.Manifest
	if s.validate(m) != nil || m.Fingerprint() != fingerprint || m.Topology.Group != group || r.Endpoint == nil ||
		r.Endpoint.Rank != *m.Rank || validateOverlayAddress(r.Endpoint.Address) != nil {
		return r, fmt.Errorf("stored overlay rank binding changed")
	}
	if _, err := validateOverlayKey(r.Endpoint.PublicKey); err != nil {
		return r, err
	}
	if r.State == "closed" {
		if r.Key != "" {
			return r, fmt.Errorf("closed enrollment retained a private key")
		}
	} else {
		private, err := base64.StdEncoding.DecodeString(r.Key)
		public, keyErr := OverlayPublicKey(private)
		if err != nil || keyErr != nil || base64.StdEncoding.EncodeToString(private) != r.Key || public != r.Endpoint.PublicKey {
			return r, fmt.Errorf("original overlay key missing or invalid")
		}
	}
	if r.State == "pinned" || r.Endpoints != nil {
		if ValidateOverlayRoster(m, r.Endpoints) != nil || r.Endpoints[*m.Rank] != *r.Endpoint {
			return r, fmt.Errorf("stored overlay roster changed")
		}
	}
	if r.State == "prepared" && r.Endpoints != nil {
		return r, fmt.Errorf("uncommitted overlay roster")
	}
	return r, nil
}

func writeOverlay(root *os.Root, r overlayRecord) error {
	data, err := json.Marshal(r)
	if err != nil || len(data) > 32768 {
		return fmt.Errorf("overlay record exceeds storage bound")
	}
	return writePrivateFile(root, "overlay.json", data)
}

func (s OverlayStore) Prepare(m Manifest, address string) (OverlayStatus, error) {
	if err := s.validate(m); err != nil {
		return OverlayStatus{}, err
	}
	if err := validateOverlayAddress(address); err != nil {
		return OverlayStatus{}, err
	}
	group, fingerprint := m.Topology.Group, m.Fingerprint()
	dir, fresh, err := s.directory(group, fingerprint, true)
	if err != nil {
		return OverlayStatus{}, err
	}
	defer dir.Close()
	if fresh {
		key, err := ecdh.X25519().GenerateKey(rand.Reader)
		if err != nil {
			return OverlayStatus{}, err
		}
		e := OverlayEndpoint{Rank: *m.Rank, Address: address, PublicKey: base64.StdEncoding.EncodeToString(key.PublicKey().Bytes())}
		r := overlayRecord{OverlayStatus: OverlayStatus{Protocol: 1, Owner: s.Owner, Node: s.Node, Group: group, TopologyHash: fingerprint, State: "prepared", Endpoint: &e}, Manifest: &m, Key: base64.StdEncoding.EncodeToString(key.Bytes())}
		if err := writeOverlay(dir, r); err != nil {
			return OverlayStatus{}, err
		}
	}
	r, err := s.read(dir, group, fingerprint)
	if err != nil {
		return OverlayStatus{}, err
	}
	if r.Manifest != nil && (!reflect.DeepEqual(*r.Manifest, m) || r.Endpoint.Address != address) {
		return OverlayStatus{}, fmt.Errorf("original overlay enrollment cannot be replaced")
	}
	return r.OverlayStatus, nil
}

// Pin commits the complete owner-authenticated original roster once. Lost reply
// retries return the same roster; changes require an explicit new group ID.
func (s OverlayStore) Pin(group, fingerprint string, endpoints []OverlayEndpoint) (OverlayStatus, error) {
	dir, _, err := s.directory(group, fingerprint, false)
	if err != nil {
		return OverlayStatus{}, err
	}
	defer dir.Close()
	r, err := s.read(dir, group, fingerprint)
	if err != nil {
		return OverlayStatus{}, err
	}
	if r.State == "closed" {
		return OverlayStatus{}, fmt.Errorf("overlay enrollment is closed")
	}
	if err := ValidateOverlayRoster(*r.Manifest, endpoints); err != nil {
		return OverlayStatus{}, err
	}
	if endpoints[*r.Manifest.Rank] != *r.Endpoint {
		return OverlayStatus{}, fmt.Errorf("roster replaced this node's original endpoint")
	}
	if r.State == "pinned" {
		if !reflect.DeepEqual(r.Endpoints, endpoints) {
			return OverlayStatus{}, fmt.Errorf("original overlay roster cannot be replaced")
		}
	} else {
		r.Endpoints, r.State = endpoints, "pinned"
		if err := writeOverlay(dir, r); err != nil {
			return OverlayStatus{}, err
		}
	}
	return r.OverlayStatus, nil
}

func (s OverlayStore) Status(group, fingerprint string) (OverlayStatus, error) {
	dir, _, err := s.directory(group, fingerprint, false)
	if err != nil {
		return OverlayStatus{}, err
	}
	defer dir.Close()
	r, err := s.read(dir, group, fingerprint)
	if err != nil {
		return OverlayStatus{}, err
	}
	return r.OverlayStatus, nil
}

// Close is an enrollment fence only. A prior kernel installation keeps its key
// until the lifecycle owner separately reaps workloads and deletes namespaces.
func (s OverlayStore) Close(group, fingerprint string) (OverlayStatus, error) {
	dir, fresh, err := s.directory(group, fingerprint, true)
	if err != nil {
		return OverlayStatus{}, err
	}
	defer dir.Close()
	r := overlayRecord{OverlayStatus: OverlayStatus{Protocol: 1, Owner: s.Owner, Node: s.Node, Group: group, TopologyHash: fingerprint, State: "closed"}}
	if !fresh {
		r, err = s.read(dir, group, fingerprint)
		if err != nil {
			return OverlayStatus{}, err
		}
	}
	if fresh || r.State != "closed" {
		r.State, r.Key = "closed", ""
		if err := writeOverlay(dir, r); err != nil {
			return OverlayStatus{}, err
		}
	}
	return r.OverlayStatus, nil
}

// Material is node-internal and must never be exposed by an RPC. No material is
// available until the full original roster is pinned or after close fencing.
func (s OverlayStore) Material(m Manifest) ([]byte, []OverlayEndpoint, error) {
	if err := s.validate(m); err != nil {
		return nil, nil, err
	}
	dir, _, err := s.directory(m.Topology.Group, m.Fingerprint(), false)
	if err != nil {
		return nil, nil, err
	}
	defer dir.Close()
	r, err := s.read(dir, m.Topology.Group, m.Fingerprint())
	if err != nil {
		return nil, nil, err
	}
	if r.State != "pinned" || !reflect.DeepEqual(r.Manifest, &m) {
		return nil, nil, fmt.Errorf("exact original overlay roster is not pinned")
	}
	private, _ := base64.StdEncoding.DecodeString(r.Key)
	return private, r.Endpoints, nil
}
