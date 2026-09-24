package lifecycle

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

func TestGroupRuntimeGateDeliveryAndRelease(t *testing.T) {
	for _, ending := range []string{"stop", "reconcile", "cancel"} {
		t.Run(ending, func(t *testing.T) {
			m, driver, digest, key, ca, caPEM := groupCredentialFixture(t)
			id := m.instanceID(digest, "group")
			if op := commitPrepared(t, m, digest, "unsigned", "prepare", 1); op.State != "failed" {
				t.Fatal(op)
			}
			if driver.starts != 0 || len(driver.hooks) != 0 || m.Snapshot().Instances[id].State != "prepared" {
				t.Fatal("unsigned start consumed preparation or executed code")
			}
			e, err := m.GroupPeer(id, digest, 1, "", "", false)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = m.GroupPeer(id, digest, 1, signGroupEnrollment(t, e, key, ca), caPEM, true); err != nil {
				t.Fatal(err)
			}
			in := m.ledger.Instances[id]
			binding := m.groupRuntimeBinding(in, 2)
			runtimePath, err := groupcredentials.RuntimePath(m.groupRuntimeRoot(), binding)
			if err != nil {
				t.Fatal(err)
			}
			if ending == "cancel" {
				// Simulate a crash halfway through export, before start commits its generation.
				if err = os.MkdirAll(runtimePath, 0700); err != nil {
					t.Fatal(err)
				}
				if err = os.WriteFile(filepath.Join(runtimePath, "key.pem"), []byte("partial"), 0600); err != nil {
					t.Fatal(err)
				}
				if op := commitPrepared(t, m, digest, "partial-start", "prepare", 1); op.State != "failed" {
					t.Fatal("repaired partial launch", op)
				}
				if op := submit(t, m, digest, "cancel", "stop", "group", 1); op.State != "succeeded" {
					t.Fatal(op)
				}
			} else {
				if op := commitPrepared(t, m, digest, "signed", "prepare", 1); op.State != "succeeded" {
					t.Fatal(op)
				}
				in = m.ledger.Instances[id]
				if in.StartPreparationID != "" || in.Generation != 2 || driver.starts != 1 {
					t.Fatal("unexpected start", in)
				}
				c := m.boundComponent(m.ledger.Installations[digest].Definition.Components[0], in)
				if c.Env["PANTHEON_GROUP_CREDENTIALS"] != runtimePath || c.Env["PANTHEON_MODEL_CREDENTIALS"] != "" {
					t.Fatal("credential isolation failed")
				}
				c.Runtime = "container"
				c = m.boundComponent(c, in)
				if c.Env["PANTHEON_GROUP_CREDENTIALS"] != groupPeerContainerPath || c.groupPeerDir != runtimePath {
					t.Fatal("container binding")
				}
				c.GroupPeer = false
				c.Env["PANTHEON_GROUP_CREDENTIALS"] = "injected"
				if other := m.boundComponent(c, in); other.Env["PANTHEON_GROUP_CREDENTIALS"] != "" {
					t.Fatal("unmarked component received credentials")
				}
				public, _ := json.Marshal(m.Snapshot())
				if strings.Contains(string(public), runtimePath) || strings.Contains(string(public), "PRIVATE KEY") {
					t.Fatal("private runtime leaked into ledger")
				}
				if ending == "stop" {
					driver.blocked = true
					if op := submit(t, m, digest, "blocked-stop", "stop", "group", 2); op.State == "succeeded" {
						t.Fatal("blocked stop succeeded")
					}
					if err = groupcredentials.CheckRuntime(runtimePath); err != nil {
						t.Fatal("live process lost bundle", err)
					}
					driver.blocked = false
					if op := submit(t, m, digest, "stop", "stop", "group", 2); op.State != "succeeded" {
						t.Fatal(op)
					}
				} else {
					driver.mu.Lock()
					for resource := range driver.alive {
						driver.alive[resource] = false
					}
					driver.mu.Unlock()
					if op := submit(t, m, digest, "reconcile", "reconcile", "group", 2); op.State != "succeeded" {
						t.Fatal(op)
					}
				}
			}
			if _, err = os.Stat(runtimePath); !os.IsNotExist(err) {
				t.Fatal("bundle survived release", err)
			}
			if len(m.Snapshot().Instances[id].Reservations) != 0 {
				t.Fatal("budget survived release")
			}
			data, err := os.ReadFile(filepath.Join(m.root, "ledger.json"))
			if err != nil {
				t.Fatal(err)
			}
			var ledger Ledger
			if err = json.Unmarshal(data, &ledger); err != nil || ledger.Protocol != 3 {
				t.Fatal("missing old Runner fence", err)
			}
		})
	}
}

func TestGroupPeerManifestAndDriverRejectCallerPaths(t *testing.T) {
	d := definition()
	d.AppID = "model-service"
	d.Components[0].GroupPeer = true
	d.Components[0].Resources = &ResourceRequest{MemoryBytes: 1 << 30}
	if err := d.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"other-app", "duplicate", "unbudgeted", "env", "container-user", "parent-mount", "child-mount", "exact-mount"} {
		t.Run(kind, func(t *testing.T) {
			v := clone(d)
			switch kind {
			case "other-app":
				v.AppID = "example"
			case "duplicate":
				c := v.Components[0]
				c.Name = "second"
				v.Components = append(v.Components, c)
			case "unbudgeted":
				v.Components[0].Resources = nil
			case "env":
				v.Components[0].Env = map[string]string{"PANTHEON_GROUP_CREDENTIALS": "/tmp/forged"}
			default:
				v.Dependencies.ContainerEngine = &EngineDependency{Provider: "docker", Provision: "never"}
				v.Components[0].Runtime = "container"
				v.Components[0].RunAsOwner = true
				v.Components[0].Image = "test@sha256:" + strings.Repeat("a", 64)
				switch kind {
				case "container-user":
					v.Components[0].RunAsOwner = false
				case "parent-mount":
					v.Components[0].Mounts = map[string]string{"data": "/run/pantheon"}
				case "child-mount":
					v.Components[0].ReadOnlyMounts = map[string]string{"package": groupPeerContainerPath + "/child"}
				case "exact-mount":
					v.Components[0].Mounts = map[string]string{"data": groupPeerContainerPath}
				}
			}
			if err := v.Validate(); err == nil {
				t.Fatal("invalid credential manifest accepted")
			}
		})
	}
	if _, err := (NativeDriver{}).Start(context.Background(), d.Components[0], Paths{}, "never-launch"); err == nil {
		t.Fatal("driver allowed an unbound credential consumer")
	}
	var c Component
	if err := StrictDecode([]byte(`{"name":"engine","groupPeerDir":"/tmp/forged"}`), &c); err == nil {
		t.Fatal("decoded private path")
	}
}

func TestGroupRuntimeSurvivesRunnerRestartUntilOriginalProcessStops(t *testing.T) {
	m, driver, digest, key, ca, caPEM := groupCredentialFixture(t)
	id := m.instanceID(digest, "group")
	e, err := m.GroupPeer(id, digest, 1, "", "", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = m.GroupPeer(id, digest, 1, signGroupEnrollment(t, e, key, ca), caPEM, true); err != nil {
		t.Fatal(err)
	}
	if op := commitPrepared(t, m, digest, "start", "prepare", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	bundle, err := groupcredentials.RuntimePath(m.groupRuntimeRoot(), m.groupRuntimeBinding(m.ledger.Instances[id], 2))
	if err != nil {
		t.Fatal(err)
	}
	if err = m.Close(); err != nil {
		t.Fatal(err)
	}
	if err = groupcredentials.CheckRuntime(bundle); err != nil {
		t.Fatal("Runner shutdown removed live credentials", err)
	}
	again, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()
	if op := submit(t, again, digest, "reconcile-live", "reconcile", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if err = groupcredentials.CheckRuntime(bundle); err != nil {
		t.Fatal("live reconciliation removed credentials", err)
	}
	if op := submit(t, again, digest, "stop", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if _, err = os.Stat(bundle); !os.IsNotExist(err) {
		t.Fatal("restart lost cleanup binding", err)
	}
	if _, err = again.FenceStart(Request{Protocol: 1, OperationID: "delayed-start", Action: "start", Digest: digest, Scope: "group", Generation: 1, StartPreparationID: "prepare"}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	if err != nil {
		t.Fatal(err)
	}
	var disk Ledger
	if err = json.Unmarshal(data, &disk); err != nil || disk.Protocol != 3 {
		t.Fatal("fencing downgraded credential ledger", err)
	}
}
