package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// A node installed only as `fleet` gave its commands no `pantheon`.
func TestANodeGivesItsCommandsThePantheonCommand(t *testing.T) {
	empty := t.TempDir()
	t.Setenv("PATH", empty)
	state := t.TempDir()
	exposeCommand(state)
	found, err := exec.LookPath("pantheon")
	if err != nil {
		t.Fatal(err)
	}
	exe, _ := os.Executable()
	exe, _ = filepath.EvalSymlinks(exe)
	if got, _ := filepath.EvalSymlinks(found); got != exe || filepath.Dir(found) != filepath.Join(state, "bin") {
		t.Fatalf("%s -> %s, want %s", found, got, exe)
	}
	// Started again, the node reuses the link.
	t.Setenv("PATH", empty)
	exposeCommand(state)
	if again, err := exec.LookPath("pantheon"); err != nil || again != found {
		t.Fatal(again, err)
	}
}
