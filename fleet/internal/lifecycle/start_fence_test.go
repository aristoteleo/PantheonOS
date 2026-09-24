package lifecycle

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
)

func preparationRequest(digest, id string) Request {
	return Request{Protocol: 1, OperationID: id, Action: "prepare_start", Digest: digest, Scope: "group"}
}

func TestInstallFenceSurvivesRestartAndPreventsLateInstallation(t *testing.T) {
	m, driver, digest := setup(t) // Staged only; no installation has run.
	req := Request{Protocol: 1, OperationID: "late-install", Action: "install", Digest: digest, Scope: "group"}
	if op, err := m.FenceStart(req); err != nil || op.State != "cancelled" {
		t.Fatal(op, err)
	}
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	for range 2 {
		if op, err := reopened.Submit(req); err != nil || op.State != "cancelled" {
			t.Fatal("late installation escaped durable cancellation", op, err)
		}
	}
	state := reopened.Snapshot()
	if len(state.Installations) != 0 || len(state.Instances) != 0 || driver.starts != 0 || len(driver.hooks) != 0 {
		t.Fatal("cancelled installation performed work", state)
	}
	// A new identity cannot repurpose the tombstone into another request.
	req.Scope = "other"
	if _, err := reopened.Submit(req); err == nil {
		t.Fatal("changed installation reused cancelled operation identity")
	}
}

func TestInstallFencePreservesAcceptedInstallation(t *testing.T) {
	m, driver, digest := setup(t)
	req := Request{Protocol: 1, OperationID: "accepted-install", Action: "install", Digest: digest, Scope: "group"}
	m.serial.Lock()
	_, err := m.Submit(req)
	op, fenceErr := m.FenceStart(req)
	m.serial.Unlock()
	if err != nil || fenceErr != nil || op.State != "queued" {
		t.Fatal("fence changed accepted installation", op, err, fenceErr)
	}
	if completed := wait(t, m, req.OperationID); completed.State != "succeeded" {
		t.Fatal(completed)
	}
	if op, err := m.Submit(req); err != nil || op.State != "succeeded" {
		t.Fatal("duplicate installation did not return original result", op, err)
	}
	state := m.Snapshot()
	if state.Installations[digest].State != "installed" || len(state.Operations) != 1 || driver.starts != 0 {
		t.Fatal(state)
	}
}

func TestStartFencePersistsAndPreventsDelayedPreparation(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	req := preparationRequest(digest, "delayed-prepare")
	for range 2 {
		op, err := m.FenceStart(req)
		if err != nil || op.State != "cancelled" || !reflect.DeepEqual(op.Request, req) {
			t.Fatal(op, err)
		}
	}
	if len(m.Snapshot().Instances) != 0 || driver.starts != 0 || len(driver.hooks) != 0 {
		t.Fatal("fence executed code or reserved resources")
	}
	b, err := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	var disk Ledger
	if err != nil || json.Unmarshal(b, &disk) != nil || disk.Protocol != 2 || m.Snapshot().Protocol != 1 {
		t.Fatal("durable fence did not protect downgrade", err)
	}
	if err = m.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if op, err := reopened.Submit(req); err != nil || op.State != "cancelled" {
		t.Fatal("delayed request escaped persistent fence", op, err)
	}
	if len(reopened.Snapshot().Instances) != 0 || driver.starts != 0 {
		t.Fatal("delayed request allocated resources")
	}
	req.Generation++
	if _, err := reopened.Submit(req); err == nil {
		t.Fatal("changed request reused cancelled operation id")
	}
	if _, err := reopened.FenceStart(req); err == nil {
		t.Fatal("changed fence reused cancelled operation id")
	}
}

func TestStartFencePreservesPreparedBudgetUntilConfirmedStop(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	req := Request{Protocol: 1, OperationID: "late-start", Action: "start", Digest: digest,
		Scope: "group", Generation: 1, StartPreparationID: "prepare"}
	if op, err := m.FenceStart(req); err != nil || op.State != "cancelled" {
		t.Fatal(op, err)
	}
	in := m.Snapshot().Instances[m.instanceID(digest, "group")]
	if in.State != "prepared" || len(in.Reservations) != 1 || in.Generation != 1 {
		t.Fatal("fence modified the prepared generation", in)
	}
	if op := submit(t, m, digest, "cancel-prepare", "stop", "group", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op, err := m.Submit(req); err != nil || op.State != "cancelled" || driver.starts != 0 {
		t.Fatal("delayed start ran after cleanup", op, err, driver.starts)
	}
}

func TestStartFenceDoesNotCancelQueuedAcceptedStart(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	submit(t, m, digest, "prepare", "prepare_start", "group", 0)
	req := Request{Protocol: 1, OperationID: "accepted-start", Action: "start", Digest: digest,
		Scope: "group", Generation: 1, StartPreparationID: "prepare"}
	// Keep the accepted operation queued while fencing races its execution.
	m.serial.Lock()
	_, err := m.Submit(req)
	op, fenceErr := m.FenceStart(req)
	m.serial.Unlock()
	if err != nil || fenceErr != nil || op.State != "queued" {
		t.Fatal("fence cancelled previously accepted work", op, err, fenceErr)
	}
	if completed := wait(t, m, req.OperationID); completed.State != "succeeded" || driver.starts != 1 {
		t.Fatal(completed, driver.starts)
	}
	if stopped := submit(t, m, digest, "stop", "stop", "group", 2); stopped.State != "succeeded" {
		t.Fatal(stopped)
	}
}

func TestStartFenceAndSubmitRaceAcceptsAtMostOneOutcome(t *testing.T) {
	for range 12 {
		m, driver, digest := preparedFixture(t)
		submit(t, m, digest, "prepare", "prepare_start", "group", 0)
		req := Request{Protocol: 1, OperationID: "race-start", Action: "start", Digest: digest,
			Scope: "group", Generation: 1, StartPreparationID: "prepare"}
		var wg sync.WaitGroup
		gate := make(chan struct{})
		errors := make(chan error, 2)
		for _, method := range []func(Request) (Operation, error){m.Submit, m.FenceStart} {
			wg.Add(1)
			go func(call func(Request) (Operation, error)) {
				defer wg.Done()
				<-gate
				_, err := call(req)
				errors <- err
			}(method)
		}
		close(gate)
		wg.Wait()
		for range 2 {
			if err := <-errors; err != nil {
				t.Fatal(err)
			}
		}
		op := wait(t, m, req.OperationID)
		if op.State != "cancelled" && op.State != "succeeded" {
			t.Fatal(op)
		}
		expected := 0
		if op.State == "succeeded" {
			expected = 1
		}
		if driver.starts != expected {
			t.Fatal("race produced an unrecorded process", op, driver.starts)
		}
		if result, err := m.FenceStart(req); err != nil || result.State != op.State {
			t.Fatal("fence changed an accepted operation", result, err)
		}
		in := m.Snapshot().Instances[m.instanceID(digest, "group")]
		if stopped := submit(t, m, digest, "cleanup", "stop", "group", in.Generation); stopped.State != "succeeded" {
			t.Fatal(stopped)
		}
	}
}

func TestStartFencePersistenceFailureRollsBack(t *testing.T) {
	m, _, digest := preparedFixture(t)
	req := preparationRequest(digest, "failed-fence")
	blocked := filepath.Join(m.root, "ledger.tmp")
	if err := os.Mkdir(blocked, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := m.FenceStart(req); err == nil {
		t.Fatal("fence acknowledged failed durable write")
	}
	if m.Snapshot().Operations[req.OperationID] != nil || m.ledger.Protocol != 1 {
		t.Fatal("failed fence poisoned in-memory state")
	}
	if err := os.Remove(blocked); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Submit(req); err != nil || wait(t, m, req.OperationID).State != "succeeded" {
		t.Fatal("failed fence prevented valid original submission", err)
	}
}

func TestStartFenceRejectsNonGroupRequests(t *testing.T) {
	m, _, digest := preparedFixture(t)
	for _, req := range []Request{
		{Protocol: 1, OperationID: "plain", Action: "start", Digest: digest, Scope: "group"},
		{Protocol: 1, OperationID: "stop", Action: "stop", Digest: digest, Scope: "group"},
		{Protocol: 1, OperationID: "invalid", Action: "prepare_start", Digest: digest, Scope: "group", IfIdle: true},
		{Protocol: 1, OperationID: "invalid-source", Action: "prepare_start", Digest: digest, Scope: "group", DataSource: &DataSource{Digest: digest}},
		{Protocol: 1, OperationID: "install-generation", Action: "install", Digest: digest, Scope: "group", Generation: 1},
		{Protocol: 1, OperationID: "install-preparation", Action: "install", Digest: digest, Scope: "group", StartPreparationID: "prepare"},
	} {
		if _, err := m.FenceStart(req); err == nil {
			t.Fatal("accepted non-group fence", req)
		}
	}
}

func TestStartFenceRejectsCorruptCancellationOnReopen(t *testing.T) {
	m, driver, digest := preparedFixture(t)
	req := preparationRequest(digest, "cancelled")
	if _, err := m.FenceStart(req); err != nil {
		t.Fatal(err)
	}
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(m.root, "ledger.json")
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var disk Ledger
	if err := json.Unmarshal(b, &disk); err != nil {
		t.Fatal(err)
	}
	disk.Operations[req.OperationID].Request.Action = "stop"
	b, _ = json.Marshal(disk)
	if err := os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err == nil {
		_ = reopened.Close()
		t.Fatal("accepted a corrupted cancellation tombstone")
	}
}
