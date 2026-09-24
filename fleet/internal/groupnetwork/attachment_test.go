package groupnetwork

import (
	"context"
	"errors"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

type simulatedAttachment struct {
	kernel   *simulatedKernel
	identity string
	lost     bool
	foreign  bool
}

func (a *simulatedAttachment) Identity() string { return a.identity }
func (a *simulatedAttachment) Attach(ctx context.Context, name string) error {
	state, _, err := a.kernel.store.Network(a.kernel.manifest)
	if err != nil || state.Stage != "creating" || state.AttachedIdentity != a.identity {
		a.kernel.t.Fatal("attach without original persisted container identity", err)
	}
	a.kernel.namespace = name
	a.kernel.view = Observation{Identity: a.identity, Links: []Link{{Name: "lo"}}, PIDs: []int{41}}
	if a.foreign {
		a.kernel.view.Links = append(a.kernel.view.Links, Link{Name: "eth0"})
	}
	if a.lost {
		return errors.New("lost attach acknowledgement")
	}
	return nil
}

func TestAttachedNamespaceKeepsOriginalContainerOwnership(t *testing.T) {
	for _, lost := range []bool{false, true} {
		t.Run(map[bool]string{false: "ready", true: "lost-reply"}[lost], func(t *testing.T) {
			s, m := durableFixture(t)
			k := &simulatedKernel{t: t, store: s, manifest: m}
			a := &simulatedAttachment{kernel: k, identity: "4:800", lost: lost}
			n, err := CreateDurableAttached(context.Background(), s, m, k, a)
			if n == nil || (err != nil) != lost {
				t.Fatal(n, err)
			}
			state, _, _ := s.Network(m)
			if state.AttachedIdentity != a.identity {
				t.Fatal("original attachment lost")
			}
			calls := k.calls
			if _, err = CreateDurableAttached(context.Background(), s, m, k, a); err == nil || k.calls != calls {
				t.Fatal("replayed container attachment")
			}
			// Restarted owner cannot drop the namespace while its container lives.
			restarted := groupcredentials.OverlayStore{Root: s.Root, Owner: s.Owner, Node: s.Node}
			if err = StopDurable(context.Background(), restarted, m, k, noWorkloads); err == nil {
				t.Fatal("deleted a live container namespace")
			}
			k.view.PIDs = nil // Original container was now reaped by lifecycle owner.
			k.view.Identity = "4:801"
			if err = StopDurable(context.Background(), restarted, m, k, noWorkloads); err == nil {
				t.Fatal("adopted replacement namespace after lost reply")
			}
			k.view.Identity = a.identity
			if err = StopDurable(context.Background(), restarted, m, k, noWorkloads); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestAttachedNamespaceRejectsBridgeAndMutableIdentity(t *testing.T) {
	s, m := durableFixture(t)
	k := &simulatedKernel{t: t, store: s, manifest: m}
	a := &simulatedAttachment{kernel: k, identity: "4:800", foreign: true}
	if _, err := CreateDurableAttached(context.Background(), s, m, k, a); err == nil || k.calls != 0 {
		t.Fatal("configured a container with an external interface")
	}
	state, _, err := s.Network(m)
	if err != nil {
		t.Fatal(err)
	}
	replacement := *state
	replacement.AttachedIdentity = "4:801"
	replacement.NamespaceIdentity = "4:801"
	replacement.Stage = "namespace"
	if err = s.AdvanceNetwork(m, *state, replacement); err == nil {
		t.Fatal("rewrote original target")
	}
}
