//go:build linux

package codingagents

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// bubblewrap, where it runs: the folder and home are writable, .team only
// through its inbox, nothing else of the computer's home is there, and a
// process outside cannot be signalled (a process namespace of its own).
func TestBubblewrapKeepsToTheFolder(t *testing.T) {
	if kind, why := SandboxKind(); kind == "" {
		t.Skip(why)
	}
	base := t.TempDir()
	folder, dir, outside := filepath.Join(base, "agent"), filepath.Join(base, "sbx"), filepath.Join(base, "secret.txt")
	inbox := filepath.Join(folder, ".team", "inbox")
	_ = os.MkdirAll(inbox, 0o700)
	_ = os.WriteFile(outside, []byte("secret"), 0o600)
	t.Setenv("HOME", t.TempDir())
	victim := exec.Command("/bin/sleep", "60")
	_ = victim.Start()
	defer victim.Process.Kill()
	s := Sandbox{Folder: folder, TeamDir: filepath.Join(folder, ".team"), Inbox: inbox, Dir: dir, Program: "/bin/sh"}
	if err := s.Prepare(); err != nil {
		t.Fatal(err)
	}
	for script, ok := range map[string]bool{
		"echo x > " + folder + "/f && cat " + folder + "/f": true,
		"echo x > " + s.Home() + "/f":                       true,
		"echo x > " + inbox + "/r":                          true,
		"cat " + outside:                                    false,
		"echo x > " + folder + "/.team/script":              false,
		fmt.Sprintf("kill -0 %d", victim.Process.Pid):       false,
	} {
		prog, args, err := s.Wrap([]string{"-c", script})
		if err != nil {
			t.Fatal(err)
		}
		if err := exec.Command(prog, args...).Run(); (err == nil) != ok {
			t.Errorf("%q: err %v, want ok=%v", script, err, ok)
		}
	}
	// Outside its folder and home it writes only in its own throwaway
	// /tmp: nothing reaches the computer's.
	prog, args, _ := s.Wrap([]string{"-c", "echo x > " + base + "/new"})
	_ = exec.Command(prog, args...).Run()
	if _, err := os.Stat(filepath.Join(base, "new")); err == nil {
		t.Error("a write outside the folder reached the computer")
	}
}
