//go:build !windows

package ptyapp

import (
	"encoding/base64"
	"strings"
	"testing"
	"time"
)

func decode(t *testing.T, s string) string {
	t.Helper()
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil {
		t.Fatalf("not base64: %v", err)
	}
	return string(b)
}

func TestOpenWriteAttachClose(t *testing.T) {
	t.Setenv("PANTHEON_PTY_BANNER", "0")
	app := NewApp(nil, "", t.TempDir())
	defer app.Close()

	res := app.open(100, 30, "", "", "")
	if ok, _ := res["success"].(bool); !ok {
		t.Fatalf("open failed: %v", res)
	}
	sid := res["session_id"].(string)
	if res["cols"] != 100 || res["rows"] != 30 {
		t.Fatalf("size not honored: %v", res)
	}

	// keystrokes reach the shell; output lands in scrollback for attach
	w := app.write(sid, base64.StdEncoding.EncodeToString([]byte("echo pty-go-alive\r")))
	if ok, _ := w["success"].(bool); !ok {
		t.Fatalf("write failed: %v", w)
	}
	deadline := time.Now().Add(5 * time.Second)
	var scroll string
	for time.Now().Before(deadline) {
		att := app.attach(sid, 0, 0)
		if ok, _ := att["success"].(bool); ok {
			scroll = decode(t, att["scrollback"].(string))
			if strings.Contains(scroll, "pty-go-alive") {
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !strings.Contains(scroll, "pty-go-alive") {
		t.Fatalf("scrollback never showed the echo: %q", scroll)
	}

	rz := app.resize(sid, 120, 40)
	if ok, _ := rz["success"].(bool); !ok || rz["cols"] != 120 {
		t.Fatalf("resize failed: %v", rz)
	}

	lst := app.list()
	if n := len(lst["sessions"].([]any)); n != 1 {
		t.Fatalf("expected 1 session, got %d", n)
	}

	cl := app.closeSession(sid)
	if ok, _ := cl["success"].(bool); !ok {
		t.Fatalf("close failed: %v", cl)
	}
	if again := app.closeSession(sid); again["success"].(bool) {
		t.Fatalf("double close should fail: %v", again)
	}
}

func TestBannerInInitialOutput(t *testing.T) {
	t.Setenv("PANTHEON_PTY_BANNER", "")
	app := NewApp(nil, "", t.TempDir())
	defer app.Close()
	res := app.open(80, 24, "", "", "")
	if ok, _ := res["success"].(bool); !ok {
		t.Fatalf("open failed: %v", res)
	}
	initial := decode(t, res["initial_output"].(string))
	if !strings.Contains(initial, "an agent operating system") {
		t.Fatalf("banner missing from initial output: %q", initial[:min(len(initial), 200)])
	}
	app.Close()
}

// A command (a coding agent's interface) runs in the login shell, and a
// shell takes over the window when it exits.
func TestOpenRunsACommandThenAShell(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("SHELL", "/bin/sh")
	app := NewApp(nil, "", t.TempDir())
	defer app.Close()
	res := app.open(80, 24, "", "/bin/sh", "echo 'agent '\"ran\"")
	if ok, _ := res["success"].(bool); !ok {
		t.Fatalf("open failed: %v", res)
	}
	sid := res["session_id"].(string)
	if strings.Contains(decode(t, res["initial_output"].(string)), "an agent operating system") {
		t.Fatal("a command session has no banner")
	}
	app.write(sid, base64.StdEncoding.EncodeToString([]byte("echo still-$((40+2))\r")))
	deadline := time.Now().Add(5 * time.Second)
	var scroll string
	for time.Now().Before(deadline) {
		if att := app.attach(sid, 0, 0); att["success"] == true {
			scroll = decode(t, att["scrollback"].(string))
			if strings.Contains(scroll, "still-42") {
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !strings.Contains(scroll, "agent ran") || !strings.Contains(scroll, "still-42") {
		t.Fatalf("scrollback %q", scroll)
	}
	if res["cwd"] != home {
		t.Fatalf("an agent without a folder starts in home, got %v", res["cwd"])
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
