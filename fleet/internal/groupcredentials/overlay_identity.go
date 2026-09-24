package groupcredentials

import (
	"bytes"
	"crypto/ecdh"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/netip"
	"reflect"
	"strings"
)

// OverlayEndpoint identifies one original node-owned WireGuard key and private
// UDP endpoint. It contains no engine address, private key or filesystem path.
type OverlayEndpoint struct {
	Rank      int    `json:"rank"`
	Address   string `json:"address"`
	PublicKey string `json:"public_key"`
}

func validateOverlayKey(value string) ([]byte, error) {
	raw, err := base64.StdEncoding.DecodeString(value)
	if err != nil || len(raw) != 32 || base64.StdEncoding.EncodeToString(raw) != value || string(raw) == string(make([]byte, 32)) {
		return nil, fmt.Errorf("invalid WireGuard public key")
	}
	// Generated Curve25519 public keys have canonical field encodings; reject
	// aliases so two roster keys cannot represent the same peer identity.
	if raw[31]&128 != 0 {
		return nil, fmt.Errorf("noncanonical WireGuard public key")
	}
	atLeastP := raw[31] == 127
	for i := 30; i > 0; i-- {
		atLeastP = atLeastP && raw[i] == 255
	}
	if atLeastP && raw[0] >= 237 {
		return nil, fmt.Errorf("noncanonical WireGuard public key")
	}
	// Reject low-order Curve25519 inputs as well as noncanonical encodings.
	peer, err := ecdh.X25519().NewPublicKey(raw)
	if err != nil {
		return nil, fmt.Errorf("invalid WireGuard public key")
	}
	probe, _ := ecdh.X25519().NewPrivateKey([]byte(strings.Repeat("x", 32)))
	if _, err = probe.ECDH(peer); err != nil {
		return nil, fmt.Errorf("invalid WireGuard public key")
	}
	return raw, nil
}

func OverlayPublicKey(private []byte) (string, error) {
	if len(private) != 32 || bytes.Equal(private, make([]byte, 32)) {
		return "", fmt.Errorf("invalid WireGuard private key")
	}
	key, err := ecdh.X25519().NewPrivateKey(private)
	if err != nil {
		return "", fmt.Errorf("invalid WireGuard private key")
	}
	return base64.StdEncoding.EncodeToString(key.PublicKey().Bytes()), nil
}

func validateOverlayManifest(m Manifest) error {
	data, _ := json.Marshal(m)
	parsed, err := ParseManifest(data)
	if err != nil || !reflect.DeepEqual(parsed, m) {
		return fmt.Errorf("use the exact canonical group manifest")
	}
	addresses := map[string]bool{}
	ipv6 := strings.Contains(m.Topology.Members[0].Address, ":")
	for _, member := range m.Topology.Members {
		if addresses[member.Address] || strings.Contains(member.Address, ":") != ipv6 {
			return fmt.Errorf("use distinct overlay addresses in one address family")
		}
		addresses[member.Address] = true
	}
	return nil
}

func validateOverlayAddress(address string) error {
	a, err := netip.ParseAddrPort(address)
	if err != nil || a.String() != address || !a.Addr().IsPrivate() || a.Addr().Is4In6() || a.Addr().Zone() != "" || a.Port() < 1024 {
		return fmt.Errorf("use a canonical private UDP endpoint")
	}
	return nil
}

// ValidateOverlayRoster is shared by durable enrollment and kernel setup.
func ValidateOverlayRoster(m Manifest, endpoints []OverlayEndpoint) error {
	if err := validateOverlayManifest(m); err != nil {
		return err
	}
	if len(endpoints) != len(m.Topology.Members) {
		return fmt.Errorf("pin every underlay endpoint")
	}
	keys, sockets := map[string]bool{}, map[string]bool{}
	for rank, e := range endpoints {
		if e.Rank != rank {
			return fmt.Errorf("pin endpoints in rank order")
		}
		if err := validateOverlayAddress(e.Address); err != nil {
			return err
		}
		if _, err := validateOverlayKey(e.PublicKey); err != nil {
			return err
		}
		if keys[e.PublicKey] || sockets[e.Address] {
			return fmt.Errorf("use distinct original keys and endpoints")
		}
		keys[e.PublicKey], sockets[e.Address] = true, true
	}
	return nil
}
