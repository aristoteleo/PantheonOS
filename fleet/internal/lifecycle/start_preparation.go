package lifecycle

import (
	"fmt"
	"reflect"
	"time"
)

// prepareStart holds the installed manifest's complete budget without running
// dependencies, hooks or processes. A multi-node coordinator can prepare every
// member before committing starts. This is node-local atomic admission, not a
// distributed transaction. Lost acknowledgements must be inspected by operation
// ID; a deadline never releases a committed or possibly running generation.
// The caller holds serial, as for every other lifecycle operation.
func (m *Manager) prepareStart(op *Operation, installation *Installation, previous *Instance) error {
	if installation == nil || installation.State != "installed" {
		return fmt.Errorf("prepare_start requires an installed artifact")
	}
	if previous != nil && (previous.State != "stopped" || len(previous.Resources) != 0 || len(previous.Reservations) != 0) {
		return fmt.Errorf("prepare_start requires a stopped instance with no resources")
	}
	def := installation.Definition
	if err := m.eligibility(def); err != nil {
		return err
	}
	var requests []ResourceRequest
	reservations := map[string]ResourceReservation{}
	for _, component := range def.Components {
		if component.Resources != nil {
			requests = append(requests, *component.Resources)
			reservations["component-"+component.Name] = ResourceReservation{
				Request: clone(*component.Resources), CreatedAt: time.Now().UTC(),
			}
		}
	}
	if len(requests) == 0 {
		return fmt.Errorf("prepare_start requires manifest resource budgets")
	}
	inv := m.sampleResources()
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return fmt.Errorf("Runner is shutting down")
	}
	if err := m.admitLocked(inv, requests); err != nil {
		return err
	}
	req := op.Request
	id := m.instanceID(req.Digest, req.Scope)
	in := &Instance{ID: id, AppID: def.AppID, Version: def.Version,
		Digest: req.Digest, Scope: req.Scope, Generation: req.Generation + 1,
		State: "prepared", StartPreparationID: req.OperationID,
		Resources: []Resource{}, Reservations: reservations}
	if previous != nil {
		in.AutoStop, in.KeepAlive, in.DataSource = previous.AutoStop, previous.KeepAlive, previous.DataSource
	}
	oldProtocol := m.ledger.Protocol
	// Older Runners would reconcile a process-free hold as dead. Fence downgrade
	// in the persisted ledger before allowing any hold. The wire protocol stays 1.
	if m.ledger.Protocol < 2 {
		m.ledger.Protocol = 2
	}
	m.ledger.Instances[id] = in
	if err := m.persist(); err != nil {
		m.ledger.Protocol = oldProtocol
		if previous == nil {
			delete(m.ledger.Instances, id)
		} else {
			m.ledger.Instances[id] = previous
		}
		return err
	}
	return nil
}

func checkPreparedReservations(in *Instance, def Definition) error {
	if in.StartPreparationID == "" || len(in.Resources) != 0 || in.ReadyGeneration != 0 {
		return fmt.Errorf("invalid prepared instance; inspect its lifecycle state")
	}
	count := 0
	for _, component := range def.Components {
		if component.Resources == nil {
			continue
		}
		count++
		lease, ok := in.Reservations["component-"+component.Name]
		if !ok || !reflect.DeepEqual(lease.Request, *component.Resources) {
			return fmt.Errorf("prepared component budget differs from its installed artifact")
		}
	}
	if count == 0 || count != len(in.Reservations) {
		return fmt.Errorf("invalid prepared resource set")
	}
	return nil
}

// Only an unconsumed preparation can be cancelled without a process stop.
// Generation CAS in perform serializes cancellation against a committed start.
func (m *Manager) cancelPreparedStart(in *Instance) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if in.State != "prepared" || len(in.Resources) != 0 {
		return fmt.Errorf("start has already consumed its preparation")
	}
	if err := m.clearGroupPeerRuntime(in, in.Generation+1); err != nil {
		return err
	}
	before := clone(*in)
	in.State, in.Error, in.StartPreparationID = "stopped", "", ""
	in.Generation++
	in.Reservations = nil
	if err := m.persist(); err != nil {
		*in = before
		return err
	}
	return nil
}
