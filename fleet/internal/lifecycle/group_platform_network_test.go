package lifecycle

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

var platformDetected = node.PlatformNetwork{Mode: "modal-i6pn", Address: "fdaa::5", Interface: "eth0", Scope: "ap-1/us-east"}

func platformCaps() proto.Capability {
	return proto.Capability{Caps: []string{"proc", node.PlatformNetworkCap}, Runtimes: platformDetected.Runtimes()}
}
func platformDefinition(d *Definition) {
	d.Requires.Caps = []string{node.PlatformNetworkCap}
	d.Components[0].GroupPlatformNetwork = "modal-i6pn"
}

func TestGroupPlatformNetworkValidation(t *testing.T) {
	def := definition()
	def.AppID = "model-service"
	def.Components[0].GroupPeer = true
	def.Components[0].Resources = &ResourceRequest{MemoryBytes: 1 << 30}
	platformDefinition(&def)
	if err := def.Validate(); err != nil {
		t.Fatal(err)
	}
	var decoded Component
	if err := StrictDecode([]byte(`{"name":"peer","runtime":"process","group_platform_network":"modal-i6pn"}`), &decoded); err != nil || decoded.GroupPlatformNetwork != "modal-i6pn" {
		t.Fatal("field not decoded", err)
	}
	cases := []struct {
		name   string
		mutate func(*Definition)
		ok     bool
	}{
		{name: "unset", mutate: func(d *Definition) { d.Components[0].GroupPlatformNetwork = ""; d.Requires.Caps = nil }, ok: true},
		{name: "unknown mode", mutate: func(d *Definition) { d.Components[0].GroupPlatformNetwork = "wireguard" }},
		{name: "not group peer", mutate: func(d *Definition) { d.Components[0].GroupPeer = false }},
		{name: "missing capability", mutate: func(d *Definition) { d.Requires.Caps = []string{"model-group-private-network"} }},
		{name: "with group network", mutate: func(d *Definition) {
			d.Components[0].GroupNetwork = true
			d.Requires.Caps = append(d.Requires.Caps, "model-group-private-network")
		}},
		{name: "container", mutate: func(d *Definition) {
			d.Dependencies.ContainerEngine = &EngineDependency{Provider: "docker", Provision: "never"}
			c := &d.Components[0]
			c.Runtime, c.RunAsOwner, c.Image = "container", true, "python@sha256:"+strings.Repeat("a", 64)
		}},
		{name: "package env", mutate: func(d *Definition) {
			d.Components[0].Env = map[string]string{"PANTHEON_GROUP_PLATFORM_ADDRESS": "fdaa::6"}
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			v := clone(def)
			tc.mutate(&v)
			if err := v.Validate(); (err == nil) != tc.ok {
				t.Fatal(err)
			}
		})
	}
}

func TestGroupPlatformNetworkInstallEligibility(t *testing.T) {
	injected := proto.Capability{Caps: []string{"proc", node.PlatformNetworkCap}} // no detected runtimes
	for name, caps := range map[string]proto.Capability{"missing": {Caps: []string{"proc"}}, "injected": injected, "detected": platformCaps()} {
		t.Run(name, func(t *testing.T) {
			m, err := Open(t.TempDir(), "owner", "node", caps, &fakeDriver{alive: map[string]bool{}})
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			def := definition()
			def.AppID = "model-service"
			def.Components[0].GroupPeer = true
			def.Components[0].Resources = &ResourceRequest{MemoryBytes: 1 << 30}
			platformDefinition(&def)
			archive, digest := bundle(t, def, nil)
			if _, err = m.Stage(digest, 0, archive); err != nil {
				t.Fatal(err)
			}
			op := submit(t, m, digest, "install", "install", "group", 0)
			if name != "detected" {
				if op.State != "failed" || !strings.Contains(op.Error, node.PlatformNetworkCap) {
					t.Fatal("installed without platform network", op)
				}
				return
			}
			if op.State != "succeeded" || m.ledger.Protocol != 5 {
				t.Fatal("install did not fence older Runners", op, m.ledger.Protocol)
			}
		})
	}
}

func TestGroupPlatformNetworkStart(t *testing.T) {
	m, driver, digest, key, ca, caPEM := groupCredentialFixtureWith(t, platformCaps(), platformDefinition)
	id := m.instanceID(digest, "group")
	e, err := m.GroupPeer(id, digest, 1, "", "", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = m.GroupPeer(id, digest, 1, signGroupEnrollment(t, e, key, ca), caPEM, true); err != nil {
		t.Fatal(err)
	}
	refusals := []struct {
		name   string
		detect func(string) (node.PlatformNetwork, error)
		other  bool
		off    bool
		want   string
	}{
		{name: "flag off", off: true, want: node.PlatformNetworkCap},
		{name: "detection fails", detect: func(string) (node.PlatformNetwork, error) { return node.PlatformNetwork{}, errors.New("not modal") }, want: "unavailable"},
		{name: "address changed", detect: func(string) (node.PlatformNetwork, error) {
			p := platformDetected
			p.Address = "fdaa::6"
			return p, nil
		}, want: "changed"},
		{name: "second peer", other: true, want: "another platform group peer"},
	}
	ok := func(string) (node.PlatformNetwork, error) { return platformDetected, nil }
	for _, tc := range refusals {
		t.Run(tc.name, func(t *testing.T) {
			m.platformDetect = ok
			if tc.detect != nil {
				m.platformDetect = tc.detect
			}
			saved := m.platform
			if tc.off {
				m.platform = node.PlatformNetwork{}
			}
			if tc.other {
				m.mu.Lock()
				m.ledger.Instances["other"] = &Instance{ID: "other", Digest: digest, Scope: "other", State: "ready", Resources: []Resource{{ID: "live", Component: "backend", Runtime: "process"}}}
				m.mu.Unlock()
			}
			op := commitPrepared(t, m, digest, "refuse-"+strings.ReplaceAll(tc.name, " ", "-"), "prepare", 1)
			m.platform = saved
			m.mu.Lock()
			delete(m.ledger.Instances, "other")
			m.mu.Unlock()
			if op.State != "failed" || !strings.Contains(op.Error, tc.want) || driver.starts != 0 || m.Snapshot().Instances[id].State != "prepared" {
				t.Fatal("unsafe platform start", op)
			}
		})
	}
	m.platformDetect = ok
	if op := commitPrepared(t, m, digest, "start", "prepare", 1); op.State != "succeeded" || driver.starts != 1 {
		t.Fatal(op)
	}
	in := m.ledger.Instances[id]
	c := m.boundComponent(m.ledger.Installations[digest].Definition.Components[0], in)
	if c.Env["PANTHEON_GROUP_PLATFORM_ADDRESS"] != "fdaa::5" || c.Env["PANTHEON_GROUP_PLATFORM_INTERFACE"] != "eth0" || c.Env["PANTHEON_GROUP_CREDENTIALS"] == "" {
		t.Fatal("platform network not bound", c.Env)
	}
	c.GroupPlatformNetwork = ""
	c.Env["PANTHEON_GROUP_PLATFORM_ADDRESS"] = "fdaa::6"
	if other := m.boundComponent(c, in); other.Env["PANTHEON_GROUP_PLATFORM_ADDRESS"] != "" {
		t.Fatal("unmarked component received platform network")
	}
	if op := submit(t, m, digest, "stop", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	raw, err := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	var ledger Ledger
	if err != nil || json.Unmarshal(raw, &ledger) != nil || ledger.Protocol != 5 {
		t.Fatal("missing old Runner fence", err, ledger.Protocol)
	}
	if err = m.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal("current Runner refused its own ledger", err)
	}
	reopened.Close()
	ledger.Protocol = 6
	raw, _ = json.Marshal(ledger)
	if err = os.WriteFile(filepath.Join(m.root, "ledger.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = Open(m.root, m.owner, m.node, m.caps, driver); err == nil {
		t.Fatal("opened a newer ledger")
	}
}

func TestNativeDriverRefusesUnboundPlatformNetwork(t *testing.T) {
	c := definition().Components[0]
	c.GroupPlatformNetwork = "modal-i6pn"
	if _, err := (NativeDriver{}).Start(context.Background(), c, Paths{}, "id"); err == nil || !strings.Contains(err.Error(), "not bound") {
		t.Fatal(err)
	}
}
