package modelservices

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func testConnector(t *testing.T) *Connector {
	t.Helper()
	root, _ := filepath.Abs("../../../apps/model-service")
	c, err := NewConnector(treeFromDir(t, root))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

const gib = 1 << 30

func gpu(id string, mem int64) map[string]any {
	return map[string]any{"id": id, "backend": "cuda", "memory_bytes": mem, "exclusive": true}
}

// Engine packages generated in Go match the Python generator byte for byte.
func TestEnginePackageMatchesPython(t *testing.T) {
	python, runtime := pythonFor(t)
	c := testConnector(t)
	cases := []struct {
		target string
		config map[string]any
	}{
		{"linux-amd64", map[string]any{"recipe_id": "ollama-0.34.2-linux-amd64", "context_length": 8192, "parallel": 2, "keep_alive_seconds": 0,
			"load_policy": "on_demand", "resources": map[string]any{"memory_bytes": 16 * gib, "devices": []any{gpu("GPU-1", 24*gib)}}}},
		{"linux-amd64", map[string]any{"recipe_id": "ollama-0.34.2-linux-amd64", "context_length": 4096, "parallel": 1, "keep_alive_seconds": 300,
			"load_policy": "warm", "resources": map[string]any{"memory_bytes": 8 * gib, "devices": []any{}}}},
		{"darwin-arm64", map[string]any{"recipe_id": "llmster-0.0.25-1-darwin-arm64", "context_length": 8192, "parallel": 1, "keep_alive_seconds": 600,
			"resources": map[string]any{"memory_bytes": 32 * gib, "devices": []any{map[string]any{"id": "apple-metal", "backend": "metal", "memory_bytes": 24 * gib, "exclusive": false}}}}},
		{"linux-amd64", map[string]any{"recipe_id": "sglang-0.5.20-linux-amd64", "context_length": 32768, "parallel": 4, "keep_alive_seconds": 0,
			"model_artifact_sha256": strings.Repeat("a", 64), "tensor_parallel_size": 2, "load_policy": "resident",
			"resources": map[string]any{"memory_bytes": 64 * gib, "devices": []any{gpu("GPU-1", 80*gib), gpu("GPU-2", 80*gib)}}}},
		{"linux-amd64", map[string]any{"recipe_id": "sglang-0.5.20-linux-amd64-node", "model_recipe_id": "qwen3-8b", "context_length": 32768, "parallel": 8,
			"keep_alive_seconds": 0, "load_policy": "resident",
			"resources": map[string]any{"memory_bytes": 64 * gib, "devices": []any{gpu("GPU-1", 80*gib)}}}},
		{"linux-amd64", map[string]any{"recipe_id": "speaches-0.9.0-rc.3-linux-amd64-cpu", "model_recipe_id": "kokoro-82m-v1", "context_length": 512, "parallel": 1,
			"keep_alive_seconds": 0, "load_policy": "resident", "resources": map[string]any{"memory_bytes": 8 * gib, "devices": []any{}}}},
		{"linux-amd64", map[string]any{"recipe_id": "sglang-diffusion-0.5.20-linux-amd64", "model_recipe_id": "sdxl-turbo-71153311", "context_length": 512, "parallel": 1,
			"keep_alive_seconds": 0, "load_policy": "resident", "resources": map[string]any{"memory_bytes": 64 * gib, "devices": []any{gpu("GPU-1", 80*gib)}}}},
	}
	for _, tc := range cases {
		raw, _ := json.Marshal(tc.config)
		cmd := exec.Command(python, "-c", `import json,sys
from pantheon.models.managed import package
from pantheon.apps.lifecycle import build_artifact
with package(json.loads(sys.argv[1]), sys.argv[2]) as root:
    print(build_artifact(root, sys.argv[2])[1])`, string(raw), tc.target)
		cmd.Dir = runtime
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("%s: python: %s", tc.config["recipe_id"], out)
		}
		var config any
		_ = decode(raw, &config)
		tree, err := c.EnginePackage(config, tc.target)
		if err != nil {
			t.Fatalf("%s: %v", tc.config["recipe_id"], err)
		}
		_, digest, err := BuildArtifact(tree, tc.target)
		if err != nil || digest != strings.TrimSpace(string(out)) {
			t.Fatalf("%s: go %s python %s (%v)", tc.config["recipe_id"], digest, out, err)
		}
	}
}

func TestManagedValidationRefusesWhatPythonRefuses(t *testing.T) {
	c := testConnector(t)
	bad := []map[string]any{
		{"recipe_id": "ollama-0.34.2-linux-amd64", "context_length": 100, "parallel": 1, "keep_alive_seconds": 0, "resources": map[string]any{"memory_bytes": 8 * gib, "devices": []any{}}},
		{"recipe_id": "ollama-0.34.2-linux-amd64", "context_length": 4096, "parallel": 1, "keep_alive_seconds": 0, "load_policy": "warm", "resources": map[string]any{"memory_bytes": 8 * gib, "devices": []any{}}},
		{"recipe_id": "sglang-0.5.20-linux-amd64", "context_length": 4096, "parallel": 1, "keep_alive_seconds": 0, "resources": map[string]any{"memory_bytes": 8 * gib, "devices": []any{gpu("GPU-1", 80*gib)}}},
		{"recipe_id": "ollama-0.34.2-linux-amd64", "context_length": 4096, "parallel": 1, "keep_alive_seconds": 0, "surprise": 1, "resources": map[string]any{"memory_bytes": 8 * gib, "devices": []any{}}},
	}
	for _, b := range bad {
		raw, _ := json.Marshal(b)
		var config any
		_ = decode(raw, &config)
		if _, err := c.ValidateManaged(config, "linux-amd64"); err == nil {
			t.Fatalf("accepted %v", b)
		}
	}
}
