package relaygeo

import (
	"net"
	"testing"
)

// a valid peer id so multiaddr.NewMultiaddr parses the /p2p component
const testPID = "12D3KooWA7CQ4CSu9cjkA3KZepxpKyCcgkbNcCRygQMn42wei6Cz"

func relayAddr(ip string) string {
	return "/ip4/" + ip + "/udp/4250/quic-v1/p2p/" + testPID
}

func TestContinent(t *testing.T) {
	cases := map[string]string{
		"8.8.8.8":         "NA", // Google DNS, US
		"114.114.114.114": "AS", // China public DNS
		"10.0.0.1":        "",   // private
		"192.168.1.1":     "",   // private
	}
	for ip, want := range cases {
		if got := Continent(net.ParseIP(ip)); got != want {
			t.Errorf("Continent(%s) = %q, want %q", ip, got, want)
		}
	}
}

func TestSortRelaysForIP(t *testing.T) {
	us := relayAddr("8.8.8.8")         // NA
	cn := relayAddr("114.114.114.114") // AS
	relays := []string{cn, us}         // operator order: CN then US

	// US node → US (same-continent) relay first.
	if got := SortRelaysForIP(relays, net.ParseIP("8.8.8.8")); got[0] != us {
		t.Errorf("US node: want US relay first, got %v", got)
	}
	// Asia node → CN relay first.
	if got := SortRelaysForIP(relays, net.ParseIP("114.114.114.114")); got[0] != cn {
		t.Errorf("Asia node: want CN relay first, got %v", got)
	}
	// Unknown (private) node IP → order unchanged (never guess).
	if got := SortRelaysForIP(relays, net.ParseIP("10.0.0.1")); got[0] != cn || got[1] != us {
		t.Errorf("private node: want unchanged order, got %v", got)
	}
	// A single relay is returned unchanged.
	if got := SortRelaysForIP([]string{us}, net.ParseIP("114.114.114.114")); len(got) != 1 || got[0] != us {
		t.Errorf("single relay: want unchanged, got %v", got)
	}
	// Stability: with two same-continent relays, operator order is preserved.
	us2 := relayAddr("8.8.4.4") // also NA
	if got := SortRelaysForIP([]string{us, us2}, net.ParseIP("8.8.8.8")); got[0] != us || got[1] != us2 {
		t.Errorf("stable within continent: want [us, us2], got %v", got)
	}
}
