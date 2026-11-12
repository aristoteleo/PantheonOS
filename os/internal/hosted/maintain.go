package hosted

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"
)

// DeferLogEvery is how often a maintenance deferral that goes on (the same
// error for the same journal) is logged again: a provider that is away (a
// paused Workspace answers "nats: no responders") deferred every pass, about
// 5,000 lines an hour on staging (combined check 1); with about sixteen owners
// waiting at once, a reminder every 10 minutes was still about 100 lines an hour.
const DeferLogEvery = time.Hour

type deferral struct {
	err        string
	logged     time.Time
	suppressed int
	paths      map[string]bool // the journals deferred now
}

// deferLog logs an owner's maintenance deferrals of one kind (bindings,
// sessions) once when they start or their error changes, then once every
// DeferLogEvery with how many passes and journals it skipped, however many
// journals share it; the owner's last deferred journal maintained again is
// logged once (deferResolved).
func (a *Allocator) deferLog(fleet, kind, path string, err error, now time.Time) {
	key := fleet + "|" + kind
	a.mu.Lock()
	if a.deferred == nil {
		a.deferred = map[string]*deferral{}
	}
	d := a.deferred[key]
	text := err.Error()
	if d == nil {
		d = &deferral{paths: map[string]bool{}}
		a.deferred[key] = d
	}
	d.paths[path] = true
	if d.err == text && now.Sub(d.logged) < DeferLogEvery {
		d.suppressed++
		a.mu.Unlock()
		return
	}
	repeat, skipped, since, journals := d.err == text, d.suppressed, now.Sub(d.logged), len(d.paths)
	d.err, d.logged, d.suppressed = text, now, 0
	a.mu.Unlock()
	if repeat {
		log.Printf("[hosted] %s %s maintenance still deferred (%d passes over %d journals in %s): %v",
			fleet, kind, skipped+1, journals, since.Round(time.Second), err)
		return
	}
	log.Printf("[hosted] %s %s maintenance deferred: %v", fleet, kind, err)
}

func (a *Allocator) deferResolved(fleet, kind, path string) {
	key := fleet + "|" + kind
	a.mu.Lock()
	d := a.deferred[key]
	if d == nil || !d.paths[path] {
		a.mu.Unlock()
		return
	}
	delete(d.paths, path)
	done := len(d.paths) == 0
	if done {
		delete(a.deferred, key)
	}
	a.mu.Unlock()
	if done {
		log.Printf("[hosted] %s %s maintenance resumed", fleet, kind)
	}
}

type retirement struct {
	Protocol  int               `json:"protocol"`
	Consumer  identity          `json:"consumer"`
	OwnerRef  string            `json:"owner_ref"`
	State     string            `json:"state"` // retiring, retired
	Resources map[string]string `json:"resources"`
}

var settledResources = map[string]bool{"unallocated": true, "released": true, "expired": true, "lost": true, "failed": true}

// retire fences every binding revision of a logical owner, revokes its grants,
// then releases its sessions (former LiveDependencyOwner.retire). The
// consumer drains its runs first; shared providers are never stopped.
func (a *Allocator) retire(ctx context.Context, fleet string, consumer identity, ownerRef string) (*retirement, error) {
	if !identRE.MatchString(ownerRef) {
		return nil, fmt.Errorf("use the original logical owner identity")
	}
	dir, err := a.dir(fleet, "retirements")
	if err != nil {
		return nil, err
	}
	path := filepath.Join(dir, digest(consumer, ownerRef)+".json")
	unlock := a.lock(path)
	defer unlock()
	var result retirement
	exists, err := readJournal(path, &result)
	if err != nil {
		return nil, err
	}
	if exists {
		if result.Protocol != 1 || result.Consumer != consumer || result.OwnerRef != ownerRef {
			return nil, fmt.Errorf("invalid logical-owner retirement")
		}
		if result.State == "retired" {
			return &result, nil
		}
	} else {
		if len(journals(dir)) >= 4096 {
			return nil, fmt.Errorf("logical-owner retirement journal is full")
		}
		result = retirement{Protocol: 1, Consumer: consumer, OwnerRef: ownerRef, State: "retiring", Resources: map[string]string{}}
		// Persist before the first external mutation.
		if err := writeJournal(path, result); err != nil {
			return nil, err
		}
	}
	bindings, err := a.dir(fleet, "bindings")
	if err != nil {
		return nil, err
	}
	sessions := map[string]sessionRecipe{}
	for _, bpath := range journals(bindings) {
		done, err := a.retireBinding(ctx, fleet, bpath, consumer, ownerRef, sessions)
		if err != nil {
			return nil, err
		}
		_ = done
	}
	for _, operation := range sortedKeys(sessions) {
		record, err := a.release(ctx, fleet, consumer, operation)
		switch {
		case err != nil && record == nil:
			return nil, err
		case record == nil:
			result.Resources[operation] = "unallocated"
		case record.Reason == "provider_unavailable":
			result.Resources[operation] = "lost"
		case record.Receipt != nil:
			result.Resources[operation] = record.Receipt.State
		default:
			result.Resources[operation] = "unknown"
		}
		if err := writeJournal(path, result); err != nil {
			return nil, err
		}
	}
	retired := true
	for _, state := range result.Resources {
		retired = retired && settledResources[state]
	}
	if retired {
		result.State = "retired"
		if err := writeJournal(path, result); err != nil {
			return nil, err
		}
	}
	return &result, nil
}

// retireBinding marks one binding revision retiring and revokes its grants,
// collecting the sessions it owns.
func (a *Allocator) retireBinding(ctx context.Context, fleet, path string, consumer identity, ownerRef string, sessions map[string]sessionRecipe) (bool, error) {
	unlock := a.lock(path)
	defer unlock()
	var record bindingRecord
	if _, err := readJournal(path, &record); err != nil {
		return false, err
	}
	if record.Recipe.Consumer != consumer || record.Recipe.OwnerRef != ownerRef {
		return false, nil
	}
	record.Phase = "retiring"
	if err := writeJournal(path, record); err != nil {
		return false, err
	}
	for _, s := range record.Plan.Sessions {
		sessions[s.OperationID] = *s
	}
	resolved := true
	for _, q := range record.Plan.Requests {
		for _, r := range q.Methods {
			for _, v := range r.Bound {
				if string(v) == `"`+pendingSession+`"` {
					resolved = false
				}
			}
		}
	}
	for _, alias := range sortedKeys(record.Plan.Requests) {
		if contains(record.Terminal, alias) {
			continue
		}
		r := record.Renewals[alias]
		if r == nil && resolved {
			// Recover a grant whose delivery was lost, so it can be revoked.
			grant, err := a.issue(ctx, fleet, record.Plan.Requests[alias])
			if gone(err) {
				record.Terminal = append(record.Terminal, alias)
				if err := writeJournal(path, record); err != nil {
					return false, err
				}
				continue
			}
			if err != nil {
				return false, err
			}
			q := record.Plan.Requests[alias]
			r = &receipt{GrantID: str(grant["grant_id"]), Consumer: q.Consumer.public(fleet), Provider: q.Provider.public(fleet), Expires: int64(num(grant["expires"]))}
			record.Renewals[alias] = r
			if err := writeJournal(path, record); err != nil {
				return false, err
			}
		}
		if r != nil && r.State != "revoked" && r.State != "expired" {
			if err := a.Grants.RevokeDependency(fleet, r.GrantID); err != nil {
				return false, err
			}
			r.State = "revoked"
			if err := writeJournal(path, record); err != nil {
				return false, err
			}
		}
	}
	return true, nil
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// Run maintains every owner's journals every interval until ctx ends: renews
// grants of live consumers, revokes those of ended ones (never reissuing an
// expired grant), keeps sessions alive or releases them, and finishes
// retirements.
func (a *Allocator) Run(ctx context.Context, interval time.Duration) {
	tick := time.NewTicker(interval)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
		fleets, _ := os.ReadDir(a.Root)
		for _, f := range fleets {
			if f.IsDir() && identRE.MatchString(f.Name()) {
				c, cancel := context.WithTimeout(ctx, 2*time.Minute)
				a.MaintainOnce(c, f.Name())
				cancel()
			}
		}
	}
}

// MaintainOnce runs one maintenance pass for one fleet.
func (a *Allocator) MaintainOnce(ctx context.Context, fleet string) {
	if dir, err := a.dir(fleet, "bindings"); err == nil {
		for _, path := range journals(dir) {
			if err := a.maintainBinding(ctx, fleet, path); err != nil {
				a.deferLog(fleet, "binding", path, err, a.now())
			} else {
				a.deferResolved(fleet, "binding", path)
			}
		}
	}
	if dir, err := a.dir(fleet, "sessions"); err == nil {
		for _, path := range journals(dir) {
			unlock := a.lock(path)
			var record sessionRecord
			if _, err := readJournal(path, &record); err == nil && record.Phase != "terminal" {
				if _, err := a.maintainSession(ctx, fleet, path, &record, false); err != nil {
					a.deferLog(fleet, "session", path, err, a.now())
				} else {
					a.deferResolved(fleet, "session", path)
				}
			}
			unlock()
		}
	}
	if dir, err := a.dir(fleet, "retirements"); err == nil {
		for _, path := range journals(dir) {
			var r retirement
			if _, err := readJournal(path, &r); err != nil || r.State == "retired" {
				continue
			}
			if _, err := a.retire(ctx, fleet, r.Consumer, r.OwnerRef); err != nil {
				log.Printf("[hosted] %s retirement deferred: %v", fleet, err)
			}
		}
	}
}

// maintainBinding renews or revokes one live binding's grants.
func (a *Allocator) maintainBinding(ctx context.Context, fleet, path string) error {
	unlock := a.lock(path)
	defer unlock()
	var record bindingRecord
	if _, err := readJournal(path, &record); err != nil {
		return err
	}
	if record.Protocol != 1 || (record.Phase != "binding" && record.Phase != "bound") || len(record.Renewals) == 0 {
		return nil
	}
	active := false
	for _, r := range record.Renewals {
		active = active || (r.State != "expired" && r.State != "revoked")
	}
	if !active {
		return nil
	}
	consumer := record.Recipe.Consumer
	state, err := a.lifecycleStatus(ctx, fleet, consumer.NodeID)
	if err != nil {
		return err
	}
	in := instanceOf(state, consumer.InstanceID)
	if in != nil && int64(num(in["generation"])) < consumer.Generation {
		return nil // an older inventory cannot prove this generation stopped
	}
	exact := in != nil && str(in["digest"]) == consumer.Revision && int64(num(in["generation"])) == consumer.Generation
	terminal := !exact || str(in["state"]) == "stopped" || str(in["state"]) == "failed" || str(in["state"]) == "removed"
	changed := false
	for _, alias := range sortedKeys(record.Renewals) {
		r := record.Renewals[alias]
		if r.State == "expired" || r.State == "revoked" {
			continue
		}
		if terminal {
			if err := a.Grants.RevokeDependency(fleet, r.GrantID); err != nil {
				continue
			}
			r.State, changed = "revoked", true
			continue
		}
		if r.Expires > a.now().Add(300*time.Second).Unix() {
			continue
		}
		value, err := a.Grants.RenewDependency(ctx, fleet, r.GrantID, a.now().Add(15*time.Minute).Unix())
		if gone(err) {
			r.State, changed = "expired", true
			continue
		}
		if err != nil {
			continue
		}
		r.Expires, r.State, changed = int64(num(value["expires"])), "active", true
	}
	if changed {
		return writeJournal(path, record)
	}
	return nil
}

var _ = json.Valid
