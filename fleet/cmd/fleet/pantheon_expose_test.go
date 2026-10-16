package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
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

// The node reads `pantheon node X` and `fleet [node] X` alike; under the name
// `pantheon`, anything else is not the node's (`pantheon up` is local mode),
// so a node that restarts itself as `pantheon` goes through `node`.
func TestTheNodeReadsItsCommandUnderEitherName(t *testing.T) {
	saved := os.Args
	defer func() { os.Args = saved }()
	for _, c := range []struct {
		argv  []string
		node  []string
		argv0 []string
	}{
		{[]string{"/bin/pantheon", "node", "up", "--key", "k"}, []string{"up", "--key", "k"}, []string{"/bin/pantheon", "node"}},
		{[]string{"/bin/pantheon", "up"}, nil, []string{"/bin/pantheon", "node"}},
		{[]string{"/bin/fleet", "up"}, []string{"up"}, []string{"/bin/fleet"}},
		{[]string{"/bin/fleet", "node", "capture", "doctor"}, []string{"capture", "doctor"}, []string{"/bin/fleet"}},
	} {
		os.Args = c.argv
		if got := nodeCommandArgs(); !slices.Equal(got, c.node) {
			t.Errorf("%v: node reads %v", c.argv, got)
		}
		if got := nodeArgv0(); !slices.Equal(got, c.argv0) {
			t.Errorf("%v: restarts as %v", c.argv, got)
		}
	}
}
