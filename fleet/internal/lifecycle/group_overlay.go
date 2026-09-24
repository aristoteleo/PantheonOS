package lifecycle

import (
	"encoding/json"
	"fmt"
	"path/filepath"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

// OverlayRequest has no private-key, filesystem, namespace or driver options.
// Only the authenticated owner control plane can enroll or pin original peers.
type OverlayRequest struct {
	Manifest     json.RawMessage                    `json:"manifest,omitempty"`
	Address      string                             `json:"address,omitempty"`
	Group        string                             `json:"group_id,omitempty"`
	TopologyHash string                             `json:"topology_sha256,omitempty"`
	Endpoints    []groupcredentials.OverlayEndpoint `json:"endpoints,omitempty"`
}

func (m *Manager) GroupOverlay(action string, q OverlayRequest) (groupcredentials.OverlayStatus, error) {
	if !m.serial.TryLock() {
		return groupcredentials.OverlayStatus{}, fmt.Errorf("node lifecycle is busy; retry the same overlay operation")
	}
	defer m.serial.Unlock()
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return groupcredentials.OverlayStatus{}, fmt.Errorf("Runner is shutting down")
	}
	s := groupcredentials.OverlayStore{Root: filepath.Join(m.root, "group-overlays"), Owner: m.owner, Node: m.node}
	if action == "prepare" {
		if q.Group != "" || q.TopologyHash != "" || q.Endpoints != nil {
			return groupcredentials.OverlayStatus{}, fmt.Errorf("prepare accepts only original manifest and local UDP endpoint")
		}
		manifest, err := groupcredentials.ParseManifest(q.Manifest)
		if err != nil {
			return groupcredentials.OverlayStatus{}, err
		}
		return s.Prepare(manifest, q.Address)
	}
	if len(q.Manifest) != 0 || q.Address != "" || (action != "pin" && q.Endpoints != nil) {
		return groupcredentials.OverlayStatus{}, fmt.Errorf("overlay operation cannot replace original enrollment")
	}
	switch action {
	case "status":
		return s.Status(q.Group, q.TopologyHash)
	case "close":
		return s.Close(q.Group, q.TopologyHash)
	case "pin":
		return s.Pin(q.Group, q.TopologyHash, q.Endpoints)
	default:
		return groupcredentials.OverlayStatus{}, fmt.Errorf("unknown overlay operation")
	}
}
