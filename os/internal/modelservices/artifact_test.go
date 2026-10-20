package modelservices

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// treeFromDir reads a source directory as the release ships it.
func treeFromDir(t *testing.T, root string) Tree {
	t.Helper()
	tree := Tree{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		info, _ := d.Info()
		data, err := os.ReadFile(path)
		tree[filepath.ToSlash(rel)] = File{Data: data, Executable: info.Mode()&0o111 != 0}
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return tree
}

func pythonFor(t *testing.T) (string, string) {
	t.Helper()
	runtime, _ := filepath.Abs("../../..")
	python := os.Getenv("PANTHEON_TEST_PYTHON")
	if python == "" {
		python = "/Users/weizexu/Projects/model-services-design-2026-09-21/group-container/runtime/.venv/bin/python"
	}
	if _, err := os.Stat(python); err != nil {
		t.Skip("set PANTHEON_TEST_PYTHON to a Python with the runtime's dependencies")
	}
	return python, runtime
}

// pythonEnv makes a Python the tests start import this checkout's packages
// (os/python, apps/agent), not whatever a shared environment has installed.
func pythonEnv(runtime string) []string {
	return append(os.Environ(), "PYTHONPATH="+strings.Join([]string{
		filepath.Join(runtime, "os", "python"), filepath.Join(runtime, "apps", "agent"), runtime,
	}, string(os.PathListSeparator)))
}

// The connector package must be byte-identical to the Python build, so
// installed revisions keep matching.
func TestBuildArtifactMatchesPython(t *testing.T) {
	python, runtime := pythonFor(t)
	source := filepath.Join(runtime, "apps", "model-service")
	for _, platform := range []string{"linux-amd64", "darwin-arm64"} {
		cmd := exec.Command(python, "-c", `import sys
from pathlib import Path
from pantheonos.appkit.lifecycle import build_artifact
from pantheonos.appkit.portable import execution_package
with execution_package(Path(sys.argv[1]), sys.argv[2]) as root:
    payload, digest = build_artifact(root, sys.argv[2])
print(digest)`, source, platform)
		cmd.Dir, cmd.Env = runtime, pythonEnv(runtime)
		out, err := cmd.Output()
		if err != nil {
			t.Fatal(err)
		}
		_, digest, err := BuildArtifact(treeFromDir(t, source), platform)
		if err != nil || digest != strings.TrimSpace(string(out)) {
			t.Fatalf("%s: go %s python %s (%v)", platform, digest, out, err)
		}
	}
}
