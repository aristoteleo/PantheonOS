package groupnetwork

import (
	"context"
	"fmt"
	"net/netip"
	"reflect"
	"strconv"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

// VerifyAttached is read-only. Recovery can reconnect to the exact namespace
// of a committed engine; it cannot create/adopt a replacement after a crash.
// Only individual public WireGuard fields are read, never dump/showconf/keys.
func VerifyAttached(ctx context.Context, store groupcredentials.OverlayStore, m groupcredentials.Manifest, identity string) error {
	private, endpoints, err := store.Material(m)
	if err != nil {
		return err
	}
	clear(private)
	n, _, err := store.Network(m)
	if err != nil {
		return err
	}
	if n == nil || n.Stage != "ready" || identity == "" || n.AttachedIdentity != identity || n.NamespaceIdentity != identity {
		return fmt.Errorf("original ready container network is unavailable")
	}
	o, err := (Commands{}).Observe(ctx, *n)
	if err != nil {
		return err
	}
	if err = verifyAttachedLinks(*n, m, identity, o); err != nil {
		return err
	}
	fields := map[string]string{}
	for _, field := range []string{"public-key", "listen-port", "peers", "allowed-ips", "endpoints"} {
		value, err := inspectCommand(ctx, "ip", "netns", "exec", n.Namespace, "wg", "show", "wg0", field)
		if err != nil {
			return err
		}
		fields[field] = strings.TrimSpace(string(value))
	}
	return verifyAttachedPeers(m, endpoints, fields)
}

func verifyAttachedLinks(n groupcredentials.OverlayNetwork, m groupcredentials.Manifest, identity string, o Observation) error {
	if o.Identity != identity || o.Host != nil || len(o.Links) < 2 {
		return fmt.Errorf("original private interface set changed")
	}
	alias := fmt.Sprintf("pantheon-model-group:%s:r%d:%s", m.Fingerprint(), *m.Rank, n.Namespace)
	found := false
	for _, link := range o.Links {
		if link.Name == "lo" {
			continue
		}
		if inertKernelInterface(link) {
			continue
		}
		if found || link.Name != "wg0" || link.Info.Kind != "wireguard" || link.Alias != alias {
			return fmt.Errorf("original collective interface identity changed")
		}
		found = true
		up := false
		for _, flag := range link.Flags {
			up = up || flag == "UP"
		}
		if !up {
			return fmt.Errorf("original collective interface is down")
		}
		ip := netip.MustParseAddr(m.Topology.Members[*m.Rank].Address)
		if len(link.Addresses) != 1 {
			return fmt.Errorf("original collective address changed")
		}
		address, ok := link.Addresses[0].(map[string]any)
		if !ok || address["local"] != ip.String() || address["prefixlen"] != float64(ip.BitLen()) {
			return fmt.Errorf("original collective address changed")
		}
	}
	if !found {
		return fmt.Errorf("original collective interface missing")
	}
	return nil
}

func verifyAttachedPeers(m groupcredentials.Manifest, endpoints []groupcredentials.OverlayEndpoint, fields map[string]string) error {
	local := endpoints[*m.Rank]
	address := netip.MustParseAddrPort(local.Address)
	if fields["public-key"] != local.PublicKey || fields["listen-port"] != strconv.Itoa(int(address.Port())) {
		return fmt.Errorf("original collective key or listening port changed")
	}
	expectedPeers, expectedIPs, expectedEndpoints := map[string]bool{}, map[string]string{}, map[string]string{}
	for rank, peer := range endpoints {
		if rank == *m.Rank {
			continue
		}
		ip := netip.MustParseAddr(m.Topology.Members[rank].Address)
		expectedPeers[peer.PublicKey] = true
		expectedIPs[peer.PublicKey] = netip.PrefixFrom(ip, ip.BitLen()).String()
		expectedEndpoints[peer.PublicKey] = peer.Address
	}
	peers := map[string]bool{}
	for _, key := range strings.Fields(fields["peers"]) {
		if peers[key] {
			return fmt.Errorf("duplicate collective peer")
		}
		peers[key] = true
	}
	if !reflect.DeepEqual(peers, expectedPeers) {
		return fmt.Errorf("original collective peers changed")
	}
	for field, expected := range map[string]map[string]string{"allowed-ips": expectedIPs, "endpoints": expectedEndpoints} {
		actual := map[string]string{}
		for _, line := range strings.Split(fields[field], "\n") {
			parts := strings.Fields(line)
			if len(parts) != 2 || actual[parts[0]] != "" {
				return fmt.Errorf("invalid collective peer metadata")
			}
			actual[parts[0]] = parts[1]
		}
		if !reflect.DeepEqual(actual, expected) {
			return fmt.Errorf("original collective %s changed", field)
		}
	}
	return nil
}
