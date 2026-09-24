package groupnetwork

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

func TestRecoveryRejectsReplacedInterfacesAndPeerMetadata(t *testing.T) {
	s, _ := fixture(t)
	n := groupcredentials.OverlayNetwork{Namespace: "pf-group-aaaaaaaaaaaaaaaaaaaaaaaa"}
	wg := Link{Name: "wg0", Flags: []string{"UP"},
		Alias:     fmt.Sprintf("pantheon-model-group:%s:r0:%s", s.Manifest.Fingerprint(), n.Namespace),
		Addresses: []any{map[string]any{"local": "10.251.1.1", "prefixlen": float64(32)}}}
	wg.Info.Kind = "wireguard"
	original := Observation{Identity: "4:1234", Links: []Link{{Name: "lo"}, wg, {Name: "tunl0"}}, PIDs: []int{123}}
	if err := verifyAttachedLinks(n, s.Manifest, "4:1234", original); err != nil {
		t.Fatal(err)
	}
	for _, variant := range []string{"identity", "host", "external-interface", "alias", "kind", "down", "address", "prefix", "extra-address", "active-fallback", "addressed-fallback"} {
		t.Run(variant, func(t *testing.T) {
			data, _ := json.Marshal(original)
			var o Observation
			if err := json.Unmarshal(data, &o); err != nil {
				t.Fatal(err)
			}
			switch variant {
			case "active-fallback":
				o.Links[2].Flags = []string{"UP"}
			case "addressed-fallback":
				o.Links[2].Addresses = []any{map[string]any{"local": "192.168.1.1"}}
			case "identity":
				o.Identity = "4:1235"
			case "host":
				o.Host = &Link{Name: "pfgunexpected"}
			case "external-interface":
				o.Links = append(o.Links, Link{Name: "eth0"})
			case "alias":
				o.Links[1].Alias = "another-owner"
			case "kind":
				o.Links[1].Info.Kind = "veth"
			case "down":
				o.Links[1].Flags = nil
			case "address":
				o.Links[1].Addresses[0].(map[string]any)["local"] = "10.251.1.3"
			case "prefix":
				o.Links[1].Addresses[0].(map[string]any)["prefixlen"] = float64(24)
			case "extra-address":
				o.Links[1].Addresses = append(o.Links[1].Addresses, map[string]any{"local": "192.168.1.1"})
			}
			if verifyAttachedLinks(n, s.Manifest, "4:1234", o) == nil {
				t.Fatal("accepted changed interface", variant)
			}
		})
	}
	fields := map[string]string{"public-key": s.Endpoints[0].PublicKey, "listen-port": "51891",
		"peers": s.Endpoints[1].PublicKey, "allowed-ips": s.Endpoints[1].PublicKey + "\t10.251.1.2/32",
		"endpoints": s.Endpoints[1].PublicKey + "\t10.250.123.1:51892"}
	if err := verifyAttachedPeers(s.Manifest, s.Endpoints, fields); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"public-key", "listen-port", "peers", "allowed-ips", "endpoints"} {
		copy := map[string]string{}
		for k, v := range fields {
			copy[k] = v
		}
		copy[field] += "\nchanged"
		if verifyAttachedPeers(s.Manifest, s.Endpoints, copy) == nil {
			t.Fatal("accepted changed peer metadata", field)
		}
	}
	fields["allowed-ips"] = s.Endpoints[1].PublicKey + "\t0.0.0.0/0"
	if verifyAttachedPeers(s.Manifest, s.Endpoints, fields) == nil {
		t.Fatal("accepted widened route")
	}
}
