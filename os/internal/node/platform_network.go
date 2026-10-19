package node

import (
	"bufio"
	"context"
	"encoding/hex"
	"fmt"
	"net"
	"os"
	"runtime"
	"strings"
	"time"
)

// Model-group collectives on a platform-provided private network. Opt-in only;
// the capability is derived from detection, never accepted from --caps.
const (
	PlatformNetworkModalI6PN = "modal-i6pn"
	PlatformNetworkCap       = "model-group-platform-network"
	platformHostModalI6PN    = "i6pn.modal.local"
)

// Runtime keys advertised with PlatformNetworkCap.
var PlatformNetworkRuntimeKeys = []string{"group-platform-network", "group-platform-address", "group-platform-interface", "group-platform-scope"}

type PlatformNetwork struct {
	Mode, Address, Interface, Scope string
}

func (p PlatformNetwork) Runtimes() map[string]string {
	return map[string]string{
		"group-platform-network":   p.Mode,
		"group-platform-address":   p.Address,
		"group-platform-interface": p.Interface,
		"group-platform-scope":     p.Scope,
	}
}

// InterfaceAddr is one address assigned to a local interface.
type InterfaceAddr struct {
	Name     string
	Loopback bool
	IP       net.IP
}

// PlatformProbe is injectable so detection is testable off-platform.
type PlatformProbe struct {
	Getenv         func(string) string
	LookupIP       func(host string) ([]net.IP, error)
	InterfaceAddrs func() ([]InterfaceAddr, error)
}

// DetectPlatformNetwork re-checks the live environment; call it at join and
// again before starting a component that consumes the network.
func DetectPlatformNetwork(mode string) (PlatformNetwork, error) {
	return PlatformProbe{Getenv: os.Getenv, LookupIP: lookupIPv6, InterfaceAddrs: systemInterfaceAddrs}.Detect(mode)
}

func (p PlatformProbe) Detect(mode string) (PlatformNetwork, error) {
	if mode != PlatformNetworkModalI6PN {
		return PlatformNetwork{}, fmt.Errorf("unsupported platform network %q", mode)
	}
	if p.Getenv("MODAL_TASK_ID") == "" {
		return PlatformNetwork{}, fmt.Errorf("not a Modal container (MODAL_TASK_ID unset)")
	}
	ips, err := p.LookupIP(platformHostModalI6PN)
	if err != nil {
		return PlatformNetwork{}, fmt.Errorf("resolve %s: %w", platformHostModalI6PN, err)
	}
	var addr net.IP
	for _, ip := range ips {
		if ip.To4() != nil {
			continue
		}
		if addr != nil && !addr.Equal(ip) {
			return PlatformNetwork{}, fmt.Errorf("%s resolves to several IPv6 addresses", platformHostModalI6PN)
		}
		addr = ip
	}
	if addr == nil {
		return PlatformNetwork{}, fmt.Errorf("%s has no AAAA address", platformHostModalI6PN)
	}
	if addr[0]&0xfe != 0xfc {
		return PlatformNetwork{}, fmt.Errorf("%s address %s is not unique-local (fc00::/7)", platformHostModalI6PN, addr)
	}
	addrs, err := p.InterfaceAddrs()
	if err != nil {
		return PlatformNetwork{}, fmt.Errorf("list interfaces: %w", err)
	}
	names := map[string]bool{}
	for _, a := range addrs {
		if !a.Loopback && a.IP.Equal(addr) {
			names[a.Name] = true
		}
	}
	if len(names) != 1 {
		return PlatformNetwork{}, fmt.Errorf("address %s is on %d non-loopback interfaces, want exactly 1", addr, len(names))
	}
	var ifname string
	for n := range names {
		ifname = n
	}
	app := p.Getenv("MODAL_APP_ID")
	if app == "" {
		app = p.Getenv("MODAL_ENVIRONMENT")
	}
	return PlatformNetwork{Mode: mode, Address: addr.String(), Interface: ifname, Scope: app + "/" + p.Getenv("MODAL_REGION")}, nil
}

func lookupIPv6(host string) ([]net.IP, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return net.DefaultResolver.LookupIP(ctx, "ip6", host)
}

// systemInterfaceAddrs unions net package results with /proc/net/if_inet6:
// gVisor may expose IPv6 addresses only through the latter.
func systemInterfaceAddrs() ([]InterfaceAddr, error) {
	var out []InterfaceAddr
	seen := map[string]bool{}
	add := func(a InterfaceAddr) {
		if k := a.Name + "|" + a.IP.String(); !seen[k] {
			seen[k] = true
			out = append(out, a)
		}
	}
	ifaces, netErr := net.Interfaces()
	for _, iface := range ifaces {
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok {
				add(InterfaceAddr{Name: iface.Name, Loopback: iface.Flags&net.FlagLoopback != 0 || n.IP.IsLoopback(), IP: n.IP})
			}
		}
	}
	if runtime.GOOS == "linux" {
		if f, err := os.Open("/proc/net/if_inet6"); err == nil {
			defer f.Close()
			for _, a := range parseIfInet6(bufio.NewScanner(f)) {
				add(a)
			}
		}
	}
	if len(out) == 0 && netErr != nil {
		return nil, netErr
	}
	return out, nil
}

// Line format: <32 hex addr> <ifindex> <prefixlen> <scope> <flags> <ifname>.
func parseIfInet6(s *bufio.Scanner) []InterfaceAddr {
	var out []InterfaceAddr
	for s.Scan() {
		f := strings.Fields(s.Text())
		if len(f) != 6 || len(f[0]) != 32 {
			continue
		}
		b, err := hex.DecodeString(f[0])
		if err != nil {
			continue
		}
		ip := net.IP(b)
		out = append(out, InterfaceAddr{Name: f[5], Loopback: f[5] == "lo" || ip.IsLoopback(), IP: ip})
	}
	return out
}
