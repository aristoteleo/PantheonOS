package node

import (
	"bufio"
	"errors"
	"net"
	"strings"
	"testing"
)

func TestDetectPlatformNetwork(t *testing.T) {
	ula := net.ParseIP("fdaa:1:2::5")
	env := map[string]string{"MODAL_TASK_ID": "ta-1", "MODAL_APP_ID": "ap-1", "MODAL_ENVIRONMENT": "main", "MODAL_REGION": "us-east"}
	iface := []InterfaceAddr{{Name: "lo", Loopback: true, IP: net.IPv6loopback}, {Name: "eth0", IP: ula}, {Name: "eth0", IP: net.ParseIP("10.0.0.2")}}
	cases := []struct {
		name  string
		mode  string
		env   map[string]string
		ips   []net.IP
		dnsEr error
		addrs []InterfaceAddr
		want  string // error substring; empty = ok
		scope string
	}{
		{name: "flag off", mode: "", env: env, ips: []net.IP{ula}, addrs: iface, want: "unsupported"},
		{name: "other mode", mode: "wireguard", env: env, ips: []net.IP{ula}, addrs: iface, want: "unsupported"},
		{name: "not modal", mode: "modal-i6pn", env: map[string]string{}, ips: []net.IP{ula}, addrs: iface, want: "MODAL_TASK_ID"},
		{name: "unresolvable", mode: "modal-i6pn", env: env, dnsEr: errors.New("nxdomain"), addrs: iface, want: "resolve"},
		{name: "ipv4 only", mode: "modal-i6pn", env: env, ips: []net.IP{net.ParseIP("10.0.0.2")}, addrs: iface, want: "no AAAA"},
		{name: "several", mode: "modal-i6pn", env: env, ips: []net.IP{ula, net.ParseIP("fdaa::9")}, addrs: iface, want: "several"},
		{name: "non-ULA", mode: "modal-i6pn", env: env, ips: []net.IP{net.ParseIP("2001:db8::1")}, addrs: []InterfaceAddr{{Name: "eth0", IP: net.ParseIP("2001:db8::1")}}, want: "unique-local"},
		{name: "zero interfaces", mode: "modal-i6pn", env: env, ips: []net.IP{ula}, addrs: iface[:1], want: "0 non-loopback"},
		{name: "loopback only", mode: "modal-i6pn", env: env, ips: []net.IP{ula}, addrs: []InterfaceAddr{{Name: "lo", Loopback: true, IP: ula}}, want: "0 non-loopback"},
		{name: "multiple interfaces", mode: "modal-i6pn", env: env, ips: []net.IP{ula}, addrs: append(append([]InterfaceAddr{}, iface...), InterfaceAddr{Name: "eth1", IP: ula}), want: "2 non-loopback"},
		{name: "ok", mode: "modal-i6pn", env: env, ips: []net.IP{net.ParseIP("10.0.0.2"), ula, ula}, addrs: iface, scope: "ap-1/us-east"},
		{name: "ok environment scope", mode: "modal-i6pn", env: map[string]string{"MODAL_TASK_ID": "ta-1", "MODAL_ENVIRONMENT": "main"}, ips: []net.IP{ula}, addrs: iface, scope: "main/"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p := PlatformProbe{
				Getenv:         func(k string) string { return tc.env[k] },
				LookupIP:       func(string) ([]net.IP, error) { return tc.ips, tc.dnsEr },
				InterfaceAddrs: func() ([]InterfaceAddr, error) { return tc.addrs, nil },
			}
			got, err := p.Detect(tc.mode)
			if tc.want != "" {
				if err == nil || !strings.Contains(err.Error(), tc.want) {
					t.Fatalf("got %v, %v; want error %q", got, err, tc.want)
				}
				return
			}
			if err != nil || got.Address != "fdaa:1:2::5" || got.Interface != "eth0" || got.Scope != tc.scope || got.Mode != "modal-i6pn" {
				t.Fatalf("got %+v, %v", got, err)
			}
			rt := got.Runtimes()
			if len(rt) != len(PlatformNetworkRuntimeKeys) || rt["group-platform-address"] != got.Address {
				t.Fatalf("runtimes %v", rt)
			}
		})
	}
}

func TestParseIfInet6(t *testing.T) {
	in := "00000000000000000000000000000001 01 80 10 80       lo\n" +
		"fdaa0001000200000000000000000005 02 40 00 80     eth0\n" +
		"garbage\n"
	got := parseIfInet6(bufio.NewScanner(strings.NewReader(in)))
	if len(got) != 2 || !got[0].Loopback || got[1].Loopback || got[1].Name != "eth0" || !got[1].IP.Equal(net.ParseIP("fdaa:1:2::5")) {
		t.Fatalf("%+v", got)
	}
}
