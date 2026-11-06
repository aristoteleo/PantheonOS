package codingagents

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

// A script runtime's interpreter (#!/bin/sh) never opens / or the home
// folder to the sandbox.
func TestInstallReadsNeverOpenRootOrHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	bin := filepath.Join(home, "bin")
	_ = os.MkdirAll(bin, 0o755)
	prog := filepath.Join(bin, "claude")
	_ = os.WriteFile(prog, []byte("#!/bin/sh\nexit 0\n"), 0o755)
	reads := InstallReads(prog, nil)
	realHome, _ := filepath.EvalSymlinks(home)
	for _, p := range reads {
		if p == "/" || strings.Count(p, "/") < 2 || underDir(realHome, p) {
			t.Errorf("InstallReads opens %s: %v", p, reads)
		}
	}
	if !slices.Contains(reads, filepath.Join(realHome, "bin")) {
		t.Errorf("InstallReads lacks the program's folder: %v", reads)
	}
}

// The profile itself, run by sandbox-exec: the folder and home are
// readable and writable, a file beside them is not, .team only through its
// inbox, and only the gateway's loopback port is open.
func TestSeatbeltProfileKeepsToTheFolder(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS sandbox")
	}
	if kind, why := SandboxKind(); kind == "" {
		t.Skip(why)
	}
	base := t.TempDir()
	folder, dir, outside := filepath.Join(base, "agent"), filepath.Join(base, "sbx"), filepath.Join(base, "secret.txt")
	inbox := filepath.Join(folder, ".team", "inbox")
	_ = os.MkdirAll(inbox, 0o700)
	_ = os.WriteFile(outside, []byte("secret"), 0o600)
	t.Setenv("HOME", t.TempDir())
	s := Sandbox{Folder: folder, TeamDir: filepath.Join(folder, ".team"), Inbox: inbox, Dir: dir, Program: "/bin/sh"}
	if err := s.Prepare(); err != nil {
		t.Fatal(err)
	}
	run := func(script string) error {
		prog, args, err := s.Wrap([]string{"-c", script})
		if err != nil {
			t.Fatal(err)
		}
		return exec.Command(prog, args...).Run()
	}
	for script, ok := range map[string]bool{
		"echo x > " + folder + "/f && cat " + folder + "/f": true,
		"echo x > " + s.Home() + "/f":                       true,
		"echo x > " + inbox + "/r":                          true,
		"cat " + outside:                                    false,
		"echo x > " + base + "/new":                         false,
		"echo x > " + folder + "/.team/script":              false,
		"ls " + base:                                        false,
	} {
		if err := run(script); (err == nil) != ok {
			t.Errorf("%q: err %v, want ok=%v", script, err, ok)
		}
	}
}

func TestGatewayPort(t *testing.T) {
	if GatewayPort("http://127.0.0.1:4321") != 4321 || GatewayPort("http://example.com:80") != 0 || GatewayPort("http://localhost:1") != 0 {
		t.Fatal("GatewayPort")
	}
}
