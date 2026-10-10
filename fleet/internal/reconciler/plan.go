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
	StepRecover   StepKind = "recover"
	StepMoveData  StepKind = "move_data"
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
	Source      *lifecycle.DataSource           // clone_data, move_data
	SourceNode  string                          // move_data: the node holding the stopped source
	Providers   map[string]apptransport.Binding // start: binding alias -> provider
	Refs        map[string]apptransport.Binding // start: App named by {"$app"} in its configuration
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

func pin(b apptransport.Binding) string {
	return fmt.Sprintf("%s/%s/%d", b.Node, b.Instance, b.Generation)
}

// refKey is the pin key of a configuration reference ("$app:<name>"); binding
// pins use their alias.
func refKey(app string) string { return "$app:" + app }

type result struct {
	status deployments.AppStatus
	step   *Step
	live   *apptransport.Binding
}

// PlanDeployment returns the next steps and the status to record.
func PlanDeployment(d deployments.Deployment, release *Release, v View) Plan {
	now := v.Now.Unix()
	plan := Plan{Status: deployments.Status{ObservedRevision: d.Revision, Apps: map[string]deployments.AppStatus{}}}
	units, err := deployments.Units(d.Spec)
	if err != nil {
		plan.Status.Conditions = []deployments.Condition{{Type: "Ready", Reason: err.Error(), Since: now}}
		return plan
	}
	ready := map[string]apptransport.Binding{}
	var waiting []string
	for _, unit := range units {
		planners := make([]*appPlanner, len(unit))
		for i, name := range unit {
			planners[i] = &appPlanner{d: d, name: name, a: d.Spec.Apps[name], prev: d.Status.Apps[name], v: v, now: now, ready: ready, release: release}
		}
		var results []result
		if len(unit) == 1 {
			results = []result{planners[0].plan()}
		} else {
			results = planUnit(planners)
		}
		for i, r := range results {
			name, prev := unit[i], d.Status.Apps[unit[i]]
			if r.status.State != prev.State || r.status.Reason != prev.Reason {
				r.status.Since = now
			} else {
				r.status.Since = prev.Since
			}
			plan.Status.Apps[name] = r.status
			if r.step != nil {
				plan.Steps = append(plan.Steps, *r.step)
			}
			if r.live != nil {
				ready[name] = *r.live
			}
			if d.Spec.Apps[name].Intent == deployments.Running && r.live == nil {
				waiting = append(waiting, name)
			}
		}
	}
	sort.Strings(waiting)
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

	// set by locate
	lastNode string // where the App ran before this pass placed it
	node    string
	variant Variant
	ledger  *lifecycle.Ledger
	target  *lifecycle.Instance
	all     []located
}

type located struct {
	node string
	in   *lifecycle.Instance
}

// dep is one App this App depends on: a binding (key = alias) or a
// configuration reference (key = refKey(app)).
type dep struct{ key, app string }

func (p *appPlanner) deps() []dep {
	var out []dep
	for alias, b := range p.a.Bindings {
		out = append(out, dep{alias, b.App})
	}
	for _, app := range deployments.ConfigRefs(p.a) {
		out = append(out, dep{refKey(app), app})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].key < out[j].key })
	return out
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

func (p *appPlanner) is(state, reason string) result { return result{status: p.status(state, reason)} }

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

func (p *appPlanner) step(kind StepKind, generation uint64, in *lifecycle.Instance, id string) *Step {
	s := &Step{App: p.name, Kind: kind, Node: p.node, Variant: p.variant, Scope: p.a.Scope, Generation: generation, OpID: id}
	if in != nil {
		s.Instance = in.ID
	}
	return s
}

func (p *appPlanner) liveIdentity(in *lifecycle.Instance, generation uint64) apptransport.Binding {
	return apptransport.Binding{Fleet: p.v.Fleet, Node: p.node, Instance: in.ID, Revision: in.Digest,
		Generation: generation, Component: "backend", Port: "http"}
}

// locate places the App and finds its target instance. It returns a result
// when the App cannot go further this pass (stopped intent, waiting for a node,
// another revision or node still to stop).
func (p *appPlanner) locate() *result {
	variants := p.variants()
	if len(variants) == 0 {
		r := p.is("blocked", "package "+p.a.Package+" is not in the release set")
		return &r
	}
	p.all = p.instances()
	if p.a.Intent == deployments.Stopped {
		r := p.stopAll(p.all, "")
		return &r
	}
	node, reason := p.place(variants)
	if node == "" {
		// An App on a node that is offline (within grace) keeps its record.
		r := p.is("waiting", reason)
		return &r
	}
	p.node, p.variant, p.ledger = node, variants[p.v.Nodes[node].Platform], p.v.Nodes[node].Ledger
	if p.ledger == nil {
		r := p.is("waiting", "cannot read node "+node+" this pass")
		return &r
	}
	// One live instance per App: stop any other revision or node first.
	var strays []located
	for _, l := range p.all {
		if l.node == node && l.in.Digest == p.variant.Revision {
			p.target = l.in
		} else if l.in.State != "stopped" {
			strays = append(strays, l)
		}
	}
	if len(strays) > 0 {
		r := p.stopAll(strays, "replacing")
		r.status.NodeID = node
		return &r
	}
	s := p.prev
	p.lastNode = s.NodeID
	s.NodeID, s.Revision = node, p.variant.Revision
	p.prev = s
	return nil
}

func (p *appPlanner) backingOff() bool { return p.prev.NextAttempt > p.now }

// install ensures the artifact is installed and the App's data carried from
// its previous revision; it returns a result while that is still to do.
func (p *appPlanner) install() *result {
	hold := func() *result { r := p.is("backoff", p.prev.Reason); return &r }
	installation := p.ledger.Installations[p.variant.Revision]
	if installation == nil || installation.State != "installed" {
		state := ""
		if installation != nil {
			state = installation.State
		}
		switch state {
		case "installing":
			r := p.is("installing", "")
			return &r
		case "unknown", "removing", "remove_failed":
			gen := uint64(0)
			if p.target != nil {
				gen = p.target.Generation
			}
			id := p.op(StepReconcile, p.node, p.variant.Revision, gen, p.now/60)
			r := p.is("installing", "settling an interrupted installation")
			if busy, _ := pending(p.ledger, id); !busy {
				r.step = p.step(StepReconcile, gen, p.target, id)
			}
			return &r
		}
		if p.backingOff() {
			return hold()
		}
		id := p.op(StepInstall, p.node, p.variant.Revision, 0, int64(p.prev.Attempts))
		if busy, failed := pending(p.ledger, id); busy {
			r := p.is("installing", "")
			return &r
		} else if failed != "" {
			return &result{status: p.failedAttempt("install: " + failed)}
		}
		return &result{status: p.status("installing", ""), step: p.step(StepInstall, 0, nil, id)}
	}
	if p.target != nil {
		return nil
	}
	// Carry the App's data from its newest stopped revision on this node.
	var source *lifecycle.Instance
	for _, l := range p.all {
		if l.node == p.node && l.in.State == "stopped" && l.in.Generation > 0 &&
			p.ledger.Installations[l.in.Digest] != nil && p.ledger.Installations[l.in.Digest].State == "installed" &&
			(source == nil || l.in.Generation > source.Generation) {
			source = l.in
		}
	}
	if source == nil {
		return p.move(hold)
	}
	if p.backingOff() {
		return hold()
	}
	id := p.op(StepCloneData, p.node, p.variant.Revision, 0, int64(p.prev.Attempts))
	r := p.is("starting", "copying data from the previous revision")
	if busy, failed := pending(p.ledger, id); busy {
		return &r
	} else if failed != "" {
		return &result{status: p.failedAttempt("data copy: " + failed)}
	}
	r.step = p.step(StepCloneData, 0, nil, id)
	r.step.Source = &lifecycle.DataSource{Digest: source.Digest, Generation: source.Generation}
	return &r
}

// move carries the App's state from another node: the stopped instance on the
// node it last ran on, else the newest stopped one elsewhere. A lost node's
// data cannot be read; the App then starts empty there.
func (p *appPlanner) move(hold func() *result) *result {
	var source *located
	for i, l := range p.all {
		if l.node == p.node || l.in.State != "stopped" || l.in.Generation == 0 || len(l.in.Resources) > 0 {
			continue
		}
		if source == nil || (l.node == p.lastNode) != (source.node == p.lastNode) && l.node == p.lastNode ||
			(l.node == p.lastNode) == (source.node == p.lastNode) && l.in.Generation > source.in.Generation {
			source = &p.all[i]
		}
	}
	if source == nil {
		return nil
	}
	if p.backingOff() {
		return hold()
	}
	id := p.op(StepMoveData, p.node, p.variant.Revision, source.in.Generation, int64(p.prev.Attempts))
	r := p.is("starting", "moving data from "+source.node)
	if busy, failed := pending(p.ledger, id); busy {
		return &r
	} else if failed != "" {
		return &result{status: p.failedAttempt("data move: " + failed)}
	}
	r.step = p.step(StepMoveData, 0, nil, id)
	r.step.Source = &lifecycle.DataSource{Digest: source.in.Digest, Generation: source.in.Generation}
	r.step.SourceNode = source.node
	return &r
}

// prepare reserves the next start of a stopped (or absent) target.
func (p *appPlanner) prepare() result {
	if p.target != nil && (len(p.target.Resources) > 0 || len(p.target.Reservations) > 0) {
		return p.stopOne(p.node, p.target, "releasing resources")
	}
	if p.backingOff() {
		return p.is("backoff", p.prev.Reason)
	}
	gen := uint64(0)
	if p.target != nil {
		gen = p.target.Generation
	}
	id := p.op(StepPrepare, p.node, p.variant.Revision, gen, int64(p.prev.Attempts))
	if busy, failed := pending(p.ledger, id); busy {
		return p.is("starting", "")
	} else if failed != "" {
		return result{status: p.failedAttempt("prepare: " + failed)}
	}
	return result{status: p.status("starting", ""), step: p.step(StepPrepare, gen, p.target, id)}
}

// start consumes our prepared start with the given dependency identities.
func (p *appPlanner) start(identities map[string]apptransport.Binding) result {
	id := opID("start", p.target.StartPreparationID)
	s := p.status("starting", "")
	// Status names the generation the start will run (prepared + 1).
	s.InstanceID, s.Generation = p.target.ID, int64(p.target.Generation)+1
	if busy, _ := pending(p.ledger, id); busy {
		return result{status: s}
	}
	step := p.step(StepStart, p.target.Generation, p.target, id)
	step.Preparation = p.target.StartPreparationID
	step.Providers, step.Refs = map[string]apptransport.Binding{}, map[string]apptransport.Binding{}
	for alias, b := range p.a.Bindings {
		step.Providers[alias] = identities[b.App]
	}
	for _, app := range deployments.ConfigRefs(p.a) {
		step.Refs[app] = identities[app]
	}
	return result{status: s, step: step}
}

// running reports a target that is up at its committed ready generation.
func (p *appPlanner) running() bool {
	t := p.target
	return t != nil && (t.State == "ready" || t.State == "recovered") && t.ReadyGeneration == t.Generation
}

// steady checks a running target's pins against the current identities; it
// returns the status (adopting an instance it did not start) and the first
// dependency that moved.
func (p *appPlanner) steady(identities map[string]apptransport.Binding) (deployments.AppStatus, string) {
	s := p.status("ready", "")
	adopted := s.InstanceID != p.target.ID || s.Generation != int64(p.target.Generation)
	s.InstanceID, s.Generation = p.target.ID, int64(p.target.Generation)
	s.Attempts, s.NextAttempt = 0, 0
	if adopted {
		// Started by an earlier pass whose status write was lost, or by an
		// earlier coordinator: its dependencies are taken as they are now.
		s.Providers, s.Grants, s.Renewed = map[string]string{}, nil, 0
		for _, d := range p.deps() {
			if id, ok := identities[d.app]; ok {
				s.Providers[d.key] = pin(id)
			}
		}
	}
	for _, d := range p.deps() {
		id, ok := identities[d.app]
		if !ok {
			s.Reason = d.app + " is not ready"
			continue
		}
		if s.Providers[d.key] != pin(id) {
			return s, d.app
		}
	}
	return s, ""
}

// failed handles a target that failed or is otherwise not usable: it is
// stopped, and the failure counted once per generation.
func (p *appPlanner) failed() result {
	t := p.target
	switch t.State {
	case "unknown":
		id := p.op(StepReconcile, p.node, p.variant.Revision, t.Generation, p.now/60)
		r := p.is("recovering", "inspecting an interrupted operation")
		if busy, _ := pending(p.ledger, id); !busy {
			r.step = p.step(StepReconcile, t.Generation, t, id)
		}
		return r
	case "failed", "degraded":
		s := p.failedAttempt(t.State + ": " + t.Error)
		if p.prev.State == "failed" && p.prev.Generation == int64(t.Generation) {
			s.Attempts, s.NextAttempt = p.prev.Attempts, p.prev.NextAttempt
		}
		s.Generation = int64(t.Generation)
		p.prev = s
		r := p.stopOne(p.node, t, s.Reason)
		r.status = s
		return r
	default: // recovery_required, stop_blocked
		r := p.stopOne(p.node, t, t.State+": "+t.Error)
		r.status.State = "attention"
		return r
	}
}

// plan converges a single-App unit.
func (p *appPlanner) plan() result {
	if r := p.locate(); r != nil {
		return *r
	}
	if r := p.install(); r != nil {
		return *r
	}
	state := "stopped"
	if p.target != nil {
		state = p.target.State
	}
	switch state {
	case "stopped":
		for _, d := range p.deps() {
			if _, ok := p.ready[d.app]; !ok {
				return p.is("waiting", "waiting for "+d.app)
			}
		}
		return p.prepare()
	case "prepared":
		if !ours(p.target.StartPreparationID) {
			return p.stopOne(p.node, p.target, "cancelling a start this deployment did not prepare")
		}
		for _, d := range p.deps() {
			if _, ok := p.ready[d.app]; !ok {
				// A dependency went away after preparation; cancel and wait.
				return p.stopOne(p.node, p.target, "waiting for "+d.app)
			}
		}
		return p.start(p.ready)
	case "starting", "draining":
		return p.is(state, "")
	case "ready", "recovered":
		if !p.running() {
			return p.is("starting", "")
		}
		s, moved := p.steady(p.ready)
		if moved != "" {
			// Grants and references are pinned to a generation: restart on the new one.
			p.prev = s
			return p.stopOne(p.node, p.target, moved+" restarted")
		}
		live := p.liveIdentity(p.target, p.target.Generation)
		return result{status: s, live: &live}
	default:
		return p.failed()
	}
}

// planUnit converges Apps that refer to each other's exact instances in a
// cycle. They are all prepared first, which fixes every member's next
// identity; then started in binding order with those identities; and if any
// member stops or a dependency outside the unit moves, all are restarted.
func planUnit(members []*appPlanner) []result {
	results := make([]result, len(members))
	in := map[string]bool{}
	for _, m := range members {
		in[m.name] = true
	}
	ready := members[0].ready
	// One member stopped by its owner stops the unit.
	for _, m := range members {
		if m.a.Intent == deployments.Stopped {
			for i, n := range members {
				n.a.Intent = deployments.Stopped
				if r := n.locate(); r != nil {
					results[i] = *r
				}
				if n.name != m.name && results[i].status.State == "stopped" {
					results[i].status.Reason = "stopped with " + m.name
				}
			}
			return results
		}
	}
	located := true
	for i, m := range members {
		if r := m.locate(); r != nil {
			results[i], located = *r, false
		}
	}
	if !located {
		return results // waiting for a node, or stopping another revision
	}
	external := ""
	for _, m := range members {
		for _, d := range m.deps() {
			if _, ok := ready[d.app]; !ok && !in[d.app] && external == "" {
				external = d.app
			}
		}
	}
	// Steady: every member running and pinned to the current identities.
	identities := map[string]apptransport.Binding{}
	for app, id := range ready {
		identities[app] = id
	}
	allRunning := true
	for _, m := range members {
		if !m.running() {
			allRunning = false
			continue
		}
		identities[m.name] = m.liveIdentity(m.target, m.target.Generation)
	}
	if allRunning {
		moved := ""
		for i, m := range members {
			s, dep := m.steady(identities)
			results[i] = result{status: s}
			if dep != "" && moved == "" {
				moved = dep
			}
		}
		if moved == "" && external == "" {
			for i, m := range members {
				live := identities[m.name]
				results[i].live = &live
			}
			return results
		}
		if moved == "" {
			return results // running; an outside dependency is not ready
		}
		return stopUnit(members, results, moved+" restarted")
	}
	// Starting: every member is prepared by us, starting, or already up.
	startup, broken := false, ""
	for _, m := range members {
		t := m.target
		switch {
		case t == nil || t.State == "stopped":
		case t.State == "prepared" && ours(t.StartPreparationID), t.State == "starting":
			startup = true
		case m.running():
		default:
			if broken == "" {
				broken = m.name
			}
		}
	}
	if broken != "" {
		for i, m := range members {
			if m.name == broken {
				results[i] = m.failed()
			}
		}
		return stopUnit(members, results, broken+" failed")
	}
	all := true // every member past preparation: their identities are fixed
	for _, m := range members {
		if t := m.target; t == nil || t.State == "stopped" {
			all = false
		} else if t.State == "prepared" {
			identities[m.name] = m.liveIdentity(t, t.Generation+1)
		} else {
			identities[m.name] = m.liveIdentity(t, t.Generation)
		}
	}
	if !startup {
		// Every member is stopped: a running member alone means the unit broke.
		for _, m := range members {
			if m.running() {
				return stopUnit(members, results, "restarting the unit")
			}
		}
	}
	for i, m := range members {
		t := m.target
		switch {
		case t == nil || t.State == "stopped":
			if r := m.install(); r != nil {
				results[i] = *r
			} else if external != "" {
				results[i] = m.is("waiting", "waiting for "+external)
			} else {
				results[i] = m.prepare()
			}
		case t.State == "prepared":
			if !all {
				results[i] = m.is("starting", "waiting for the unit to be prepared")
				continue
			}
			waiting := ""
			for _, d := range m.deps() {
				if _, known := identities[d.app]; !known {
					waiting = d.app
				}
			}
			for _, b := range m.a.Bindings {
				// A provider is called at start: it must be up, not just prepared.
				if in[b.App] && !members[slices.IndexFunc(members, func(n *appPlanner) bool { return n.name == b.App })].running() {
					waiting = b.App
				}
			}
			if waiting != "" {
				results[i] = m.is("starting", "waiting for "+waiting)
				continue
			}
			results[i] = m.start(identities)
		case t.State == "starting":
			results[i] = m.is("starting", "")
		default: // running, waiting for the rest of the unit
			s, _ := m.steady(identities)
			s.State, s.Reason = "starting", "waiting for the unit"
			results[i] = result{status: s}
		}
	}
	return results
}

// stopUnit stops every member that is not stopped, keeping results that
// already carry a step (e.g. a failed member's stop).
func stopUnit(members []*appPlanner, results []result, reason string) []result {
	for i, m := range members {
		if results[i].step != nil {
			continue
		}
		t := m.target
		if t == nil || (t.State == "stopped" && len(t.Resources) == 0 && len(t.Reservations) == 0) {
			s := results[i].status
			if s.State == "" || s.State == "ready" {
				s = m.status("stopped", "restarting: "+reason)
			}
			results[i] = result{status: s}
			continue
		}
		if t.State == "draining" {
			results[i] = m.is("stopping", reason)
			continue
		}
		r := m.stopOne(m.node, t, reason)
		if results[i].status.State == "failed" {
			r.status = results[i].status
		}
		results[i] = r
	}
	return results
}

// stopOne stops (or cancels the prepared start of) one instance. A blocked
// stop (e.g. its drain hook runs in a process that died with its node) is
// recovered instead, at most once a minute: recovery settles an instance
// whose processes are gone, after which an ordinary stop applies.
func (p *appPlanner) stopOne(node string, in *lifecycle.Instance, reason string) result {
	ledger := p.v.Nodes[node].Ledger
	s := p.status("stopping", reason)
	if in.State == "stop_blocked" || in.State == "recovery_required" {
		id := p.op(StepRecover, node, in.Digest, in.Generation, p.now/60)
		if busy, _ := pending(ledger, id); busy {
			return result{status: s}
		}
		return result{status: s, step: &Step{App: p.name, Kind: StepRecover, Node: node, Variant: Variant{Revision: in.Digest},
			Scope: p.a.Scope, Generation: in.Generation, Instance: in.ID, OpID: id}}
	}
	id := p.op(StepStop, node, in.Digest, in.Generation, 0)
	if busy, _ := pending(ledger, id); busy {
		return result{status: s}
	}
	if op := ledger.Operations[id]; op != nil && op.State == "succeeded" && in.State != "stopped" {
		id = p.op(StepStop, node, in.Digest, in.Generation, p.now/60)
	}
	step := &Step{App: p.name, Kind: StepStop, Node: node, Variant: Variant{Revision: in.Digest}, Scope: p.a.Scope,
		Generation: in.Generation, Instance: in.ID, OpID: id}
	return result{status: s, step: step}
}

// stopAll stops the first live instance among list; stopped when none is live.
func (p *appPlanner) stopAll(list []located, reason string) result {
	for _, l := range list {
		if l.in.State == "stopped" && len(l.in.Resources) == 0 && len(l.in.Reservations) == 0 {
			continue
		}
		if l.in.State == "draining" {
			return p.is("stopping", reason)
		}
		return p.stopOne(l.node, l.in, reason)
	}
	s := p.status("stopped", "")
	s.Grants, s.Providers, s.Renewed = nil, nil, 0
	return result{status: s}
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
		if _, online := p.v.Nodes[current]; !online {
			return "", "waiting for node " + current + " to return"
		}
		// Stay where the App has run (its data is there). A node it was only
		// assigned to — e.g. after a failover, before it could start — does not
		// hold it: the rule places it again (back on the brain when it returns).
		ran := false
		for _, l := range p.all {
			if l.node == current {
				ran = true
			}
		}
		if ran && eligible(current) {
			return current, ""
		}
	}
	var prefer []string
	for _, variant := range variants {
		prefer = variant.Prefer
	}
	prefer = append(append([]string{}, p.a.Placement.Prefer...), prefer...)
	providers := map[string]bool{}
	for _, d := range p.deps() {
		if provider, ok := p.ready[d.app]; ok {
			providers[provider.Node] = true
		}
	}
	var best string
	var bestKey [3]int
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
		key := [3]int{preferred, colocated, len(n.Caps)}
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
