package reconciler

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
	"github.com/aristoteleo/pantheon-fleet/internal/hosted"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

const (
	revAlloc   = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	revAgent   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	revFiles   = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
	revAgentV2 = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
)

func testRelease(agentRev string) *Release {
	v := func(id, rev string, requires ...string) map[string]Variant {
		return map[string]Variant{"linux-amd64": {AppID: id, Version: "1.0.0", Revision: rev, Bytes: 1, Artifact: "artifacts/" + rev, Requires: requires}}
	}
	return &Release{Index: Index{Protocol: 2, Apps: map[string]map[string]Variant{
		"allocator": v("pantheon-allocator", revAlloc),
		"agent":     v("pantheon-agent", agentRev),
		"files":     v("pantheon-files", revFiles, "fs:workspace"),
	}}}
}

func testDeployment() deployments.Deployment {
	app := func(pkg string) deployments.AppSpec {
		return deployments.AppSpec{Package: pkg, Scope: "deployment", Intent: deployments.Running}
	}
	agent := app("agent")
	agent.Bindings = map[string]deployments.Binding{"allocator": {App: "allocator", Component: "backend", Methods: json.RawMessage(`{}`)}}
	return deployments.Deployment{Fleet: "f_1", Name: "general-team", Revision: 3, Spec: deployments.Spec{
		Apps: map[string]deployments.AppSpec{"allocator": app("allocator"), "agent": agent, "files": app("files")}}}
}

type world struct {
	view View
}

func newWorld() *world {
	empty := func(id string) *lifecycle.Ledger {
		return &lifecycle.Ledger{Owner: "f_1", Node: id, Installations: map[string]*lifecycle.Installation{},
			Instances: map[string]*lifecycle.Instance{}, Operations: map[string]*lifecycle.Operation{}}
	}
	return &world{View{Fleet: "f_1", Now: time.Unix(1_000_000, 0), Lost: map[string]bool{}, Nodes: map[string]NodeView{
		"brain":   {ID: "brain", Kind: "pod", Platform: "linux-amd64", Caps: []string{"proc"}, Ledger: empty("brain")},
		"sandbox": {ID: "sandbox", Kind: "sandbox", Platform: "linux-amd64", Caps: []string{"proc", "fs:workspace", "net"}, Ledger: empty("sandbox")},
	}}}
}

func (w *world) install(node string, revs ...string) {
	for _, rev := range revs {
		w.view.Nodes[node].Ledger.Installations[rev] = &lifecycle.Installation{Digest: rev, State: "installed"}
	}
}

func (w *world) instance(node, id, appID, rev, state string, gen uint64) *lifecycle.Instance {
	in := &lifecycle.Instance{ID: id, AppID: appID, Digest: rev, Scope: "deployment", State: state, Generation: gen}
	if state == "ready" {
		// A settled App: the deployment already marked it kept alive.
		in.ReadyGeneration, in.KeepAlive = gen, true
	}
	w.view.Nodes[node].Ledger.Instances[id] = in
	return in
}

func steps(p Plan) map[string]Step {
	out := map[string]Step{}
	for _, s := range p.Steps {
		out[s.App] = s
	}
	return out
}

func TestFreshDeploymentInstallsEachAppWhereItFits(t *testing.T) {
	w := newWorld()
	p := PlanDeployment(testDeployment(), testRelease(revAgent), w.view)
	got := steps(p)
	for app, node := range map[string]string{"allocator": "brain", "agent": "brain", "files": "sandbox"} {
		if got[app].Kind != StepInstall || got[app].Node != node {
			t.Fatalf("%s: want install on %s, got %+v", app, node, got[app])
		}
		if !ours(got[app].OpID) {
			t.Fatalf("operation id %q is not ours", got[app].OpID)
		}
	}
	if p.Status.Conditions[0].Status {
		t.Fatal("not ready yet")
	}
}

func TestRevisionRemovedAfterInstallIsInstalledAgainUnderANewOperation(t *testing.T) {
	w := newWorld()
	first := steps(PlanDeployment(testDeployment(), testRelease(revAgent), w.view))["files"]
	// The install succeeded, then the node's garbage collection removed the
	// revision while the App was not running.
	w.view.Nodes["sandbox"].Ledger.Operations[first.OpID] = &lifecycle.Operation{State: "succeeded"}
	again := steps(PlanDeployment(testDeployment(), testRelease(revAgent), w.view))["files"]
	if again.Kind != StepInstall || again.OpID == first.OpID || !ours(again.OpID) {
		t.Fatalf("reinstall under a new operation: first %q, again %+v", first.OpID, again)
	}
}

func TestPrepareWithUnknownOutcomeIsRetriedUnderANewOperation(t *testing.T) {
	w := newWorld()
	w.install("sandbox", revFiles)
	first := steps(PlanDeployment(testDeployment(), testRelease(revAgent), w.view))["files"]
	if first.Kind != StepPrepare {
		t.Fatalf("prepare first: %+v", first)
	}
	// The Runner restarted before acknowledging the preparation.
	w.view.Nodes["sandbox"].Ledger.Operations[first.OpID] = &lifecycle.Operation{State: "unknown",
		Error: "Runner restarted before operation acknowledgement; reconcile before retrying"}
	d := testDeployment()
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := p.Status.Apps["files"]; s.State != "failed" || s.Attempts != 1 {
		t.Fatalf("unknown outcome counts as a failed attempt: %+v", s)
	}
	d.Status = p.Status
	w.view.Now = w.view.Now.Add(time.Hour)
	again := steps(PlanDeployment(d, testRelease(revAgent), w.view))["files"]
	if again.Kind != StepPrepare || again.OpID == first.OpID {
		t.Fatalf("retried under a new operation: %+v", again)
	}
}

func TestConsumerWaitsForProviderThenStartsPinnedToIt(t *testing.T) {
	w := newWorld()
	w.install("brain", revAlloc, revAgent)
	w.install("sandbox", revFiles)
	d := testDeployment()
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	got := steps(p)
	if got["allocator"].Kind != StepPrepare || got["allocator"].Generation != 0 {
		t.Fatalf("allocator: %+v", got["allocator"])
	}
	if _, ok := got["agent"]; ok || !strings.Contains(p.Status.Apps["agent"].Reason, "waiting for allocator") {
		t.Fatalf("agent must wait: %+v %+v", got["agent"], p.Status.Apps["agent"])
	}

	// Allocator prepared by us -> start; then ready -> agent prepares, starts.
	prep := got["allocator"].OpID
	in := w.instance("brain", "i-alloc", "pantheon-allocator", revAlloc, "prepared", 1)
	in.StartPreparationID = prep
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["allocator"]; s.Kind != StepStart || s.Preparation != prep || s.Generation != 1 {
		t.Fatalf("allocator start: %+v", s)
	}
	w.instance("brain", "i-alloc", "pantheon-allocator", revAlloc, "ready", 2)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	agentPrep := steps(p)["agent"]
	if agentPrep.Kind != StepPrepare {
		t.Fatalf("agent prepare: %+v", agentPrep)
	}
	agent := w.instance("brain", "i-agent", "pantheon-agent", revAgent, "prepared", 1)
	agent.StartPreparationID = agentPrep.OpID
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	start := steps(p)["agent"]
	if start.Kind != StepStart || start.Providers["allocator"].Instance != "i-alloc" || start.Providers["allocator"].Generation != 2 {
		t.Fatalf("agent start: %+v", start)
	}
}

func readyWorld(t *testing.T) (*world, deployments.Deployment) {
	w := newWorld()
	w.install("brain", revAlloc, revAgent)
	w.install("sandbox", revFiles)
	w.instance("brain", "i-alloc", "pantheon-allocator", revAlloc, "ready", 2)
	w.instance("brain", "i-agent", "pantheon-agent", revAgent, "ready", 2)
	w.instance("sandbox", "i-files", "pantheon-files", revFiles, "ready", 2)
	d := testDeployment()
	d.Status.Apps = map[string]deployments.AppStatus{
		"allocator": {NodeID: "brain", InstanceID: "i-alloc", Generation: 2, State: "ready"},
		"agent":     {NodeID: "brain", InstanceID: "i-agent", Generation: 2, State: "ready", Providers: map[string]string{"allocator": "brain/i-alloc/2"}},
		"files":     {NodeID: "sandbox", InstanceID: "i-files", Generation: 2, State: "ready"},
	}
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if len(p.Steps) != 0 || !p.Status.Conditions[0].Status {
		t.Fatalf("converged deployment must be quiet and ready: %+v %+v", p.Steps, p.Status)
	}
	return w, d
}

func TestProviderRestartRestartsConsumer(t *testing.T) {
	w, d := readyWorld(t)
	w.instance("brain", "i-alloc", "pantheon-allocator", revAlloc, "ready", 4)
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["agent"]; s.Kind != StepStop || s.Instance != "i-agent" || s.Generation != 2 {
		t.Fatalf("agent must restart on the new provider: %+v", s)
	}
}

func TestOwnerStopStopsOnlyThatApp(t *testing.T) {
	w, d := readyWorld(t)
	a := d.Spec.Apps["files"]
	a.Intent = deployments.Stopped
	d.Spec.Apps["files"] = a
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if len(p.Steps) != 1 || p.Steps[0].App != "files" || p.Steps[0].Kind != StepStop || p.Steps[0].Variant.Revision != revFiles {
		t.Fatalf("only files stops: %+v", p.Steps)
	}
	w.instance("sandbox", "i-files", "pantheon-files", revFiles, "stopped", 2)
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if len(p.Steps) != 0 || p.Status.Apps["files"].State != "stopped" || !p.Status.Conditions[0].Status {
		t.Fatalf("stopped App is settled: %+v %+v", p.Steps, p.Status)
	}
}

func TestFailedStartBacksOffThenRetriesWithANewOperation(t *testing.T) {
	w, d := readyWorld(t)
	in := w.instance("sandbox", "i-files", "pantheon-files", revFiles, "failed", 3)
	in.Error = "readiness probe failed"
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	s := p.Status.Apps["files"]
	if steps(p)["files"].Kind != StepStop || s.Attempts != 1 || s.NextAttempt != w.view.Now.Unix()+10 {
		t.Fatalf("failed instance is stopped and backed off: %+v %+v", steps(p), s)
	}
	// Seen again before it stopped: the failure is not counted twice.
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if p.Status.Apps["files"].Attempts != 1 {
		t.Fatalf("counted twice: %+v", p.Status.Apps["files"])
	}
	w.instance("sandbox", "i-files", "pantheon-files", revFiles, "stopped", 3)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if _, ok := steps(p)["files"]; ok || p.Status.Apps["files"].State != "backoff" {
		t.Fatalf("holds during backoff: %+v", p.Status.Apps["files"])
	}
	w.view.Now = w.view.Now.Add(11 * time.Second)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["files"]; s.Kind != StepPrepare || s.Generation != 3 {
		t.Fatalf("retries after backoff: %+v", s)
	}
}

func TestNodeLossWaitsForGraceThenReplaces(t *testing.T) {
	w, d := readyWorld(t)
	delete(w.view.Nodes, "brain")
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if _, ok := steps(p)["allocator"]; ok || !strings.Contains(p.Status.Apps["allocator"].Reason, "to return") {
		t.Fatalf("within grace the App waits for its node: %+v", p.Status.Apps["allocator"])
	}
	w.view.Lost["brain"] = true
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["allocator"]; s.Kind != StepInstall || s.Node != "sandbox" {
		t.Fatalf("lost node: re-placed on the remaining node: %+v", s)
	}
}

func TestForeignPreparedStartIsCancelled(t *testing.T) {
	w := newWorld()
	w.install("brain", revAlloc)
	in := w.instance("brain", "i-alloc", "pantheon-allocator", revAlloc, "prepared", 1)
	in.StartPreparationID = "preset-manual-123"
	p := PlanDeployment(testDeployment(), testRelease(revAgent), w.view)
	if s := steps(p)["allocator"]; s.Kind != StepStop || s.Instance != "i-alloc" {
		t.Fatalf("foreign hold is cancelled: %+v", s)
	}
}

func TestReleaseUpdateStopsOldRevisionThenCopiesItsData(t *testing.T) {
	w, d := readyWorld(t)
	p := PlanDeployment(d, testRelease(revAgentV2), w.view)
	if s := steps(p)["agent"]; s.Kind != StepStop || s.Variant.Revision != revAgent {
		t.Fatalf("old revision stops first: %+v", s)
	}
	w.instance("brain", "i-agent", "pantheon-agent", revAgent, "stopped", 2)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgentV2), w.view)
	if s := steps(p)["agent"]; s.Kind != StepInstall || s.Variant.Revision != revAgentV2 {
		t.Fatalf("then installs the new revision: %+v", s)
	}
	w.install("brain", revAgentV2)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgentV2), w.view)
	s := steps(p)["agent"]
	if s.Kind != StepCloneData || s.Source == nil || s.Source.Digest != revAgent || s.Source.Generation != 2 {
		t.Fatalf("then copies the old data: %+v", s)
	}
}

// Generations count per instance: an older revision restarted more often has
// the higher one. The copy comes from the revision the App last ran.
func TestReleaseUpdateCopiesTheRevisionItLastRanNotTheHighestGeneration(t *testing.T) {
	w, d := readyWorld(t)
	const revOld = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
	w.install("brain", revOld)
	w.instance("brain", "i-old", "pantheon-agent", revOld, "stopped", 5)
	p := PlanDeployment(d, testRelease(revAgentV2), w.view)
	w.instance("brain", "i-agent", "pantheon-agent", revAgent, "stopped", 2)
	for _, rev := range []string{"", revAgentV2} {
		if rev != "" {
			w.install("brain", rev)
		}
		d.Status = p.Status
		p = PlanDeployment(d, testRelease(revAgentV2), w.view)
	}
	if s := steps(p)["agent"]; s.Kind != StepCloneData || s.Source == nil || s.Source.Digest != revAgent {
		t.Fatalf("copies the revision it last ran: %+v", s)
	}
	if p.Status.Apps["agent"].DataFrom != revAgent {
		t.Fatalf("the status records it: %+v", p.Status.Apps["agent"])
	}
}

func TestPinnedPlacementWaitsForItsNode(t *testing.T) {
	w := newWorld()
	d := testDeployment()
	a := d.Spec.Apps["agent"]
	a.Placement.Node = "mac"
	d.Spec.Apps["agent"] = a
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if _, ok := steps(p)["agent"]; ok || p.Status.Apps["agent"].Reason != "waiting for node mac" {
		t.Fatalf("pinned App waits: %+v", p.Status.Apps["agent"])
	}
}

func TestSingleMachineTakesEverything(t *testing.T) {
	w := newWorld()
	delete(w.view.Nodes, "brain")
	p := PlanDeployment(testDeployment(), testRelease(revAgent), w.view)
	for app, s := range steps(p) {
		if s.Node != "sandbox" {
			t.Fatalf("%s placed on %s", app, s.Node)
		}
	}
	if len(p.Steps) != 3 {
		t.Fatalf("all three install: %+v", p.Steps)
	}
}

func TestBlockedStopIsRecoveredNotRetried(t *testing.T) {
	w, d := readyWorld(t)
	in := w.instance("sandbox", "i-files", "pantheon-files", revFiles, "stop_blocked", 2)
	in.Error = "hook process is not running"
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["files"]; s.Kind != StepRecover || s.Instance != "i-files" {
		t.Fatalf("blocked stop is recovered: %+v", s)
	}
}

func TestDeferredStopIsAskedAgain(t *testing.T) {
	w, d := readyWorld(t)
	in := w.instance("sandbox", "i-files", "pantheon-files", revFiles, "stop_blocked", 2)
	in.Error = "before_stop: waiting: App calls are still draining"
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	first := steps(p)["files"]
	if first.Kind != StepStop || first.Instance != "i-files" {
		t.Fatalf("a deferred stop is asked again: %+v", first)
	}
	// The same minute does not ask twice; the next one asks again.
	w.view.Nodes["sandbox"].Ledger.Operations[first.OpID] = &lifecycle.Operation{State: "running"}
	if s, ok := steps(PlanDeployment(d, testRelease(revAgent), w.view))["files"]; ok {
		t.Fatalf("one stop at a time: %+v", s)
	}
	w.view.Nodes["sandbox"].Ledger.Operations[first.OpID].State = "failed"
	d.Status = p.Status
	next := w.view
	next.Now = w.view.Now.Add(time.Minute)
	if s := steps(PlanDeployment(d, testRelease(revAgent), next))["files"]; s.Kind != StepStop || s.OpID == first.OpID {
		t.Fatalf("asked again a minute later: %+v", s)
	}
}

func TestMovedAppCarriesItsStateFromTheNodeItLeft(t *testing.T) {
	w, d := readyWorld(t)
	a := d.Spec.Apps["agent"]
	a.Placement.Node = "sandbox"
	d.Spec.Apps["agent"] = a
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["agent"]; s.Kind != StepStop || s.Node != "brain" {
		t.Fatalf("stops on the old node first: %+v", s)
	}
	w.instance("brain", "i-agent", "pantheon-agent", revAgent, "stopped", 2)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["agent"]; s.Kind != StepInstall || s.Node != "sandbox" {
		t.Fatalf("installs on the new node: %+v", s)
	}
	w.install("sandbox", revAgent)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	s := steps(p)["agent"]
	if s.Kind != StepMoveData || s.SourceNode != "brain" || s.Source.Generation != 2 || s.Node != "sandbox" {
		t.Fatalf("moves the stopped state: %+v", s)
	}
}

func TestUnstartedFailoverPlacementReturnsToTheBestNode(t *testing.T) {
	w, d := readyWorld(t)
	// After a failover the allocator was assigned to the sandbox but never
	// started there; its data is still on the brain.
	w.instance("brain", "i-alloc", "pantheon-allocator", revAlloc, "stopped", 2)
	s := d.Status.Apps["allocator"]
	s.NodeID = "sandbox"
	d.Status.Apps["allocator"] = s
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if got := p.Status.Apps["allocator"].NodeID; got != "brain" {
		t.Fatalf("placed by the rule again: %s", got)
	}
	// Once it ran on the sandbox, it stays there while no better node is online...
	w.install("sandbox", revAlloc)
	w.instance("sandbox", "i-alloc-s", "pantheon-allocator", revAlloc, "stopped", 3)
	brain := w.view.Nodes["brain"]
	delete(w.view.Nodes, "brain")
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if got := p.Status.Apps["allocator"].NodeID; got != "sandbox" {
		t.Fatalf("an App stays where it ran: %s", got)
	}
	// ...and moves home, with its data, once the brain is back.
	w.view.Nodes["brain"] = brain
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if got := p.Status.Apps["allocator"].NodeID; got != "brain" {
		t.Fatalf("an App returns to the node the rule ranks first: %s", got)
	}
}

func TestReadyAppIsKeptFromTheNodeIdleStop(t *testing.T) {
	w, d := readyWorld(t)
	w.view.Nodes["brain"].Ledger.Instances["i-agent"].KeepAlive = false
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	s := steps(p)["agent"]
	if s.Kind != StepKeepAlive || s.Node != "brain" || s.Instance != "i-agent" || s.Generation != 2 || s.Variant.Revision != revAgent {
		t.Fatalf("keep_alive: %+v", s)
	}
	if p.Status.Apps["agent"].State != "ready" || !p.Status.Conditions[0].Status {
		t.Fatalf("keeping alive must not unready the App: %+v", p.Status)
	}
}

func TestHostedAppIsReadyAtItsFixedIdentityAndConsumersStartAgainstIt(t *testing.T) {
	w := newWorld()
	w.install("brain", revAgent)
	d := testDeployment()
	d.Spec.Apps["allocator"] = deployments.AppSpec{Hosted: "dependency-binding", Scope: "allocator", Intent: deployments.Running}
	if err := d.Spec.Validate(); err != nil && !strings.Contains(err.Error(), "release") {
		t.Fatal(err)
	}
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	s := p.Status.Apps["allocator"]
	want, _ := hosted.Binding("f_1", "general-team", "allocator", "dependency-binding")
	if s.State != "ready" || s.NodeID != hosted.Node || s.InstanceID != want.Instance || s.Revision != want.Revision || s.Generation != 1 {
		t.Fatalf("hosted status: %+v", s)
	}
	if _, ok := steps(p)["allocator"]; ok {
		t.Fatal("a hosted App has nothing to install or start")
	}
	// The Agent no longer waits for an allocator process: it prepares at once.
	if got := steps(p)["agent"]; got.Kind != StepPrepare {
		t.Fatalf("agent: %+v %+v", got, p.Status.Apps["agent"])
	}
	// The package it replaces is stopped on its node.
	w.instance("brain", "i-old", "dependency-binding", revAlloc, "ready", 4).Scope = "allocator"
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if got := steps(p)["allocator"]; got.Kind != StepStop || got.Instance != "i-old" || p.Status.Apps["allocator"].State != "ready" {
		t.Fatalf("former package: %+v %+v", got, p.Status.Apps["allocator"])
	}
	d.Spec.Apps["allocator"] = deployments.AppSpec{Hosted: "dependency-binding", Scope: "allocator", Intent: deployments.Stopped}
	if s := PlanDeployment(d, testRelease(revAgent), w.view).Status.Apps["allocator"]; s.State != "stopped" {
		t.Fatalf("stopped hosted App: %+v", s)
	}
	d.Spec.Apps["allocator"] = deployments.AppSpec{Hosted: "nothing-here", Scope: "allocator", Intent: deployments.Running}
	if s := PlanDeployment(d, testRelease(revAgent), w.view).Status.Apps["allocator"]; s.State != "blocked" {
		t.Fatalf("unknown hosted service: %+v", s)
	}
}

const revFork = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

// installOwn records a package the owner installed on a node themselves.
func (w *world) installOwn(node, rev, appID string) {
	w.view.Nodes[node].Ledger.Installations[rev] = &lifecycle.Installation{Digest: rev, State: "installed",
		Definition: lifecycle.Definition{AppID: appID, Version: "1.0.1"}}
}

func TestOwnersInstalledPackageReplacesTheReleasePackageAndCarriesItsData(t *testing.T) {
	w, d := readyWorld(t)
	a := d.Spec.Apps["agent"]
	a.Digest = revFork
	d.Spec.Apps["agent"] = a
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := p.Status.Apps["agent"]; s.State != "blocked" || !strings.Contains(s.Reason, "not installed") {
		t.Fatalf("waits for the package without touching the running Agent: %+v", s)
	}
	if _, ok := steps(p)["agent"]; ok {
		t.Fatal("no step while the package is missing")
	}
	w.installOwn("brain", revFork, "pantheon-files")
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := p.Status.Apps["agent"]; s.State != "blocked" || !strings.Contains(s.Reason, "is pantheon-files, not pantheon-agent") {
		t.Fatalf("another App's package is refused: %+v", s)
	}
	w.installOwn("brain", revFork, "pantheon-agent")
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["agent"]; s.Kind != StepStop || s.Variant.Revision != revAgent {
		t.Fatalf("the release package stops first: %+v", s)
	}
	w.instance("brain", "i-agent", "pantheon-agent", revAgent, "stopped", 3)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	s := steps(p)["agent"]
	if s.Kind != StepCloneData || s.Variant.Revision != revFork || s.Source == nil || s.Source.Digest != revAgent {
		t.Fatalf("then the owner's package starts from its data, with no install: %+v", s)
	}
}

func TestOwnersPackageRunsOnlyWhereItIsInstalledAndIsNeverInstalledFromTheRelease(t *testing.T) {
	w, d := readyWorld(t)
	a := d.Spec.Apps["agent"]
	a.Digest, a.Placement.Node = revFork, "brain"
	d.Spec.Apps["agent"] = a
	w.installOwn("sandbox", revFork, "pantheon-agent")
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := p.Status.Apps["agent"]; s.State != "waiting" || !strings.Contains(s.Reason, "cannot run") {
		t.Fatalf("a pinned node without the package cannot take it: %+v", s)
	}
	if s, ok := steps(p)["agent"]; ok && s.Kind == StepInstall {
		t.Fatalf("never installs from the release set: %+v", s)
	}
}

func TestReturningToTheReleasePackageResumesItsOwnData(t *testing.T) {
	w, d := readyWorld(t)
	w.installOwn("brain", revFork, "pantheon-agent")
	w.instance("brain", "i-agent", "pantheon-agent", revAgent, "stopped", 3)
	w.instance("brain", "i-fork", "pantheon-agent", revFork, "ready", 2)
	st := d.Status.Apps["agent"]
	st.InstanceID, st.Revision = "i-fork", revFork
	d.Status.Apps["agent"] = st
	p := PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["agent"]; s.Kind != StepStop || s.Instance != "i-fork" {
		t.Fatalf("the owner's package stops: %+v", s)
	}
	w.instance("brain", "i-fork", "pantheon-agent", revFork, "stopped", 3)
	d.Status = p.Status
	p = PlanDeployment(d, testRelease(revAgent), w.view)
	if s := steps(p)["agent"]; s.Kind != StepPrepare || s.Instance != "i-agent" {
		t.Fatalf("the release package resumes its own instance: %+v", s)
	}
}

func TestDigestIsAPackageHashAndOnlyForPackages(t *testing.T) {
	d := testDeployment()
	d.Spec.Release = deployments.Release{URL: "https://example.test/set.tar.gz", SHA256: revAlloc}
	a := d.Spec.Apps["agent"]
	a.Digest = "not-a-digest"
	d.Spec.Apps["agent"] = a
	if err := d.Spec.Validate(); err == nil || !strings.Contains(err.Error(), "digest") {
		t.Fatalf("invalid digest: %v", err)
	}
	a.Digest = revFork
	d.Spec.Apps["agent"] = a
	if err := d.Spec.Validate(); err != nil {
		t.Fatal(err)
	}
	d.Spec.Apps["fleet"] = deployments.AppSpec{Hosted: "fleet", Digest: revFork, Scope: "fleet", Intent: deployments.Running}
	if err := d.Spec.Validate(); err == nil {
		t.Fatal("a hosted App has no package digest")
	}
}

func TestARunningAppCollectsWhatAReleaseUpdateLeft(t *testing.T) {
	w, d := readyWorld(t)
	ledger := w.view.Nodes["brain"].Ledger
	revOld, revOlder := strings.Repeat("e", 64), strings.Repeat("f", 64)
	stopped := func(id, rev string, at time.Time) {
		ledger.Installations[rev] = &lifecycle.Installation{Digest: rev, State: "installed",
			Definition: lifecycle.Definition{AppID: "pantheon-agent"}}
		ledger.Operations["stop-"+rev] = &lifecycle.Operation{State: "succeeded", CreatedAt: at,
			Request: lifecycle.Request{Action: "stop", Digest: rev}}
		w.instance("brain", id, "pantheon-agent", rev, "stopped", 2)
	}
	stopped("i-old", revOld, w.view.Now.Add(-2*time.Hour))
	stopped("i-older", revOlder, w.view.Now.Add(-3*time.Hour))
	s := steps(PlanDeployment(d, testRelease(revAgent), w.view))["agent"]
	if s.Kind != StepUninstall || s.Variant.Revision != revOlder || s.Scope != "deployment" || s.Node != "brain" {
		t.Fatalf("the older superseded installation is collected: %+v", s)
	}
	// The newest stopped revision stays: the data source and rollback target.
	ledger.Installations[revOlder].State = "absent"
	if s, ok := steps(PlanDeployment(d, testRelease(revAgent), w.view))["agent"]; ok {
		t.Fatalf("the newest stopped revision stays installed: %+v", s)
	}
	// Nothing operated on recently, and nothing another scope uses.
	ledger.Installations[revOlder].State = "installed"
	ledger.Operations["stop-"+revOlder].CreatedAt = w.view.Now.Add(-5 * time.Minute)
	ledger.Operations["stop-"+revOld].CreatedAt = w.view.Now.Add(-time.Minute)
	if s, ok := steps(PlanDeployment(d, testRelease(revAgent), w.view))["agent"]; ok {
		t.Fatalf("a recent installation may be an install waiting for its start: %+v", s)
	}
	ledger.Operations["stop-"+revOlder].CreatedAt = w.view.Now.Add(-3 * time.Hour)
	ledger.Instances["i-older"].Scope = "someone-else"
	if s, ok := steps(PlanDeployment(d, testRelease(revAgent), w.view))["agent"]; ok {
		t.Fatalf("an installation another scope uses stays: %+v", s)
	}
}
