package appsvc

import (
	"context"
	"strings"
	"testing"
)

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
