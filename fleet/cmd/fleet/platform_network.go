package main

import (
	"fmt"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// advertisePlatformNetwork derives the platform-network capability from
// detection only. Caller --caps or probed runtimes can never claim it.
func advertisePlatformNetwork(capa *proto.Capability, mode string, detect func(string) (node.PlatformNetwork, error)) error {
	caps := capa.Caps[:0]
	for _, cap := range capa.Caps {
		if cap != node.PlatformNetworkCap {
			caps = append(caps, cap)
		}
	}
	capa.Caps = caps
	for _, key := range node.PlatformNetworkRuntimeKeys {
		delete(capa.Runtimes, key)
	}
	if mode == "" {
		return nil
	}
	if mode != node.PlatformNetworkModalI6PN {
		return fmt.Errorf("--group-platform-network accepts only %q", node.PlatformNetworkModalI6PN)
	}
	p, err := detect(mode)
	if err != nil {
		return fmt.Errorf("--group-platform-network=%s: %w", mode, err)
	}
	if capa.Runtimes == nil {
		capa.Runtimes = map[string]string{}
	}
	for k, v := range p.Runtimes() {
		capa.Runtimes[k] = v
	}
	capa.Caps = append(capa.Caps, node.PlatformNetworkCap)
	return nil
}
