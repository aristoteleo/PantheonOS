// Package relaygeo orders a Fleet's relays nearest-first for a joining Node.
//
// go-libp2p's AutoRelay shuffles its relay candidates and reserves on only
// `desiredRelays` (2) of them — it has no latency/geo awareness (there's a TODO
// for it in relay_finder.go). So "use the closest relay" can't be a node-side
// decision; the Controller must hand each Node a list that already puts nearby
// relays first. The Node then reserves on the first couple, which are the closest.
//
// This is a coarse, continent-level ordering (GeoIP the Node's IP and each relay's
// IP to a continent, put same-continent relays first). It's enough to keep an Asia
// node off a US relay when both exist; same-continent tie-breaking by lat/long is a
// future refinement. Unknown geo (private IPs, GeoIP miss) leaves the order as-is.
package relaygeo

import (
	"net"
	"sort"
	"strings"

	"github.com/multiformats/go-multiaddr"
	"github.com/phuslu/iploc"
)

// Continent returns a coarse continent code (NA, SA, EU, AS, AF, OC, AN) for an
// IP, or "" if unknown (private/reserved IP, or GeoIP miss).
func Continent(ip net.IP) string {
	if ip == nil || !ip.IsGlobalUnicast() || ip.IsPrivate() {
		return ""
	}
	return countryToContinent[strings.ToUpper(iploc.Country(ip))]
}

// SortRelaysForIP returns relays reordered so that relays on the same continent as
// nodeIP come first, preserving the operator's original order within each group
// (stable). It never drops a relay — only reorders — so a Node still learns about
// every relay as a fallback. Returns the input unchanged when there's nothing to
// reorder (0/1 relay, or the node's location is unknown).
func SortRelaysForIP(relays []string, nodeIP net.IP) []string {
	if len(relays) < 2 {
		return relays
	}
	nodeCont := Continent(nodeIP)
	if nodeCont == "" {
		return relays // unknown node location — don't guess, keep operator order
	}
	// Precompute each relay's "same continent as the node?" once (avoid re-GeoIP in
	// the comparator).
	near := make([]bool, len(relays))
	idx := make([]int, len(relays))
	for i, r := range relays {
		idx[i] = i
		near[i] = relayContinent(r) == nodeCont
	}
	sort.SliceStable(idx, func(a, b int) bool {
		// near relays before far ones; stable keeps original order within a group.
		return near[idx[a]] && !near[idx[b]]
	})
	out := make([]string, len(relays))
	for i, j := range idx {
		out[i] = relays[j]
	}
	return out
}

// relayContinent extracts the /ip4 or /ip6 address from a relay multiaddr and maps
// it to a continent.
func relayContinent(relayMultiaddr string) string {
	m, err := multiaddr.NewMultiaddr(relayMultiaddr)
	if err != nil {
		return ""
	}
	if v, err := m.ValueForProtocol(multiaddr.P_IP4); err == nil {
		return Continent(net.ParseIP(v))
	}
	if v, err := m.ValueForProtocol(multiaddr.P_IP6); err == nil {
		return Continent(net.ParseIP(v))
	}
	return ""
}

// countryToContinent maps ISO-3166 alpha-2 country codes to a coarse continent.
// Built once at init from per-continent lists (more compact + readable than a flat
// 250-entry literal). Russia/Turkey/etc. that straddle two continents are placed
// where the bulk of their population + internet infrastructure sits.
var countryToContinent = func() map[string]string {
	groups := map[string]string{
		// North America + Central America + Caribbean
		"US CA MX GT CU HT DO HN NI SV CR PA JM TT BZ BS BB LC GD VC AG DM KN PR GL BM": "NA",
		// South America
		"BR AR CO PE VE CL EC BO PY UY GY SR GF FK": "SA",
		// Europe
		"GB IE FR DE IT ES PT NL BE LU CH AT DK SE NO FI IS PL CZ SK HU RO BG GR HR SI RS BA ME MK AL XK UA BY MD LT LV EE RU TR CY MT LI MC AD SM VA GI FO": "EU",
		// Asia + Middle East
		"CN JP KR IN SG HK TW ID TH VN MY PH BD PK LK NP BT MM KH LA MN KZ UZ TM KG TJ AF IR IQ SA AE IL JO LB SY YE OM KW QA BH GE AM AZ PS MO BN TL MV": "AS",
		// Africa
		"ZA EG NG KE MA DZ TN GH ET TZ UG AO CM CI SN ZW ZM MZ MW RW BJ BF ML NE TD SD SS SO LY MR GA CG CD GN SL LR TG NA BW LS SZ MG MU RE SC KM DJ ER GM GW CV ST GQ CF BI": "AF",
		// Oceania
		"AU NZ FJ PG SB VU WS TO KI FM MH NR PW TV NC PF": "OC",
		// Antarctica
		"AQ": "AN",
	}
	m := make(map[string]string, 256)
	for codes, cont := range groups {
		for _, c := range strings.Fields(codes) {
			m[c] = cont
		}
	}
	return m
}()
