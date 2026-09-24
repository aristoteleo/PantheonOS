package lifecycle

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

// GroupAuthority runs only through the owner-authenticated control plane. Rank
// zero stores the CA privately so Agent replacement does not replace trust.
// No App execution, private key export or remote peer discovery happens here.
func (m *Manager) GroupAuthority(action, group, fingerprint string, topology json.RawMessage, claim *groupcredentials.Claim) (any, error) {
	if !m.serial.TryLock() {
		return nil, fmt.Errorf("node lifecycle is busy; retry the same authority operation")
	}
	defer m.serial.Unlock()
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return nil, fmt.Errorf("Runner is shutting down")
	}
	store := groupcredentials.AuthorityStore{Root: filepath.Join(m.root, "group-authorities"), Owner: m.owner, Node: m.node}
	if action == "prepare" {
		if group != "" || fingerprint != "" || claim != nil {
			return nil, fmt.Errorf("prepare accepts only the complete group topology")
		}
		parsed, err := groupcredentials.ParseTopology(topology)
		if err != nil {
			return nil, err
		}
		return store.Prepare(parsed)
	}
	if len(topology) != 0 || (action != "issue" && claim != nil) {
		return nil, fmt.Errorf("authority operation cannot replace the group topology")
	}
	switch action {
	case "status":
		return store.Status(group, fingerprint)
	case "close":
		return store.Close(group, fingerprint)
	case "issue":
		if claim == nil {
			return nil, fmt.Errorf("missing exact group enrollment claim")
		}
		return store.Issue(group, fingerprint, *claim)
	default:
		return nil, fmt.Errorf("unknown group authority operation")
	}
}

// GroupPeer enrolls/installs only for a still-unconsumed preparation. The RPC
// caller supplies no owner, topology, CA pin, private key, or filesystem path.
// Those come from node identity and the exact installed artifact. A stop/start
// racing this operation is serialized and must pass its own generation CAS.
func (m *Manager) GroupPeer(instance, revision string, generation uint64, certificate, authority string, install bool) (groupcredentials.Enrollment, error) {
	if !m.serial.TryLock() {
		return groupcredentials.Enrollment{}, fmt.Errorf("node lifecycle is busy; retry the same group credential operation")
	}
	defer m.serial.Unlock()
	m.mu.Lock()
	defer m.mu.Unlock()
	in := m.ledger.Instances[instance]
	if m.closed || in == nil || in.Digest != revision || in.Generation != generation || in.State != "prepared" || generation >= 1<<63-1 {
		return groupcredentials.Enrollment{}, fmt.Errorf("group credentials require the exact current prepared instance")
	}
	installation := m.ledger.Installations[revision]
	if installation == nil || installation.State != "installed" || installation.Definition.AppID != "model-service" {
		return groupcredentials.Enrollment{}, fmt.Errorf("group credentials require an installed model-service artifact")
	}
	if err := checkPreparedReservations(in, installation.Definition); err != nil {
		return groupcredentials.Enrollment{}, err
	}
	root, err := os.OpenRoot(m.paths(revision, in.Scope).Package)
	if err != nil {
		return groupcredentials.Enrollment{}, err
	}
	defer root.Close()
	info, err := root.Lstat("group-peer.json")
	if err != nil || !info.Mode().IsRegular() || info.Size() > 16384 {
		return groupcredentials.Enrollment{}, fmt.Errorf("artifact has no regular bounded group-peer.json")
	}
	f, err := root.Open("group-peer.json")
	if err != nil {
		return groupcredentials.Enrollment{}, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 16385))
	if err != nil {
		return groupcredentials.Enrollment{}, err
	}
	manifest, err := groupcredentials.ParseManifest(data)
	if err != nil {
		return groupcredentials.Enrollment{}, err
	}
	binding := groupcredentials.Binding{Owner: m.owner, Node: m.node, Instance: instance, Revision: revision, Generation: generation + 1, Preparation: in.StartPreparationID}
	store := groupcredentials.Store{Root: filepath.Join(m.root, "group-credentials")}
	if install {
		return store.Install(binding, manifest, certificate, authority)
	}
	if certificate != "" || authority != "" {
		return groupcredentials.Enrollment{}, fmt.Errorf("enrollment accepts no certificate material")
	}
	return store.Enroll(binding, manifest)
}
