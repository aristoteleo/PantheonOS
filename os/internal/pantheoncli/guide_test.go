package pantheoncli

import (
	"encoding/base64"
	"errors"
	"strings"
	"testing"
)

func signIn(t *testing.T) *fakePlatform {
	t.Helper()
	f := newFakePlatform(t)
	if out, errOut, code := run(t, "login", "--hub", f.server.URL, "--key", "pbk_test"); code != 0 {
		t.Fatalf("login: %s %s", out, errOut)
	}
	return f
}

func TestSkillsAreReadOutsideAConversation(t *testing.T) {
	f := signIn(t)
	f.answer = func(method string, args map[string]any) (any, bool) {
		if method != "agent_skills" {
			return nil, false
		}
		if args["action"] == "list" {
			return map[string]any{"success": true, "skills": []any{
				map[string]any{"name": "bio/qc", "scope": "factory", "description": "Quality control of cells"}}}, true
		}
		return map[string]any{"success": true, "name": args["name"], "description": "QC",
			"content": "1. Filter cells.", "files": []any{"references/limits.md"},
			"hint": "Read a file of this skill: pantheon skills view bio/qc <file>"}, true
	}
	out, errOut, code := run(t, "skills", "list")
	if code != 0 || !strings.Contains(out, "bio/qc") || !strings.Contains(out, "Quality control") ||
		!strings.Contains(out, "pantheon skills view <name>") {
		t.Fatalf("list: %d %q %s", code, out, errOut)
	}
	out, errOut, code = run(t, "skills", "view", "bio/qc")
	if code != 0 || !strings.Contains(out, "1. Filter cells.") || !strings.Contains(out, "references/limits.md") {
		t.Fatalf("view: %d %q %s", code, out, errOut)
	}
	last := f.calls[len(f.calls)-1]
	if last["instance"] != "agt" || last["args"].(map[string]any)["name"] != "bio/qc" {
		t.Fatalf("view went to %#v", last)
	}
	// Changing skills belongs to a conversation, and says what to do instead.
	_, errOut, code = run(t, "skills", "create", "x")
	if code == 0 || !strings.Contains(errOut, "pantheon skills list") || !strings.Contains(errOut, "pantheon agent chat -i") {
		t.Fatalf("create: %d %s", code, errOut)
	}
}

func TestAnOlderAgentListsTheUsersOwnSkills(t *testing.T) {
	f := signIn(t)
	f.answer = func(method string, args map[string]any) (any, bool) {
		switch method {
		case "agent_skills":
			return errors.New("No registered method: agent_skills"), true
		case "agent_skill_files":
			if args["operation"] == "read" {
				if args["scope"] == "global" && args["path"] == "mine/SKILL.md" {
					return map[string]any{"data": base64.StdEncoding.EncodeToString([]byte("# Mine"))}, true
				}
				return errors.New("Skill file does not exist"), true
			}
			if args["scope"] == "global" {
				return map[string]any{"files": []any{map[string]any{"name": "mine", "type": "directory"}}}, true
			}
			return map[string]any{"files": []any{}}, true
		}
		return nil, false
	}
	out, errOut, code := run(t, "skills", "list")
	if code != 0 || !strings.Contains(out, "mine") || !strings.Contains(out, "older release") {
		t.Fatalf("list: %d %q %s", code, out, errOut)
	}
	if out, errOut, code = run(t, "skills", "view", "mine"); code != 0 || !strings.Contains(out, "# Mine") {
		t.Fatalf("view: %d %q %s", code, out, errOut)
	}
}

func TestConversationCommandsSayWhatToDoOutsideOne(t *testing.T) {
	signIn(t)
	for _, args := range [][]string{{"task", "outputs"}, {"agent", "list"}, {"jobs"}} {
		_, errOut, code := run(t, args...)
		if code == 0 || !strings.Contains(errOut, "not run by one") || !strings.Contains(errOut, "pantheon agent chat -i") {
			t.Errorf("%v: %d %s", args, code, errOut)
		}
	}
}

func TestBrowserAndDesktopSayTheNextStep(t *testing.T) {
	f := signIn(t)
	noViewport := "no desktop to ask — no viewport is open on this pod"
	f.answer = func(method string, args map[string]any) (any, bool) {
		switch method {
		case "browser_pages":
			return map[string]any{"success": true, "pages": []any{}}, true
		case "browser_open":
			return map[string]any{"success": false, "page_id": "p1", "title": "Example Domain", "error": noViewport,
				"shown": false}, true
		case "desktop_open":
			return map[string]any{"success": false, "error": noViewport}, true
		}
		return nil, false
	}
	out, errOut, code := run(t, "browser", "pages")
	if code != 0 || !strings.Contains(out, "pantheon browser open url=") {
		t.Fatalf("pages: %d %q %s", code, out, errOut)
	}
	if out, _, _ = run(t, "browser", "pages", "--json"); !strings.Contains(out, `"hint"`) {
		t.Fatalf("pages --json: %s", out)
	}
	// The page opened: only showing it failed. That is not a failure of the command.
	out, errOut, code = run(t, "browser", "open", "url=https://example.com")
	if code != 0 || !strings.Contains(out, `"shown": false`) || !strings.Contains(out, "pantheon browser read page_id=p1") {
		t.Fatalf("open: %d %q %s", code, out, errOut)
	}
	// A window the desktop did not confirm: the page is there all the same.
	f.answer = func(method string, args map[string]any) (any, bool) {
		if method == "browser_open" {
			return map[string]any{"success": false, "page_id": "p2", "error": "the desktop did not answer in 270s"}, true
		}
		if method == "desktop_open" {
			return map[string]any{"success": false, "error": noViewport}, true
		}
		return nil, false
	}
	out, errOut, code = run(t, "browser", "open", "url=https://example.com")
	if code != 0 || !strings.Contains(out, "did not confirm") || !strings.Contains(out, "page_id=p2") {
		t.Fatalf("unconfirmed open: %d %q %s", code, out, errOut)
	}
	_, errOut, code = run(t, "desktop", "open", "path=/w/a.md")
	if code == 0 || !strings.Contains(errOut, f.server.URL+"/desktop.html") || !strings.Contains(errOut, "run this again") {
		t.Fatalf("desktop open: %d %s", code, errOut)
	}
	if out, _, code = run(t, "browser", "--help"); code != 0 || !strings.Contains(out, "pantheon browser read") {
		t.Fatalf("help: %s", out)
	}
}

func TestRunKeepsTheCallersQuoting(t *testing.T) {
	for _, c := range []struct {
		words []string
		want  string
	}{
		{[]string{"ls", "-l", "/tmp"}, "ls -l /tmp"},
		{[]string{"ls", "*.py"}, "ls *.py"},
		{[]string{"sh", "-c", "ls -l /w; echo"}, "sh -c 'ls -l /w; echo'"},
		{[]string{"echo", "it's here"}, `echo 'it'\''s here'`},
		{[]string{"ls -l /w | head"}, "ls -l /w | head"},
	} {
		if got := commandLine(c.words, "shell"); got != c.want {
			t.Errorf("%q: %q, want %q", c.words, got, c.want)
		}
	}
	if got := commandLine([]string{"print(1)", "x"}, "python"); got != "print(1) x" {
		t.Errorf("python: %q", got)
	}
}
