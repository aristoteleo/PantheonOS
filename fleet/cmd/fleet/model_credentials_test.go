package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalCredentialCLIAndPrivateReader(t *testing.T) {
	state := t.TempDir()
	const key = "synthetic-local-api-key"
	opts := []string{"put", "--state-dir", state, "--fleet", "test-fleet", "--name", "provider", "--endpoint", "https://api.example/v1", "--stdin"}
	var output bytes.Buffer
	if err := modelCredentials(opts, strings.NewReader(key+"\n"), &output); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), key) || !strings.Contains(output.String(), "node-secret://provider") {
		t.Fatal("provisioning revealed contents or lost reference")
	}
	output.Reset()
	if err := modelCredentials([]string{"list", "--state-dir", state, "--fleet", "test-fleet"}, nil, &output); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), key) {
		t.Fatal("list revealed contents")
	}
	t.Setenv("PANTHEON_MODEL_CREDENTIALS", filepath.Join(state, "apps", "test-fleet", "model-credentials"))
	output.Reset()
	if err := readModelCredential(strings.NewReader(`{"ref":"node-secret://provider","endpoint":"https://other.example/v1"}`), &output); err == nil || output.Len() != 0 {
		t.Fatal("reader forwarded key to different endpoint")
	}
	if err := readModelCredential(strings.NewReader(`{"ref":"node-secret://provider","endpoint":"https://api.example/v1"}`), &output); err != nil {
		t.Fatal(err)
	}
	var result map[string]string
	if json.Unmarshal(output.Bytes(), &result) != nil || result["key"] != key {
		t.Fatal("private pipe did not receive key")
	}
	output.Reset()
	if err := modelCredentials([]string{"delete", "--state-dir", state, "--fleet", "test-fleet", "--name", "provider"}, nil, &output); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), key) {
		t.Fatal("delete revealed key")
	}
}
func TestCredentialCLIRejectsArgumentsContainingSecrets(t *testing.T) {
	var output bytes.Buffer
	for _, args := range [][]string{
		{"put", "--fleet", "f", "--key", "synthetic-secret"},
		{"put", "--fleet", "f", "synthetic-secret"},
		{"put", "--fleet", "../other", "--stdin"},
		{"put", "--fleet", "f", "--stdin", "--file", "unused"},
	} {
		err := modelCredentials(args, strings.NewReader("synthetic-secret"), &output)
		if err == nil || strings.Contains(err.Error(), "synthetic-secret") || output.Len() != 0 {
			t.Fatal("unsafe CLI input response")
		}
	}
	path := filepath.Join(t.TempDir(), "input")
	if err := os.WriteFile(path, []byte("synthetic-secret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	args := []string{"put", "--state-dir", t.TempDir(), "--fleet", "f", "--name", "provider", "--endpoint", "https://api.example", "--file", path}
	if err := modelCredentials(args, nil, &output); err != nil {
		t.Fatal(err)
	}
}
