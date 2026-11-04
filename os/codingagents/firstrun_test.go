package codingagents

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func readJSON(t *testing.T, file string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func trusted(m map[string]any, dir string) bool {
	p, _ := m["projects"].(map[string]any)
	e, _ := p[dir].(map[string]any)
	v, _ := e["hasTrustDialogAccepted"].(bool)
	return v
}

func TestFirstRunLeavesAPersonalHomeAlone(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("FLEET_NODE_KIND", "")
	t.Setenv("PANTHEON_CLI_HOME", "")
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	PrepareFirstRun("claude", filepath.Join(home, "proj"), nil)
	if _, err := os.Stat(filepath.Join(home, ".claude.json")); !os.IsNotExist(err) {
		t.Fatalf("personal ~/.claude.json was written: %v", err)
	}
	if env := PrepareFirstRun("gemini", home, nil); env != nil {
		t.Fatalf("gemini env on a personal machine: %v", env)
	}
}

func TestFirstRunSeedsAWorkspaceAndKeepsTheUsersValues(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("FLEET_NODE_KIND", "sandbox")
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	file := filepath.Join(home, ".claude.json")
	other := filepath.Join(home, "other")
	existing := `{"theme":"light","oauthAccount":{"emailAddress":"x@y"},"numStartups":7,"userID":"123456789012345678901234567890",
"projects":{"` + other + `":{"allowedTools":["Bash"],"hasTrustDialogAccepted":false}}}`
	if err := os.WriteFile(file, []byte(existing), 0o640); err != nil {
		t.Fatal(err)
	}
	cwd := filepath.Join(home, "proj")
	if err := os.MkdirAll(cwd, 0o755); err != nil {
		t.Fatal(err)
	}
	PrepareFirstRun("claude", cwd, nil)
	m := readJSON(t, file)
	if m["hasCompletedOnboarding"] != true || m["theme"] != "light" || m["numStartups"] != float64(7) ||
		m["userID"] != "123456789012345678901234567890" || m["oauthAccount"] == nil {
		t.Fatalf("merge lost or changed values: %v", m)
	}
	if !trusted(m, cwd) || trusted(m, other) {
		t.Fatalf("trust: %v", m["projects"])
	}
	if p := m["projects"].(map[string]any)[other].(map[string]any); !slices.Equal(p["allowedTools"].([]any), []any{"Bash"}) {
		t.Fatalf("other project changed: %v", p)
	}
	if st, _ := os.Stat(file); st.Mode().Perm() != 0o640 {
		t.Fatalf("mode %v", st.Mode())
	}
	if env := PrepareFirstRun("gemini", cwd, nil); !slices.Equal(env, []string{"GEMINI_CLI_TRUST_WORKSPACE=true"}) {
		t.Fatalf("gemini env %v", env)
	}
	if env := PrepareFirstRun("gemini", cwd, []string{"GEMINI_CLI_TRUST_WORKSPACE=false"}); env != nil {
		t.Fatalf("gemini env over the user's: %v", env)
	}
}

func TestFirstRunNewConfigAndUnreadableConfig(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("FLEET_NODE_KIND", "sandbox")
	cfg := filepath.Join(home, "cfg")
	PrepareFirstRun("claude", "/work", []string{"CLAUDE_CONFIG_DIR=" + cfg})
	file := filepath.Join(cfg, ".claude.json")
	m := readJSON(t, file)
	if m["hasCompletedOnboarding"] != true || m["theme"] != "dark" || !trusted(m, "/work") {
		t.Fatalf("seed %v", m)
	}
	if st, _ := os.Stat(file); st.Mode().Perm() != 0o600 {
		t.Fatalf("new file mode %v", st.Mode())
	}
	bad := []byte("{not json")
	if err := os.WriteFile(file, bad, 0o600); err != nil {
		t.Fatal(err)
	}
	PrepareFirstRun("claude", "/work2", []string{"CLAUDE_CONFIG_DIR=" + cfg})
	if b, _ := os.ReadFile(file); string(b) != string(bad) {
		t.Fatalf("a config that does not parse was rewritten: %q", b)
	}
}

func TestFirstRunUnderTheMembershipHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("FLEET_NODE_KIND", "")
	cli := filepath.Join(home, "state", "fleets", "f_x", "cli")
	t.Setenv("PANTHEON_CLI_HOME", cli)
	// Pointed at the membership's home: seeded.
	PrepareFirstRun("claude", "/w", []string{"CLAUDE_CONFIG_DIR=" + filepath.Join(cli, "claude")})
	if m := readJSON(t, filepath.Join(cli, "claude", ".claude.json")); m["hasCompletedOnboarding"] != true {
		t.Fatalf("membership home not seeded: %v", m)
	}
	// Anywhere else: not.
	elsewhere := filepath.Join(home, "elsewhere")
	PrepareFirstRun("claude", "/w", []string{"CLAUDE_CONFIG_DIR=" + elsewhere})
	if _, err := os.Stat(filepath.Join(elsewhere, ".claude.json")); !os.IsNotExist(err) {
		t.Fatalf("config outside the membership home written: %v", err)
	}
	if ManagedHome(cli+"-other") || !ManagedHome(cli) {
		t.Fatal("within")
	}
}

func TestFirstRunWaitsOutAHeldLock(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("FLEET_NODE_KIND", "pod")
	if err := os.Mkdir(filepath.Join(home, ".claude.json.lock"), 0o700); err != nil {
		t.Fatal(err)
	}
	PrepareFirstRun("claude", "/w", nil)
	if _, err := os.Stat(filepath.Join(home, ".claude.json")); !os.IsNotExist(err) {
		t.Fatalf("written while Claude Code held its lock: %v", err)
	}
}

func TestClaudeSettingsBypassConsent(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	var m map[string]any
	_ = json.Unmarshal(ClaudeSettings(true, true), &m)
	if m["skipDangerousModePermissionPrompt"] != true || m["hooks"] == nil {
		t.Fatalf("auto launch settings %v", m)
	}
	m = nil
	_ = json.Unmarshal(ClaudeSettings(true, false), &m)
	if _, ok := m["skipDangerousModePermissionPrompt"]; ok {
		t.Fatalf("consent without the flag: %v", m)
	}
	m = nil
	_ = json.Unmarshal(ClaudeSettings(false, true), &m)
	if m["hooks"] != nil || m["skipDangerousModePermissionPrompt"] != true {
		t.Fatalf("no-hooks settings %v", m)
	}
}
