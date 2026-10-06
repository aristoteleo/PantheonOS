package lifecycle

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestDataSchemaAdmissionAtNodeCopyBoundary(t *testing.T) {
	v1 := &DataSchema{ID: "example-data", Version: 1, Accepts: []int{1}}
	v2 := &DataSchema{ID: "example-data", Version: 2, Accepts: []int{2}}
	compatible := &DataSchema{ID: "example-data", Version: 2, Accepts: []int{1, 2}}
	for _, tc := range []struct {
		name           string
		source, target *DataSchema
		accepted       bool
	}{
		{"legacy", nil, nil, true}, {"same-format", v1, v1, true},
		{"declared-compatible", v1, compatible, true}, {"new-format-only", v1, v2, false},
		{"downgrade", v2, v1, false}, {"missing-source", nil, v1, false},
		{"missing-target", v1, nil, false},
		{"wrong-identity", v1, &DataSchema{ID: "other", Version: 1, Accepts: []int{1}}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m, _, _ := setup(t)
			install := func(id string, schema *DataSchema) string {
				def := definition()
				def.Version = id
				def.DataSchema = schema
				b, digest := bundle(t, def, nil)
				if _, err := m.Stage(digest, 0, b); err != nil {
					t.Fatal(err)
				}
				if _, err := m.Submit(Request{Protocol: 1, OperationID: "install-" + id, Action: "install", Digest: digest, Scope: "schema"}); err != nil {
					t.Fatal(err)
				}
				if op := wait(t, m, "install-"+id); op.State != "succeeded" {
					t.Fatal(op)
				}
				return digest
			}
			old, next := install("old", tc.source), install("new", tc.target)
			m.mu.Lock()
			protocol := m.ledger.Protocol
			m.mu.Unlock()
			if (tc.source != nil || tc.target != nil) && protocol < 8 {
				t.Fatal("schema installation lacks older-runner fence")
			}
			run := func(id, action, digest string, generation uint64, source *DataSource) Operation {
				if _, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "schema", Generation: generation, DataSource: source}); err != nil {
					t.Fatal(err)
				}
				return wait(t, m, id)
			}
			if op := run("start", "start", old, 0, nil); op.State != "succeeded" {
				t.Fatal(op)
			}
			sourcePath := filepath.Join(m.paths(old, "schema").Data, "history")
			if err := os.WriteFile(sourcePath, []byte("retained source"), 0600); err != nil {
				t.Fatal(err)
			}
			if op := run("stop", "stop", old, 1, nil); op.State != "succeeded" {
				t.Fatal(op)
			}
			op := run("copy", "clone_data", next, 0, &DataSource{old, 2})
			if (op.State == "succeeded") != tc.accepted {
				t.Fatalf("unexpected admission: %+v", op)
			}
			targetPath := m.paths(next, "schema").Data
			if tc.accepted {
				raw, err := os.ReadFile(filepath.Join(targetPath, "history"))
				if err != nil || string(raw) != "retained source" {
					t.Fatal(string(raw), err)
				}
			} else if _, err := os.Stat(targetPath); !os.IsNotExist(err) {
				t.Fatalf("rejected copy created data: %v", err)
			}
			if raw, err := os.ReadFile(sourcePath); err != nil || string(raw) != "retained source" {
				t.Fatal("source changed", err)
			}
		})
	}
}

func TestDataSchemaDefinitionValidation(t *testing.T) {
	for _, schema := range []*DataSchema{
		{ID: "Bad", Version: 1, Accepts: []int{1}}, {ID: "data", Version: 0, Accepts: []int{1}},
		{ID: "data", Version: 1}, {ID: "data", Version: 1, Accepts: []int{2}},
		{ID: "data", Version: 1, Accepts: []int{1, 1}}, {ID: "data", Version: 1, Accepts: []int{1, -1}},
	} {
		def := definition()
		def.DataSchema = schema
		if def.Validate() == nil {
			t.Fatal("invalid schema accepted", schema)
		}
	}
}

func TestInstalledManifestRejectsConflictingDataSchema(t *testing.T) {
	m, _, _ := setup(t)
	def := definition()
	def.DataSchema = &DataSchema{ID: "data", Version: 1, Accepts: []int{1}}
	manifest, _ := json.Marshal(map[string]any{"id": def.AppID, "version": def.Version, "dataSchema": &DataSchema{ID: "data", Version: 2, Accepts: []int{2}}})
	b, digest := bundle(t, def, map[string]string{"app.json": string(manifest)})
	if _, err := m.Stage(digest, 0, b); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Submit(Request{Protocol: 1, OperationID: "install-schema", Action: "install", Digest: digest, Scope: "schema"}); err != nil {
		t.Fatal(err)
	}
	if op := wait(t, m, "install-schema"); op.State != "succeeded" {
		t.Fatal(op)
	}
	if _, err := m.InstalledManifest(digest); err == nil {
		t.Fatal("conflicting schema accepted")
	}
}

func TestDataSchemaLedgerRequiresVersionFence(t *testing.T) {
	m, _, _ := setup(t)
	def := definition()
	def.DataSchema = &DataSchema{ID: "data", Version: 1, Accepts: []int{1}}
	b, digest := bundle(t, def, nil)
	if _, err := m.Stage(digest, 0, b); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Submit(Request{Protocol: 1, OperationID: "schema-install", Action: "install", Digest: digest, Scope: "schema"}); err != nil {
		t.Fatal(err)
	}
	if op := wait(t, m, "schema-install"); op.State != "succeeded" {
		t.Fatal(op)
	}
	m.mu.Lock()
	ledger := clone(m.ledger)
	m.mu.Unlock()
	m.Close()
	ledger.Protocol = 7
	raw, err := json.Marshal(ledger)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(m.root, "ledger.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, "owner", "node", m.caps, m.driver)
	if err == nil {
		reopened.Close()
		t.Fatal("unfenced schema ledger accepted")
	}
}
