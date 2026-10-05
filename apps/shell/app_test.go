package shellapp

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRunCommandBasicAndSessionPersistence(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()

	res, err := app.runCommand(map[string]any{"command": "echo hello-go-shell"})
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := res["success"].(bool); !ok {
		t.Fatalf("run_command failed: %v", res)
	}
	if !strings.Contains(res["output"].(string), "hello-go-shell") {
		t.Fatalf("missing output: %v", res)
	}
	if res["status"] != "completed" || res["truncated"] != false {
		t.Fatalf("unexpected result shape: %v", res)
	}

	// env + cwd persist across commands in the same (default-keyed) session
	if _, err := app.runCommand(map[string]any{"command": "export P3_MARK=alive; cd /"}); err != nil {
		t.Fatal(err)
	}
	res, err = app.runCommand(map[string]any{"command": "echo $P3_MARK $(pwd)"})
	if err != nil {
		t.Fatal(err)
	}
	if out := res["output"].(string); !strings.Contains(out, "alive /") {
		t.Fatalf("session state lost: %q", out)
	}
}

func TestSessionKeyIsolation(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()

	ctxA := map[string]any{"context_variables": map[string]any{"chat_id": "chat-a"}}
	ctxB := map[string]any{"context_variables": map[string]any{"chat_id": "chat-b"}}
	if _, err := app.runCommand(map[string]any{"command": "export WHO=a", "context_variables": ctxA["context_variables"]}); err != nil {
		t.Fatal(err)
	}
	res, err := app.runCommand(map[string]any{"command": "echo WHO=${WHO:-unset}", "context_variables": ctxB["context_variables"]})
	if err != nil {
		t.Fatal(err)
	}
	if out := res["output"].(string); !strings.Contains(out, "WHO=unset") {
		t.Fatalf("chats shared a shell: %q", out)
	}
}

func TestOutputWithoutTrailingNewline(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()
	for _, value := range []string{"first", "next\\nlast", "中文", ""} {
		res, err := app.runCommand(map[string]any{"command": "printf '" + value + "'"})
		if err != nil {
			t.Fatal(err)
		}
		want := strings.ReplaceAll(value, "\\n", "\n")
		if res["status"] != "completed" || res["output"] != want {
			t.Fatalf("output lost or framing leaked: want %q, got %v", want, res)
		}
	}
}

func TestTimeoutThenDrain(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()

	res, err := app.runCommand(map[string]any{
		"command": "echo before; sleep 2; echo after", "timeout": 1})
	if err != nil {
		t.Fatal(err)
	}
	if res["status"] != "timeout" {
		t.Fatalf("expected timeout status: %v", res)
	}
	out := res["output"].(string)
	if !strings.Contains(out, "before") || strings.Contains(out, "after") {
		t.Fatalf("partial output wrong: %q", out)
	}
	if !strings.Contains(out, "interrupted because of the timeout") {
		t.Fatalf("missing timeout warning: %q", out)
	}

	// drain with get_shell_output using the same shell id
	shellID := res["shell_id"].(string)
	drained := app.getShellOutput(shellID, 5, 0)
	if ok, _ := drained["success"].(bool); !ok {
		t.Fatalf("drain failed: %v", drained)
	}
	if !strings.Contains(drained["output"].(string), "after") {
		t.Fatalf("drain missing completed output: %v", drained)
	}
	if drained["status"] != "completed" {
		t.Fatalf("drain should complete: %v", drained)
	}
}

func TestManualShellLifecycle(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()

	created, err := app.newShell()
	if err != nil {
		t.Fatal(err)
	}
	id := created["shell_id"].(string)
	res := app.runInShell(id, "echo in-manual-shell", 0)
	if !strings.Contains(res["output"].(string), "in-manual-shell") {
		t.Fatalf("manual shell run failed: %v", res)
	}
	if res["command"] != "echo in-manual-shell" {
		t.Fatalf("command echo missing: %v", res)
	}
	closed := app.closeShell(id)
	if ok, _ := closed["success"].(bool); !ok {
		t.Fatalf("close failed: %v", closed)
	}
	again := app.closeShell(id)
	if ok, _ := again["success"].(bool); ok || again["error"] != "Shell not found" {
		t.Fatalf("double close should be Shell not found: %v", again)
	}
}

func TestTruncateMirrorsPython(t *testing.T) {
	long := strings.Repeat("x", 5000)
	out := truncate(long, 1000)
	if len(out) > 1100 {
		t.Fatalf("truncate too long: %d", len(out))
	}
	if !strings.Contains(out, "...truncated...") || !strings.Contains(out, "[truncated 4,000/5,000 chars]") {
		// Go %d has no thousands separators — accept the unseparated form too
		if !strings.Contains(out, "[truncated 4000/5000 chars]") {
			t.Fatalf("truncate format: %q", out[len(out)-60:])
		}
	}
}

func TestBusyOwnerNeverBorrowsAnotherOwnersShell(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()
	command := func(owner, text string, timeout int) map[string]any {
		t.Helper()
		res, err := app.runCommand(map[string]any{"command": text, "timeout": timeout,
			"context_variables": map[string]any{"chat_id": owner}})
		if err != nil {
			t.Fatal(err)
		}
		return res
	}
	a := command("a", "export WHO=owner-a; mkdir -p a; cd a", 5)
	b := command("b", "export WHO=owner-b; mkdir -p b; cd b", 5)
	if a["shell_id"] == b["shell_id"] {
		t.Fatal("owners shared a session")
	}
	busy := command("a", "echo begin; sleep 2; echo end", 1)
	if busy["status"] != "timeout" {
		t.Fatal(busy)
	}
	rejected := command("a", "export WHO=intruder; touch leaked", 1)
	if rejected["success"] != false || rejected["status"] != "busy" || rejected["shell_id"] != a["shell_id"] {
		t.Fatal("busy request was not rejected on its own session", rejected)
	}
	other := command("b", "echo $WHO; basename \"$PWD\"; test ! -f leaked && echo clean", 5)
	if out, _ := other["output"].(string); !strings.Contains(out, "owner-b\nb\nclean") {
		t.Fatal("other owner's environment was touched", other)
	}
	drained := app.getShellOutput(a["shell_id"].(string), 5, 0)
	if drained["status"] != "completed" || !strings.Contains(drained["output"].(string), "end") {
		t.Fatal("pending command output was lost", drained)
	}
	again := command("a", "echo $WHO; basename \"$PWD\"; test ! -f leaked && echo clean", 5)
	if out, _ := again["output"].(string); !strings.Contains(out, "owner-a\na\nclean") {
		t.Fatal("owner state was replaced", again)
	}
}

func TestExplicitSessionRejectsConcurrentCommandAndOutputReader(t *testing.T) {
	dir := t.TempDir()
	app := NewApp(dir)
	defer app.Close()
	created, err := app.newShell()
	if err != nil {
		t.Fatal(err)
	}
	id := created["shell_id"].(string)
	done := make(chan map[string]any, 1)
	go func() {
		done <- app.runInShell(id, "touch entered; while [ ! -f release ]; do sleep 0.01; done; echo original-output", 5)
	}()
	defer os.WriteFile(filepath.Join(dir, "release"), nil, 0600)
	entered := false
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); {
		if _, err := os.Stat(filepath.Join(dir, "entered")); err == nil {
			entered = true
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if !entered {
		t.Fatal("shell did not start the command")
	}
	for _, command := range []string{"touch unexpected", ""} {
		res := app.runInShell(id, command, 1)
		if res["success"] != false || res["status"] != "busy" {
			t.Fatal("concurrent caller entered the same stream", res)
		}
	}
	if err := os.WriteFile(filepath.Join(dir, "release"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	select {
	case result := <-done:
		if result["status"] != "completed" || !strings.Contains(result["output"].(string), "original-output") {
			t.Fatal("command completion was stolen", result)
		}
	case <-time.After(6 * time.Second):
		t.Fatal("original command never finished")
	}
	if _, err := os.Stat(filepath.Join(dir, "unexpected")); !os.IsNotExist(err) {
		t.Fatal("rejected command ran")
	}
}

func TestClosedExplicitSessionIsNotReplaced(t *testing.T) {
	app := NewApp(t.TempDir())
	defer app.Close()
	created, err := app.newShell()
	if err != nil {
		t.Fatal(err)
	}
	id := created["shell_id"].(string)
	app.closeShell(id)
	res, err := app.runCommand(map[string]any{"shell_id": id, "command": "touch unexpected"})
	if err != nil || res["success"] != false || len(app.shells) != 0 {
		t.Fatal("lost explicit session silently replaced", res, err)
	}
}
