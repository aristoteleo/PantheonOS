package reconciler

// The dependency contract a start must satisfy, checked against the installed
// manifests (the same rules as the former Python compile_contract): a binding
// alias is a declared credential input of the consumer component, every
// startup dependency is bound, the provider's version is in range, and the
// granted methods come from the declared interfaces with exactly their
// parameters.

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

// Installed is one node's app_manifest reply.
type Installed struct {
	Protocol   int                  `json:"protocol"`
	Revision   string               `json:"revision"`
	Manifest   json.RawMessage      `json:"manifest"`
	Definition lifecycle.Definition `json:"definition"`
}

type manifest struct {
	APIVersion   int                   `json:"apiVersion"`
	ID           string                `json:"id"`
	Version      string                `json:"version"`
	Dependencies map[string]dependency `json:"dependencies"`
	Provides     struct {
		Interfaces []struct {
			Name    string   `json:"name"`
			Version *int     `json:"version"`
			Tools   []string `json:"tools"`
		} `json:"interfaces"`
		Tools []struct {
			Name   string `json:"name"`
			Params []struct {
				Name     string `json:"name"`
				Required *bool  `json:"required"`
			} `json:"params"`
		} `json:"tools"`
	} `json:"provides"`
}

type dependency struct {
	Range   string   `json:"range"`
	Uses    []string `json:"uses"`
	Binding string   `json:"binding"`
}

func (d dependency) phase() string {
	if d.Binding == "" {
		return "startup"
	}
	return d.Binding
}

var semverRE = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func version(v string) ([3]int, error) {
	m := semverRE.FindStringSubmatch(v)
	if m == nil {
		return [3]int{}, fmt.Errorf("dependency assembly requires a stable three-part version, got %q", v)
	}
	var out [3]int
	for i := range out {
		out[i], _ = strconv.Atoi(m[i+1])
	}
	return out, nil
}

func less(a, b [3]int) bool {
	for i := range a {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return false
}

func compatible(actual, constraint string) error {
	v, err := version(actual)
	if err != nil {
		return err
	}
	if constraint == "" || constraint == "*" {
		return nil
	}
	prefix := ""
	for _, p := range []string{">=", "^", "~"} {
		if strings.HasPrefix(constraint, p) {
			prefix = p
			break
		}
	}
	low, err := version(constraint[len(prefix):])
	if err != nil {
		return err
	}
	var high [3]int
	switch {
	case prefix == "":
		if v != low {
			return fmt.Errorf("version %s does not match %s", actual, constraint)
		}
		return nil
	case prefix == ">=":
		if less(v, low) {
			return fmt.Errorf("version %s does not match %s", actual, constraint)
		}
		return nil
	case prefix == "~":
		high = [3]int{low[0], low[1] + 1, 0}
	case low[0] > 0:
		high = [3]int{low[0] + 1, 0, 0}
	case low[1] > 0:
		high = [3]int{0, low[1] + 1, 0}
	default:
		high = [3]int{0, 0, low[2] + 1}
	}
	if less(v, low) || !less(v, high) {
		return fmt.Errorf("version %s does not match %s", actual, constraint)
	}
	return nil
}

// methods checks requested method rules against the provider's interfaces.
func methods(dep dependency, provider manifest, raw json.RawMessage) (map[string]appgateway.RPCMethod, error) {
	bad := fmt.Errorf("provider %s does not satisfy the declared dependency interface or method arguments", provider.ID)
	interfaces := map[string][]string{}
	for _, in := range provider.Provides.Interfaces {
		v := 1
		if in.Version != nil {
			v = *in.Version
		}
		key := in.Name + "@" + strconv.Itoa(v)
		if _, dup := interfaces[key]; dup || v < 1 {
			return nil, bad
		}
		interfaces[key] = in.Tools
	}
	params := map[string]map[string]bool{} // tool -> param -> required
	for _, tool := range provider.Provides.Tools {
		if _, dup := params[tool.Name]; dup {
			return nil, bad
		}
		params[tool.Name] = map[string]bool{}
		for _, p := range tool.Params {
			if _, dup := params[tool.Name][p.Name]; dup {
				return nil, bad
			}
			params[tool.Name][p.Name] = p.Required == nil || *p.Required
		}
	}
	allowed := map[string]bool{}
	if len(dep.Uses) == 0 {
		return nil, bad
	}
	for _, use := range dep.Uses {
		tools, ok := interfaces[use]
		if !ok {
			return nil, bad
		}
		for _, t := range tools {
			allowed[t] = true
		}
	}
	var requested map[string]appgateway.RPCMethod
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&requested) != nil || len(requested) == 0 || len(requested) > 64 {
		return nil, bad
	}
	for method, rule := range requested {
		declared, ok := params[method]
		if !allowed[method] || !ok || rule.Arguments == nil || rule.Bound == nil {
			return nil, bad
		}
		selected := map[string]bool{}
		for _, a := range rule.Arguments {
			if selected[a] {
				return nil, bad
			}
			selected[a] = true
		}
		for b := range rule.Bound {
			if selected[b] {
				return nil, bad
			}
			selected[b] = true
		}
		for name := range selected {
			if _, ok := declared[name]; !ok {
				return nil, bad
			}
		}
		for name, required := range declared {
			if required && !selected[name] {
				return nil, bad
			}
		}
	}
	return requested, nil
}

// contract validates a start and returns the components' configuration
// (dependencies still empty) and each binding alias's granted methods.
func contract(consumer Installed, components map[string]lifecycle.ComponentConfig, bindings map[string]deployments.Binding,
	providers map[string]Installed) (map[string]map[string]appgateway.RPCMethod, error) {
	var m manifest
	if json.Unmarshal(consumer.Manifest, &m) != nil || m.APIVersion != 2 {
		return nil, fmt.Errorf("dependency assembly requires an App manifest v2")
	}
	declared := map[string]*lifecycle.ConfigDeclaration{}
	for _, c := range consumer.Definition.Components {
		if c.Configuration != nil {
			declared[c.Name] = c.Configuration
		}
	}
	if len(components) != len(declared) {
		return nil, fmt.Errorf("configure exactly the declared App components")
	}
	for name, cfg := range components {
		decl, ok := declared[name]
		if !ok {
			return nil, fmt.Errorf("component %s declares no configuration", name)
		}
		for key := range cfg.Values {
			if _, ok := decl.Values[key]; !ok {
				return nil, fmt.Errorf("undeclared value %s.%s", name, key)
			}
		}
		for key, field := range decl.Values {
			if field.Required && (cfg.Values[key] == nil || string(cfg.Values[key]) == "null") {
				return nil, fmt.Errorf("missing required value %s.%s", name, key)
			}
		}
		for key := range cfg.Credentials {
			if _, ok := decl.Credentials[key]; !ok {
				return nil, fmt.Errorf("undeclared credential %s.%s", name, key)
			}
		}
	}
	rules := map[string]map[string]appgateway.RPCMethod{}
	bound := map[string]bool{}
	for alias, b := range bindings {
		provider := providers[alias]
		var pm manifest
		if json.Unmarshal(provider.Manifest, &pm) != nil || pm.APIVersion != 2 {
			return nil, fmt.Errorf("provider of %s needs an App manifest v2", alias)
		}
		appID := b.AppID
		if appID == "" {
			appID = pm.ID
		}
		dep, ok := m.Dependencies[appID]
		decl := declared[b.Component]
		if !ok || decl == nil {
			return nil, fmt.Errorf("binding %s: %s is not a declared dependency of component %s", alias, appID, b.Component)
		}
		if _, ok := decl.Credentials[alias]; !ok {
			return nil, fmt.Errorf("binding %s is not a declared credential input of %s", alias, b.Component)
		}
		if _, dup := components[b.Component].Credentials[alias]; dup {
			return nil, fmt.Errorf("binding %s is also configured as a credential", alias)
		}
		if dep.phase() != "startup" {
			return nil, fmt.Errorf("runtime dependency %s is allocated under its live owner policy, not bound at start", appID)
		}
		if pm.ID != appID {
			return nil, fmt.Errorf("binding %s: provider is %s, not %s", alias, pm.ID, appID)
		}
		if err := compatible(pm.Version, dep.Range); err != nil {
			return nil, fmt.Errorf("binding %s: %w", alias, err)
		}
		r, err := methods(dep, pm, b.Methods)
		if err != nil {
			return nil, err
		}
		rules[alias] = r
		bound[appID] = true
	}
	for appID, dep := range m.Dependencies {
		if dep.phase() == "startup" && !bound[appID] {
			return nil, fmt.Errorf("startup dependency %s needs a binding", appID)
		}
	}
	for name, decl := range declared {
		for key, field := range decl.Credentials {
			if !field.Required {
				continue
			}
			_, local := components[name].Credentials[key]
			b, isBinding := bindings[key]
			if !local && !(isBinding && b.Component == name) {
				return nil, fmt.Errorf("missing required credential %s.%s", name, key)
			}
		}
	}
	return rules, nil
}
