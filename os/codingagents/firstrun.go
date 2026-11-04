package codingagents

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/pantheoncli"
)

// First start of an agent's own interface: Claude Code asks for a theme, to
// trust the folder and to accept bypass permissions before a session opens,
// two of them with "No, exit" preselected; Gemini CLI asks to trust the
// folder. A launch from Coding Agents answers what it can before the agent
// starts:
//
//   - The bypass-permissions consent goes in the launch's own --settings
//     file (ClaudeSettings), only when the launch passes the flag. No
//     file of the user's changes.
//   - Onboarding, theme and the launched folder's trust are Claude Code's
//     own configuration (<config dir>/.claude.json). They are seeded only
//     where that configuration is PantheonOS's to set: on a hosted Workspace
//     (a sandbox or pod node, whose home the platform provides), or where
//     Claude Code's config dir lies in the membership's pantheon home
//     (PANTHEON_CLI_HOME, ~/.pantheon/cli for the first fleet). On a
//     person's own computer their ~/.claude.json is never touched, and the
//     questions stay theirs to answer.
//   - Gemini CLI's folder trust is GEMINI_CLI_TRUST_WORKSPACE for that
//     process, on the same nodes.
//
// Codex asks nothing on a full-auto launch (its trust question is skipped
// when the sandbox mode is given), so it needs no answer.

// ManagedHome reports whether an agent's configuration under dir is
// PantheonOS's to set: on a hosted Workspace node, or under the membership's
// pantheon home.
func ManagedHome(dir string) bool {
	switch os.Getenv("FLEET_NODE_KIND") {
	case "sandbox", "pod":
		return true
	}
	if dir == "" {
		return false
	}
	home, err := pantheoncli.ConfigDir()
	if err != nil || home == "" {
		return false
	}
	return within(home, dir)
}

// within reports whether path is root or inside it.
func within(root, path string) bool {
	rel, err := filepath.Rel(filepath.Clean(root), filepath.Clean(path))
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

// envValue is NAME's value in env, else this process's.
func envValue(env []string, name string) string {
	v, found := "", false
	for _, kv := range env {
		if value, ok := strings.CutPrefix(kv, name+"="); ok {
			v, found = value, true
		}
	}
	if !found {
		v = os.Getenv(name)
	}
	return strings.TrimSpace(v)
}

// ClaudeConfigFile is the file Claude Code keeps its global configuration
// in for a process with env: <CLAUDE_CONFIG_DIR>/.claude.json, else
// ~/.claude.json; the legacy <config dir>/.config.json while that one is
// in use. dir is the config dir itself.
func ClaudeConfigFile(env []string) (file, dir string) {
	home := envValue(env, "HOME")
	if home == "" {
		home, _ = os.UserHomeDir()
	}
	dir = envValue(env, "CLAUDE_CONFIG_DIR")
	if dir == "" {
		dir = filepath.Join(home, ".claude")
		file = filepath.Join(home, ".claude.json")
	} else {
		file = filepath.Join(dir, ".claude.json")
	}
	if st, err := os.Stat(filepath.Join(dir, ".config.json")); err == nil && st.Mode().IsRegular() && st.Size() > 0 {
		file = filepath.Join(dir, ".config.json")
	}
	return file, dir
}

// PrepareFirstRun answers an agent's first-start questions for a launch in
// cwd with env, where its configuration is PantheonOS's to set
// (ManagedHome). It returns the variables the launch adds (nil for most).
// A failure leaves the agent to ask, as it would have.
func PrepareFirstRun(agentID, cwd string, env []string) []string {
	switch agentID {
	case "claude":
		file, dir := ClaudeConfigFile(env)
		if ManagedHome(dir) {
			_ = seedClaudeConfig(file, cwd)
		}
	case "gemini":
		if envValue(env, "GEMINI_CLI_TRUST_WORKSPACE") == "" && ManagedHome(envValue(env, "GEMINI_CLI_HOME")) {
			return []string{"GEMINI_CLI_TRUST_WORKSPACE=true"}
		}
	}
	return nil
}

// claudeSeedTheme is the theme a seeded configuration starts with (Claude
// Code's own default).
const claudeSeedTheme = "dark"

// seedClaudeConfig merges into Claude Code's configuration file what its
// first interactive start would ask: onboarding done, a theme, and cwd
// trusted. Only missing values are added; everything else in the file
// (sign-in, settings, other projects) is kept as it is. A file that does not
// parse is left alone. The write waits for Claude Code's own lock on the
// file and replaces it in one rename.
func seedClaudeConfig(file, cwd string) error {
	var doc map[string]any
	b, err := os.ReadFile(file)
	switch {
	case err == nil && len(bytes.TrimSpace(b)) > 0:
		dec := json.NewDecoder(bytes.NewReader(b))
		dec.UseNumber()
		if err := dec.Decode(&doc); err != nil || doc == nil {
			return errors.New("claude config does not parse")
		}
	case err == nil, errors.Is(err, os.ErrNotExist):
		doc = map[string]any{}
	default:
		return err
	}
	changed := false
	if v, _ := doc["hasCompletedOnboarding"].(bool); !v {
		doc["hasCompletedOnboarding"] = true
		changed = true
	}
	if s, _ := doc["theme"].(string); s == "" {
		doc["theme"] = claudeSeedTheme
		changed = true
	}
	if cwd != "" && filepath.IsAbs(cwd) {
		projects, _ := doc["projects"].(map[string]any)
		if projects == nil {
			if doc["projects"] != nil {
				return errors.New("claude config projects is not an object")
			}
			projects = map[string]any{}
		}
		for _, key := range trustKeys(cwd) {
			p, _ := projects[key].(map[string]any)
			if p == nil {
				if projects[key] != nil {
					continue
				}
				p = map[string]any{}
			}
			if v, _ := p["hasTrustDialogAccepted"].(bool); v {
				continue
			}
			p["hasTrustDialogAccepted"] = true
			projects[key] = p
			changed = true
		}
		doc["projects"] = projects
	}
	if !changed {
		return nil
	}
	out, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return err
	}
	return writeUnderClaudeLock(file, append(out, '\n'))
}

// trustKeys are the project keys Claude Code may look cwd up by: the path
// as given and its real path.
func trustKeys(cwd string) []string {
	keys := []string{filepath.Clean(cwd)}
	if real, err := filepath.EvalSymlinks(cwd); err == nil && real != keys[0] {
		keys = append(keys, real)
	}
	return keys
}

// writeUnderClaudeLock replaces file with data while Claude Code does not
// hold its lock on it (a "<file>.lock" folder): it waits up to two seconds
// for a held lock, then gives up rather than race a running Claude Code.
// The file keeps its mode (owner-only when new).
func writeUnderClaudeLock(file string, data []byte) error {
	if real, err := filepath.EvalSymlinks(file); err == nil {
		file = real // a linked file (dotfiles) stays linked
	}
	lock := file + ".lock"
	for deadline := time.Now().Add(2 * time.Second); ; {
		st, err := os.Stat(lock)
		if err != nil || time.Since(st.ModTime()) > 30*time.Second {
			break
		}
		if time.Now().After(deadline) {
			return errors.New("claude config is locked")
		}
		time.Sleep(100 * time.Millisecond)
	}
	mode := os.FileMode(0o600)
	if st, err := os.Stat(file); err == nil {
		mode = st.Mode().Perm()
	}
	if err := os.MkdirAll(filepath.Dir(file), 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(file), ".claude.json.pantheon-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), file)
}
