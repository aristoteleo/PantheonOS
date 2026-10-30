package codingagentsapp

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// Each agent's skills, from the places that agent loads them, with the
// node's settings for it (a replaced command, its environment).

// skills lists one agent's skills for the project folder cwd: Claude
// Code's (the default), Codex's, Gemini CLI's or Cursor Agent's. An agent
// without skills has none.
func skills(cwd, agent string) map[string]any {
	list, err := agentSkills(cwd, agent)
	if err != nil {
		return failure("%v", err)
	}
	return map[string]any{"success": true, "skills": list}
}

func agentSkills(cwd, agent string) ([]Skill, error) {
	if cwd != "" {
		cwd = filepath.Clean(expandHome(cwd))
	}
	if agent == "" {
		agent = "claude"
	}
	a, ok := codingagents.Lookup(agent)
	if !ok {
		return nil, fmt.Errorf("Unknown coding agent %q", agent)
	}
	if a.Headless == nil || !a.Headless.Canvas.Skills {
		return []Skill{}, nil
	}
	s := codingagents.LoadSettings()
	env := codingagents.UserEnv(s, agent)
	switch agent {
	case "claude":
		return skillList(cwd), nil
	case "codex":
		return codexSkills(cwd, env), nil
	case "gemini":
		return geminiSkills(cwd, env), nil
	case "cursor":
		return cursorSkills(cwd), nil
	}
	return []Skill{}, nil
}

// findSkill is the skill of one agent named name, for cwd.
func findSkill(cwd, agent, name string) (Skill, bool) {
	list, _ := agentSkills(cwd, agent)
	for _, s := range list {
		if s.Name == name {
			return s, true
		}
	}
	return Skill{}, false
}

// envValue is a variable's value in env (KEY=VALUE entries), the last wins.
func envValue(env []string, key string) string {
	v := ""
	for _, kv := range env {
		if k, val, ok := strings.Cut(kv, "="); ok && k == key {
			v = val
		}
	}
	return v
}

func homeDir() string {
	home, _ := os.UserHomeDir()
	return home
}

// skillDir is one folder of skills (<dir>/<name>/SKILL.md), their source
// and, for a plugin's or an extension's, its name.
type skillDir struct{ dir, source, plugin string }

// skillsFrom reads skill folders in order; a later skill of the same name
// replaces an earlier one when later wins, else the first is kept. keep, if
// set, decides which skills count, from their front matter.
func skillsFrom(dirs []skillDir, laterWins bool, keep func(meta map[string]string) bool) []Skill {
	byName := map[string]int{}
	out := []Skill{}
	for _, d := range dirs {
		for _, sk := range skillsIn(d.dir, d.source, "") {
			if keep != nil {
				if meta, _ := frontMatter(sk.Path); !keep(meta) {
					continue
				}
			}
			sk.Plugin = d.plugin
			if i, ok := byName[sk.Name]; ok {
				if laterWins {
					out[i] = sk
				}
				continue
			}
			byName[sk.Name] = len(out)
			out = append(out, sk)
		}
	}
	sortSkills(out)
	return out
}

// codexSkills are the skills Codex itself says it loads in cwd (its
// app-server's skills/list): the user's, the repository's, its own
// (system) and its plugins'. Where Codex cannot be asked, the folders it
// reads are listed instead, under the CODEX_HOME it runs with.
func codexSkills(cwd string, env []string) []Skill {
	dir := firstNonEmpty(cwd, homeDir())
	if r, err := resolve("codex"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if list, err := codingagents.CodexSkills(ctx, r.path, env, dir); err == nil {
			out := make([]Skill, 0, len(list))
			for _, k := range list {
				source := map[string]string{"repo": "project"}[k.Scope]
				if k.Plugin != "" {
					source = "plugin"
				}
				out = append(out, Skill{Name: k.Name, Description: k.Description, Path: k.Path,
					Source: firstNonEmpty(source, k.Scope), Plugin: k.Plugin})
			}
			sortSkills(out)
			return out
		}
	}
	codexHome := firstNonEmpty(envValue(env, "CODEX_HOME"), filepath.Join(homeDir(), ".codex"))
	dirs := []skillDir{{dir: filepath.Join(codexHome, "skills"), source: "user"},
		{dir: filepath.Join(homeDir(), ".agents", "skills"), source: "user"},
		{dir: filepath.Join(codexHome, "skills", ".system"), source: "system"}}
	if cwd != "" {
		dirs = append([]skillDir{{dir: filepath.Join(cwd, ".codex", "skills"), source: "project"},
			{dir: filepath.Join(cwd, ".agents", "skills"), source: "project"}}, dirs...)
	}
	return skillsFrom(dirs, false, nil)
}

// geminiSkills are Gemini CLI's, in the order it reads them, a later one
// replacing an earlier one of the same name: its built-in skills, those of
// the extensions enabled for cwd, ~/.gemini/skills, ~/.agents/skills, then
// the project's .gemini/skills and .agents/skills. Its home is
// GEMINI_CLI_HOME when that is set. A skill needs a name and a description
// in its front matter; those its settings list in skills.disabled are left
// out, and none are when skills.enabled is false.
func geminiSkills(cwd string, env []string) []Skill {
	home := firstNonEmpty(envValue(env, "GEMINI_CLI_HOME"), homeDir())
	settings := []string{filepath.Join(home, ".gemini", "settings.json")}
	if cwd != "" {
		settings = append(settings, filepath.Join(cwd, ".gemini", "settings.json"))
	}
	disabled := map[string]bool{}
	for _, f := range settings {
		var st struct {
			Skills struct {
				Enabled  *bool    `json:"enabled"`
				Disabled []string `json:"disabled"`
			} `json:"skills"`
		}
		if b, err := os.ReadFile(f); err != nil || json.Unmarshal(b, &st) != nil {
			continue
		}
		if st.Skills.Enabled != nil && !*st.Skills.Enabled {
			return []Skill{}
		}
		for _, n := range st.Skills.Disabled {
			disabled[strings.ToLower(n)] = true
		}
	}
	var dirs []skillDir
	if r, err := resolve("gemini"); err == nil {
		// Built in: next to the CLI's bundle (bundle/gemini.js, bundle/builtin).
		if bin, err := filepath.EvalSymlinks(r.path); err == nil {
			dirs = append(dirs, skillDir{dir: filepath.Join(filepath.Dir(bin), "builtin"), source: "system"})
		}
	}
	dirs = append(dirs, geminiExtensionSkills(home, firstNonEmpty(cwd, home))...)
	dirs = append(dirs, skillDir{dir: filepath.Join(home, ".gemini", "skills"), source: "user"},
		skillDir{dir: filepath.Join(home, ".agents", "skills"), source: "user"})
	if cwd != "" {
		dirs = append(dirs, skillDir{dir: filepath.Join(cwd, ".gemini", "skills"), source: "project"},
			skillDir{dir: filepath.Join(cwd, ".agents", "skills"), source: "project"})
	}
	list := skillsFrom(dirs, true, func(meta map[string]string) bool {
		_, hasDesc := meta["description"]
		return meta["name"] != "" && hasDesc
	})
	return slices.DeleteFunc(list, func(s Skill) bool { return disabled[strings.ToLower(s.Name)] })
}

// geminiExtensionSkills are the skill folders of Gemini CLI's extensions
// (~/.gemini/extensions/<dir>, or where a linked one's install record
// points) enabled for cwd by extension-enablement.json: an extension is on
// unless the last of its rules matching cwd starts with "!"; a rule ending
// in "*" covers the folders under it too.
func geminiExtensionSkills(home, cwd string) []skillDir {
	root := filepath.Join(home, ".gemini", "extensions")
	entries, _ := os.ReadDir(root)
	var enablement map[string]struct {
		Overrides []string `json:"overrides"`
	}
	if b, err := os.ReadFile(filepath.Join(root, "extension-enablement.json")); err == nil {
		_ = json.Unmarshal(b, &enablement)
	}
	at := filepath.ToSlash(cwd)
	if !strings.HasSuffix(at, "/") {
		at += "/"
	}
	var out []skillDir
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		dir := filepath.Join(root, e.Name())
		var install struct {
			Type   string `json:"type"`
			Source string `json:"source"`
		}
		if b, err := os.ReadFile(filepath.Join(dir, ".gemini-extension-install.json")); err == nil &&
			json.Unmarshal(b, &install) == nil && install.Type == "link" && install.Source != "" {
			dir = install.Source
		}
		var manifest struct {
			Name string `json:"name"`
		}
		b, err := os.ReadFile(filepath.Join(dir, "gemini-extension.json"))
		if err != nil || json.Unmarshal(b, &manifest) != nil {
			continue
		}
		name := firstNonEmpty(manifest.Name, e.Name())
		on := true
		for _, rule := range enablement[name].Overrides {
			disable := strings.HasPrefix(rule, "!")
			base := strings.TrimPrefix(rule, "!")
			sub := strings.HasSuffix(base, "*")
			base = strings.TrimSuffix(base, "*")
			if !strings.HasPrefix(base, "/") {
				base = "/" + base
			}
			if !strings.HasSuffix(base, "/") {
				base += "/"
			}
			if at == base || sub && strings.HasPrefix(at, base) {
				on = !disable
			}
		}
		if on {
			out = append(out, skillDir{dir: filepath.Join(dir, "skills"), source: "extension", plugin: name})
		}
	}
	return out
}

// cursorFolders are the folders Cursor Agent reads skills from, in a
// project and in the home directory (other agents' included, as it does by
// default).
var cursorFolders = []string{".cursor", ".claude", ".codex", ".grok", ".agents"}

// cursorSkills are Cursor Agent's: <folder>/skills of cursorFolders in
// the project, then in the home directory, then its own built-in ones
// (~/.cursor/skills-cursor). Skills of its plugins are not listed: which
// plugins are on is Cursor's account's to say.
func cursorSkills(cwd string) []Skill {
	var dirs []skillDir
	if cwd != "" {
		for _, f := range cursorFolders {
			dirs = append(dirs, skillDir{dir: filepath.Join(cwd, f, "skills"), source: "project"})
		}
	}
	for _, f := range cursorFolders {
		dirs = append(dirs, skillDir{dir: filepath.Join(homeDir(), f, "skills"), source: "user"})
	}
	dirs = append(dirs, skillDir{dir: filepath.Join(homeDir(), ".cursor", "skills-cursor"), source: "system"})
	return skillsFrom(dirs, false, nil)
}
