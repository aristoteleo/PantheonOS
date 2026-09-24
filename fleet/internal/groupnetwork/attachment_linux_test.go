//go:build linux

package groupnetwork

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"golang.org/x/sys/unix"
)

// Only scripts/verify-group-containers.py supplies these owned disposable PIDs.
// This runs in its own network/mount namespace, with host PID visibility solely
// to open namespace handles. Neither host routes nor a Docker socket are used.
func TestKernelAttachedContainers(t *testing.T) {
	if os.Getenv("PANTHEON_GROUP_NETWORK_TEST") != "isolated-container" || os.Getenv("PANTHEON_GROUP_TEST_PIDS") == "" {
		t.Skip("requires two disposable --network none containers")
	}
	if _, err := os.Stat("/.dockerenv"); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	command := func(args ...string) string {
		t.Helper()
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s: %v", args, out, err)
		}
		return string(out)
	}
	if h, err := OpenContainerNamespace(os.Getpid()); err == nil {
		h.Close()
		t.Fatal("accepted Fleet host namespace")
	}
	values := strings.Split(os.Getenv("PANTHEON_GROUP_TEST_PIDS"), ",")
	token := os.Getenv("PANTHEON_GROUP_TEST_TOKEN")
	if len(values) != 2 || len(token) != 32 {
		t.Fatal("missing original test ownership")
	}
	handles := make([]NamespaceHandle, 2)
	pidfds := make([]int, 2)
	for i, value := range values {
		pid, err := strconv.Atoi(value)
		if err != nil || pid <= 1 {
			t.Fatal("invalid test PID")
		}
		fd, err := unix.PidfdOpen(pid, 0)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = unix.Close(fd) })
		pidfds[i] = fd
		env, err := os.ReadFile(fmt.Sprintf("/proc/%d/environ", pid))
		if err != nil || !strings.Contains(string(env), "PANTHEON_GROUP_TEST_TOKEN="+token+"\x00") {
			t.Fatal("original container identity missing")
		}
		status := command("cat", fmt.Sprintf("/proc/%d/status", pid))
		if !strings.Contains(status, "CapEff:\t0000000000000000") {
			t.Fatal("test workload retained capabilities")
		}
		handles[i], err = OpenContainerNamespace(pid)
		if err != nil {
			t.Fatal(err)
		}
		handle := handles[i]
		t.Cleanup(func() { _ = handle.Close() })
	}
	if handles[0].Identity() == handles[1].Identity() {
		t.Fatal("containers share a namespace")
	}
	command("ip", "address", "add", "10.250.123.1/32", "dev", "lo")
	rootRoutes := command("ip", "-j", "route", "show", "table", "all")
	spec, _ := fixture(t)
	stores := make([]groupcredentials.OverlayStore, 2)
	manifests := make([]groupcredentials.Manifest, 2)
	for rank := range stores {
		m := spec.Manifest
		m.Rank = &rank
		manifests[rank] = m
		stores[rank] = groupcredentials.OverlayStore{Root: filepath.Join(t.TempDir(), "overlay"), Owner: m.Topology.Owner, Node: m.Topology.Members[rank].Node}
		status, err := stores[rank].Prepare(m, spec.Endpoints[rank].Address)
		if err != nil {
			t.Fatal(err)
		}
		spec.Endpoints[rank] = *status.Endpoint
	}
	networks := make([]*Network, 2)
	for rank, s := range stores {
		m := manifests[rank]
		if _, err := s.Pin(m.Topology.Group, m.Fingerprint(), spec.Endpoints); err != nil {
			t.Fatal(err)
		}
		n, err := CreateDurableAttached(ctx, s, m, nil, handles[rank])
		if n != nil {
			t.Cleanup(func() {
				// pidfd cannot signal a replacement process if Docker already reaped it.
				_ = unix.PidfdSendSignal(pidfds[rank], unix.SIGTERM, nil, 0)
				_ = handles[rank].Close()
				guard := func(c context.Context) error {
					deadline := time.Now().Add(5 * time.Second)
					for time.Now().Before(deadline) {
						state, _, e := s.Network(m)
						if e != nil {
							return e
						}
						o, e := (Commands{}).Observe(c, *state)
						if e != nil {
							return e
						}
						if len(o.PIDs) == 0 {
							return nil
						}
						time.Sleep(30 * time.Millisecond)
					}
					return fmt.Errorf("original test container did not exit")
				}
				cleanup, done := context.WithTimeout(context.Background(), 10*time.Second)
				defer done()
				if e := StopDurable(cleanup, s, m, nil, guard); e != nil {
					t.Error(e)
				}
			})
		}
		if err != nil {
			t.Fatal(err)
		}
		networks[rank] = n
		state, _, _ := s.Network(m)
		if state.NamespaceIdentity != handles[rank].Identity() || state.Stage != "ready" {
			t.Fatal("container namespace was replaced")
		}
		for _, address := range []string{"1.1.1.1", "10.250.123.1", "10.251.1.99"} {
			if exec.CommandContext(ctx, "ip", "-n", n.Namespace(), "route", "get", address).Run() == nil {
				t.Fatal("external route", address)
			}
		}
	}
	for rank, n := range networks {
		peer := 1 - rank
		code := fmt.Sprintf("import socket,time\ntime.sleep(.1)\ns=socket.create_connection(('10.251.1.%d',32123),5);s.sendall(b'container-overlay');assert s.recv(128)==b'container-overlay';s.close();print('encrypted peer OK')", peer+1)
		command("ip", "netns", "exec", n.Namespace(), "setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--bounding-set=-all", "--no-new-privs", "python3", "-c", code)
		handshakes := strings.Fields(command("ip", "netns", "exec", n.Namespace(), "wg", "show", "wg0", "latest-handshakes"))
		if len(handshakes) != 2 || handshakes[1] == "0" {
			t.Fatal("no authenticated WireGuard handshake")
		}
	}
	if rootRoutes != command("ip", "-j", "route", "show", "table", "all") {
		t.Fatal("host routing changed")
	}
	t.Log("two actual Docker namespaces retained; unprivileged workloads exchanged TCP through WireGuard; no external routes")
}
