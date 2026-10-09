// Package reconciler converges owners' desired App deployments onto Fleet
// nodes. A pass observes node ledgers, plans at most one lifecycle step per
// App (this file, pure), executes the steps, and records status. Every step
// carries an operation ID derived from what it does, so a pass that crashes or
// loses a reply is simply repeated. See docs/fleet-orchestration.md §5.
package reconciler

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

// NodeView is one online, App-capable node as observed in this pass.
type NodeView struct {
	ID       string
	Kind     string
	Platform string // os-arch
	Caps     []string
	Ledger   *lifecycle.Ledger // nil when its status could not be read this pass
}

// View is everything a plan depends on besides the deployment itself.
type View struct {
	Fleet string
	Now   time.Time
	Nodes map[string]NodeView
	// Lost nodes have been absent from the registry past the grace period.
	// Nodes neither online nor lost are still within the grace period.
	Lost map[string]bool
}

type StepKind string

const (
	StepInstall   StepKind = "install"
	StepCloneData StepKind = "clone_data"
	StepPrepare   StepKind = "prepare_start"
	StepStart     StepKind = "start"
	StepStop      StepKind = "stop"
	StepReconcile StepKind = "reconcile"
)

// Step is one lifecycle request for one App.
type Step struct {
	App         string
	Kind        StepKind
	Node        string
	Variant     Variant
	Scope       string
	Generation  uint64
	Instance    string
	OpID        string
	Preparation string                          // start: the prepared start it consumes
	Source      *lifecycle.DataSource           // clone_data
	Providers   map[string]apptransport.Binding // start: binding alias -> provider
}

type Plan struct {
	Steps  []Step
	Status deployments.Status
}

// Backoff after a failed attempt: 10 s doubling to 10 min.
func backoff(attempts int) time.Duration {
	d := 10 * time.Second
	for i := 1; i < attempts && d < 10*time.Minute; i++ {
		d *= 2
	}
	return min(d, 10*time.Minute)
}

const opPrefix = "d"

// opID is stable for the same intended step: retries reuse it.
func opID(parts ...any) string {
	sum := sha256.Sum256([]byte(fmt.Sprint(parts...)))
	return opPrefix + hex.EncodeToString(sum[:20])
}

func ours(id string) bool {
	return len(id) == 41 && strings.HasPrefix(id, opPrefix)
}

func running(state string) bool { return state == "ready" || state == "recovered" }

func pin(b apptransport.Binding) string {
	return fmt.Sprintf("%s/%s/%d", b.Node, b.Instance, b.Generation)
}

// PlanDeployment returns the next steps and the status to record.
func PlanDeployment(d deployments.Deployment, release *Release, v View) Plan {
	now := v.Now.Unix()
	plan := Plan{Status: deployments.Status{ObservedRevision: d.Revision, Apps: map[string]deployments.AppStatus{}}}
	order, err := deployments.Order(d.Spec)
	if err != nil {
		plan.Status.Conditions = []deployments.Condition{{Type: "Ready", Reason: err.Error(), Since: now}}
		return plan
	}
	ready := map[string]apptransport.Binding{}
	waiting := []string{}
	for _, name := range order {
		a := d.Spec.Apps[name]
		prev := d.Status.Apps[name]
		p := appPlanner{d: d, name: name, a: a, prev: prev, v: v, now: now, ready: ready, release: release}
		status, step, live := p.plan()
		if status.State != prev.State || status.Reason != prev.Reason {
			status.Since = now
		} else {
			status.Since = prev.Since
		}
		plan.Status.Apps[name] = status
		if step != nil {
			plan.Steps = append(plan.Steps, *step)
		}
		if live != nil {
			ready[name] = *live
		}
		if a.Intent == deployments.Running && live == nil {
			waiting = append(waiting, name)
		}
	}
	cond := deployments.Condition{Type: "Ready", Status: len(waiting) == 0, Since: now}
	if len(waiting) > 0 {
		cond.Reason = "not ready: " + strings.Join(waiting, ", ")
	}
	for _, c := range d.Status.Conditions {
		if c.Type == cond.Type && c.Status == cond.Status && c.Reason == cond.Reason {
			cond.Since = c.Since
		}
	}
	plan.Status.Conditions = []deployments.Condition{cond}
	return plan
}

type appPlanner struct {
	d       deployments.Deployment
	name    string
	a       deployments.AppSpec
	prev    deployments.AppStatus
	v       View
	now     int64
	ready   map[string]apptransport.Binding
	release *Release
}

type located struct {
	node string
	in   *lifecycle.Instance
}

func (p *appPlanner) variants() map[string]Variant {
	if p.release == nil {
		return nil
	}
	return p.release.Index.Apps[p.a.Package]
}

// instances of this App (App id + scope) on every observed node.
func (p *appPlanner) instances() []located {
	var appID string
	for _, variant := range p.variants() {
		appID = variant.AppID
	}
	var out []located
	for id, n := range p.v.Nodes {
		if n.Ledger == nil {
			continue
		}
		for _, in := range n.Ledger.Instances {
			if in.AppID == appID && in.Scope == p.a.Scope {
				out = append(out, located{id, in})
			}
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].node != out[j].node {
			return out[i].node < out[j].node
		}
		return out[i].in.ID < out[j].in.ID
	})
	return out
}

func (p *appPlanner) op(kind StepKind, node, revision string, generation uint64, attempt int64) string {
	return opID(p.v.Fleet, "\x00", p.d.Name, "\x00", p.name, "\x00", kind, "\x00", node, "\x00", revision, "\x00", generation, "\x00", attempt)
}

func (p *appPlanner) status(state, reason string) deployments.AppStatus {
	s := p.prev
	s.State, s.Reason = state, reason
	return s
}

// pending reports whether op is still queued/running, and its error if it failed.
func pending(ledger *lifecycle.Ledger, id string) (inFlight bool, failed string) {
	op := ledger.Operations[id]
	if op == nil {
		return false, ""
	}
	switch op.State {
	case "queued", "running":
		return true, ""
	case "failed":
		if op.Error == "" {
			return false, "operation failed"
		}
		return false, op.Error
	}
	return false, ""
}

// failedAttempt records a failure once and schedules the retry.
func (p *appPlanner) failedAttempt(reason string) deployments.AppStatus {
	s := p.status("failed", reason)
	s.Attempts = p.prev.Attempts + 1
	s.NextAttempt = p.now + int64(backoff(s.Attempts)/time.Second)
	return s
}

func (p *appPlanner) step(kind StepKind, node string, variant Variant, generation uint64, in *lifecycle.Instance, id string) *Step {
	s := &Step{App: p.name, Kind: kind, Node: node, Variant: variant, Scope: p.a.Scope, Generation: generation, OpID: id}
	if in != nil {
		s.Instance = in.ID
	}
	return s
}

func (p *appPlanner) plan() (deployments.AppStatus, *Step, *apptransport.Binding) {
	variants := p.variants()
	if len(variants) == 0 {
		return p.status("blocked", "package "+p.a.Package+" is not in the release set"), nil, nil
	}
	all := p.instances()
	if p.a.Intent == deployments.Stopped {
		return p.stopAll(all, "")
	}
	node, reason := p.place(variants)
	if node == "" {
		s := p.status("waiting", reason)
		// An App on a node that is offline (within grace) keeps its record.
		return s, nil, nil
	}
	variant := variants[p.v.Nodes[node].Platform]
	ledger := p.v.Nodes[node].Ledger
	if ledger == nil {
		return p.status("waiting", "cannot read node "+node+" this pass"), nil, nil
	}
	// One live instance per App: stop any other revision or node first.
	var target *lifecycle.Instance
	for _, l := range all {
		if l.node == node && l.in.Digest == variant.Revision {
			target = l.in
		}
	}
	var strays []located
	for _, l := range all {
		if (l.node != node || l.in.Digest != variant.Revision) && l.in.State != "stopped" {
			strays = append(strays, l)
		}
	}
	if len(strays) > 0 {
		s, step, _ := p.stopAll(strays, "replacing")
		s.NodeID = node
		return s, step, nil
	}
	s := p.prev
	s.NodeID, s.Revision = node, variant.Revision
	p.prev = s
	// After a failure, new install/copy/start attempts wait; stops do not.
	backingOff := p.prev.NextAttempt > p.now
	hold := func() (deployments.AppStatus, *Step, *apptransport.Binding) {
		return p.status("backoff", p.prev.Reason), nil, nil
	}

	// Install the exact artifact (the executor stages it first).
	installation := ledger.Installations[variant.Revision]
	if installation == nil || installation.State != "installed" {
		state := ""
		if installation != nil {
			state = installation.State
		}
		switch state {
		case "installing":
			return p.status("installing", ""), nil, nil
		case "unknown", "removing", "remove_failed":
			gen := uint64(0)
			if target != nil {
				gen = target.Generation
			}
			id := p.op(StepReconcile, node, variant.Revision, gen, p.now/60)
			if busy, _ := pending(ledger, id); busy {
				return p.status("installing", "settling an interrupted installation"), nil, nil
			}
			return p.status("installing", "settling an interrupted installation"), p.step(StepReconcile, node, variant, gen, target, id), nil
		}
		if backingOff {
			return hold()
		}
		id := p.op(StepInstall, node, variant.Revision, 0, int64(p.prev.Attempts))
		if busy, failed := pending(ledger, id); busy {
			return p.status("installing", ""), nil, nil
		} else if failed != "" {
			return p.failedAttempt("install: " + failed), nil, nil
		}
		return p.status("installing", ""), p.step(StepInstall, node, variant, 0, nil, id), nil
	}

	// Carry the App's data from its newest stopped revision on this node.
	if target == nil {
		var source *lifecycle.Instance
		for _, l := range all {
			if l.node == node && l.in.State == "stopped" && l.in.Generation > 0 &&
				ledger.Installations[l.in.Digest] != nil && ledger.Installations[l.in.Digest].State == "installed" &&
				(source == nil || l.in.Generation > source.Generation) {
				source = l.in
			}
		}
		if source != nil && backingOff {
			return hold()
		}
		if source != nil {
			id := p.op(StepCloneData, node, variant.Revision, 0, int64(p.prev.Attempts))
			if busy, failed := pending(ledger, id); busy {
				return p.status("starting", "copying data from the previous revision"), nil, nil
			} else if failed != "" {
				return p.failedAttempt("data copy: " + failed), nil, nil
			}
			step := p.step(StepCloneData, node, variant, 0, nil, id)
			step.Source = &lifecycle.DataSource{Digest: source.Digest, Generation: source.Generation}
			return p.status("starting", "copying data from the previous revision"), step, nil
		}
	}

	state := "stopped"
	if target != nil {
		state = target.State
	}
	switch state {
	case "stopped":
		if target != nil && (len(target.Resources) > 0 || len(target.Reservations) > 0) {
			return p.stopOne(node, variant, target, "releasing resources")
		}
		for alias, b := range p.a.Bindings {
			if _, ok := p.ready[b.App]; !ok {
				return p.status("waiting", "waiting for "+b.App+" ("+alias+")"), nil, nil
			}
		}
		if backingOff {
			return hold()
		}
		gen := uint64(0)
		if target != nil {
			gen = target.Generation
		}
		id := p.op(StepPrepare, node, variant.Revision, gen, int64(p.prev.Attempts))
		if busy, failed := pending(ledger, id); busy {
			return p.status("starting", ""), nil, nil
		} else if failed != "" {
			return p.failedAttempt("prepare: " + failed), nil, nil
		}
		return p.status("starting", ""), p.step(StepPrepare, node, variant, gen, target, id), nil
	case "prepared":
		if !ours(target.StartPreparationID) {
			return p.stopOne(node, variant, target, "cancelling a start this deployment did not prepare")
		}
		providers := map[string]apptransport.Binding{}
		for alias, b := range p.a.Bindings {
			provider, ok := p.ready[b.App]
			if !ok {
				// The provider went away after preparation; cancel and wait.
				return p.stopOne(node, variant, target, "waiting for "+b.App+" ("+alias+")")
			}
			providers[alias] = provider
		}
		id := opID("start", target.StartPreparationID)
		if busy, _ := pending(ledger, id); busy {
			return p.status("starting", ""), nil, nil
		}
		step := p.step(StepStart, node, variant, target.Generation, target, id)
		step.Preparation, step.Providers = target.StartPreparationID, providers
		// Status names the generation the start will run (prepared + 1).
		s := p.status("starting", "")
		s.InstanceID, s.Generation = target.ID, int64(target.Generation)+1
		return s, step, nil
	case "starting", "draining":
		return p.status(state, ""), nil, nil
	case "ready", "recovered":
		if target.ReadyGeneration != target.Generation {
			return p.status("starting", ""), nil, nil
		}
		s := p.status("ready", "")
		adopted := s.InstanceID != target.ID || s.Generation != int64(target.Generation)
		s.InstanceID, s.Generation = target.ID, int64(target.Generation)
		s.Attempts, s.NextAttempt = 0, 0
		if adopted {
			// Started by an earlier pass whose status write was lost, or by an
			// earlier coordinator: its providers are taken as they are now.
			s.Providers, s.Grants, s.Renewed = map[string]string{}, nil, 0
			for alias, b := range p.a.Bindings {
				if provider, ok := p.ready[b.App]; ok {
					s.Providers[alias] = pin(provider)
				}
			}
		}
		for alias, b := range p.a.Bindings {
			provider, ok := p.ready[b.App]
			if !ok {
				s.Reason = "provider " + b.App + " is not ready"
				continue
			}
			if s.Providers[alias] != pin(provider) {
				// Grants are pinned to the provider generation: restart on the new one.
				p.prev = s
				return p.stopOne(node, variant, target, "provider "+b.App+" restarted")
			}
		}
		live := apptransport.Binding{Fleet: p.v.Fleet, Node: node, Instance: target.ID, Revision: target.Digest,
			Generation: target.Generation, Component: "backend", Port: "http"}
		return s, nil, &live
	case "failed", "degraded":
		s := p.failedAttempt(state + ": " + target.Error)
		if p.prev.State == "failed" && p.prev.Generation == int64(target.Generation) {
			s.Attempts, s.NextAttempt = p.prev.Attempts, p.prev.NextAttempt
		}
		s.Generation = int64(target.Generation)
		p.prev = s
		_, step, _ := p.stopOne(node, variant, target, s.Reason)
		return s, step, nil
	case "unknown":
		id := p.op(StepReconcile, node, variant.Revision, target.Generation, p.now/60)
		if busy, _ := pending(ledger, id); busy {
			return p.status("recovering", "inspecting an interrupted operation"), nil, nil
		}
		return p.status("recovering", "inspecting an interrupted operation"), p.step(StepReconcile, node, variant, target.Generation, target, id), nil
	default: // recovery_required, stop_blocked
		s, step, _ := p.stopOne(node, variant, target, state+": "+target.Error)
		s.State = "attention"
		return s, step, nil
	}
}

// stopOne stops (or cancels the prepared start of) one instance. Repeated stops
// of a blocked instance are retried at most once a minute.
func (p *appPlanner) stopOne(node string, variant Variant, in *lifecycle.Instance, reason string) (deployments.AppStatus, *Step, *apptransport.Binding) {
	ledger := p.v.Nodes[node].Ledger
	bucket := int64(0)
	if in.State == "stop_blocked" || in.State == "recovery_required" {
		bucket = p.now / 60
	}
	id := p.op(StepStop, node, in.Digest, in.Generation, bucket)
	s := p.status("stopping", reason)
	if busy, _ := pending(ledger, id); busy {
		return s, nil, nil
	}
	if op := ledger.Operations[id]; op != nil && op.State == "succeeded" && in.State != "stopped" {
		id = p.op(StepStop, node, in.Digest, in.Generation, p.now/60)
	}
	step := p.step(StepStop, node, variant, in.Generation, in, id)
	step.Variant.Revision = in.Digest
	return s, step, nil
}

// stopAll stops the first live instance among list; stopped when none is live.
func (p *appPlanner) stopAll(list []located, reason string) (deployments.AppStatus, *Step, *apptransport.Binding) {
	for _, l := range list {
		if l.in.State == "stopped" && len(l.in.Resources) == 0 && len(l.in.Reservations) == 0 {
			continue
		}
		if l.in.State == "stopping" || l.in.State == "draining" {
			return p.status("stopping", reason), nil, nil
		}
		return p.stopOne(l.node, Variant{}, l.in, reason)
	}
	s := p.status("stopped", "")
	s.Grants, s.Providers, s.Renewed = nil, nil, 0
	return s, nil, nil
}

// place chooses the node for a running App (docs §6). Returns "" and why when
// no node can take it now.
func (p *appPlanner) place(variants map[string]Variant) (string, string) {
	eligible := func(id string) bool {
		n, ok := p.v.Nodes[id]
		if !ok {
			return false
		}
		variant, ok := variants[n.Platform]
		if !ok {
			return false
		}
		for _, c := range variant.Requires {
			if !slices.Contains(n.Caps, c) {
				return false
			}
		}
		return !slices.Contains(p.a.Placement.Avoid, n.Kind) || p.a.Placement.Node == id
	}
	if pinned := p.a.Placement.Node; pinned != "" {
		if eligible(pinned) {
			return pinned, ""
		}
		if _, online := p.v.Nodes[pinned]; online {
			return "", "node " + pinned + " cannot run " + p.a.Package
		}
		return "", "waiting for node " + pinned
	}
	if current := p.prev.NodeID; current != "" && !p.v.Lost[current] {
		if eligible(current) {
			return current, ""
		}
		if _, online := p.v.Nodes[current]; !online {
			return "", "waiting for node " + current + " to return"
		}
	}
	var prefer []string
	for _, variant := range variants {
		prefer = variant.Prefer
	}
	prefer = append(append([]string{}, p.a.Placement.Prefer...), prefer...)
	providers := map[string]bool{}
	for _, b := range p.a.Bindings {
		if provider, ok := p.ready[b.App]; ok {
			providers[provider.Node] = true
		}
	}
	var best string
	var bestKey [4]int
	for id, n := range p.v.Nodes {
		if !eligible(id) {
			continue
		}
		preferred := 1
		if slices.Contains(prefer, n.Kind) {
			preferred = 0
		}
		colocated := 1
		if providers[id] {
			colocated = 0
		}
		// Fewest capabilities first: an App that needs little stays off the
		// workspace node (the brain), and one machine still takes everything.
		key := [4]int{preferred, colocated, len(n.Caps), 0}
		if best == "" || slices.Compare(key[:], bestKey[:]) < 0 || (key == bestKey && id < best) {
			best, bestKey = id, key
		}
	}
	if best == "" {
		var requires []string
		for _, variant := range variants {
			requires = variant.Requires
		}
		return "", fmt.Sprintf("no online node offers %v for %s", requires, p.a.Package)
	}
	return best, ""
}
