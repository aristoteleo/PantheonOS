package lifecycle

import (
	"context"
	"encoding/json"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func preparedFixture(t *testing.T) (*Manager, *fakeDriver, string) {
	t.Helper()
	m, driver, _ := setup(t)
	m.SetResourceSampler(resourceInventory)
	def := definition()
	def.Components[0].Resources = &ResourceRequest{MemoryBytes: 4 << 30}
	b, digest := bundle(t, def, nil)
	if _, err := m.Stage(digest, 0, b); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "install", "install", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	return m, driver, digest
}

func commitPrepared(t *testing.T, m *Manager, digest, id, preparation string, generation uint64) Operation {
	t.Helper()
	_, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: "start", Digest: digest,
		Scope: "group", Generation: generation, StartPreparationID: preparation})
	if err != nil {
		t.Fatal(err)
	}
	return wait(t, m, id)
}

func TestPreparedStartReservesBeforeAnyProcessAndConsumesExactlyOnce(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	key := m.instanceID(digest, "group")
	in := m.Snapshot().Instances[key]
	if in.State != "prepared" || in.Generation != 1 || driver.starts != 0 || len(driver.hooks) != 0 || len(in.Reservations) != 1 {
		t.Fatal("preparation executed code or lost its budget", in, driver)
	}
	// Snapshot/caller mutation cannot modify the durable budget.
	in.Reservations["component-backend"] = ResourceReservation{}
	if op := submit(t, m, digest, "competitor", "start", "other", 0); op.State != "failed" || !strings.Contains(op.Error, "memory") {
		t.Fatal("prepared budget was overbooked", op)
	}
	if op := submit(t, m, digest, "unqualified", "start", "group", 1); op.State != "failed" {
		t.Fatal("start bypassed preparation identity", op)
	}
	if op := commitPrepared(t, m, digest, "wrong", "other-preparation", 1); op.State != "failed" {
		t.Fatal("wrong preparation consumed", op)
	}
	if op := commitPrepared(t, m, digest, "commit", "prepare", 1); op.State != "succeeded" {
		t.Fatal("hold was double-counted at commit", op)
	}
	in = m.Snapshot().Instances[key]
	if in.State != "ready" || in.Generation != 2 || len(in.Reservations) != 1 || driver.starts != 1 || in.StartPreparationID != "" {
		t.Fatal(in, driver.starts)
	}
	if op := commitPrepared(t, m, digest, "commit", "prepare", 1); op.State != "succeeded" || driver.starts != 1 {
		t.Fatal("acknowledgement retry started twice", op)
	}
	if op := submit(t, m, digest, "stale-cancel", "stop", "group", 1); op.State != "failed" {
		t.Fatal("late preparation cancel stopped a running generation", op)
	}
	if op := submit(t, m, digest, "stop", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if len(m.Snapshot().Instances[key].Reservations) != 0 {
		t.Fatal("confirmed stop did not release budget")
	}
}

func TestPreparedStartSurvivesRestartAndExplicitCancelFencesDelayedStart(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	data, err := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	if err != nil {
		t.Fatal(err)
	}
	var disk Ledger
	if err := json.Unmarshal(data, &disk); err != nil || disk.Protocol != 2 || m.Snapshot().Protocol != 1 {
		t.Fatal("old Runner downgrade not fenced or wire protocol changed", err, disk.Protocol)
	}
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
	restarted.SetResourceSampler(resourceInventory)
	key := restarted.instanceID(digest, "group")
	restarted.observeOnce()
	if op := submit(t, restarted, digest, "reconcile", "reconcile", "group", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	in := restarted.Snapshot().Instances[key]
	if in.State != "prepared" || len(in.Reservations) != 1 || driver.starts != 0 {
		t.Fatal("restart/observation released a prepared hold", in)
	}
	if op := submit(t, restarted, digest, "cancel", "stop", "group", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	in = restarted.Snapshot().Instances[key]
	if in.State != "stopped" || in.Generation != 2 || len(in.Reservations) != 0 || len(driver.hooks) != 0 {
		t.Fatal("cancellation executed hooks or retained budget", in, driver.hooks)
	}
	if op := commitPrepared(t, restarted, digest, "late-start", "prepare", 1); op.State != "failed" || driver.starts != 0 {
		t.Fatal("cancelled preparation started", op)
	}
	if op := submit(t, restarted, digest, "prepare-next", "prepare_start", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := commitPrepared(t, restarted, digest, "old-id-new-generation", "prepare", 3); op.State != "failed" {
		t.Fatal("old preparation identity consumed a new hold", op)
	}
}

func TestPreparedStartFailureRetainsBudgetUntilActualStop(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	driver.failProbe = true
	if op := commitPrepared(t, m, digest, "commit", "prepare", 1); op.State != "failed" {
		t.Fatal(op)
	}
	in := m.Snapshot().Instances[m.instanceID(digest, "group")]
	if in.State != "failed" || in.Generation != 2 || len(in.Reservations) != 1 || len(in.Resources) != 1 {
		t.Fatal("uncertain startup resources lost", in)
	}
	if op := submit(t, m, digest, "late-cancel", "stop", "group", 1); op.State != "failed" || len(m.Snapshot().Instances[in.ID].Reservations) != 1 {
		t.Fatal("late cancel released possibly running process", op)
	}
	if op := submit(t, m, digest, "cleanup", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
}

func TestPreparedStartAndCancellationRaceDoesNotRunWithoutBudget(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	var wg sync.WaitGroup
	errors := make(chan error, 2)
	for _, req := range []Request{
		{Protocol: 1, OperationID: "start", Action: "start", Digest: digest, Scope: "group", Generation: 1, StartPreparationID: "prepare"},
		{Protocol: 1, OperationID: "cancel", Action: "stop", Digest: digest, Scope: "group", Generation: 1},
	} {
		wg.Add(1)
		go func(req Request) { defer wg.Done(); _, err := m.Submit(req); errors <- err }(req)
	}
	wg.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}
	start, cancel := wait(t, m, "start"), wait(t, m, "cancel")
	in := m.Snapshot().Instances[m.instanceID(digest, "group")]
	if start.State == "succeeded" {
		if cancel.State != "failed" || in.State != "ready" || len(in.Reservations) != 1 || driver.starts != 1 {
			t.Fatal(start, cancel, in)
		}
	} else if cancel.State != "succeeded" || in.State != "stopped" || len(in.Reservations) != 0 || driver.starts != 0 {
		t.Fatal(start, cancel, in)
	}
}

func TestPrepareStartRequiresInstallationAndDeclaredBudgets(t *testing.T) {
	m, driver, digest := setup(t)
	m.SetResourceSampler(resourceInventory)
	if op := submit(t, m, digest, "not-installed", "prepare_start", "group", 0); op.State != "failed" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "install", "install", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "no-budget", "prepare_start", "group", 0); op.State != "failed" {
		t.Fatal(op)
	}
	if driver.starts != 0 || len(m.Snapshot().Instances) != 0 {
		t.Fatal("invalid preparation changed instances")
	}
}

func TestTwoNodePreparationRejectsPartialCapacityWithoutStartingEither(t *testing.T) {
	first, firstDriver, firstDigest := preparedFixture(t)
	second, secondDriver, secondDigest := preparedFixture(t)
	second.SetResourceSampler(func() proto.ResourceInventory {
		inv := resourceInventory()
		free := uint64(1 << 30)
		inv.Memory.AvailableBytes = &free
		return inv
	})
	if op := submit(t, first, firstDigest, "group-prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, second, secondDigest, "group-prepare", "prepare_start", "group", 0); op.State != "failed" {
		t.Fatal(op)
	}
	if firstDriver.starts != 0 || secondDriver.starts != 0 {
		t.Fatal("partial group started before admission")
	}
	// The coordinator cancels only the acknowledged, unconsumed generation.
	if op := submit(t, first, firstDigest, "group-abort", "stop", "group", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if len(first.Snapshot().Instances[first.instanceID(firstDigest, "group")].Reservations) != 0 || len(second.Snapshot().Instances) != 0 {
		t.Fatal("partial admission leaked reservations")
	}
}

func TestPreparedStartControlsRealNativeProcess(t *testing.T) {
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("POSIX subprocess fixture")
	}
	m, err := Open(t.TempDir(), "owner", "node", proto.Capability{}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	m.SetResourceSampler(resourceInventory)
	def := Definition{Protocol: 1, AppID: "prepared-fixture", Version: "1", Components: []Component{{
		Name: "backend", Runtime: "process", Argv: []string{"sh", "-c", "exec sleep 60"},
		Resources: &ResourceRequest{MemoryBytes: 4 << 30},
		Readiness: Probe{Argv: []string{"sh", "-c", "exit 0"}, TimeoutSeconds: 2}, StopSeconds: 2,
	}}}
	b, digest := bundle(t, def, nil)
	if _, err := m.Stage(digest, 0, b); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "install", "install", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	key := m.instanceID(digest, "group")
	if len(m.Snapshot().Instances[key].Resources) != 0 {
		t.Fatal("preparation spawned a process")
	}
	// Stop owned resources even when a later assertion fails.
	defer func() {
		in := m.Snapshot().Instances[key]
		for _, r := range in.Resources {
			_ = m.driver.Stop(context.Background(), def.Components[0], r)
		}
	}()
	if op := commitPrepared(t, m, digest, "start", "prepare", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	in := m.Snapshot().Instances[key]
	if len(in.Resources) != 1 || len(in.Reservations) != 1 {
		t.Fatal(in)
	}
	r := in.Resources[0]
	if alive, err := m.driver.Alive(context.Background(), r); err != nil || !alive {
		t.Fatal("actual process not running", err)
	}
	if op := submit(t, m, digest, "stop", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if alive, err := m.driver.Alive(context.Background(), r); err != nil || alive {
		t.Fatal("actual process survived stop", err)
	}
	in = m.Snapshot().Instances[key]
	if len(in.Resources) != 0 || len(in.Reservations) != 0 {
		t.Fatal("stop retained owned resources", in)
	}
}

func TestPreparedStartPersistenceFailureDoesNotChangeAdmission(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	// Inject an actual filesystem write failure after installation. Invoke the
	// transaction under its normal serialization lock so Submit's earlier write
	// cannot consume the failure before the reservation transaction is exercised.
	blocked := filepath.Join(m.root, "ledger.tmp")
	if err := os.Mkdir(blocked, 0700); err != nil {
		t.Fatal(err)
	}
	m.serial.Lock()
	err := m.prepareStart(&Operation{Request: Request{OperationID: "failed-prepare", Digest: digest, Scope: "group"}}, m.ledger.Installations[digest], nil)
	m.serial.Unlock()
	if err == nil || len(m.Snapshot().Instances) != 0 || m.ledger.Protocol != 1 || driver.starts != 0 {
		t.Fatal("failed persistence left a hold", err)
	}
	if err := os.Remove(blocked); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	in := m.ledger.Instances[m.instanceID(digest, "group")]
	before := clone(*in)
	if err := os.Mkdir(blocked, 0700); err != nil {
		t.Fatal(err)
	}
	m.serial.Lock()
	err = m.cancelPreparedStart(in)
	m.serial.Unlock()
	if err == nil || !reflect.DeepEqual(*in, before) {
		t.Fatal("failed cancellation released the in-memory hold", err, in)
	}
	if err := os.Remove(blocked); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "cancel", "stop", "group", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
}

func TestPreparedStartRejectsCorruptedPersistedBudget(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(m.root, "ledger.json")
	data, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	var ledger Ledger
	if err := json.Unmarshal(data, &ledger); err != nil {
		t.Fatal(err)
	}
	ledger.Instances[m.instanceID(digest, "group")].Reservations = nil
	data, err = json.Marshal(ledger)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, data, 0600); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err == nil {
		reopened.Close()
		t.Fatal("corrupted preparation accepted")
	}
	if !strings.Contains(err.Error(), "invalid prepared") {
		t.Fatal(err)
	}
}
