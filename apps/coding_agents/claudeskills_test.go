package codingagentsapp

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// listing is a Claude Code session line holding a skill_listing record.
func listing(t *testing.T, names []string, content string) string {
	t.Helper()
	b, err := json.Marshal(map[string]any{"type": "attachment", "attachment": map[string]any{
		"type": "skill_listing", "content": content, "names": names, "isInitial": true}})
	if err != nil {
		t.Fatal(err)
	}
	return string(b) + "\n"
}

func skillNamed(list []Skill, name string) (Skill, bool) {
	for _, s := range list {
		if s.Name == name {
			return s, true
		}
	}
	return Skill{}, false
}

// A skill that comes with Claude Code (dataviz) is in no skills folder: it
// is known from what Claude Code listed in its last session in the folder.
func TestClaudeSkillsBuiltinAndAccount(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	t.Setenv("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "")
	claude := filepath.Join(home, ".claude")
	writeFile(t, filepath.Join(claude, "skills", "pdf", "SKILL.md"), "---\nname: pdf\ndescription: Mine\n---\n")
	// Synced from the account, in the signed-in account's folder only.
	writeFile(t, filepath.Join(home, ".claude.json"), `{"oauthAccount":{"organizationUuid":"org1","accountUuid":"acc1"}}`)
	writeFile(t, filepath.Join(claude, "skills", "synced", "org1_acc1", "xlsx", "SKILL.md"), "---\nname: xlsx\ndescription: Sheets\n---\n")
	writeFile(t, filepath.Join(claude, "skills", "synced", "org2_acc2", "other", "SKILL.md"), "---\nname: other\n---\n")
	writeFile(t, filepath.Join(claude, "skills", "synced", "org1_acc1", "manifest.json"), "{}")

	names := []string{"pdf", "Notion:find", "dataviz", "claude-api", "anthropic-skills:xlsx"}
	content := "- pdf: Mine\n- Notion:find: Find pages\n- dataviz: Use this skill for charts.\n" +
		"- claude-api: Reference for the API.\nTRIGGER when it is named.\n- anthropic-skills:xlsx: Sheets\n"
	sessions := filepath.Join(claude, "projects", "-Users-me")
	writeFile(t, filepath.Join(sessions, "old.jsonl"), `{"type":"user"}`+"\n"+listing(t, []string{"stale"}, "- stale: Gone\n"))
	writeFile(t, filepath.Join(sessions, "new.jsonl"), `{"type":"user"}`+"\n"+listing(t, names, content)+`{"type":"assistant"}`+"\n")
	past := time.Now().Add(-time.Hour)
	if err := os.Chtimes(filepath.Join(sessions, "old.jsonl"), past, past); err != nil {
		t.Fatal(err)
	}

	got := skillList("/Users/me")
	dataviz, ok := skillNamed(got, "dataviz")
	if !ok || dataviz.Source != "builtin" || dataviz.Path != "" || dataviz.Description != "Use this skill for charts." {
		t.Fatalf("dataviz: %+v in %+v", dataviz, got)
	}
	if api, _ := skillNamed(got, "claude-api"); api.Description != "Reference for the API.\nTRIGGER when it is named." {
		t.Errorf("a description over more lines: %q", api.Description)
	}
	if pdf, _ := skillNamed(got, "pdf"); pdf.Source != "user" || pdf.Path == "" {
		t.Errorf("a skill on disk keeps its folder: %+v", pdf)
	}
	if xlsx, ok := skillNamed(got, "anthropic-skills:xlsx"); !ok || xlsx.Source != "account" || xlsx.Description != "Sheets" ||
		!strings.HasSuffix(xlsx.Path, filepath.Join("org1_acc1", "xlsx", "SKILL.md")) {
		t.Errorf("the account's skill: %+v", xlsx)
	}
	for _, name := range []string{"stale", "Notion:find", "anthropic-skills:other"} {
		if _, ok := skillNamed(got, name); ok {
			t.Errorf("%s should not be listed: %+v", name, got)
		}
	}
	n := 0
	for _, s := range got {
		if s.Name == "pdf" {
			n++
		}
	}
	if n != 1 {
		t.Errorf("pdf listed %d times", n)
	}

	// A folder without sessions falls back to the home folder's; none at all, none.
	writeFile(t, filepath.Join(claudeProjectDir(filepath.Join(claude, "projects"), home), "s.jsonl"),
		listing(t, []string{"verify"}, "- verify: Check it\n"))
	if _, ok := skillNamed(skillList("/elsewhere"), "verify"); !ok {
		t.Errorf("the home folder's listing: %+v", skillList("/elsewhere"))
	}

	// Settings: an override off hides a skill; bundled skills can be turned off.
	writeFile(t, filepath.Join(claude, "settings.json"), `{"skillOverrides":{"claude-api":"off","dataviz":"name-only"}}`)
	got = skillList("/Users/me")
	if _, ok := skillNamed(got, "claude-api"); ok {
		t.Error("claude-api is off")
	}
	if _, ok := skillNamed(got, "dataviz"); !ok {
		t.Error("name-only still lists dataviz")
	}
	t.Setenv("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "1")
	if _, ok := skillNamed(skillList("/Users/me"), "dataviz"); ok {
		t.Error("bundled skills are disabled")
	}
	t.Setenv("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "")
	writeFile(t, filepath.Join(claude, "settings.json"), `{"disableBundledSkills":true}`)
	if _, ok := skillNamed(skillList("/Users/me"), "dataviz"); ok {
		t.Error("disableBundledSkills")
	}
	if _, ok := skillNamed(skillList("/Users/me"), "anthropic-skills:xlsx"); !ok {
		t.Error("the account's skills are not bundled ones")
	}

	// Typed as /dataviz, it counts as the turn's skill.
	writeFile(t, filepath.Join(claude, "settings.json"), `{}`)
	if got := slashSkills("/dataviz chart this", "/Users/me"); len(got) != 1 || got[0] != "dataviz" {
		t.Errorf("slash skills: %v", got)
	}
}

func TestClaudeAccountSkillsWithoutConfig(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	synced := filepath.Join(home, ".claude", "skills", "synced")
	writeFile(t, filepath.Join(synced, "a_b", "pdf", "SKILL.md"), "---\nname: pdf\n---\n")
	if got := claudeAccountSkills(); len(got) != 1 || got[0].Name != "anthropic-skills:pdf" {
		t.Errorf("the only folder: %+v", got)
	}
	writeFile(t, filepath.Join(synced, "c_d", "pdf", "SKILL.md"), "---\nname: pdf\n---\n")
	if got := claudeAccountSkills(); len(got) != 0 {
		t.Errorf("which account is unknown: %+v", got)
	}
}

func TestClaudeProjectDir(t *testing.T) {
	if got := claudeProjectDir("/p", "/Users/me/my_proj.v2"); got != filepath.Join("/p", "-Users-me-my-proj-v2") {
		t.Errorf("project dir: %s", got)
	}
}
