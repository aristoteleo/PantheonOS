package reconciler

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
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
		in.ReadyGeneration = gen
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
