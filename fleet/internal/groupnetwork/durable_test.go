package groupnetwork

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

func durableFixture(t *testing.T) (groupcredentials.OverlayStore, groupcredentials.Manifest) {
	t.Helper()
	spec, _ := fixture(t)
	var local groupcredentials.OverlayStore
	for rank := range spec.Endpoints {
		m := spec.Manifest
		m.Rank = &rank
		s := groupcredentials.OverlayStore{Root: filepath.Join(t.TempDir(), "overlay"), Owner: m.Topology.Owner, Node: m.Topology.Members[rank].Node}
		status, err := s.Prepare(m, spec.Endpoints[rank].Address)
		if err != nil {
			t.Fatal(err)
		}
		spec.Endpoints[rank] = *status.Endpoint
		if rank == 0 {
			local = s
		}
	}
	if _, err := local.Pin(spec.Manifest.Topology.Group, spec.Manifest.Fingerprint(), spec.Endpoints); err != nil {
		t.Fatal(err)
	}
	return local, spec.Manifest
}

type simulatedKernel struct {
	t               *testing.T
	store           groupcredentials.OverlayStore
	manifest        groupcredentials.Manifest
	namespace       string
	view            Observation
	calls           int
	failAt          int
	afterEffect     bool
	inspectionError bool
}

func (k *simulatedKernel) Observe(ctx context.Context, n groupcredentials.OverlayNetwork) (Observation, error) {
	if k.inspectionError {
		return Observation{}, errors.New("inspection unavailable")
	}
	if n.Namespace != k.namespace {
		return Observation{}, nil
	}
	out := k.view
	out.Links = append([]Link(nil), out.Links...)
	if out.Host != nil {
		l := *out.Host
		out.Host = &l
	}
	return out, nil
}

func (k *simulatedKernel) Run(ctx context.Context, args []string, input []byte) error {
	k.t.Helper()
	k.calls++
	state, _, err := k.store.Network(k.manifest)
	if err != nil || state == nil {
		k.t.Fatal("effect without persisted original ownership", err)
	}
	joined := strings.Join(args, " ")
	want := "configuring"
	switch {
	case strings.HasPrefix(joined, "ip netns add "):
		want = "creating"
	case strings.HasPrefix(joined, "ip link add "):
		want = "linking"
	case strings.HasPrefix(joined, "ip link set ") && strings.Contains(joined, " alias "):
		want = "linking"
	case strings.HasPrefix(joined, "ip link set "):
		want = "moving"
	case strings.Contains(joined, " name wg0"):
		want = "naming"
	case strings.HasPrefix(joined, "ip link del "), strings.HasPrefix(joined, "ip netns del "):
		want = "closing"
	}
	if state.Stage != want {
		k.t.Fatalf("effect without stage intent: %s want %s", state.Stage, want)
	}
	if k.calls == k.failAt && !k.afterEffect {
		return errors.New("command unavailable")
	}
	switch want {
	case "creating":
		k.namespace = state.Namespace
		k.view.Identity = "4:1234"
		k.view.Links = []Link{{Name: "lo"}}
	case "linking":
		if args[2] == "add" {
			link := Link{Name: state.HostInterface}
			link.Info.Kind = "wireguard"
			k.view.Host = &link // Real virtual-link creation may ignore ifalias.
		} else {
			for i, a := range args {
				if a == "alias" {
					k.view.Host.Alias = args[i+1]
				}
			}
		}
	case "moving":
		k.view.Host.Alias = "" // Linux clears aliases across a namespace move.
		k.view.Links = append(k.view.Links, *k.view.Host)
		k.view.Host = nil
	case "naming":
		k.view.Links[len(k.view.Links)-1].Name = "wg0"
		for i, a := range args {
			if a == "alias" {
				k.view.Links[len(k.view.Links)-1].Alias = args[i+1]
			}
		}
	case "closing":
		if args[1] == "link" {
			k.view.Host = nil
		} else {
			k.view.Identity = ""
			k.view.Links = nil
		}
	}
	if k.calls == k.failAt {
		return errors.New("lost command acknowledgement")
	}
	return nil
}

func noWorkloads(context.Context) error { return nil }

func TestDurableOverlayCrashAtEveryKernelMutation(t *testing.T) {
	// Ten commands cover namespace, aliased interface, move, rename, key
	// configuration, address, lo, MTU and exact peer route. Both a lost reply
	// and an effect that never occurred must reconcile without replay.
	for command := 1; command <= 10; command++ {
		for _, after := range []bool{false, true} {
			t.Run(fmt.Sprintf("command_%d_applied_%t", command, after), func(t *testing.T) {
				s, m := durableFixture(t)
				k := &simulatedKernel{t: t, store: s, manifest: m, failAt: command, afterEffect: after}
				n, err := CreateDurable(context.Background(), s, m, k)
				if err == nil || n == nil {
					t.Fatal("uncertain creation did not retain its claim", err)
				}
				before := k.calls
				if _, err := CreateDurable(context.Background(), s, m, k); err == nil || k.calls != before {
					t.Fatal("retry repeated uncertain creation")
				}
				restarted := groupcredentials.OverlayStore{Root: s.Root, Owner: s.Owner, Node: s.Node}
				if err := StopDurable(context.Background(), restarted, m, k, noWorkloads); err != nil {
					t.Fatal(err)
				}
				state, _, err := restarted.Network(m)
				if err != nil || state.Stage != "closed" || k.view.Identity != "" || k.view.Host != nil {
					t.Fatal("resources not reconciled", err)
				}
				if _, err := CreateDurable(context.Background(), restarted, m, k); err == nil {
					t.Fatal("closed network restarted")
				}
			})
		}
	}
}

func TestDurableOverlayCleanupRequiresWorkloadAndOwnershipProof(t *testing.T) {
	for _, obstacle := range []string{"guard", "pid", "inode", "alias", "foreign_link", "inspection"} {
		t.Run(obstacle, func(t *testing.T) {
			s, m := durableFixture(t)
			k := &simulatedKernel{t: t, store: s, manifest: m}
			n, err := CreateDurable(context.Background(), s, m, k)
			if err != nil {
				t.Fatal(err)
			}
			if err := n.Close(context.Background()); err == nil {
				t.Fatal("in-memory close bypassed durable guard")
			}
			original, _ := k.Observe(context.Background(), n.journal.state)
			guard := noWorkloads
			switch obstacle {
			case "guard":
				guard = func(context.Context) error { return errors.New("container still owns namespace") }
			case "pid":
				k.view.PIDs = []int{234}
			case "inode":
				k.view.Identity = "4:9999"
			case "alias":
				k.view.Links[1].Alias = "foreign"
			case "foreign_link":
				k.view.Links = append(k.view.Links, Link{Name: "eth0"})
			case "inspection":
				k.inspectionError = true
			}
			calls := k.calls
			if err := StopDurable(context.Background(), s, m, k, guard); err == nil || k.calls != calls {
				t.Fatal("uncertain resources removed")
			}
			state, _, err := s.Network(m)
			if err != nil || state.Stage != "closing" {
				t.Fatal("cleanup claim lost", err)
			}
			k.view = original
			k.inspectionError = false
			if err := StopDurable(context.Background(), s, m, k, noWorkloads); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestDurableOverlayLostDeleteReplyDoesNotRecreateOrDoubleDelete(t *testing.T) {
	s, m := durableFixture(t)
	k := &simulatedKernel{t: t, store: s, manifest: m}
	if _, err := CreateDurable(context.Background(), s, m, k); err != nil {
		t.Fatal(err)
	}
	k.failAt = k.calls + 1
	k.afterEffect = true
	if err := StopDurable(context.Background(), s, m, k, noWorkloads); err == nil {
		t.Fatal("expected lost delete acknowledgement")
	}
	calls := k.calls
	restarted := groupcredentials.OverlayStore{Root: s.Root, Owner: s.Owner, Node: s.Node}
	if err := StopDurable(context.Background(), restarted, m, k, noWorkloads); err != nil || k.calls != calls {
		t.Fatal("repeated acknowledged-by-inspection deletion", err)
	}
}

func TestDurableOverlayLostFirstNamespaceObservation(t *testing.T) {
	s, m := durableFixture(t)
	k := &simulatedKernel{t: t, store: s, manifest: m, failAt: 1, afterEffect: true}
	if _, err := CreateDurable(context.Background(), s, m, k); err == nil {
		t.Fatal("expected interrupted creation")
	}
	state, _, err := s.Network(m)
	if err != nil || state.NamespaceIdentity != "" {
		t.Fatal("fixture already acknowledged inode", err)
	}
	if err := StopDurable(context.Background(), s, m, k, noWorkloads); err != nil {
		t.Fatal(err)
	}
	state, _, err = s.Network(m)
	if err != nil || state.NamespaceIdentity != "4:1234" || state.Stage != "closed" {
		t.Fatal("original inode not pinned before removal", err)
	}
}
