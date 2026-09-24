package lifecycle

import (
	"fmt"
	"slices"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func consumesGroupPlatformNetwork(def Definition) bool {
	for _, c := range def.Components {
		if c.GroupPlatformNetwork != "" {
			return true
		}
	}
	return false
}

// advertisedPlatformNetwork is fixed at Open; only `fleet up` detection sets it.
func advertisedPlatformNetwork(caps proto.Capability) node.PlatformNetwork {
	rt := caps.Runtimes
	if !slices.Contains(caps.Caps, node.PlatformNetworkCap) || rt["group-platform-network"] != node.PlatformNetworkModalI6PN ||
		rt["group-platform-address"] == "" || rt["group-platform-interface"] == "" {
		return node.PlatformNetwork{}
	}
	return node.PlatformNetwork{Mode: rt["group-platform-network"], Address: rt["group-platform-address"], Interface: rt["group-platform-interface"], Scope: rt["group-platform-scope"]}
}

// checkGroupPlatformNetwork runs before hooks or resources: the advertised
// network must still be live, and host-network ports allow one peer per node.
func (m *Manager) checkGroupPlatformNetwork(def Definition, key string) error {
	if !consumesGroupPlatformNetwork(def) {
		return nil
	}
	if m.platform.Mode == "" {
		return fmt.Errorf("node did not opt in to platform group networking (fleet up --group-platform-network)")
	}
	detect := m.platformDetect
	if detect == nil {
		detect = node.DetectPlatformNetwork
	}
	live, err := detect(m.platform.Mode)
	if err != nil {
		return fmt.Errorf("platform group network unavailable: %w", err)
	}
	if live.Address != m.platform.Address || live.Interface != m.platform.Interface {
		return fmt.Errorf("platform group network changed since the node joined; restart the node")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for id, in := range m.ledger.Instances {
		if id == key || len(in.Resources) == 0 {
			continue
		}
		if install := m.ledger.Installations[in.Digest]; install != nil && consumesGroupPlatformNetwork(install.Definition) {
			return fmt.Errorf("another platform group peer is live on this node")
		}
	}
	return nil
}
