package codingagents

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestTheNoteReachesAgentsWithoutAnInstructionsFlag(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	t.Setenv("OPENCODE_CONFIG_CONTENT", "")
	// OpenCode: an instructions file, merged into the configuration env gives.
	_, env, err := NoteLaunch("opencode", "use pantheon", []string{`OPENCODE_CONFIG_CONTENT={"instructions":["a.md"],"model":"m"}`, "X=1"})
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Instructions []string `json:"instructions"`
		Model        string   `json:"model"`
	}
	if err := json.Unmarshal([]byte(strings.TrimPrefix(env[0], "OPENCODE_CONFIG_CONTENT=")), &doc); err != nil {
		t.Fatal(err)
	}
	if len(doc.Instructions) != 2 || doc.Instructions[0] != "a.md" || doc.Model != "m" || env[1] != "X=1" {
		t.Fatalf("%+v %v", doc, env)
	}
	if b, _ := os.ReadFile(doc.Instructions[1]); string(b) != "use pantheon\n" {
		t.Fatalf("instructions file %q", b)
	}
	// Gemini CLI: GEMINI.md in an included folder.
	args, _, err := NoteLaunch("gemini", "use pantheon", nil)
	if err != nil || len(args) != 2 || args[0] != "--include-directories" {
		t.Fatalf("%v %v", args, err)
	}
	if b, _ := os.ReadFile(filepath.Join(args[1], "GEMINI.md")); !strings.Contains(string(b), "use pantheon") {
		t.Fatalf("GEMINI.md %q", b)
	}
	// Cursor Agent: a plugin whose session-start hook adds it.
	if runtime.GOOS != "windows" {
		args, _, err = NoteLaunch("cursor", "use pantheon", nil)
		if err != nil || len(args) != 2 || args[0] != "--plugin-dir" {
			t.Fatalf("%v %v", args, err)
		}
		hooks, _ := os.ReadFile(filepath.Join(args[1], "hooks", "hooks.json"))
		if !strings.Contains(string(hooks), "sessionStart") || !strings.Contains(string(hooks), "context.json") {
			t.Fatalf("hooks %s", hooks)
		}
		if _, err := os.Stat(filepath.Join(args[1], ".cursor-plugin", "plugin.json")); err != nil {
			t.Fatal(err)
		}
	}
	// Others, and no note: nothing.
	if args, env, _ := NoteLaunch("aider", "x", []string{"A=1"}); args != nil || len(env) != 1 {
		t.Fatal(args, env)
	}
	if args, _, _ := NoteLaunch("gemini", "", nil); args != nil {
		t.Fatal(args)
	}
}
