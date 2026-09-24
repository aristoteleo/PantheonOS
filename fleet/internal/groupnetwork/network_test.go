package groupnetwork

import (
	"bytes"
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

type call struct {
	argv  []string
	stdin []byte
}
type recorder struct {
	calls []call
	fail  int
}

func (r *recorder) Run(ctx context.Context, argv []string, input []byte) error {
	r.calls = append(r.calls, call{append([]string{}, argv...), append([]byte{}, input...)})
	if len(r.calls) == r.fail {
		return errors.New("injected command failure")
	}
	return ctx.Err()
}
func fixture(t *testing.T) (Spec, [][]byte) {
	t.Helper()
	keys := [][]byte{}
	endpoints := []Endpoint{}
	for rank := 0; rank < 2; rank++ {
		key, err := ecdh.X25519().GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		keys = append(keys, key.Bytes())
		pub, _ := PublicKey(key.Bytes())
		endpoints = append(endpoints, Endpoint{Rank: rank, Address: []string{"10.250.123.1:51891", "10.250.123.1:51892"}[rank], PublicKey: pub})
	}
	raw := `{"protocol":1,"rank":0,"ca_sha256":"` + strings.Repeat("a", 64) + `","topology":{"protocol":1,"owner":"f_0123456789abcdef","group_id":"network-test","model_sha256":"` + strings.Repeat("b", 64) + `","launch_sha256":"` + strings.Repeat("c", 64) + `","members":[{"rank":0,"node_id":"node0","generation":2,"address":"10.251.1.1","port":18400},{"rank":1,"node_id":"node1","generation":2,"address":"10.251.1.2","port":18400}]}}`
	manifest, err := groupcredentials.ParseManifest([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	return Spec{Manifest: manifest, Endpoints: endpoints, Interface: "wg0"}, keys
}
func TestOnlyPeerRoutesAndKeyViaStdin(t *testing.T) {
	s, keys := fixture(t)
	r := &recorder{}
	n, err := Create(context.Background(), s, keys[0], r)
	if err != nil {
		t.Fatal(err)
	}
	defer n.Close(context.Background())
	if len(r.calls) != 9 {
		t.Fatalf("unexpected commands: %+v", r.calls)
	}
	if got := strings.Join(r.calls[4].argv, " "); !strings.Contains(got, "allowed-ips 10.251.1.2/32 endpoint 10.250.123.1:51892") || !strings.Contains(got, "private-key /dev/stdin") {
		t.Fatal(got)
	}
	for i, c := range r.calls {
		argv := strings.Join(c.argv, " ")
		if strings.Contains(argv, "default") || strings.Contains(argv, "--network=host") {
			t.Fatal(argv)
		}
		if (i == 4) != (len(c.stdin) > 0) {
			t.Fatalf("key passed to wrong command: %d", i)
		}
	}
	if got := strings.Join(r.calls[8].argv, " "); !strings.Contains(got, "route add 10.251.1.2/32 dev wg0") {
		t.Fatal(got)
	}
	if err = n.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	count := len(r.calls)
	if err = n.Close(context.Background()); err != nil || len(r.calls) != count {
		t.Fatal("close not idempotent", err)
	}
}
func TestBadRosterRejectedBeforeEffects(t *testing.T) {
	cases := []string{"public", "loopback", "mapped", "duplicate-key", "zero-key", "order", "missing", "scope", "interface", "wrong-private", "overlay-order", "overlay-duplicate"}
	for _, mode := range cases {
		t.Run(mode, func(t *testing.T) {
			s, keys := fixture(t)
			switch mode {
			case "public":
				s.Endpoints[1].Address = "8.8.8.8:1234"
			case "loopback":
				s.Endpoints[1].Address = "127.0.0.1:1234"
			case "mapped":
				s.Endpoints[1].Address = "[::ffff:10.1.1.1]:1234"
			case "duplicate-key":
				s.Endpoints[1].PublicKey = s.Endpoints[0].PublicKey
			case "zero-key":
				s.Endpoints[1].PublicKey = strings.Repeat("A", 43) + "="
			case "order":
				s.Endpoints[1].Rank = 0
			case "missing":
				s.Endpoints = s.Endpoints[:1]
			case "scope":
				s.Manifest.Topology.Owner = "someone"
			case "interface":
				s.Interface = "eth0"
			case "wrong-private":
				keys[0] = keys[1]
			case "overlay-order":
				s.Manifest.Topology.Members[0], s.Manifest.Topology.Members[1] = s.Manifest.Topology.Members[1], s.Manifest.Topology.Members[0]
			case "overlay-duplicate":
				s.Manifest.Topology.Members[1].Address = s.Manifest.Topology.Members[0].Address
			}
			r := &recorder{}
			if _, err := Create(context.Background(), s, keys[0], r); err == nil || len(r.calls) != 0 {
				t.Fatal("invalid plan caused network effects", err)
			}
		})
	}
}
func TestIPv6HostRoutesAndNoDefault(t *testing.T) {
	s, keys := fixture(t)
	s.Manifest.Topology.Members[0].Address = "fd12::1"
	s.Manifest.Topology.Members[1].Address = "fd12::2"
	s.Endpoints[0].Address = "[fd34::1]:51891"
	s.Endpoints[1].Address = "[fd34::2]:51892"
	r := &recorder{}
	n, err := Create(context.Background(), s, keys[0], r)
	if err != nil {
		t.Fatal(err)
	}
	defer n.Close(context.Background())
	raw, _ := json.Marshal(r.calls[4].argv)
	if !strings.Contains(string(raw), "fd12::2/128") {
		t.Fatal(string(raw))
	}
	if got := strings.Join(r.calls[8].argv, " "); !strings.Contains(got, "route add fd12::2/128") {
		t.Fatal(got)
	}
}
func TestSetupFailureCleansOnlyNewNamespace(t *testing.T) {
	for fail := 2; fail <= 9; fail++ {
		s, keys := fixture(t)
		r := &recorder{fail: fail}
		if _, err := Create(context.Background(), s, keys[0], r); err == nil {
			t.Fatal("failure swallowed")
		}
		last := r.calls[len(r.calls)-1].argv
		if len(last) != 4 || strings.Join(last[:3], " ") != "ip netns del" || !strings.HasPrefix(last[3], "pf-group-") {
			t.Fatal(last)
		}
	}
}

func TestUncertainSetupRetainsCleanupHandle(t *testing.T) {
	s, keys := fixture(t)
	r := &recorder{fail: 1}
	n, err := Create(context.Background(), s, keys[0], r)
	if err == nil || n == nil || !n.namespaceCreated {
		t.Fatal("lost creation outcome discarded ownership")
	}
	if err = n.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestCanonicalCurveKeys(t *testing.T) {
	s, _ := fixture(t)
	raw, err := base64.StdEncoding.DecodeString(s.Endpoints[0].PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	raw[31] |= 128
	s.Endpoints[0].PublicKey = base64.StdEncoding.EncodeToString(raw)
	if err = s.Validate(); err == nil {
		t.Fatal("accepted masked duplicate key")
	}
	p := bytes.Repeat([]byte{255}, 32)
	p[0] = 237
	p[31] = 127
	s.Endpoints[0].PublicKey = base64.StdEncoding.EncodeToString(p)
	if err = s.Validate(); err == nil {
		t.Fatal("accepted field alias")
	}
	if _, err = PublicKey(make([]byte, 32)); err == nil {
		t.Fatal("accepted zero private key")
	}
}

func TestCancelledBeforeSetupHasNoEffects(t *testing.T) {
	s, keys := fixture(t)
	r := &recorder{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	n, err := Create(ctx, s, keys[0], r)
	if n != nil || !errors.Is(err, context.Canceled) || len(r.calls) != 0 {
		t.Fatal("cancelled setup performed effects", err)
	}
}
