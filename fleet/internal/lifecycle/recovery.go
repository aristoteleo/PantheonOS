package lifecycle

import (
	"context"
	"fmt"
	"reflect"
)

// recover reattaches a fully started generation without running hooks, starting
// processes or releasing reservations. A fresh readiness probe is required even
// when the ledger says ready. Dead resources are reconciled, never restarted by
// this action; the caller must explicitly start the resulting stopped generation.
func (m *Manager) recover(ctx context.Context, op *Operation, installation *Installation, in *Instance, p Paths) (err error) {
	defer func() {
		if err != nil && in != nil && in.State == "ready" {
			_ = m.update(func() { in.State = "degraded"; in.Error = err.Error() })
		}
	}()
	if installation == nil || installation.State != "installed" || in == nil {
		return fmt.Errorf("recovery requires an installed, existing instance")
	}
	if in.State == "stopped" && len(in.Resources) == 0 && len(in.Reservations) == 0 {
		return nil
	}
	def := installation.Definition
	if in.ID != m.instanceID(in.Digest, in.Scope) || in.AppID != def.AppID || in.Version != def.Version {
		return fmt.Errorf("recovery instance identity differs from its installation")
	}
	aliveCount := 0
	for _, r := range in.Resources {
		alive, err := m.driver.Alive(ctx, r)
		if err != nil {
			return fmt.Errorf("cannot verify recovered resource ownership: %w", err)
		}
		if alive {
			aliveCount++
		}
	}
	if aliveCount == 0 {
		return m.reconcile(ctx, op, in)
	}
	if in.ReadyGeneration != in.Generation || in.Generation == 0 {
		return fmt.Errorf("startup or stop outcome is uncertain; complete a safe stop in Fleet before starting again")
	}
	if aliveCount != len(def.Components) || len(in.Resources) != len(def.Components) {
		return fmt.Errorf("only part of the instance survived; complete a safe stop in Fleet")
	}
	for n, c := range def.Components {
		r := in.Resources[n]
		if r.Component != c.Name || r.Runtime != c.Runtime || r.ID != fmt.Sprintf("pa-%s-%d-%s", in.ID, in.Generation, c.Name) {
			return fmt.Errorf("recovered component identity differs from the recorded generation")
		}
		if c.Resources != nil {
			lease, ok := in.Reservations["component-"+c.Name]
			if !ok || !reflect.DeepEqual(lease.Request, *c.Resources) {
				return fmt.Errorf("recovered component resource reservation is missing or changed")
			}
		}
	}
	// Rebind original private listeners only after ownership, committed readiness
	// and reservations are checked. Failed recovery releases new listeners while
	// preserving the original model process, network and resource reservations.
	var restored []string
	if restorer, ok := m.driver.(interface {
		RecoverGroupIngress(context.Context, Component, Paths, Resource) (bool, error)
		ReleaseGroupIngress(string)
	}); ok {
		defer func() {
			if err != nil {
				for _, id := range restored {
					restorer.ReleaseGroupIngress(id)
				}
			}
		}()
		for n, c := range def.Components {
			if !c.GroupNetwork {
				continue
			}
			created, e := restorer.RecoverGroupIngress(ctx, m.boundComponent(c, in), p, in.Resources[n])
			if e != nil {
				return e
			}
			if created {
				restored = append(restored, in.Resources[n].ID)
			}
		}
	}
	if err := m.checkReady(ctx, op, def, in, p); err != nil {
		return err
	}
	return m.update(func() { in.State = "ready"; in.Error = "" })
}
