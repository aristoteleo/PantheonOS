package lifecycle

import (
	"context"
	"testing"
)

func TestConsumerIdentityRejectsStaleDeadAndStoppedInstances(t *testing.T) {
	m, driver, d := setup(t)
	submit(t, m, d, "start", "start", "app", 0)
	id := m.instanceID(d, "app")
	check := func(generation uint64, preparation string) error {
		_, err := m.Dispatch(context.Background(), Command{Protocol: 1, Method: "check_instance", Instance: id, Revision: d, Generation: generation, Preparation: preparation})
		return err
	}
	if err := check(1, ""); err != nil {
		t.Fatal(err)
	}
	if check(2, "") == nil || check(1, "invented-preparation") == nil {
		t.Fatal("stale identity allowed")
	}
	for _, state := range []string{"prepared", "unknown", "draining", "stop_blocked", "stopped", "starting"} {
		_ = m.update(func() { m.ledger.Instances[id].State = state })
		if check(1, "") == nil {
			t.Fatalf("consumer admitted in %s", state)
		}
	}
	_ = m.update(func() { m.ledger.Instances[id].State = "ready" })
	driver.mu.Lock()
	driver.alive[m.Snapshot().Instances[id].Resources[0].ID] = false
	driver.mu.Unlock()
	if check(1, "") == nil {
		t.Fatal("dead process with stale ready ledger admitted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if m.CheckInstance(ctx, id, d, 1, "") == nil {
		t.Fatal("canceled check succeeded")
	}
}

func TestConsumerIdentityPreparedGrantIsNotAnInvocation(t *testing.T) {
	m, _, _ := setup(t)
	putTestCredential(t, m)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "consumer")
	check := func(g uint64, p string) error { return m.CheckInstance(context.Background(), in.ID, in.Digest, g, p) }
	if err := check(in.Generation+1, in.StartPreparationID); err != nil {
		t.Fatal(err)
	}
	if check(in.Generation+1, "") == nil || check(in.Generation, in.StartPreparationID) == nil || check(in.Generation+1, "wrong") == nil {
		t.Fatal("unprepared/stale identity admitted")
	}
	configureForTest(t, m, in, cfg)
	if op := startConfigured(t, m, in, "run-consumer"); op.State != "succeeded" {
		t.Fatal(op)
	}
	if err := check(in.Generation+1, ""); err != nil {
		t.Fatal(err)
	}
	if check(in.Generation+1, in.StartPreparationID) == nil {
		t.Fatal("consumed preparation accepted")
	}
	submit(t, m, in.Digest, "stop", "stop", in.Scope, in.Generation+1)
	if check(in.Generation+1, "") == nil {
		t.Fatal("stopped consumer admitted")
	}
}

type checkRaceDriver struct {
	*fakeDriver
	started, release chan struct{}
}

func (d *checkRaceDriver) Alive(ctx context.Context, r Resource) (bool, error) {
	close(d.started)
	<-d.release
	return true, nil
}
func TestConsumerIdentityRechecksStateAfterLivenessProbe(t *testing.T) {
	m, driver, d := setup(t)
	submit(t, m, d, "start", "start", "app", 0)
	racer := &checkRaceDriver{driver, make(chan struct{}), make(chan struct{})}
	m.driver = racer
	id := m.instanceID(d, "app")
	done := make(chan error, 1)
	go func() { done <- m.CheckInstance(context.Background(), id, d, 1, "") }()
	<-racer.started
	// Proves the driver probe does not hold the Manager lock.
	_ = m.update(func() { m.ledger.Instances[id].State = "draining" })
	close(racer.release)
	if <-done == nil {
		t.Fatal("state changed during check but was admitted")
	}
}

func TestConsumerStartingRequiresLiveGenerationOwnedResource(t *testing.T) {
	m, driver, d := setup(t)
	submit(t, m, d, "start", "start", "app", 0)
	id := m.instanceID(d, "app")
	_ = m.update(func() { in := m.ledger.Instances[id]; in.State = "starting"; in.ReadyGeneration = 0 })
	if err := m.CheckInstance(context.Background(), id, d, 1, ""); err != nil {
		t.Fatal("live starting consumer denied", err)
	}
	driver.mu.Lock()
	driver.alive[m.Snapshot().Instances[id].Resources[0].ID] = false
	driver.mu.Unlock()
	if err := m.CheckInstance(context.Background(), id, d, 1, ""); err == nil {
		t.Fatal("dead starting consumer admitted")
	}
	_ = m.update(func() { m.ledger.Instances[id].Resources = nil })
	if err := m.CheckInstance(context.Background(), id, d, 1, ""); err == nil {
		t.Fatal("resource reservation treated as live consumer")
	}
}
