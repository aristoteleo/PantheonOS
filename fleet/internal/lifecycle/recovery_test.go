package lifecycle

import (
	"reflect"
	"testing"
)

func TestRecoverCommittedGenerationWithoutReplay(t *testing.T) {
	m, driver, digest := setup(t)
	if op := submit(t, m, digest, "start", "start", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	id := m.instanceID(digest, "app")
	before := m.Snapshot().Instances[id]
	m.Close()
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	for _, action := range []string{"reconcile", "recover", "recover"} {
		op := submit(t, reopened, digest, action+"-"+reopened.Snapshot().Instances[id].State, action, "app", before.Generation)
		if op.State != "succeeded" {
			t.Fatal(op)
		}
	}
	after := reopened.Snapshot().Instances[id]
	if after.State != "ready" || after.Generation != before.Generation || !reflect.DeepEqual(after.Resources, before.Resources) || driver.starts != 1 || len(driver.hooks) != 0 {
		t.Fatal("recovery restarted/replayed or changed identity", after, driver)
	}
}

func TestRecoverRefusesUncertainOrUnhealthyResources(t *testing.T) {
	for _, reason := range []string{"interrupted-start", "interrupted-stop", "partial", "identity", "probe", "reservation", "generation"} {
		t.Run(reason, func(t *testing.T) {
			m, driver, digest := setup(t)
			if op := submit(t, m, digest, "start", "start", "app", 0); op.State != "succeeded" {
				t.Fatal(op)
			}
			id := m.instanceID(digest, "app")
			if reason == "interrupted-stop" {
				driver.blocked = true
				if op := submit(t, m, digest, "stop", "stop", "app", 1); op.State != "failed" {
					t.Fatal(op)
				}
			}
			_ = m.update(func() {
				in := m.ledger.Instances[id]
				in.State = "unknown"
				switch reason {
				case "interrupted-start":
					in.ReadyGeneration = 0
				case "partial":
					m.ledger.Installations[digest].Definition.Components = append(m.ledger.Installations[digest].Definition.Components, Component{Name: "missing"})
				case "identity":
					in.Resources[0].Component = "unrelated"
				case "probe":
					driver.failProbe = true
				case "reservation":
					m.ledger.Installations[digest].Definition.Components[0].Resources = &ResourceRequest{MemoryBytes: 1 << 30}
				}
			})
			generation := uint64(1)
			if reason == "generation" {
				generation = 2
			}
			if op := submit(t, m, digest, "recover", "recover", "app", generation); op.State != "failed" {
				t.Fatal("unsafe recovery", op)
			}
			if m.Snapshot().Instances[id].State == "ready" || driver.starts != 1 {
				t.Fatal("uncertain process was published or restarted")
			}
		})
	}
}

func TestRecoverRetainsLeasesAndReleasesOnlyAfterConfirmedExit(t *testing.T) {
	m, driver, in := readyResourceInstance(t)
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "loaded", ResourceRequest{MemoryBytes: 5 << 30}); err != nil {
		t.Fatal(err)
	}
	before := m.Snapshot().Instances[in.ID]
	m.Close()
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if op := submit(t, reopened, in.Digest, "recover-alive", "recover", "app", in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if !reflect.DeepEqual(reopened.Snapshot().Instances[in.ID].Reservations, before.Reservations) {
		t.Fatal("live leases changed")
	}
	driver.mu.Lock()
	for id := range driver.alive {
		driver.alive[id] = false
	}
	driver.mu.Unlock()
	if op := submit(t, reopened, in.Digest, "recover-dead", "recover", "app", in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	after := reopened.Snapshot().Instances[in.ID]
	if after.State != "stopped" || after.Generation != in.Generation+1 || len(after.Reservations) != 0 || after.ReadyGeneration != 0 {
		t.Fatal(after)
	}
	if op := submit(t, reopened, in.Digest, "recover-stopped", "recover", "app", after.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if reopened.Snapshot().Instances[in.ID].Generation != after.Generation {
		t.Fatal("repeated reconcile advanced an already stopped generation")
	}
}
