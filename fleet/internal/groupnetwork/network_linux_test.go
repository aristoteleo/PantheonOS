//go:build linux

package groupnetwork

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

// Real namespaces only inside the explicitly isolated acceptance container.
func TestKernelEncryptedNamespace(t *testing.T) {
	if os.Getenv("PANTHEON_GROUP_NETWORK_TEST") != "isolated-container" {
		t.Skip("requires disposable privileged Linux acceptance container")
	}
	if _, err := os.Stat("/.dockerenv"); err != nil {
		t.Fatal("not inside acceptance container")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	command := func(argv ...string) string {
		t.Helper()
		out, err := exec.CommandContext(ctx, argv[0], argv[1:]...).CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s: %v", argv, out, err)
		}
		return string(out)
	}
	command("ip", "address", "add", "10.250.123.1/32", "dev", "lo")
	defer exec.Command("ip", "address", "del", "10.250.123.1/32", "dev", "lo").Run()
	rootRoutes := command("ip", "-j", "route", "show", "table", "all")
	spec, keys := fixture(t)
	stores := make([]groupcredentials.OverlayStore, len(spec.Endpoints))
	for rank := range spec.Endpoints {
		manifest := spec.Manifest
		manifest.Rank = &rank
		s := groupcredentials.OverlayStore{Root: filepath.Join(t.TempDir(), "overlay"), Owner: manifest.Topology.Owner, Node: manifest.Topology.Members[rank].Node}
		status, err := s.Prepare(manifest, spec.Endpoints[rank].Address)
		if err != nil {
			t.Fatal(err)
		}
		spec.Endpoints[rank] = *status.Endpoint
		stores[rank] = s
	}
	for rank, s := range stores {
		if _, err := s.Pin(spec.Manifest.Topology.Group, spec.Manifest.Fingerprint(), spec.Endpoints); err != nil {
			t.Fatal(err)
		}
		// Reconstruct the node store, then feed only the persisted original
		// key/roster to the real kernel. No fixture-generated key replacement.
		restarted := groupcredentials.OverlayStore{Root: s.Root, Owner: s.Owner, Node: s.Node}
		manifest := spec.Manifest
		manifest.Rank = &rank
		key, _, err := restarted.Material(manifest)
		if err != nil {
			t.Fatal(err)
		}
		keys[rank] = key
	}
	a, err := Create(ctx, spec, keys[0], nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := a.Close(context.Background()); err != nil {
			t.Error(err)
		}
	}()
	rank := 1
	spec.Manifest.Rank = &rank
	b, err := Create(ctx, spec, keys[1], nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := b.Close(context.Background()); err != nil {
			t.Error(err)
		}
	}()
	for _, n := range []*Network{a, b} {
		var links []struct {
			Name      string            `json:"ifname"`
			Flags     []string          `json:"flags"`
			Addresses []json.RawMessage `json:"addr_info"`
		}
		if err := json.Unmarshal([]byte(command("ip", "-n", n.Namespace(), "-j", "address", "show")), &links); err != nil {
			t.Fatal(err)
		}
		active := map[string]bool{}
		// Linux may instantiate inert fallback tunnel devices in a new namespace.
		// They must stay down and address-free; workload drops NET_ADMIN/NET_RAW.
		fallback := map[string]bool{"tunl0": true, "gre0": true, "gretap0": true, "erspan0": true, "ip_vti0": true, "ip6_vti0": true, "sit0": true, "ip6tnl0": true, "ip6gre0": true}
		for _, link := range links {
			if link.Name == "lo" || link.Name == "wg0" {
				active[link.Name] = true
				continue
			}
			if !fallback[link.Name] || len(link.Addresses) != 0 {
				t.Fatalf("unexpected usable interface: %+v", link)
			}
			for _, flag := range link.Flags {
				if flag == "UP" {
					t.Fatalf("active fallback interface: %+v", link)
				}
			}
		}
		if len(active) != 2 {
			t.Fatal("missing isolated interfaces", links)
		}
		if routes := command("ip", "-n", n.Namespace(), "route", "show"); strings.Contains(routes, "default") {
			t.Fatal(routes)
		}
		for _, denied := range []string{"1.1.1.1", "10.250.123.1", "10.251.1.99"} {
			if err := exec.CommandContext(ctx, "ip", "-n", n.Namespace(), "route", "get", denied).Run(); err == nil {
				t.Fatalf("unexpected route to %s", denied)
			}
		}
	}
	server := exec.CommandContext(ctx, "ip", "netns", "exec", b.Namespace(), "setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--bounding-set=-all", "--no-new-privs", "python3", "-u", "-c", `import socket
s=socket.socket();s.settimeout(15);s.bind(('10.251.1.2',32123));s.listen(1);print('ready',flush=True)
for _ in range(2):
 c,_=s.accept();c.settimeout(5);value=c.recv(128);c.sendall(value);c.close()
s.close()`)
	stdout, err := server.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = server.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if server.ProcessState == nil {
			server.Process.Kill()
			server.Wait()
		}
	}()
	if line, err := bufio.NewReader(stdout).ReadString('\n'); err != nil || line != "ready\n" {
		t.Fatalf("echo readiness: %q %v", line, err)
	}
	client := func(namespace string) {
		response := command("ip", "netns", "exec", namespace, "setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--bounding-set=-all", "--no-new-privs", "python3", "-c", `import os,socket
assert os.getuid()==65534
status=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
assert int(status['CapEff'].strip(),16)==0
try:
 socket.socket(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_TCP)
 raise AssertionError('raw capability escaped')
except PermissionError: pass
s=socket.create_connection(('10.251.1.2',32123),10);s.sendall(b'collective-encrypted');assert s.recv(128)==b'collective-encrypted';s.close();print('encrypted-peer-ok')`)
		if !strings.Contains(response, "encrypted-peer-ok") {
			t.Fatal(response)
		}
	}
	client(a.Namespace())
	// The same claimed inner address with another private key cannot authenticate.
	_, foreignKeys := fixture(t)
	foreignPublic, _ := PublicKey(foreignKeys[0])
	rank = 0
	spec.Manifest.Rank = &rank
	spec.Endpoints[0].PublicKey = foreignPublic
	spec.Endpoints[0].Address = "10.250.123.1:51893"
	foreign, err := Create(ctx, spec, foreignKeys[0], nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := foreign.Close(context.Background()); err != nil {
			t.Error(err)
		}
	}()
	denied := command("ip", "netns", "exec", foreign.Namespace(), "setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--bounding-set=-all", "--no-new-privs", "python3", "-c", `import socket
try:
 s=socket.create_connection(('10.251.1.2',32123),2)
 s.close();raise AssertionError('foreign key reached peer')
except TimeoutError: print('foreign-key-denied')`)
	if !strings.Contains(denied, "foreign-key-denied") {
		t.Fatal(denied)
	}
	client(a.Namespace())
	if err = foreign.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if err = server.Wait(); err != nil {
		t.Fatal(err)
	}
	for _, n := range []*Network{a, b} {
		// Never use wg dump/showconf; those expose private keys.
		fields := strings.Fields(command("ip", "netns", "exec", n.Namespace(), "wg", "show", "wg0", "transfer"))
		var received, sent uint64
		if len(fields) != 3 {
			t.Fatal("expected one original authenticated peer")
		}
		fmt.Sscan(fields[1], &received)
		fmt.Sscan(fields[2], &sent)
		if received == 0 || sent == 0 {
			t.Fatal("no encrypted transfer", received, sent)
		}
	}
	if after := command("ip", "-j", "route", "show", "table", "all"); after != rootRoutes {
		t.Fatal("parent routes changed")
	}
	if err = a.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if err = b.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if names := command("ip", "netns", "list"); strings.Contains(names, "pf-group-") {
		t.Fatal("namespace leak", names)
	}
	t.Log("unprivileged TCP crossed kernel WireGuard; foreign-key denied; peer-only routes; parent routes unchanged; namespaces removed")
}
