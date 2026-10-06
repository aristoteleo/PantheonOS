package appsvc

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRuntimeValuesBoundSnapshot(t *testing.T) {
	t.Setenv("PANTHEON_APP_CONFIG", "")
	if values, err := RuntimeValues(); err != nil || values != nil {
		t.Fatalf("optional: %v %v", values, err)
	}
	path := filepath.Join(t.TempDir(), "configuration.json")
	t.Setenv("PANTHEON_APP_CONFIG", path)
	for key, value := range map[string]string{"PANTHEON_FLEET_ID": "owner", "PANTHEON_NODE_ID": "node",
		"PANTHEON_INSTANCE_ID": "instance", "PANTHEON_APP_REVISION": "revision",
		"PANTHEON_COMPONENT_NAME": "backend", "PANTHEON_INSTANCE_GENERATION": "3"} {
		t.Setenv(key, value)
	}
	valid := `{"protocol":1,"owner":"owner","node_id":"node","instance_id":"instance","revision":"revision","generation":3,"component":"backend","values":{"shell":{"workspace":"/project"}},"credentials":{"private":{"endpoint":"https://example.invalid","key":"never-print-this"}}}`
	write := func(value string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write(valid)
	values, err := RuntimeValues()
	if err != nil || len(values) != 1 || string(values["shell"]) != `{"workspace":"/project"}` {
		t.Fatalf("valid snapshot: %v", err)
	}
	for _, field := range []string{"protocol", "owner", "node_id", "instance_id", "revision", "generation", "component", "values", "credentials"} {
		t.Run(field, func(t *testing.T) {
			var value map[string]any
			if err := json.Unmarshal([]byte(valid), &value); err != nil {
				t.Fatal(err)
			}
			delete(value, field)
			raw, _ := json.Marshal(value)
			write(string(raw))
			if _, err := RuntimeValues(); err == nil || strings.Contains(err.Error(), "never-print") {
				t.Fatal("missing identity/configuration was admitted or exposed")
			}
		})
	}
	for _, invalid := range []string{strings.Replace(valid, `"generation":3`, `"generation":2`, 1),
		strings.Replace(valid, `"owner":"owner"`, `"owner":"other"`, 1), valid + " {}", strings.Repeat(" ", 256<<10) + valid,
		strings.Replace(valid, `"protocol":1`, `"protocol":1,"unknown":true`, 1)} {
		write(invalid)
		if _, err := RuntimeValues(); err == nil {
			t.Fatal("invalid configured snapshot fell back")
		}
	}
}
