package reconciler

import (
	"encoding/json"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/aristoteleo/pantheonos/os/internal/lifecycle"
)

// An optional dependency (the Desktop's desktop-state) is granted when the
// deployment binds it and needs nothing when it does not; a startup one must
// still be bound.
func TestOptionalDependencyIsBoundOnlyWhenTheDeploymentBindsIt(t *testing.T) {
	consumer := func(phase string) Installed {
		return Installed{Protocol: 1, Manifest: json.RawMessage(`{"apiVersion":2,"id":"desktop","version":"1.0.0",
			"dependencies":{"desktop-state":{"range":"^1.0.0","uses":["desktop-state@1"],"binding":"` + phase + `"}}}`),
			Definition: lifecycle.Definition{Components: []lifecycle.Component{{Name: "backend",
				Configuration: &lifecycle.ConfigDeclaration{Credentials: map[string]lifecycle.ConfigField{"desktop_state": {}}}}}}}
	}
	provider := Installed{Protocol: 1, Manifest: json.RawMessage(`{"apiVersion":2,"id":"desktop-state","version":"1.0.0",
		"provides":{"interfaces":[{"name":"desktop-state","version":1,"tools":["get"]}],"tools":[{"name":"get","params":[]}]}}`)}
	components := func() map[string]lifecycle.ComponentConfig {
		return map[string]lifecycle.ComponentConfig{"backend": {}}
	}
	bindings := map[string]deployments.Binding{"desktop_state": {App: "desktop-state", Component: "backend",
		AppID: "desktop-state", Methods: json.RawMessage(`{"get":{"arguments":[],"bound":{}}}`)}}
	providers := map[string]Installed{"desktop_state": provider}

	rules, err := contract(consumer("optional"), components(), bindings, providers)
	if err != nil || len(rules["desktop_state"]) != 1 {
		t.Fatalf("bound optional dependency: %v %v", rules, err)
	}
	if _, err := contract(consumer("optional"), components(), nil, nil); err != nil {
		t.Fatalf("an unbound optional dependency refused the start: %v", err)
	}
	if _, err := contract(consumer("startup"), components(), nil, nil); err == nil {
		t.Fatal("an unbound startup dependency was accepted")
	}
	if _, err := contract(consumer("runtime"), components(), bindings, providers); err == nil {
		t.Fatal("a runtime dependency was bound at start")
	}
}
