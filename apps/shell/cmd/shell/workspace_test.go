package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestWorkspaceBinding(t *testing.T) {
	data, project := t.TempDir(), t.TempDir()
	t.Setenv("PANTHEON_APP_CONFIG", "")
	if root, err := workspaceRoot(data); err != nil || root != filepath.Join(data, "workspace") {
		t.Fatalf("legacy root: %s %v", root, err)
	}
	path := filepath.Join(data, "configuration.json")
	t.Setenv("PANTHEON_APP_CONFIG", path)
	for key, value := range map[string]string{"PANTHEON_FLEET_ID": "owner", "PANTHEON_NODE_ID": "node",
		"PANTHEON_INSTANCE_ID": "instance", "PANTHEON_APP_REVISION": "revision",
		"PANTHEON_COMPONENT_NAME": "backend", "PANTHEON_INSTANCE_GENERATION": "1"} {
		t.Setenv(key, value)
	}
	write := func(shell any) {
		t.Helper()
		raw, err := json.Marshal(map[string]any{"protocol": 1, "owner": "owner", "node_id": "node",
			"instance_id": "instance", "revision": "revision", "generation": 1, "component": "backend",
			"values": map[string]any{"shell": shell}, "credentials": map[string]any{}})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
	write(map[string]any{"workspace": project})
	expected, _ := filepath.EvalSymlinks(project)
	if root, err := workspaceRoot(data); err != nil || root != expected {
		t.Fatalf("bound root: %s %v", root, err)
	}
	for _, invalid := range []any{nil, map[string]any{}, map[string]any{"workspace": "relative"},
		map[string]any{"workspace": filepath.Join(project, "missing")}, map[string]any{"workspace": path},
		map[string]any{"workspace": project, "fallback": true}} {
		write(invalid)
		if _, err := workspaceRoot(data); err == nil {
			t.Fatal("invalid workspace silently admitted")
		}
	}
	if _, err := os.Stat(filepath.Join(project, "missing")); !os.IsNotExist(err) {
		t.Fatal("borrowed project created by provider")
	}
}
