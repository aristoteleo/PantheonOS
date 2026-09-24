// Package groupcredentials keeps model-group private keys on their owning node.
// Its public manifest binds credentials to an immutable launch roster. It does
// not authorize collective networking, start engines, or publish readiness.
package groupcredentials

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/netip"
	"regexp"
	"sort"
)

var digestRE = regexp.MustCompile(`^[a-f0-9]{64}$`)
var ownerRE = regexp.MustCompile(`^f_[a-f0-9]{16}$`)
var groupRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
var nodeRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)

type Member struct {
	Rank       *int   `json:"rank"`
	Node       string `json:"node_id"`
	Generation uint64 `json:"generation"`
	Address    string `json:"address"`
	Port       int    `json:"port"`
}

type Topology struct {
	Protocol int      `json:"protocol"`
	Owner    string   `json:"owner"`
	Group    string   `json:"group_id"`
	Model    string   `json:"model_sha256"`
	Launch   string   `json:"launch_sha256"`
	Members  []Member `json:"members"`
}

type Manifest struct {
	Protocol int      `json:"protocol"`
	Rank     *int     `json:"rank"`
	Topology Topology `json:"topology"`
	CAHash   string   `json:"ca_sha256"` // SHA256 of the exact root certificate DER.
}

func decode(data []byte, value any) error {
	check := json.NewDecoder(bytes.NewReader(data))
	check.UseNumber()
	if err := uniqueJSON(check, 0); err != nil {
		return err
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(value); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("trailing JSON data")
	}
	return nil
}

func uniqueJSON(d *json.Decoder, depth int) error {
	if depth > 16 {
		return fmt.Errorf("JSON nesting exceeds bound")
	}
	t, err := d.Token()
	if err != nil {
		return err
	}
	switch t {
	case json.Delim('{'):
		seen := map[string]bool{}
		for d.More() {
			t, err := d.Token()
			if err != nil {
				return err
			}
			key, ok := t.(string)
			if !ok || seen[key] {
				return fmt.Errorf("duplicate JSON key")
			}
			seen[key] = true
			if err := uniqueJSON(d, depth+1); err != nil {
				return err
			}
		}
		_, err = d.Token()
	case json.Delim('['):
		for d.More() {
			if err := uniqueJSON(d, depth+1); err != nil {
				return err
			}
		}
		_, err = d.Token()
	}
	return err
}

func exactKeys(data []byte, keys ...string) bool {
	var value map[string]json.RawMessage
	if json.Unmarshal(data, &value) != nil || len(value) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, ok := value[key]; !ok {
			return false
		}
	}
	return true
}

func ParseManifest(data []byte) (Manifest, error) {
	var m Manifest
	if len(data) > 16384 {
		return m, fmt.Errorf("group peer manifest too large")
	}
	if err := decode(data, &m); err != nil {
		return m, fmt.Errorf("invalid group peer manifest")
	}
	var raw map[string]json.RawMessage
	_ = json.Unmarshal(data, &raw)
	if !exactKeys(data, "protocol", "rank", "topology", "ca_sha256") ||
		!exactKeys(raw["topology"], "protocol", "owner", "group_id", "model_sha256", "launch_sha256", "members") {
		return m, fmt.Errorf("declare exact group manifest fields")
	}
	var topology map[string]json.RawMessage
	_ = json.Unmarshal(raw["topology"], &topology)
	var members []json.RawMessage
	_ = json.Unmarshal(topology["members"], &members)
	for _, member := range members {
		if !exactKeys(member, "rank", "node_id", "generation", "address", "port") {
			return m, fmt.Errorf("declare exact group member fields")
		}
	}
	t := &m.Topology
	if m.Protocol != 1 || m.Rank == nil || !digestRE.MatchString(m.CAHash) || t.Protocol != 1 ||
		!ownerRE.MatchString(t.Owner) || !groupRE.MatchString(t.Group) ||
		!digestRE.MatchString(t.Model) || !digestRE.MatchString(t.Launch) ||
		len(t.Members) < 2 || len(t.Members) > 16 || *m.Rank < 0 || *m.Rank >= len(t.Members) {
		return m, fmt.Errorf("invalid group peer identity")
	}
	nodes, ranks, endpoints := map[string]bool{}, map[int]bool{}, map[string]bool{}
	for i := range t.Members {
		p := &t.Members[i]
		ip, err := netip.ParseAddr(p.Address)
		if err != nil || ip.Zone() != "" || ip.Is4In6() || !ip.IsPrivate() ||
			p.Rank == nil || *p.Rank < 0 || *p.Rank >= len(t.Members) || ranks[*p.Rank] ||
			!nodeRE.MatchString(p.Node) || nodes[p.Node] || p.Generation == 0 || p.Generation >= 1<<63 ||
			p.Port < 1024 || p.Port > 65535 {
			return m, fmt.Errorf("invalid or duplicate private group member")
		}
		p.Address = ip.String()
		endpoint := netip.AddrPortFrom(ip, uint16(p.Port)).String()
		if endpoints[endpoint] {
			return m, fmt.Errorf("duplicate private group endpoint")
		}
		nodes[p.Node], ranks[*p.Rank], endpoints[endpoint] = true, true, true
	}
	sort.Slice(t.Members, func(i, j int) bool { return *t.Members[i].Rank < *t.Members[j].Rank })
	return m, nil
}

// Fingerprint deliberately matches Python PeerTopology's sorted-key JSON,
// including nested member keys. All strings are validated ASCII identities/IPs.
func (m Manifest) Fingerprint() string {
	t := m.Topology
	peers := make([]map[string]any, len(t.Members))
	for i, p := range t.Members {
		peers[i] = map[string]any{"rank": *p.Rank, "node_id": p.Node, "generation": p.Generation, "address": p.Address, "port": p.Port}
	}
	b, _ := json.Marshal(map[string]any{"protocol": t.Protocol, "owner": t.Owner, "group_id": t.Group, "model_sha256": t.Model, "launch_sha256": t.Launch, "members": peers})
	return hash(b)
}

func hash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func (m Manifest) Name() string {
	h := m.Fingerprint()
	return fmt.Sprintf("r%d.%s.%s.fleet-model.invalid", *m.Rank, h[:32], h[32:])
}
