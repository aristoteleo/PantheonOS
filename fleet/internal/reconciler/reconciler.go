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
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
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
	Tick, Settled time.Duration
	Now           func() time.Time

	mu       sync.Mutex
	missing  map[string]time.Time // fleet/node -> first seen absent
	inflight map[string]bool
	last     map[string]time.Time
	settled  map[string]bool
}

const grantTTL = 15 * time.Minute

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
	if r.Now == nil {
		r.Now = time.Now
	}
	r.mu.Lock()
	if r.missing == nil {
		r.missing, r.inflight, r.last, r.settled = map[string]time.Time{}, map[string]bool{}, map[string]time.Time{}, map[string]bool{}
	}
	r.mu.Unlock()
}

// Run reconciles until ctx ends: on every deployment write, and periodically.
func (r *Reconciler) Run(ctx context.Context) {
	r.defaults()
	tick := time.NewTicker(r.Tick)
	defer tick.Stop()
	slots := make(chan struct{}, 8)
	for {
		changed := map[string]bool{}
		select {
		case <-ctx.Done():
			return
		case k := <-r.Store.Changes():
			changed[k] = true
		case <-tick.C:
		}
		for _, d := range r.Store.All() {
			k := d.Fleet + "/" + d.Name
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
				settled, err := r.Pass(pctx, d.Fleet, d.Name)
				cancel()
				if err != nil {
					log.Printf("[reconcile] %s/%s: %v", d.Fleet, d.Name, err)
				}
				r.mu.Lock()
				delete(r.inflight, k)
				r.last[k], r.settled[k] = r.Now(), settled && err == nil
				r.mu.Unlock()
			}(d)
		}
	}
}

// Pass observes, plans, executes and records one deployment. It reports
// whether the deployment is settled (nothing to do, everything as intended).
func (r *Reconciler) Pass(ctx context.Context, fleet, name string) (bool, error) {
	r.defaults()
	d, err := r.Store.Get(fleet, name)
	if err != nil {
		return true, nil // deleted meanwhile
	}
	record := func(status deployments.Status) error {
		if err := r.Store.SetStatus(fleet, name, d.Revision, status); err != nil && !errors.Is(err, deployments.ErrConflict) {
			return err
		}
		return nil
	}
	fail := func(reason string) (bool, error) {
		status := d.Status
		status.ObservedRevision = d.Revision
		status.Conditions = []deployments.Condition{{Type: "Ready", Reason: reason, Since: r.Now().Unix()}}
		return false, errors.Join(errors.New(reason), record(status))
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
			}
			status.Apps[step.App] = s
		}(step)
	}
	wg.Wait()
	r.maintainGrants(ctx, d, &status)
	if err := record(status); err != nil {
		return false, err
	}
	settled := len(plan.Steps) == 0
	for _, s := range status.Apps {
		if s.State != "ready" && s.State != "stopped" {
			settled = false
		}
	}
	return settled, nil
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
		if s.NodeID == "" {
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
	var wg sync.WaitGroup
	var mu sync.Mutex
	for id, n := range view.Nodes {
		wg.Add(1)
		go func(id string, n NodeView) {
			defer wg.Done()
			var ledger lifecycle.Ledger
			c, cancel := context.WithTimeout(ctx, 15*time.Second)
			defer cancel()
			if err := r.Nodes.Call(c, d.Fleet, id, map[string]any{"method": "status"}, &ledger); err != nil || ledger.Node != id || ledger.Owner != d.Fleet {
				return
			}
			mu.Lock()
			n.Ledger = &ledger
			view.Nodes[id] = n
			mu.Unlock()
		}(id, n)
	}
	wg.Wait()
	return view, nil
}

func (r *Reconciler) submit(ctx context.Context, fleet, node string, req lifecycle.Request) error {
	req.Protocol = lifecycle.Protocol
	var out struct {
		Operation lifecycle.Operation `json:"operation"`
	}
	return r.Nodes.Call(ctx, fleet, node, map[string]any{"method": "submit", "request": req}, &out)
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
	case StepPrepare, StepStop, StepReconcile:
		return r.submit(ctx, d.Fleet, step.Node, req)
	case StepStart:
		return r.start(ctx, d, step, update)
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
		if err := r.Nodes.Call(ctx, fleet, node, map[string]any{"method": "stage", "digest": v.Revision, "offset": offset, "data": payload[offset:end]}, &out); err != nil {
			return fmt.Errorf("staging %s: %w", v.Revision[:12], err)
		}
		if out.Offset <= offset || out.Offset > int64(len(payload)) {
			return fmt.Errorf("staging %s: node reported offset %d", v.Revision[:12], out.Offset)
		}
		offset = out.Offset
	}
	return nil
}

func (r *Reconciler) manifest(ctx context.Context, fleet, node, revision string) (Installed, error) {
	var out Installed
	if err := r.Nodes.Call(ctx, fleet, node, map[string]any{"method": "app_manifest", "revision": revision}, &out); err != nil {
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
func (r *Reconciler) components(ctx context.Context, fleet, node, app string, raw map[string]json.RawMessage, refs map[string]apptransport.Binding) (map[string]lifecycle.ComponentConfig, error) {
	out := map[string]lifecycle.ComponentConfig{}
	delivered := map[string]map[string]string{}
	var render func(v any) (any, error)
	render = func(v any) (any, error) {
		switch t := v.(type) {
		case map[string]any:
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
	if err := r.Nodes.Call(ctx, fleet, node, map[string]any{"method": "credential_prepare", "credential_ref": ref, "credential_endpoint": info.Endpoint}, &challenge); err != nil {
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
	if err := r.Nodes.Call(ctx, fleet, node, map[string]any{"method": "credential_ensure", "credential_challenge": challenge.ID, "credential_envelope": envelope}, &ok); err != nil {
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
	components, err := r.components(ctx, d.Fleet, step.Node, step.App, a.Config, step.Refs)
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
		q := appgateway.DependencyRequest{
			Operation: opID("grant", step.Preparation, alias)[:41], Consumer: identity, Preparation: step.Preparation,
			Provider: provider, AppID: pm.ID, Methods: rules[alias], Expires: r.Now().Add(grantTTL).Unix(), Timeout: 60,
		}
		result, err := r.Grants.IssueDependency(ctx, q)
		if err != nil {
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
	}
	if len(components) > 0 {
		config := lifecycle.AppConfiguration{Preparation: step.Preparation, Components: components}
		var ok map[string]bool
		if err := r.Nodes.Call(ctx, d.Fleet, step.Node, map[string]any{"method": "configure", "instance_id": step.Instance,
			"revision": step.Variant.Revision, "generation": step.Generation, "configuration": config}, &ok); err != nil {
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
