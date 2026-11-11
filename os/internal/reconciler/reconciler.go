package reconciler

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/appgateway"
	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/aristoteleo/pantheonos/os/internal/hosted"
	"github.com/aristoteleo/pantheonos/os/internal/lifecycle"
	"github.com/aristoteleo/pantheonos/os/internal/modelcredentials"
	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

// Transport reaches a fleet's registry and its nodes' App lifecycle protocol.
type Transport interface {
	Nodes(ctx context.Context, fleet string) ([]proto.Node, error)
	// Call sends one app_lifecycle command and decodes its reply into out. A
	// reply carrying only {"error": ...} is returned as an error.
	Call(ctx context.Context, fleet, node string, command map[string]any, out any) error
}

// Grants is the controller's dependency grant authority (appgateway.Gateway).
type Grants interface {
	IssueDependency(ctx context.Context, q appgateway.DependencyRequest) (map[string]any, error)
	RenewDependency(ctx context.Context, fleet, id string, expires int64) (map[string]any, error)
	RevokeDependency(fleet, id string) error
}

type Reconciler struct {
	Store    *deployments.Store
	Secrets  *deployments.Secrets
	Releases *Releases
	Nodes    Transport
	Grants   Grants // nil: deployments with bindings cannot start
	// Grace before an absent node counts as lost (default 90 s).
	Grace time.Duration
	// Busy deployments are revisited every Tick, settled ones every Settled.
	// A pass that made progress is followed after Next (default 250 ms): a
	// start goes reconcile, prepare, configure and start in successive
	// passes, and a Tick between each made a cold node seconds slower.
	// While an App waits on an operation its node is carrying out (an
	// install, a start), the deployment is revisited every Busy (default
	// 400 ms) for the first BusyFor (default 2 min) of that state, so the next
	// step follows the operation's end instead of the next Tick.
	Tick, Settled, Next, Busy, BusyFor time.Duration
	// CallTimeout bounds one node call a step makes (default 20 s).
	CallTimeout time.Duration
	Now         func() time.Time

	mu       sync.Mutex
	kicks    chan string          // fleets whose nodes just connected
	missing  map[string]time.Time // fleet/node -> first seen absent
	inflight map[string]bool
	last     map[string]time.Time
	settled  map[string]bool
}

const grantTTL = 15 * time.Minute

// startPreparationExpired marks a start whose dependency grants expired before
// it could run; the planner then cancels that preparation and prepares again.
const startPreparationExpired = "start preparation expired"

// startPreparationStale marks a start whose preparation already holds an
// authorization or configuration for other dependencies (a provider was
// replaced between its grant or configure and its start); like an expired
// one, it is cancelled and prepared again.
const startPreparationStale = "start preparation is stale"

// preparationEnded reports a start step failure that retrying the same
// preparation can never fix.
func preparationEnded(reason string) bool {
	return strings.Contains(reason, startPreparationExpired) || strings.Contains(reason, startPreparationStale)
}

func (r *Reconciler) defaults() {
	if r.Grace == 0 {
		r.Grace = 90 * time.Second
	}
	if r.Tick == 0 {
		r.Tick = 5 * time.Second
	}
	if r.Settled == 0 {
		r.Settled = 30 * time.Second
	}
	if r.Next == 0 {
		r.Next = 250 * time.Millisecond
	}
	if r.Busy == 0 {
		r.Busy = 400 * time.Millisecond
	}
	if r.BusyFor == 0 {
		r.BusyFor = 2 * time.Minute
	}
	if r.CallTimeout == 0 {
		r.CallTimeout = 20 * time.Second
	}
	if r.Now == nil {
		r.Now = time.Now
	}
	r.mu.Lock()
	if r.missing == nil {
		r.missing, r.inflight, r.last, r.settled = map[string]time.Time{}, map[string]bool{}, map[string]time.Time{}, map[string]bool{}
	}
	if r.kicks == nil {
		r.kicks = make(chan string, 64)
	}
	r.mu.Unlock()
}

// kickAfter is when a fleet's deployments are passed again after one of its
// nodes connected: at once, then while the node is still registering (its
// registry record follows its credential by about a second) and announcing
// its ledger.
var kickAfter = []time.Duration{0, time.Second, 2 * time.Second, 4 * time.Second}

// Kick passes a fleet's deployments now and over the next seconds: one of its
// nodes has just connected (a brain pod back, a Workspace resumed), so an App
// waiting for that node can be placed at once instead of on the next Tick.
func (r *Reconciler) Kick(fleet string) {
	r.defaults()
	for _, after := range kickAfter {
		time.AfterFunc(after, func() {
			select {
			case r.kicks <- fleet:
			default:
			}
		})
	}
}

// transitional reports whether an App of the status waits on an operation
// its node is carrying out, and has for less than BusyFor.
func (r *Reconciler) transitional(status deployments.Status) bool {
	now := r.Now().Unix()
	for _, a := range status.Apps {
		switch a.State {
		case "starting", "installing", "recovering", "stopping":
			if now-a.Since < int64(r.BusyFor/time.Second) {
				return true
			}
		}
	}
	return false
}

// Run reconciles until ctx ends: on every deployment write, and periodically.
func (r *Reconciler) Run(ctx context.Context) {
	r.defaults()
	tick := time.NewTicker(r.Tick)
	defer tick.Stop()
	slots := make(chan struct{}, 8)
	next := make(chan string, 64)
	for {
		changed := map[string]bool{}
		select {
		case <-ctx.Done():
			return
		case k := <-r.Store.Changes():
			changed[k] = true
		case k := <-next:
			changed[k] = true
		case fleet := <-r.kicks:
			changed["fleet:"+fleet] = true
		case <-tick.C:
		}
		for _, d := range r.Store.All() {
			k := d.Fleet + "/" + d.Name
			if changed["fleet:"+d.Fleet] {
				changed[k] = true
			}
			r.mu.Lock()
			due := changed[k] || !r.settled[k] || r.Now().Sub(r.last[k]) >= r.Settled
			if !due || r.inflight[k] {
				r.mu.Unlock()
				continue
			}
			r.inflight[k] = true
			r.mu.Unlock()
			slots <- struct{}{}
			go func(d deployments.Deployment) {
				defer func() { <-slots }()
				pctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
				settled, progressed, err := r.pass(pctx, d.Fleet, d.Name)
				cancel()
				if err != nil {
					log.Printf("[reconcile] %s/%s: %v", d.Fleet, d.Name, err)
				}
				r.mu.Lock()
				delete(r.inflight, k)
				r.last[k], r.settled[k] = r.Now(), settled && err == nil
				r.mu.Unlock()
				again := time.Duration(0)
				if progressed && !settled {
					again = r.Next
				} else if !settled && err == nil {
					if current, err := r.Store.Get(d.Fleet, d.Name); err == nil && r.transitional(current.Status) {
						again = r.Busy
					}
				}
				if again > 0 {
					time.AfterFunc(again, func() {
						select {
						case next <- k:
						default:
						}
					})
				}
			}(d)
		}
	}
}

// Pass observes, plans, executes and records one deployment. It reports
// whether the deployment is settled (nothing to do, everything as intended).
func (r *Reconciler) Pass(ctx context.Context, fleet, name string) (bool, error) {
	settled, _, err := r.pass(ctx, fleet, name)
	return settled, err
}

// pass is Pass, also reporting whether any step was carried out.
func (r *Reconciler) pass(ctx context.Context, fleet, name string) (settled, progressed bool, err error) {
	r.defaults()
	d, err := r.Store.Get(fleet, name)
	if err != nil {
		return true, false, nil // deleted meanwhile
	}
	record := func(status deployments.Status) error {
		if err := r.Store.SetStatus(fleet, name, d.Revision, status); err != nil && !errors.Is(err, deployments.ErrConflict) {
			return err
		}
		return nil
	}
	fail := func(reason string) (bool, bool, error) {
		status := d.Status
		status.ObservedRevision = d.Revision
		status.Conditions = []deployments.Condition{{Type: "Ready", Reason: reason, Since: r.Now().Unix()}}
		return false, false, errors.Join(errors.New(reason), record(status))
	}
	release, err := r.Releases.Get(ctx, d.Spec.Release.URL, d.Spec.Release.SHA256)
	if err != nil {
		return fail("release set unavailable: " + err.Error())
	}
	view, err := r.observe(ctx, d)
	if err != nil {
		return fail("cannot read the fleet: " + err.Error())
	}
	plan := PlanDeployment(d, release, view)
	status := plan.Status

	var mu sync.Mutex
	var wg sync.WaitGroup
	for _, step := range plan.Steps {
		wg.Add(1)
		go func(step Step) {
			defer wg.Done()
			var update func(*deployments.AppStatus)
			err := r.execute(ctx, d, release, view, step, &update)
			mu.Lock()
			defer mu.Unlock()
			s := status.Apps[step.App]
			if update != nil {
				update(&s)
			}
			if err != nil {
				s.Reason = string(step.Kind) + ": " + err.Error()
				log.Printf("[reconcile] %s/%s %s %s on %s: %v", fleet, name, step.App, step.Kind, step.Node, err)
			} else {
				progressed = true
			}
			status.Apps[step.App] = s
		}(step)
	}
	wg.Wait()
	r.maintainGrants(ctx, d, &status)
	if err := record(status); err != nil {
		return false, progressed, err
	}
	settled = len(plan.Steps) == 0
	for _, s := range status.Apps {
		if s.State != "ready" && s.State != "stopped" {
			settled = false
		}
	}
	return settled, progressed, nil
}

func (r *Reconciler) observe(ctx context.Context, d deployments.Deployment) (View, error) {
	view := View{Fleet: d.Fleet, Now: r.Now(), Nodes: map[string]NodeView{}, Lost: map[string]bool{}}
	nodes, err := r.Nodes.Nodes(ctx, d.Fleet)
	if err != nil {
		return view, err
	}
	present := map[string]bool{}
	for _, n := range nodes {
		present[n.NodeID] = true
		if (n.State.Status != proto.StatusOnline && n.State.Status != proto.StatusBusy) || n.Capability.Runtimes["app-lifecycle"] != "1" {
			continue
		}
		view.Nodes[n.NodeID] = NodeView{ID: n.NodeID, Kind: n.Kind, Platform: n.Capability.OS + "-" + n.Capability.Arch, Caps: n.Capability.Caps}
	}
	r.mu.Lock()
	for _, s := range d.Status.Apps {
		if s.NodeID == "" || s.NodeID == hosted.Node {
			continue
		}
		k := d.Fleet + "/" + s.NodeID
		if present[s.NodeID] {
			delete(r.missing, k)
			continue
		}
		first, ok := r.missing[k]
		if !ok {
			r.missing[k], first = view.Now, view.Now
		}
		if view.Now.Sub(first) >= r.Grace {
			view.Lost[s.NodeID] = true
		}
	}
	r.mu.Unlock()
	// Ledgers are read in parallel and merged after: the nodes map must not
	// change while it is ranged over.
	var wg sync.WaitGroup
	var mu sync.Mutex
	ledgers := map[string]*lifecycle.Ledger{}
	for id := range view.Nodes {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			var ledger lifecycle.Ledger
			c, cancel := context.WithTimeout(ctx, 15*time.Second)
			defer cancel()
			if err := r.Nodes.Call(c, d.Fleet, id, map[string]any{"method": "status"}, &ledger); err != nil || ledger.Node != id || ledger.Owner != d.Fleet {
				return
			}
			mu.Lock()
			ledgers[id] = &ledger
			mu.Unlock()
		}(id)
	}
	wg.Wait()
	for id, ledger := range ledgers {
		n := view.Nodes[id]
		n.Ledger = ledger
		view.Nodes[id] = n
	}
	return view, nil
}

// call is one node call of a step, bounded by CallTimeout. The steps of a
// pass run together and the next pass waits for all of them: a node that
// took a request and never answers (a pod replaced mid-pass keeps its
// subscription until the bus notices the dead connection) would otherwise
// hold every other App's next step until the pass's deadline. The step fails
// instead, and the next pass retries it.
func (r *Reconciler) call(ctx context.Context, fleet, node string, command map[string]any, out any) error {
	method, _ := command["method"].(string)
	if method == "export_data" {
		// The first chunk builds the archive, which may take minutes.
		return r.Nodes.Call(ctx, fleet, node, command, out)
	}
	c, cancel := context.WithTimeout(ctx, r.CallTimeout)
	defer cancel()
	err := r.Nodes.Call(c, fleet, node, command, out)
	if err != nil && ctx.Err() == nil && c.Err() != nil {
		return fmt.Errorf("%s did not answer %s within %s", node, method, r.CallTimeout)
	}
	return err
}

func (r *Reconciler) submit(ctx context.Context, fleet, node string, req lifecycle.Request) error {
	req.Protocol = lifecycle.Protocol
	var out struct {
		Operation lifecycle.Operation `json:"operation"`
	}
	return r.call(ctx, fleet, node, map[string]any{"method": "submit", "request": req}, &out)
}

func (r *Reconciler) execute(ctx context.Context, d deployments.Deployment, release *Release, view View, step Step, update *func(*deployments.AppStatus)) error {
	req := lifecycle.Request{OperationID: step.OpID, Action: string(step.Kind), Digest: step.Variant.Revision, Scope: step.Scope, Generation: step.Generation}
	switch step.Kind {
	case StepInstall:
		if err := r.stage(ctx, d.Fleet, step.Node, release, step.Variant); err != nil {
			return err
		}
		return r.submit(ctx, d.Fleet, step.Node, req)
	case StepCloneData:
		req.DataSource = step.Source
		return r.submit(ctx, d.Fleet, step.Node, req)
	case StepPrepare, StepStop, StepReconcile, StepRecover, StepUninstall:
		return r.submit(ctx, d.Fleet, step.Node, req)
	case StepStart:
		return r.start(ctx, d, step, update)
	case StepMoveData:
		return r.moveData(ctx, d.Fleet, step)
	case StepKeepAlive:
		var out map[string]bool
		return r.call(ctx, d.Fleet, step.Node, map[string]any{"method": "keep_alive", "instance_id": step.Instance,
			"revision": step.Variant.Revision, "generation": step.Generation, "keep_alive": true}, &out)
	}
	return fmt.Errorf("unknown step %s", step.Kind)
}

// stage delivers the artifact bytes, resuming at the node's current offset.
func (r *Reconciler) stage(ctx context.Context, fleet, node string, release *Release, v Variant) error {
	payload, err := release.Artifact(v)
	if err != nil {
		return err
	}
	for offset := int64(0); offset < int64(len(payload)); {
		end := min(offset+lifecycle.MaxChunk, int64(len(payload)))
		var out struct {
			Offset int64 `json:"offset"`
		}
		if err := r.call(ctx, fleet, node, map[string]any{"method": "stage", "digest": v.Revision, "offset": offset, "data": payload[offset:end]}, &out); err != nil {
			return fmt.Errorf("staging %s: %w", v.Revision[:12], err)
		}
		if out.Offset <= offset || out.Offset > int64(len(payload)) {
			return fmt.Errorf("staging %s: node reported offset %d", v.Revision[:12], out.Offset)
		}
		offset = out.Offset
	}
	return nil
}

// moveData relays a stopped instance's exported state to the target node and
// imports it there (docs §8). Relayed chunks resume at the target's offset.
func (r *Reconciler) moveData(ctx context.Context, fleet string, step Step) error {
	var archive string
	for offset, size := int64(0), int64(-1); size < 0 || offset < size; {
		var chunk lifecycle.ExportChunk
		if err := r.call(ctx, fleet, step.SourceNode, map[string]any{"method": "export_data", "revision": step.Source.Digest,
			"scope": step.Scope, "generation": step.Source.Generation, "offset": offset}, &chunk); err != nil {
			return fmt.Errorf("export from %s: %w", step.SourceNode, err)
		}
		if archive != "" && chunk.SHA256 != archive {
			return fmt.Errorf("the exported state changed during the move")
		}
		archive, size = chunk.SHA256, chunk.Size
		step.Source.Schema = chunk.Schema
		var out struct {
			Offset int64 `json:"offset"`
		}
		if err := r.call(ctx, fleet, step.Node, map[string]any{"method": "import_stage", "digest": archive,
			"offset": offset, "data": chunk.Data}, &out); err != nil {
			return fmt.Errorf("import to %s: %w", step.Node, err)
		}
		if out.Offset <= offset && size > 0 {
			return fmt.Errorf("import to %s made no progress", step.Node)
		}
		offset = out.Offset
	}
	source := *step.Source
	source.Archive = archive
	return r.submit(ctx, fleet, step.Node, lifecycle.Request{OperationID: step.OpID, Action: "import_data",
		Digest: step.Variant.Revision, Scope: step.Scope, DataSource: &source})
}

func (r *Reconciler) manifest(ctx context.Context, fleet, node, revision string) (Installed, error) {
	var out Installed
	if node == hosted.Node {
		if raw, ok := hosted.ByRevision(revision); ok {
			return Installed{Protocol: 1, Revision: revision, Manifest: raw}, nil
		}
		return out, fmt.Errorf("the controller hosts no App at revision %s", revision[:min(12, len(revision))])
	}
	if err := r.call(ctx, fleet, node, map[string]any{"method": "app_manifest", "revision": revision}, &out); err != nil {
		return out, err
	}
	if out.Protocol != 1 || out.Revision != revision {
		return out, fmt.Errorf("node returned another App manifest")
	}
	return out, nil
}

// components renders an App's configuration for one node: {"$app": name}
// becomes that App's exact instance, {"$fleet": "id"|"event_prefix"} the
// owner's fleet values, {"$secret": name} {ref, endpoint} and
// {"$secret_ref": name} the reference alone (delivering the secret to the
// node's vault).
func (r *Reconciler) components(ctx context.Context, fleet, node, deployment, app string, raw map[string]json.RawMessage, refs map[string]apptransport.Binding) (map[string]lifecycle.ComponentConfig, error) {
	out := map[string]lifecycle.ComponentConfig{}
	delivered := map[string]map[string]string{}
	var render func(v any) (any, error)
	render = func(v any) (any, error) {
		switch t := v.(type) {
		case map[string]any:
			if name, ok := t["$app"].(string); ok && t["late"] == true {
				// Resolved by this App when it needs it, from the deployment's
				// status: {"$late_app": name, "deployment": ..., other keys}.
				out := map[string]any{"$late_app": name, "deployment": deployment}
				for k, v := range t {
					if k != "$app" && k != "late" {
						out[k] = v
					}
				}
				return out, nil
			}
			if name, ok := t["$app"].(string); ok {
				// The exact instance of another App (at the generation it runs):
				// {node_id, instance_id, revision, generation} plus any other keys.
				id, known := refs[name]
				if !known {
					return nil, fmt.Errorf("configuration refers to %s, which has no instance yet", name)
				}
				out := map[string]any{"node_id": id.Node, "instance_id": id.Instance, "revision": id.Revision, "generation": id.Generation}
				for k, v := range t {
					if k != "$app" {
						out[k] = v
					}
				}
				return out, nil
			}
			if marker, ok := t["$fleet"].(string); ok && len(t) == 1 {
				switch marker {
				case "id":
					return fleet, nil
				case "event_prefix":
					return "fleet." + fleet + ".apps." + app, nil
				}
				return nil, fmt.Errorf("unknown $fleet value %q", marker)
			}
			if name, ok := t["$secret"].(string); ok && len(t) == 1 {
				if ref, ok := delivered[name]; ok {
					return ref, nil
				}
				ref, err := r.deliver(ctx, fleet, node, name)
				if err != nil {
					return nil, err
				}
				delivered[name] = ref
				return ref, nil
			}
			if name, ok := t["$secret_ref"].(string); ok && len(t) == 1 {
				ref, ok := delivered[name]
				if !ok {
					var err error
					if ref, err = r.deliver(ctx, fleet, node, name); err != nil {
						return nil, err
					}
					delivered[name] = ref
				}
				return ref["ref"], nil
			}
			for k, child := range t {
				rendered, err := render(child)
				if err != nil {
					return nil, err
				}
				t[k] = rendered
			}
		case []any:
			for i, child := range t {
				rendered, err := render(child)
				if err != nil {
					return nil, err
				}
				t[i] = rendered
			}
		}
		return v, nil
	}
	for component, value := range raw {
		var parsed any
		if err := json.Unmarshal(value, &parsed); err != nil {
			return nil, err
		}
		rendered, err := render(parsed)
		if err != nil {
			return nil, err
		}
		encoded, _ := json.Marshal(rendered)
		var cfg lifecycle.ComponentConfig
		decoder := json.NewDecoder(bytes.NewReader(encoded))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&cfg); err != nil || cfg.Dependencies != nil {
			return nil, fmt.Errorf("component %s configuration is {values, credentials}", component)
		}
		cfg.Dependencies = map[string]lifecycle.AppDependencyGrant{}
		out[component] = cfg
	}
	return out, nil
}

// deliver imports a secret into a node's vault with its one-time ECDH
// challenge. The vault accepts the same value again, so this is idempotent.
func (r *Reconciler) deliver(ctx context.Context, fleet, node, name string) (map[string]string, error) {
	if r.Secrets == nil {
		return nil, fmt.Errorf("secret %s: this controller keeps no secrets", name)
	}
	value, info, err := r.Secrets.Get(fleet, name)
	if err != nil {
		return nil, fmt.Errorf("secret %s: %w", name, err)
	}
	ref := deployments.VaultRef(name, info.Version)
	var challenge modelcredentials.ImportChallenge
	if err := r.call(ctx, fleet, node, map[string]any{"method": "credential_prepare", "credential_ref": ref, "credential_endpoint": info.Endpoint}, &challenge); err != nil {
		return nil, fmt.Errorf("secret %s: %w", name, err)
	}
	if challenge.Protocol != 1 || challenge.Owner != fleet || challenge.Node != node || challenge.Ref != ref {
		return nil, fmt.Errorf("secret %s: node answered for another destination", name)
	}
	envelope, err := seal(challenge, value)
	if err != nil {
		return nil, fmt.Errorf("secret %s: %w", name, err)
	}
	var ok map[string]bool
	if err := r.call(ctx, fleet, node, map[string]any{"method": "credential_ensure", "credential_challenge": challenge.ID, "credential_envelope": envelope}, &ok); err != nil {
		return nil, fmt.Errorf("secret %s: %w", name, err)
	}
	return map[string]string{"ref": ref, "endpoint": info.Endpoint}, nil
}

func seal(c modelcredentials.ImportChallenge, value string) (modelcredentials.ImportEnvelope, error) {
	var env modelcredentials.ImportEnvelope
	nodeKey, err := base64.StdEncoding.DecodeString(c.PublicKey)
	if err != nil {
		return env, err
	}
	aad, err := base64.StdEncoding.DecodeString(c.Context)
	if err != nil {
		return env, err
	}
	peer, err := ecdh.P256().NewPublicKey(nodeKey)
	if err != nil {
		return env, err
	}
	key, err := ecdh.P256().GenerateKey(rand.Reader)
	if err != nil {
		return env, err
	}
	shared, err := key.ECDH(peer)
	if err != nil {
		return env, err
	}
	defer clear(shared)
	derived, err := hkdf.Key(sha256.New, shared, nil, "pantheon/node-credential-import/v1", 32)
	if err != nil {
		return env, err
	}
	defer clear(derived)
	block, err := aes.NewCipher(derived)
	if err != nil {
		return env, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return env, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return env, err
	}
	env.PublicKey = base64.StdEncoding.EncodeToString(key.PublicKey().Bytes())
	env.Nonce = base64.StdEncoding.EncodeToString(nonce)
	env.Data = base64.StdEncoding.EncodeToString(gcm.Seal(nil, nonce, []byte(value), aad))
	return env, nil
}

// start configures a prepared instance with its grants and starts it.
func (r *Reconciler) start(ctx context.Context, d deployments.Deployment, step Step, update *func(*deployments.AppStatus)) error {
	a := d.Spec.Apps[step.App]
	consumer, err := r.manifest(ctx, d.Fleet, step.Node, step.Variant.Revision)
	if err != nil {
		return err
	}
	providers := map[string]Installed{}
	for alias, p := range step.Providers {
		if providers[alias], err = r.manifest(ctx, d.Fleet, p.Node, p.Revision); err != nil {
			return fmt.Errorf("provider %s: %w", alias, err)
		}
	}
	components, err := r.components(ctx, d.Fleet, step.Node, d.Name, step.App, a.Config, step.Refs)
	if err != nil {
		return err
	}
	// A component that only receives dependency grants has no configuration
	// of its own; the contract still checks its required inputs.
	for _, c := range consumer.Definition.Components {
		if _, ok := components[c.Name]; c.Configuration != nil && !ok {
			components[c.Name] = lifecycle.ComponentConfig{Dependencies: map[string]lifecycle.AppDependencyGrant{}}
		}
	}
	rules, err := contract(consumer, components, a.Bindings, providers)
	if err != nil {
		return err
	}
	if len(a.Bindings) > 0 && r.Grants == nil {
		return fmt.Errorf("this controller issues no dependency grants (start it with an App gateway)")
	}
	identity := apptransport.InstanceIdentity{Fleet: d.Fleet, Node: step.Node, Instance: step.Instance, Revision: step.Variant.Revision, Generation: step.Generation + 1}
	pins, grants := map[string]string{}, map[string]string{}
	aliases := make([]string, 0, len(a.Bindings))
	for alias := range a.Bindings {
		aliases = append(aliases, alias)
	}
	sort.Strings(aliases)
	for _, alias := range aliases {
		b := a.Bindings[alias]
		provider := step.Providers[alias]
		var pm manifest
		_ = json.Unmarshal(providers[alias].Manifest, &pm)
		// The operation ID names the provider it authorizes as well as the
		// preparation: a provider replaced after this preparation's grant was
		// minted (its start then failed) gets its own grant on the retry
		// instead of colliding with the first one forever.
		q := appgateway.DependencyRequest{
			Operation: opID("grant", step.Preparation, alias, "\x00", provider.Node, "\x00", provider.Instance, "\x00", provider.Revision, "\x00", provider.Generation)[:41],
			Consumer:  identity, Preparation: step.Preparation,
			Provider: provider, AppID: pm.ID, Methods: rules[alias], Expires: r.Now().Add(grantTTL).Unix(), Timeout: appgateway.MaxDependencyTimeout,
		}
		result, err := r.Grants.IssueDependency(ctx, q)
		if err != nil {
			// The grant is bound to this preparation and outlived it (the start
			// kept failing for longer than grantTTL), or the operation was
			// minted for a different policy (methods or timeout changed under
			// the same preparation). Retrying the same preparation can never
			// succeed; the planner prepares a new start.
			var de *appgateway.DependencyError
			if errors.As(err, &de) && de.Status == 410 {
				return fmt.Errorf("grant %s: %s", alias, startPreparationExpired)
			}
			if errors.As(err, &de) && de.Status == 409 && de.Reason == appgateway.PolicyMismatch {
				return fmt.Errorf("grant %s: %s: %w", alias, startPreparationStale, err)
			}
			return fmt.Errorf("grant %s: %w", alias, err)
		}
		grant, err := dependencyGrant(result, q)
		if err != nil {
			return fmt.Errorf("grant %s: %w", alias, err)
		}
		components[b.Component].Dependencies[alias] = grant
		pins[alias], grants[alias] = pin(provider), grant.ID
	}
	for app, id := range step.Refs {
		pins[refKey(app)] = pin(id)
	}
	// Record the pins before starting: a provider restart seen later then
	// restarts this App against the new generation.
	*update = func(s *deployments.AppStatus) {
		s.Providers, s.Grants, s.Renewed = pins, grants, r.Now().Unix()
		s.InstanceID, s.Generation = step.Instance, int64(step.Generation)+1
		s.Config = step.Config
	}
	if len(components) > 0 {
		config := lifecycle.AppConfiguration{Preparation: step.Preparation, Components: components}
		var ok map[string]bool
		if err := r.call(ctx, d.Fleet, step.Node, map[string]any{"method": "configure", "instance_id": step.Instance,
			"revision": step.Variant.Revision, "generation": step.Generation, "configuration": config}, &ok); err != nil {
			// The node writes a preparation's configuration once (an exact
			// retry succeeds); one written with other grants is final.
			if strings.Contains(err.Error(), "cancel and prepare a new start") {
				return fmt.Errorf("configure: %s: %w", startPreparationStale, err)
			}
			return fmt.Errorf("configure: %w", err)
		}
	}
	return r.submit(ctx, d.Fleet, step.Node, lifecycle.Request{OperationID: step.OpID, Action: "start", Digest: step.Variant.Revision,
		Scope: step.Scope, Generation: step.Generation, StartPreparationID: step.Preparation})
}

func dependencyGrant(result map[string]any, q appgateway.DependencyRequest) (lifecycle.AppDependencyGrant, error) {
	g := lifecycle.AppDependencyGrant{Consumer: q.Consumer, Provider: q.Provider}
	g.Endpoint, _ = result["endpoint"].(string)
	g.Token, _ = result["access_token"].(string)
	g.ID, _ = result["grant_id"].(string)
	switch e := result["expires"].(type) {
	case int64:
		g.Expires = e
	case float64:
		g.Expires = int64(e)
	}
	sum := sha256.Sum256([]byte(g.Token))
	if g.Endpoint == "" || g.Token == "" || hex.EncodeToString(sum[:]) != g.ID {
		return g, fmt.Errorf("grant authority returned an invalid grant")
	}
	return g, nil
}

// maintainGrants renews the grants of running Apps and revokes those of Apps
// that stopped or were replaced (an expired grant is never reissued).
func (r *Reconciler) maintainGrants(ctx context.Context, d deployments.Deployment, status *deployments.Status) {
	if r.Grants == nil {
		return
	}
	now := r.Now()
	for name, prev := range d.Status.Apps {
		cur := status.Apps[name]
		for alias, id := range prev.Grants {
			if cur.Grants[alias] == id {
				continue
			}
			if err := r.Grants.RevokeDependency(d.Fleet, id); err != nil {
				log.Printf("[reconcile] %s/%s revoke %s: %v", d.Fleet, d.Name, alias, err)
			}
		}
	}
	for name, s := range status.Apps {
		if s.State != "ready" || len(s.Grants) == 0 || now.Unix()-s.Renewed < 300 {
			continue
		}
		renewed := true
		for alias, id := range s.Grants {
			_, err := r.Grants.RenewDependency(ctx, d.Fleet, id, now.Add(grantTTL).Unix())
			var gone *appgateway.DependencyError
			if errors.As(err, &gone) && gone.Status == 410 {
				// Expired or revoked (e.g. the controller was down): the App
				// holds a dead credential. Unpinning restarts it with new grants.
				delete(s.Providers, alias)
				s.Reason = "dependency " + alias + " expired; restarting"
			}
			if err != nil {
				renewed = false
				log.Printf("[reconcile] %s/%s renew %s.%s: %v", d.Fleet, d.Name, name, alias, err)
			}
		}
		if renewed {
			s.Renewed = now.Unix()
		}
		status.Apps[name] = s
	}
}
