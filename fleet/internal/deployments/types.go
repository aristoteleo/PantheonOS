// Package deployments holds owners' desired App deployments: what should run
// and how it is wired. Where it runs, generations and operation IDs are the
// reconciler's bookkeeping (Status), never part of the owner's Spec. See
// docs/fleet-orchestration.md.
package deployments

import (
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"sort"
)

const (
	Protocol = 1
	// MaxSpecBytes bounds one deployment spec (configuration included).
	MaxSpecBytes = 512 * 1024
	MaxApps      = 32
	MaxBindings  = 16
	MaxSecrets   = 32
	MaxPerFleet  = 64
)

var (
	nameRE   = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
	fleetRE  = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
	nodeRE   = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
	sha256RE = regexp.MustCompile(`^[a-f0-9]{64}$`)
	kindRE   = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}$`)
)

// Intent is the owner's decision for one App. The reconciler never changes it.
type Intent string

const (
	Running Intent = "running"
	Stopped Intent = "stopped"
)

type Release struct {
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
}

// Placement overrides the manifest's placement. Node pins an exact node; an
// empty Placement lets Fleet choose by capability.
type Placement struct {
	Node   string   `json:"node,omitempty"`
	Prefer []string `json:"prefer,omitempty"`
	Avoid  []string `json:"avoid,omitempty"`
}

// Binding wires a consumer credential alias to another App of the same
// deployment. Component is the consumer component whose configuration
// declares the alias; the provider is always its backend's http port. AppID
// is the consumer manifest's dependency key (defaults to the provider's App
// id). Methods carries the per-method argument rules ({method: {arguments,
// bound}}); they are checked against both manifests when reconciled.
type Binding struct {
	App       string          `json:"$app"`
	Component string          `json:"component"`
	AppID     string          `json:"app_id,omitempty"`
	Methods   json.RawMessage `json:"methods"`
}

// ModelService declares that the App registers itself as this model service.
type ModelService struct {
	DeploymentID string `json:"deployment_id"`
}

type Provides struct {
	ModelService *ModelService `json:"model_service,omitempty"`
}

type AppSpec struct {
	Package   string                     `json:"package"`
	Scope     string                     `json:"scope"`
	Intent    Intent                     `json:"intent"`
	Placement Placement                  `json:"placement"`
	Config    map[string]json.RawMessage `json:"config,omitempty"`
	Bindings  map[string]Binding         `json:"bindings,omitempty"`
	Provides  *Provides                  `json:"provides,omitempty"`
}

type Spec struct {
	Release Release            `json:"release"`
	Apps    map[string]AppSpec `json:"apps"`
	Secrets []string           `json:"secrets,omitempty"`
}

// AppStatus is where an App runs now, as last observed by the reconciler.
// Providers pins, per binding alias, the provider instance the App was started
// against ("node/instance/generation"); a different provider restarts it.
// Grants are the dependency grant ids issued for that start (public digests).
type AppStatus struct {
	NodeID      string            `json:"node_id,omitempty"`
	InstanceID  string            `json:"instance_id,omitempty"`
	Revision    string            `json:"revision,omitempty"`
	Generation  int64             `json:"generation"`
	State       string            `json:"state"`
	Reason      string            `json:"reason,omitempty"`
	Since       int64             `json:"since,omitempty"`
	Attempts    int               `json:"attempts,omitempty"`
	NextAttempt int64             `json:"next_attempt,omitempty"`
	Providers   map[string]string `json:"providers,omitempty"`
	Grants      map[string]string `json:"grants,omitempty"`
	Renewed     int64             `json:"renewed,omitempty"`
}

type Condition struct {
	Type   string `json:"type"`
	Status bool   `json:"status"`
	Reason string `json:"reason,omitempty"`
	Since  int64  `json:"since"`
}

type Status struct {
	ObservedRevision int64                `json:"observed_revision"`
	Apps             map[string]AppStatus `json:"apps,omitempty"`
	Conditions       []Condition          `json:"conditions,omitempty"`
}

type Deployment struct {
	Protocol int    `json:"protocol"`
	Fleet    string `json:"fleet_id"`
	Name     string `json:"deployment"`
	Revision int64  `json:"revision"`
	Updated  int64  `json:"updated"`
	Spec     Spec   `json:"spec"`
	Status   Status `json:"status"`
}

// Validate checks the spec's own consistency. Package contracts (configuration
// keys, binding interfaces) are checked by the reconciler against manifests.
func (s Spec) Validate() error {
	raw, err := json.Marshal(s)
	if err != nil {
		return err
	}
	if len(raw) > MaxSpecBytes {
		return fmt.Errorf("deployment spec exceeds %d bytes", MaxSpecBytes)
	}
	if err := s.ValidateRelease(); err != nil {
		return err
	}
	if len(s.Apps) == 0 || len(s.Apps) > MaxApps {
		return fmt.Errorf("a deployment has 1 to %d Apps", MaxApps)
	}
	if len(s.Secrets) > MaxSecrets {
		return fmt.Errorf("a deployment names at most %d secrets", MaxSecrets)
	}
	secrets := map[string]bool{}
	for _, name := range s.Secrets {
		if !nameRE.MatchString(name) || secrets[name] {
			return fmt.Errorf("invalid or repeated secret name %q", name)
		}
		secrets[name] = true
	}
	identities := map[string]string{}
	modelServices := map[string]string{}
	for name, app := range s.Apps {
		if !nameRE.MatchString(name) {
			return fmt.Errorf("invalid App name %q", name)
		}
		if !nameRE.MatchString(app.Package) || !nameRE.MatchString(app.Scope) {
			return fmt.Errorf("App %s: package and scope are names", name)
		}
		if other, dup := identities[app.Package+"\x00"+app.Scope]; dup {
			return fmt.Errorf("Apps %s and %s share package and scope", other, name)
		}
		identities[app.Package+"\x00"+app.Scope] = name
		if app.Intent != Running && app.Intent != Stopped {
			return fmt.Errorf("App %s: intent is running or stopped", name)
		}
		if p := app.Placement; p.Node != "" && !nodeRE.MatchString(p.Node) {
			return fmt.Errorf("App %s: invalid placement node", name)
		}
		for _, kind := range append(append([]string{}, app.Placement.Prefer...), app.Placement.Avoid...) {
			if !kindRE.MatchString(kind) {
				return fmt.Errorf("App %s: invalid placement kind %q", name, kind)
			}
		}
		if len(app.Bindings) > MaxBindings {
			return fmt.Errorf("App %s: at most %d bindings", name, MaxBindings)
		}
		for alias, b := range app.Bindings {
			if !nameRE.MatchString(alias) {
				return fmt.Errorf("App %s: invalid binding alias %q", name, alias)
			}
			if _, ok := s.Apps[b.App]; !ok || b.App == name {
				return fmt.Errorf("App %s: binding %s must name another App of this deployment", name, alias)
			}
			if !nameRE.MatchString(b.Component) || (b.AppID != "" && !nameRE.MatchString(b.AppID)) || !json.Valid(b.Methods) {
				return fmt.Errorf("App %s: binding %s needs a consumer component and method rules", name, alias)
			}
		}
		for component, value := range app.Config {
			if !nameRE.MatchString(component) || !json.Valid(value) {
				return fmt.Errorf("App %s: invalid configuration for %q", name, component)
			}
			for _, secret := range secretRefs(value) {
				if !secrets[secret] {
					return fmt.Errorf("App %s: secret %q is not declared by the deployment", name, secret)
				}
			}
			for _, ref := range markers(value, "$app") {
				if _, ok := s.Apps[ref]; !ok || ref == name {
					return fmt.Errorf("App %s: configuration refers to unknown App %q", name, ref)
				}
			}
			if left := markers(value, "$input"); len(left) > 0 {
				return fmt.Errorf("App %s: fill the profile input %q before saving", name, left[0])
			}
		}
		if app.Provides != nil && app.Provides.ModelService != nil {
			id := app.Provides.ModelService.DeploymentID
			if !nameRE.MatchString(id) {
				return fmt.Errorf("App %s: invalid model service deployment id", name)
			}
			if other, dup := modelServices[id]; dup {
				return fmt.Errorf("Apps %s and %s provide the same model service", other, name)
			}
			modelServices[id] = name
		}
	}
	if _, err := Order(s); err != nil {
		return err
	}
	return nil
}

// ValidateRelease checks the release pin alone.
func (s Spec) ValidateRelease() error {
	u, err := url.Parse(s.Release.URL)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return fmt.Errorf("pin the release set to an https URL without credentials or query")
	}
	if !sha256RE.MatchString(s.Release.SHA256) {
		return fmt.Errorf("pin the release set by its SHA-256")
	}
	return nil
}

// Order returns App names so that every App follows the Apps it binds to.
// Ties are broken by name so the order is stable.
func Order(s Spec) ([]string, error) {
	indegree := map[string]int{}
	dependents := map[string][]string{}
	for name, app := range s.Apps {
		indegree[name] += 0
		seen := map[string]bool{}
		for _, b := range app.Bindings {
			if seen[b.App] {
				continue
			}
			seen[b.App] = true
			indegree[name]++
			dependents[b.App] = append(dependents[b.App], name)
		}
	}
	var ready, order []string
	for name, n := range indegree {
		if n == 0 {
			ready = append(ready, name)
		}
	}
	for len(ready) > 0 {
		sort.Strings(ready)
		next := ready[0]
		ready = ready[1:]
		order = append(order, next)
		for _, d := range dependents[next] {
			indegree[d]--
			if indegree[d] == 0 {
				ready = append(ready, d)
			}
		}
	}
	if len(order) != len(s.Apps) {
		return nil, fmt.Errorf("App bindings form a cycle")
	}
	return order, nil
}

// secretRefs lists secret references anywhere in a JSON value. The reconciler
// delivers the secret to the App's node and replaces {"$secret": name} with
// {ref, endpoint} (a credential input) and {"$secret_ref": name} with the
// vault reference string alone (for Apps that read the vault themselves).
func secretRefs(raw json.RawMessage) []string {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return nil
	}
	var out []string
	var walk func(any)
	walk = func(v any) {
		switch t := v.(type) {
		case map[string]any:
			for _, marker := range []string{"$secret", "$secret_ref"} {
				if name, ok := t[marker].(string); ok && len(t) == 1 {
					out = append(out, name)
					return
				}
			}
			for _, child := range t {
				walk(child)
			}
		case []any:
			for _, child := range t {
				walk(child)
			}
		}
	}
	walk(value)
	return out
}

// markers lists the string values of {marker: value, ...} objects anywhere in
// a JSON value (e.g. "$app" references to other Apps).
func markers(raw json.RawMessage, marker string) []string {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return nil
	}
	var out []string
	var walk func(any)
	walk = func(v any) {
		switch t := v.(type) {
		case map[string]any:
			if name, ok := t[marker].(string); ok {
				out = append(out, name)
				return
			}
			for _, child := range t {
				walk(child)
			}
		case []any:
			for _, child := range t {
				walk(child)
			}
		}
	}
	walk(value)
	return out
}

// exactRefs lists {"$app": name} references to an App's exact running
// instance. {"$app": name, "late": true} is resolved by the App itself when it
// needs it (e.g. an allocator binding providers at request time), so it is
// neither a start dependency nor pinned.
func exactRefs(raw json.RawMessage) []string {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return nil
	}
	var out []string
	var walk func(any)
	walk = func(v any) {
		switch t := v.(type) {
		case map[string]any:
			if name, ok := t["$app"].(string); ok {
				if late, _ := t["late"].(bool); !late {
					out = append(out, name)
				}
				return
			}
			for _, child := range t {
				walk(child)
			}
		case []any:
			for _, child := range t {
				walk(child)
			}
		}
	}
	walk(value)
	return out
}

// ConfigRefs lists the Apps an App's configuration refers to at their exact
// running instance.
func ConfigRefs(a AppSpec) []string {
	seen := map[string]bool{}
	var out []string
	for _, value := range a.Config {
		for _, ref := range exactRefs(value) {
			if !seen[ref] {
				seen[ref] = true
				out = append(out, ref)
			}
		}
	}
	sort.Strings(out)
	return out
}

// Units groups Apps into start units: the strongly connected components of
// "depends on" (binding providers and configuration references). A unit of
// several Apps refers to its members' exact instances in a cycle (e.g. an
// allocator whose policy names the Agent that binds it), so it is prepared,
// started and restarted together. Units are returned dependencies first, and
// members within a unit in binding order (Order).
func Units(s Spec) ([][]string, error) {
	order, err := Order(s)
	if err != nil {
		return nil, err
	}
	position := map[string]int{}
	for i, name := range order {
		position[name] = i
	}
	deps := map[string][]string{}
	for name, a := range s.Apps {
		for _, b := range a.Bindings {
			deps[name] = append(deps[name], b.App)
		}
		deps[name] = append(deps[name], ConfigRefs(a)...)
		sort.Strings(deps[name])
	}
	// Tarjan's algorithm; emits components in reverse topological order of
	// the condensation, i.e. dependencies first.
	index, low, onStack := map[string]int{}, map[string]int{}, map[string]bool{}
	var stack []string
	var units [][]string
	next := 0
	var visit func(string)
	visit = func(v string) {
		index[v], low[v] = next, next
		next++
		stack = append(stack, v)
		onStack[v] = true
		for _, w := range deps[v] {
			if _, seen := index[w]; !seen {
				visit(w)
				low[v] = min(low[v], low[w])
			} else if onStack[w] {
				low[v] = min(low[v], index[w])
			}
		}
		if low[v] == index[v] {
			var unit []string
			for {
				w := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				onStack[w] = false
				unit = append(unit, w)
				if w == v {
					break
				}
			}
			sort.Slice(unit, func(i, j int) bool { return position[unit[i]] < position[unit[j]] })
			units = append(units, unit)
		}
	}
	for _, name := range order {
		if _, seen := index[name]; !seen {
			visit(name)
		}
	}
	return units, nil
}
