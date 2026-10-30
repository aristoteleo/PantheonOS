package codingagentsapp

import (
	"bufio"
	"bytes"
	"cmp"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// Claude Code's skills that are not in a skills folder of the user, the
// project or a plugin: those synced from the Claude account, and those that
// come with Claude Code itself.

// accountPlugin is the prefix Claude Code gives the skills synced from the
// account ("anthropic-skills:pdf").
const accountPlugin = "anthropic-skills"

// claudeAccountSkills are the skills Claude Code syncs from the signed-in
// account into <claude dir>/skills/synced/<organization>_<account>/<name>/
// SKILL.md. The folder is the account's in Claude's config (.claude.json);
// when that cannot be read and there is a single folder, it is that one.
func claudeAccountSkills() []Skill {
	root := filepath.Join(codingagents.ClaudeDir(), "skills", "synced")
	bucket := ""
	if org, account := claudeAccount(); org != "" && account != "" {
		bucket = org + "_" + account
	} else {
		entries, _ := os.ReadDir(root)
		var dirs []string
		for _, e := range entries {
			if e.IsDir() {
				dirs = append(dirs, e.Name())
			}
		}
		if len(dirs) == 1 {
			bucket = dirs[0]
		}
	}
	if bucket == "" {
		return nil
	}
	list := skillsIn(filepath.Join(root, bucket), "account", accountPlugin)
	for i := range list {
		list[i].Plugin = "" // the account's own, not a plugin's
	}
	return list
}

// claudeAccount is the organization and account Claude Code is signed in
// with, from its config: <CLAUDE_CONFIG_DIR>/.claude.json, else
// ~/.claude.json. Only those two ids are read.
func claudeAccount() (org, account string) {
	file := filepath.Join(homeDir(), ".claude.json")
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		file = filepath.Join(dir, ".claude.json")
	}
	var cfg struct {
		OAuthAccount struct {
			OrganizationUUID string `json:"organizationUuid"`
			AccountUUID      string `json:"accountUuid"`
		} `json:"oauthAccount"`
	}
	if b, err := os.ReadFile(file); err == nil && json.Unmarshal(b, &cfg) == nil {
		return cfg.OAuthAccount.OrganizationUUID, cfg.OAuthAccount.AccountUUID
	}
	return "", ""
}

// claudeBuiltinSkills are the skills that come with Claude Code (dataviz,
// code-review, …). They live inside its executable and are unpacked to a
// new temporary folder by each run, so they are taken from what Claude Code
// itself last listed to the model in cwd: the skill_listing record at the
// start of its newest session there (<claude dir>/projects/<cwd as a
// folder name>/<session>.jsonl), the home folder's when cwd has none.
// Only plain names count (a plugin's or the account's have a prefix and
// are read from their folders, with their settings); a description is the
// one listed, and there is no path.
func claudeBuiltinSkills(cwd string) []Skill {
	projects := filepath.Join(codingagents.ClaudeDir(), "projects")
	for _, dir := range []string{cwd, homeDir()} {
		if dir == "" {
			continue
		}
		if list, ok := skillListing(claudeProjectDir(projects, dir)); ok {
			return list
		}
	}
	return nil
}

// claudeProjectDir is the folder Claude Code keeps a project's sessions
// in: its path with every character but a letter or digit made a dash
// (a long one is cut at 200 and given a hash, which is matched by prefix).
func claudeProjectDir(projects, cwd string) string {
	name := []byte(filepath.Clean(cwd))
	for i, c := range name {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9') {
			name[i] = '-'
		}
	}
	if len(name) <= 200 {
		return filepath.Join(projects, string(name))
	}
	prefix := string(name[:200]) + "-"
	entries, _ := os.ReadDir(projects)
	for _, e := range entries {
		if e.IsDir() && strings.HasPrefix(e.Name(), prefix) {
			return filepath.Join(projects, e.Name())
		}
	}
	return ""
}

// skillListing reads the first skill_listing record of the newest sessions
// in dir (a few, in case the newest has none yet). ok is false when none
// has one.
func skillListing(dir string) ([]Skill, bool) {
	if dir == "" {
		return nil, false
	}
	entries, _ := os.ReadDir(dir)
	type session struct {
		path string
		mod  int64
	}
	var sessions []session
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		if info, err := e.Info(); err == nil {
			sessions = append(sessions, session{filepath.Join(dir, e.Name()), info.ModTime().UnixNano()})
		}
	}
	slices.SortFunc(sessions, func(a, b session) int { return cmp.Compare(b.mod, a.mod) })
	for i, s := range sessions {
		if i == 5 {
			break
		}
		if list, ok := skillListingIn(s.path); ok {
			return list, true
		}
	}
	return nil, false
}

// skillListingIn is the skills of the first skill_listing record among a
// session's first lines: its names, each with the description its content
// gives ("- name: description", which may run on over more lines).
func skillListingIn(path string) ([]Skill, bool) {
	f, err := os.Open(path)
	if err != nil {
		return nil, false
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 16<<20)
	for n := 0; n < 100 && sc.Scan(); n++ {
		line := sc.Bytes()
		if !bytes.Contains(line, []byte(`"skill_listing"`)) {
			continue
		}
		var rec struct {
			Attachment struct {
				Type    string   `json:"type"`
				Content string   `json:"content"`
				Names   []string `json:"names"`
			} `json:"attachment"`
		}
		if json.Unmarshal(line, &rec) != nil || rec.Attachment.Type != "skill_listing" {
			continue
		}
		return listedSkills(rec.Attachment.Names, rec.Attachment.Content), true
	}
	return nil, false
}

// listedSkills are the plain names of a listing (none with a ":"), each
// with its description from content.
func listedSkills(names []string, content string) []Skill {
	plain := map[string]bool{}
	for _, n := range names {
		if n != "" && !strings.Contains(n, ":") {
			plain[n] = true
		}
	}
	desc := map[string][]string{}
	cur := ""
	for _, line := range strings.Split(content, "\n") {
		if rest, ok := strings.CutPrefix(line, "- "); ok {
			if name, d, ok := strings.Cut(rest, ": "); ok && slices.Contains(names, name) {
				cur = name
				desc[cur] = []string{d}
				continue
			}
			if slices.Contains(names, strings.TrimSuffix(rest, ":")) {
				cur = strings.TrimSuffix(rest, ":")
				desc[cur] = nil
				continue
			}
		}
		if cur != "" {
			desc[cur] = append(desc[cur], line)
		}
	}
	out := []Skill{}
	for _, n := range names {
		if plain[n] {
			plain[n] = false
			out = append(out, Skill{Name: n, Description: strings.TrimSpace(strings.Join(desc[n], "\n")), Source: "builtin"})
		}
	}
	return out
}

// claudeSkillSettings are the user's and the project's choices on which
// skills Claude Code loads: skillOverrides set to "off" hide a skill, and
// disableBundledSkills (or CLAUDE_CODE_DISABLE_BUNDLED_SKILLS) the ones
// that come with it. A later settings file wins.
func claudeSkillSettings(cwd string) (off map[string]bool, noBuiltin bool) {
	files := []string{filepath.Join(codingagents.ClaudeDir(), "settings.json")}
	if cwd != "" {
		files = append(files, filepath.Join(cwd, ".claude", "settings.json"), filepath.Join(cwd, ".claude", "settings.local.json"))
	}
	off = map[string]bool{}
	for _, f := range files {
		var s struct {
			SkillOverrides       map[string]string `json:"skillOverrides"`
			DisableBundledSkills *bool             `json:"disableBundledSkills"`
		}
		if b, err := os.ReadFile(f); err != nil || json.Unmarshal(b, &s) != nil {
			continue
		}
		for name, v := range s.SkillOverrides {
			off[name] = v == "off"
		}
		if s.DisableBundledSkills != nil {
			noBuiltin = *s.DisableBundledSkills
		}
	}
	if v := os.Getenv("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"); v != "" && v != "0" && !strings.EqualFold(v, "false") {
		noBuiltin = true
	}
	return off, noBuiltin
}
