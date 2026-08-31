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

	res := app.open(100, 30, "", "")
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
	res := app.open(80, 24, "", "")
	if ok, _ := res["success"].(bool); !ok {
		t.Fatalf("open failed: %v", res)
	}
	initial := decode(t, res["initial_output"].(string))
	if !strings.Contains(initial, "an agent operating system") {
		t.Fatalf("banner missing from initial output: %q", initial[:min(len(initial), 200)])
	}
	app.Close()
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
