// Package groupnetwork creates an isolated, encrypted Linux collective network.
// It is a node-internal primitive, never an App-supplied Docker/network option.
package groupnetwork

import (
	"bytes"
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/netip"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

// Endpoint identities must be persisted with the original creation before use.
// Address is the private underlay UDP endpoint, not an engine's overlay IP.
type Endpoint struct {
	Rank      int    `json:"rank"`
	Address   string `json:"address"`
	PublicKey string `json:"public_key"`
}
type Spec struct {
	Manifest  groupcredentials.Manifest `json:"manifest"`
	Endpoints []Endpoint                `json:"endpoints"`
	Interface string                    `json:"interface"`
}

func validKey(value string) ([]byte, error) {
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

func (s Spec) Validate() error {
	data, err := json.Marshal(s.Manifest)
	if err != nil {
		return fmt.Errorf("invalid group manifest")
	}
	manifest, err := groupcredentials.ParseManifest(data)
	if err != nil {
		return err
	}
	canonical, _ := json.Marshal(manifest)
	if !bytes.Equal(data, canonical) {
		return fmt.Errorf("use canonical rank-sorted overlay addresses")
	}
	if s.Interface != "wg0" {
		return fmt.Errorf("collective overlay must use wg0")
	}
	if len(s.Endpoints) != len(manifest.Topology.Members) {
		return fmt.Errorf("pin every underlay endpoint")
	}
	keys, sockets, addresses := map[string]bool{}, map[string]bool{}, map[string]bool{}
	ipv6 := strings.Contains(manifest.Topology.Members[0].Address, ":")
	for rank, endpoint := range s.Endpoints {
		addr, err := netip.ParseAddrPort(endpoint.Address)
		member := manifest.Topology.Members[rank]
		if err != nil || endpoint.Rank != rank || addr.String() != endpoint.Address || !addr.Addr().IsPrivate() || addr.Addr().Is4In6() || addr.Addr().Zone() != "" || addr.Port() < 1024 {
			return fmt.Errorf("use canonical distinct private UDP endpoints in rank order")
		}
		if _, err = validKey(endpoint.PublicKey); err != nil {
			return err
		}
		if keys[endpoint.PublicKey] || sockets[endpoint.Address] || addresses[member.Address] || strings.Contains(member.Address, ":") != ipv6 {
			return fmt.Errorf("use distinct keys, sockets and one overlay address family")
		}
		keys[endpoint.PublicKey], sockets[endpoint.Address], addresses[member.Address] = true, true, true
	}
	return nil
}

// Runner's error must not include stdin; stdin may contain a private key.
type Runner interface {
	Run(context.Context, []string, []byte) error
}
type Commands struct{}

func (Commands) Run(ctx context.Context, argv []string, input []byte) error {
	deadline, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(deadline, argv[0], argv[1:]...)
	cmd.Stdin = bytes.NewReader(input)
	// Never return command output, including key/config diagnostics.
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("network command %s failed: %w", argv[0], err)
	}
	return nil
}

// Network is an owned namespace lease. Callers must retain it until all joined
// workload processes are gone. This primitive has no automatic restart/adoption.
type Network struct {
	namespace        string
	device           string
	hostInterface    string
	runner           Runner
	namespaceCreated bool
	linkCreated      bool
	moved            bool
}

func PublicKey(private []byte) (string, error) {
	if len(private) != 32 || bytes.Equal(private, make([]byte, 32)) {
		return "", fmt.Errorf("invalid WireGuard private key")
	}
	key, err := ecdh.X25519().NewPrivateKey(private)
	if err != nil {
		return "", fmt.Errorf("invalid WireGuard private key")
	}
	return base64.StdEncoding.EncodeToString(key.PublicKey().Bytes()), nil
}

// Create activates only lo and wg0, with exact peer /32 or /128 routes. The
// encrypted UDP socket remains in the parent namespace; cleartext cannot use its
// host interfaces/default route. The caller must drop NET_ADMIN and NET_RAW in workloads.
// Private key bytes originate on this node and are passed through stdin only.
func Create(ctx context.Context, s Spec, private []byte, runner Runner) (*Network, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := s.Validate(); err != nil {
		return nil, err
	}
	public, err := PublicKey(private)
	if err != nil || public != s.Endpoints[*s.Manifest.Rank].PublicKey {
		return nil, fmt.Errorf("private key does not match this original rank")
	}
	if runner == nil {
		if runtime.GOOS != "linux" {
			return nil, fmt.Errorf("collective namespaces require Linux")
		}
		runner = Commands{}
	}
	if _, native := runner.(Commands); native {
		local, _ := netip.ParseAddrPort(s.Endpoints[*s.Manifest.Rank].Address)
		addresses, err := net.InterfaceAddrs()
		if err != nil {
			return nil, err
		}
		found := false
		for _, address := range addresses {
			prefix, e := netip.ParsePrefix(address.String())
			if e == nil && prefix.Addr() == local.Addr() {
				found = true
			}
		}
		if !found {
			return nil, fmt.Errorf("original private underlay address is not on this node")
		}
	}
	nonce := make([]byte, 16)
	if _, err = rand.Read(nonce); err != nil {
		return nil, err
	}
	sum := sha256.Sum256(nonce)
	suffix := hex.EncodeToString(sum[:])
	n := &Network{namespace: "pf-group-" + suffix[:24], device: s.Interface, hostInterface: "pfg" + suffix[:12], runner: runner}
	step := func(argv ...string) error { return runner.Run(ctx, argv, nil) }
	fail := func(cause error) (*Network, error) {
		cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if closeErr := n.Close(cleanup); closeErr != nil {
			return n, fmt.Errorf("network setup failed; cleanup remains required: %w", closeErr)
		}
		return nil, cause
	}
	// Claim every possible kernel effect before sending its command. A timeout
	// can follow a successful mutation; retain the lease if cleanup is uncertain.
	// Random exclusive names prevent adopting existing resources.
	n.namespaceCreated = true
	if err = step("ip", "netns", "add", n.namespace); err != nil {
		return n, fmt.Errorf("namespace creation outcome requires inspection: %w", err)
	}
	n.linkCreated = true
	if err = step("ip", "link", "add", n.hostInterface, "type", "wireguard"); err != nil {
		return fail(err)
	}
	if err = step("ip", "link", "set", n.hostInterface, "netns", n.namespace); err != nil {
		return fail(err)
	}
	n.moved = true
	if err = step("ip", "-n", n.namespace, "link", "set", n.hostInterface, "name", n.device); err != nil {
		return fail(err)
	}
	local := s.Endpoints[*s.Manifest.Rank]
	endpoint, _ := netip.ParseAddrPort(local.Address)
	argv := []string{"ip", "netns", "exec", n.namespace, "wg", "set", n.device, "private-key", "/dev/stdin", "listen-port", strconv.Itoa(int(endpoint.Port()))}
	for rank, peer := range s.Endpoints {
		if rank == *s.Manifest.Rank {
			continue
		}
		member := s.Manifest.Topology.Members[rank]
		ip, _ := netip.ParseAddr(member.Address)
		argv = append(argv, "peer", peer.PublicKey, "allowed-ips", netip.PrefixFrom(ip, ip.BitLen()).String(), "endpoint", peer.Address, "persistent-keepalive", "5")
	}
	secret := []byte(base64.StdEncoding.EncodeToString(private) + "\n")
	err = runner.Run(ctx, argv, secret)
	for i := range secret {
		secret[i] = 0
	}
	if err != nil {
		return fail(err)
	}
	member := s.Manifest.Topology.Members[*s.Manifest.Rank]
	ip, _ := netip.ParseAddr(member.Address)
	if err = step("ip", "-n", n.namespace, "address", "add", netip.PrefixFrom(ip, ip.BitLen()).String(), "dev", n.device); err != nil {
		return fail(err)
	}
	if err = step("ip", "-n", n.namespace, "link", "set", "lo", "up"); err != nil {
		return fail(err)
	}
	if err = step("ip", "-n", n.namespace, "link", "set", n.device, "mtu", "1280", "up"); err != nil {
		return fail(err)
	}
	for rank, peer := range s.Manifest.Topology.Members {
		if rank == *s.Manifest.Rank {
			continue
		}
		ip, _ := netip.ParseAddr(peer.Address)
		if err = step("ip", "-n", n.namespace, "route", "add", netip.PrefixFrom(ip, ip.BitLen()).String(), "dev", n.device); err != nil {
			return fail(err)
		}
	}
	return n, nil
}

// Namespace returns the node-created name, never a name supplied by an App.
func (n *Network) Namespace() string { return n.namespace }

// Close requires reaping joined workloads first; namespace unlink alone cannot
// kill processes retaining references. Errors retain the lease for inspection.
func (n *Network) Close(ctx context.Context) error {
	if n.linkCreated && !n.moved {
		if err := n.runner.Run(ctx, []string{"ip", "link", "del", n.hostInterface}, nil); err != nil {
			return err
		}
		n.linkCreated = false
	}
	if n.namespaceCreated {
		if err := n.runner.Run(ctx, []string{"ip", "netns", "del", n.namespace}, nil); err != nil {
			return err
		}
		n.namespaceCreated = false
		n.linkCreated = false
	}
	return nil
}
