// Package groupnetwork creates an isolated, encrypted Linux collective network.
// It is a node-internal primitive, never an App-supplied Docker/network option.
package groupnetwork

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"net"
	"net/netip"
	"os/exec"
	"runtime"
	"strconv"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

type Endpoint = groupcredentials.OverlayEndpoint

type Spec struct {
	Manifest  groupcredentials.Manifest `json:"manifest"`
	Endpoints []Endpoint                `json:"endpoints"`
	Interface string                    `json:"interface"`
}

func (s Spec) Validate() error {
	if s.Interface != "wg0" {
		return fmt.Errorf("collective overlay must use wg0")
	}
	return groupcredentials.ValidateOverlayRoster(s.Manifest, s.Endpoints)
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
	journal          *durableNetwork
}

func PublicKey(private []byte) (string, error) {
	return groupcredentials.OverlayPublicKey(private)
}

// Create activates only lo and wg0, with exact peer /32 or /128 routes. The
// encrypted UDP socket remains in the parent namespace; cleartext cannot use its
// host interfaces/default route. The caller must drop NET_ADMIN and NET_RAW in workloads.
// Private key bytes originate on this node and are passed through stdin only.
func Create(ctx context.Context, s Spec, private []byte, runner Runner) (*Network, error) {
	return create(ctx, s, private, runner, nil)
}

func create(ctx context.Context, s Spec, private []byte, runner Runner, durable *durableNetwork) (*Network, error) {
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
	n := &Network{namespace: "pf-group-" + suffix[:24], device: s.Interface, hostInterface: "pfg" + suffix[:12], runner: runner, journal: durable}
	if durable != nil {
		if err := durable.claim(ctx, n); err != nil {
			return nil, err
		}
	}
	checkpoint := func(stage string) error {
		if durable == nil {
			return nil
		}
		return durable.advance(stage, "")
	}
	step := func(argv ...string) error { return runner.Run(ctx, argv, nil) }
	fail := func(cause error) (*Network, error) {
		if durable != nil {
			return n, cause
		}

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
	if err = checkpoint("creating"); err != nil {
		return fail(err)
	}
	n.namespaceCreated = true
	if durable != nil && durable.attachment != nil {
		err = durable.attachment.Attach(ctx, n.namespace)
	} else {
		err = step("ip", "netns", "add", n.namespace)
	}
	if err != nil {
		return n, fmt.Errorf("namespace creation outcome requires inspection: %w", err)
	}
	if durable != nil {
		observed, e := durable.backend.Observe(ctx, durable.state)
		if e != nil {
			return fail(e)
		}
		if observed.Identity == "" {
			return fail(fmt.Errorf("created namespace identity unavailable"))
		}
		if durable.attachment != nil {
			if observed.Identity != durable.state.AttachedIdentity {
				return fail(fmt.Errorf("attached namespace differs from original container"))
			}
			// Validate the same isolated interface set as teardown, but permit
			// the original waiting container process. No existing wg0 is owned.
			for _, link := range observed.Links {
				if link.Name == "wg0" || link.Name == n.hostInterface {
					return fail(fmt.Errorf("container already has a collective interface"))
				}
			}
			check := observed
			check.PIDs = nil
			if e := durable.validateCleanup(check); e != nil {
				return fail(fmt.Errorf("container network is not isolated: %w", e))
			}
		}
		if err = durable.advance("namespace", observed.Identity); err != nil {
			return fail(err)
		}
	}
	if err = checkpoint("linking"); err != nil {
		return fail(err)
	}
	n.linkCreated = true
	add := []string{"ip", "link", "add", n.hostInterface}
	if durable != nil {
		add = append(add, "alias", durable.alias())
	}
	add = append(add, "type", "wireguard")
	if err = step(add...); err != nil {
		return fail(err)
	}
	// Some Linux virtual link drivers ignore IFLA_IFALIAS on creation. Persisted
	// linking intent covers both create and explicit tagging, including their
	// acknowledgement gaps. The original random name identifies the untagged gap.
	if durable != nil {
		if err = step("ip", "link", "set", n.hostInterface, "alias", durable.alias()); err != nil {
			return fail(err)
		}
	}
	if err = checkpoint("link"); err != nil {
		return fail(err)
	}
	if err = checkpoint("moving"); err != nil {
		return fail(err)
	}
	if err = step("ip", "link", "set", n.hostInterface, "netns", n.namespace); err != nil {
		return fail(err)
	}
	n.moved = true
	if err = checkpoint("moved"); err != nil {
		return fail(err)
	}
	if err = checkpoint("naming"); err != nil {
		return fail(err)
	}
	rename := []string{"ip", "-n", n.namespace, "link", "set", n.hostInterface, "name", n.device}
	// Linux clears ifalias when moving a device to another namespace. Restore
	// ownership in the same netlink request that gives it the public wg0 name.
	if durable != nil {
		rename = append(rename, "alias", durable.alias())
	}
	if err = step(rename...); err != nil {
		return fail(err)
	}
	if err = checkpoint("named"); err != nil {
		return fail(err)
	}
	if err = checkpoint("configuring"); err != nil {
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
	if err = checkpoint("ready"); err != nil {
		return fail(err)
	}
	return n, nil
}

// Namespace returns the node-created name, never a name supplied by an App.
func (n *Network) Namespace() string { return n.namespace }

// Close requires reaping joined workloads first; namespace unlink alone cannot
// kill processes retaining references. Errors retain the lease for inspection.
func (n *Network) Close(ctx context.Context) error {
	if n.journal != nil {
		return fmt.Errorf("durable network requires StopDurable with workload reconciliation")
	}
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
