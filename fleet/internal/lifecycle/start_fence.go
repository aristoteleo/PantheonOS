package lifecycle

import (
	"fmt"
	"reflect"
	"time"
)

func validateStartFence(req Request) error {
	if err := validateRequest(req); err != nil {
		return err
	}
	if req.IfIdle || (req.Action != "prepare_start" &&
		(req.Action != "start" || req.StartPreparationID == "" || req.Generation == 0)) {
		return fmt.Errorf("only an exact preparation or prepared start can be fenced")
	}
	return nil
}

// FenceStart atomically prevents an as-yet unsubmitted group start operation.
// It does not cancel any accepted operation. Submit and FenceStart arbitrate
// under the same durable ledger lock: either the original request is accepted
// once, or a persistent tombstone prevents every delayed copy from executing.
// The node's authenticated owner supplies the complete original request, not a
// new operation id or a replacement generation. No code or hooks are executed.
func (m *Manager) FenceStart(req Request) (Operation, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return Operation{}, fmt.Errorf("Runner is shutting down")
	}
	if err := validateStartFence(req); err != nil {
		return Operation{}, err
	}
	if op := m.ledger.Operations[req.OperationID]; op != nil {
		if !reflect.DeepEqual(op.Request, req) {
			return Operation{}, fmt.Errorf("operation_id already used for a different request")
		}
		return clone(*op), nil
	}
	now := time.Now().UTC()
	op := &Operation{Request: req, State: "cancelled", Steps: []Step{}, CreatedAt: now, UpdatedAt: now,
		Error: "Operation cancelled before submission; no process was started"}
	previousProtocol := m.ledger.Protocol
	// Never let an older Runner forget this negative acknowledgement on downgrade.
	if m.ledger.Protocol < 2 {
		m.ledger.Protocol = 2
	}
	m.ledger.Operations[req.OperationID] = op
	if err := m.persist(); err != nil {
		delete(m.ledger.Operations, req.OperationID)
		m.ledger.Protocol = previousProtocol
		return Operation{}, err
	}
	return clone(*op), nil
}
