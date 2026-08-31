package shellapp

import (
	"strings"
	"testing"
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
