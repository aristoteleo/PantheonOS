package groupcredentials

import (
	"strings"
	"testing"
)

func TestOverlayNetworkIntentIsImmutableAndForwardOnly(t *testing.T) {
	s, m, endpoints := overlayFixture(t)
	if _, err := s.Pin(m.Topology.Group, m.Fingerprint(), endpoints); err != nil {
		t.Fatal(err)
	}
	claimed := OverlayNetwork{Namespace: "pf-group-" + strings.Repeat("a", 24), HostInterface: "pfg" + strings.Repeat("a", 12), Stage: "claimed"}
	if err := s.ClaimNetwork(m, claimed); err != nil {
		t.Fatal(err)
	}
	if err := s.ClaimNetwork(m, claimed); err == nil {
		t.Fatal("duplicate claim granted permission to replay")
	}
	creating := claimed
	creating.Stage = "creating"
	creating.CreateRequested = true
	if err := s.AdvanceNetwork(m, claimed, creating); err != nil {
		t.Fatal(err)
	}
	if err := s.AdvanceNetwork(m, claimed, creating); err == nil {
		t.Fatal("stale CAS accepted")
	}
	ack := creating
	ack.Stage = "namespace"
	ack.NamespaceIdentity = "4:1234"
	if err := s.AdvanceNetwork(m, creating, ack); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*OverlayNetwork){
		func(n *OverlayNetwork) { n.Stage = "ready" },
		func(n *OverlayNetwork) { n.Stage = "creating"; n.NamespaceIdentity = "" },
		func(n *OverlayNetwork) { n.Stage = "linking"; n.NamespaceIdentity = "4:5678" },
		func(n *OverlayNetwork) {
			n.Stage = "linking"
			n.Namespace = "pf-group-" + strings.Repeat("b", 24)
			n.HostInterface = "pfg" + strings.Repeat("b", 12)
		},
		func(n *OverlayNetwork) { n.Stage = "linking"; n.CreateRequested = false },
	} {
		changed := ack
		mutate(&changed)
		if err := s.AdvanceNetwork(m, ack, changed); err == nil {
			t.Fatal("ownership or phase replaced")
		}
	}
	if _, err := s.Close(m.Topology.Group, m.Fingerprint()); err != nil {
		t.Fatal(err)
	}
	linking := ack
	linking.Stage = "linking"
	if err := s.AdvanceNetwork(m, ack, linking); err == nil {
		t.Fatal("closed enrollment allowed network setup")
	}
	closing := ack
	closing.Stage = "closing"
	closing.ClosingFrom = ack.Stage
	if err := s.AdvanceNetwork(m, ack, closing); err != nil {
		t.Fatal(err)
	}
	closed := closing
	closed.Stage = "closed"
	if err := s.AdvanceNetwork(m, closing, closed); err != nil {
		t.Fatal(err)
	}
	if err := s.AdvanceNetwork(m, closed, closing); err == nil {
		t.Fatal("closed resource revived")
	}
	if state, _, err := s.Network(m); err != nil || *state != closed {
		t.Fatal("closed ownership not retained", err)
	}
}

func TestOverlayNetworkNamesCannotSelectArbitraryHostResources(t *testing.T) {
	s, m, endpoints := overlayFixture(t)
	if _, err := s.Pin(m.Topology.Group, m.Fingerprint(), endpoints); err != nil {
		t.Fatal(err)
	}
	for _, n := range []OverlayNetwork{
		{Namespace: "../../host", HostInterface: "eth0", Stage: "claimed"},
		{Namespace: "pf-group-" + strings.Repeat("a", 24), HostInterface: "eth0", Stage: "claimed"},
		{Namespace: "pf-group-" + strings.Repeat("a", 24), HostInterface: "pfg" + strings.Repeat("b", 12), Stage: "claimed"},
		{Namespace: "pf-group-" + strings.Repeat("a", 24), HostInterface: "pfg" + strings.Repeat("a", 12), Stage: "ready", CreateRequested: true, NamespaceIdentity: "4:1234"},
	} {
		if err := s.ClaimNetwork(m, n); err == nil {
			t.Fatal("caller-selected resource admitted")
		}
	}
	if state, _, err := s.Network(m); err != nil || state != nil {
		t.Fatal("invalid claims changed durable state", err)
	}
}
