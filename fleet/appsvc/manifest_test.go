package appsvc

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestCanonicalAndLegacyParameterDefaultsAgree(t *testing.T) {
	for _, optionalNull := range []string{`"required":false`, `"required":false,"default":null`} {
		for _, required := range []string{`"required":false,"default":"not_defined"`, `"required":true`, `"type":"str"`} {
			manifest := `{"id":"test","provides":{"tools":[{"name":"go","params":[
				{"name":"required",` + required + `},
				{"name":"nullable",` + optionalNull + `},
				{"name":"zero","required":false,"default":0},
				{"name":"off","required":false,"default":false},
				{"name":"empty","required":false,"default":""}]}]}}`
			tools, err := ManifestTools([]byte(manifest), map[string]Handler{"go": nop})
			if err != nil {
				t.Fatal(err)
			}
			defaults := make([]any, len(tools[0].Inputs))
			for i, p := range tools[0].Inputs {
				defaults[i] = p.Default
			}
			if !reflect.DeepEqual(defaults, []any{NotDefined, nil, float64(0), false, ""}) {
				t.Fatalf("parameter defaults changed: %#v", defaults)
			}
			// The published funcdesc shape must still distinguish null from
			// the required sentinel after JSON serialization.
			raw, err := json.Marshal(tools[0].Inputs)
			if err != nil || !strings.Contains(string(raw), `"default":null`) || !strings.Contains(string(raw), `"default":"not_defined"`) {
				t.Fatalf("lost wire defaults: %s (%v)", raw, err)
			}
		}
	}
}

func TestMalformedRequiredFlagCannotBecomeOptional(t *testing.T) {
	for _, flag := range []string{`null`, `"false"`, `1`, `{}`} {
		manifest := `{"id":"test","provides":{"tools":[{"name":"go","params":[{"name":"arg","required":` + flag + `}]}]}}`
		if _, err := ManifestTools([]byte(manifest), map[string]Handler{"go": nop}); err == nil {
			t.Fatalf("invalid required flag accepted: %s", flag)
		}
	}
}

const manifestFixture = `{
  "id": "demo",
  "provides": {"tools": [
    {"name": "list_tools", "description": "meta", "params": []},
    {"name": "go", "description": "Run.", "hidden": true, "params": [
      {"name": "cmd", "type": "str", "required": false, "default": "not_defined"},
      {"name": "timeout", "type": "int", "required": false, "default": 5},
      {"name": "cwd", "type": "str | None", "required": false, "default": null}
    ]},
    {"name": "stop", "description": "Stop."}
  ]}
}`

func nop(_ context.Context, _ map[string]any) (any, error) { return nil, nil }

func TestManifestToolsTranslation(t *testing.T) {
	tools, err := ManifestTools([]byte(manifestFixture),
		map[string]Handler{"go": nop, "stop": nop})
	if err != nil {
		t.Fatal(err)
	}
	if len(tools) != 2 {
		t.Fatalf("want 2 tools (list_tools skipped), got %d", len(tools))
	}
	g := tools[0]
	if g.Name != "go" || !g.Hidden || g.Doc != "Run." {
		t.Fatalf("bad tool head: %+v", g)
	}
	if g.Inputs[0].Default != NotDefined {
		t.Fatalf("required param default: %v", g.Inputs[0].Default)
	}
	if g.Inputs[1].Default != float64(5) {
		t.Fatalf("int default survives as number: %v", g.Inputs[1].Default)
	}
	if g.Inputs[2].Default != nil || g.Inputs[2].Type != "str | None" {
		t.Fatalf("null default / optional type: %+v", g.Inputs[2])
	}
	if tools[1].Inputs == nil || len(tools[1].Inputs) != 0 {
		t.Fatalf("missing params key means empty inputs, got %v", tools[1].Inputs)
	}
}

func TestManifestToolsWiringMismatch(t *testing.T) {
	if _, err := ManifestTools([]byte(manifestFixture),
		map[string]Handler{"go": nop}); err == nil ||
		!strings.Contains(err.Error(), `"stop" has no Go handler`) {
		t.Fatalf("missing handler must error, got %v", err)
	}
	if _, err := ManifestTools([]byte(manifestFixture),
		map[string]Handler{"go": nop, "stop": nop, "ghost": nop}); err == nil ||
		!strings.Contains(err.Error(), "ghost") {
		t.Fatalf("orphan handler must error, got %v", err)
	}
}
