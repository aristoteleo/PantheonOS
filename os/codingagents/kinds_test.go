package codingagents

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// The codex-*, cursor-* and gemini-auth-error fixtures are real runs (see
// testdata/README.md), paths shortened to /work.

// parseFile runs one agent's parser over a fixture.
func parseFile(t *testing.T, agent, name string) []Event {
	t.Helper()
	a, _ := Lookup(agent)
	f, err := os.Open(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	p := a.Headless.Parser()
	var out []Event
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		out = append(out, p.Parse(sc.Bytes())...)
	}
	return out
}

// toolSteps is each tool event as "status kind path input", in order.
func toolSteps(events []Event) []string {
	var out []string
	for _, e := range events {
		if e.Type == "tool" {
			out = append(out, fmt.Sprintf("%s %s %s %s", e.Status, e.Kind, e.Path, e.Input))
		}
	}
	return out
}

func wantSteps(t *testing.T, name string, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("%s:\n got %q\nwant %q", name, got, want)
	}
}

// A real Codex turn that reads two files (through the shell), edits one,
// runs it and searches the web.
func TestCodexKinds(t *testing.T) {
	events := parseFile(t, "codex", "codex-canvas.jsonl")
	wantSteps(t, "codex-canvas", toolSteps(events), []string{
		"running shell  cat README.md src/calc.py",
		"done shell  cat README.md src/calc.py",
		"running shell  rg --files -g AGENTS.md",
		"error shell  rg --files -g AGENTS.md",
		"running write /work/acme/src/calc.py /work/acme/src/calc.py",
		"done write /work/acme/src/calc.py /work/acme/src/calc.py",
		"running shell  python3 src/calc.py",
		"done shell  python3 src/calc.py",
		"running search  ",
		"done search  site.python.org Python 3.15 release 2026",
	})
	if events[0].Type != "session" || events[0].SessionID != "01a18e89-7ec8-7cc0-9f6b-4d6ffb9876a9" {
		t.Errorf("session %+v", events[0])
	}
	if last := events[len(events)-1]; last.Type != "done" || last.Error != "" || last.InputTokens != 60490 {
		t.Errorf("done %+v", last)
	}

	// Research off: Codex has no web search tool and says so; nothing is a
	// search or a fetch.
	for _, e := range parseFile(t, "codex", "codex-research-off.jsonl") {
		if e.Kind == KindSearch || e.Kind == KindFetch {
			t.Errorf("research off searched: %+v", e)
		}
	}
	// A skill, opened through the shell.
	wantSteps(t, "codex-skill", toolSteps(parseFile(t, "codex", "codex-skill.jsonl")), []string{
		"running skill .agents/skills/night-sky/SKILL.md night-sky",
		"done skill .agents/skills/night-sky/SKILL.md night-sky",
	})
	// Sub-agents: only waits, with nothing that says who runs.
	for _, e := range parseFile(t, "codex", "codex-subagents.jsonl") {
		if e.Type == "tool" && (e.Kind != KindOther || e.Name != "agents.wait" || e.Subagent != "" || e.Parent != "") {
			t.Errorf("sub-agent step %+v", e)
		}
	}
}

// Codex items the fixtures do not have: a change to several files, a page
// opened by its web search, an MCP call.
func TestCodexItemKinds(t *testing.T) {
	p := &codexParser{}
	got := p.Parse([]byte(`{"type":"item.completed","item":{"id":"item_7","type":"file_change","changes":[` +
		`{"path":"/work/acme/a.py","kind":"update"},{"path":"/work/acme/b.py","kind":"add"},{"path":"/work/acme/c.py","kind":"delete"}],"status":"completed"}}`))
	var ids []string
	for _, e := range got {
		ids = append(ids, e.ID+" "+e.Kind+" "+e.Path+" "+e.Status)
	}
	wantSteps(t, "file_change", ids, []string{"item_7 write /work/acme/a.py done",
		"item_7#2 write /work/acme/b.py done", "item_7#3 write /work/acme/c.py done"})

	page := p.Parse([]byte(`{"type":"item.completed","item":{"id":"ws","type":"web_search","query":"",` +
		`"action":{"type":"open_page","url":"https://night-sky.example/sirius"}}}`))
	if len(page) != 1 || page[0].Kind != KindFetch || page[0].Input != "https://night-sky.example/sirius" {
		t.Errorf("open_page %+v", page)
	}
	mcp := p.Parse([]byte(`{"type":"item.started","item":{"id":"m","type":"mcp_tool_call","server":"acme","tool":"lookup","arguments":{"query":"x"}}}`))
	if len(mcp) != 1 || mcp[0].Kind != KindOther || mcp[0].Name != "acme.lookup" {
		t.Errorf("mcp %+v", mcp)
	}
}

// A real Cursor turn (read, edit, shell, web search), and its resumed turn:
// a skill opened by reading its SKILL.md, a file outside the workspace
// (--add-dir) and a reference image read by path.
func TestCursorKinds(t *testing.T) {
	steps := toolSteps(parseFile(t, "cursor", "cursor-canvas.jsonl"))
	wantSteps(t, "cursor-canvas", steps, []string{
		"running read /work/acme/README.md /work/acme/README.md",
		"running read /work/acme/src/calc.py /work/acme/src/calc.py",
		"done read /work/acme/README.md /work/acme/README.md",
		"done read /work/acme/src/calc.py /work/acme/src/calc.py",
		"running write /work/acme/src/calc.py /work/acme/src/calc.py",
		"running other  WebSearch",
		"done other  WebSearch",
		"done write /work/acme/src/calc.py /work/acme/src/calc.py",
		"running shell  python3 src/calc.py",
		"running search  Python 3 release October 2026 latest version name",
		"done shell  python3 src/calc.py",
		"done search  Python 3 release October 2026 latest version name",
	})
	wantSteps(t, "cursor-resume", toolSteps(parseFile(t, "cursor", "cursor-resume.jsonl")), []string{
		"running skill /work/acme/.agents/skills/night-sky/SKILL.md night-sky",
		"running read /work/outside/notes.txt /work/outside/notes.txt",
		"running read /work/red.png /work/red.png",
		"done skill /work/acme/.agents/skills/night-sky/SKILL.md night-sky",
		"done read /work/outside/notes.txt /work/outside/notes.txt",
		"done read /work/red.png /work/red.png",
	})
}

// Gemini CLI's tools by the names and parameters of its source (no real
// tool stream could be recorded; see testdata/README.md). Its real error
// result ends the turn with the error.
func TestGeminiKinds(t *testing.T) {
	p := &geminiParser{}
	var got []string
	for _, l := range []string{
		`{"type":"tool_use","tool_name":"read_file","tool_id":"r","parameters":{"file_path":"/work/acme/README.md"}}`,
		`{"type":"tool_use","tool_name":"replace","tool_id":"e","parameters":{"file_path":"/work/acme/src/calc.py","old_string":"a","new_string":"b"}}`,
		`{"type":"tool_use","tool_name":"write_file","tool_id":"w","parameters":{"file_path":"/work/acme/new.py","content":"x"}}`,
		`{"type":"tool_use","tool_name":"run_shell_command","tool_id":"s","parameters":{"command":"python3 src/calc.py"}}`,
		`{"type":"tool_use","tool_name":"google_web_search","tool_id":"q","parameters":{"query":"night sky"}}`,
		`{"type":"tool_use","tool_name":"web_fetch","tool_id":"f","parameters":{"prompt":"Summarise https://night-sky.example"}}`,
		`{"type":"tool_use","tool_name":"activate_skill","tool_id":"k","parameters":{"name":"night-sky"}}`,
		`{"type":"tool_use","tool_name":"grep_search","tool_id":"g","parameters":{"pattern":"def mul"}}`,
	} {
		for _, e := range p.Parse([]byte(l)) {
			got = append(got, e.Kind+" "+e.Path+" "+e.Input)
		}
	}
	wantSteps(t, "gemini", got, []string{
		"read /work/acme/README.md /work/acme/README.md",
		"write /work/acme/src/calc.py /work/acme/src/calc.py",
		"write /work/acme/new.py /work/acme/new.py",
		"shell  python3 src/calc.py",
		"search  night sky",
		"fetch  Summarise https://night-sky.example",
		"skill  night-sky",
		"other  def mul",
	})
	events := parseFile(t, "gemini", "gemini-auth-error.jsonl")
	if last := events[len(events)-1]; last.Type != "done" || !strings.Contains(last.Error, "API key not valid") {
		t.Errorf("error result %+v", last)
	}
}

// Claude Code's own tool names map to the same kinds.
func TestClaudeKinds(t *testing.T) {
	var got []string
	for _, e := range parseFile(t, "claude", "claude-subagents-parallel.jsonl") {
		if e.Type == "tool" && e.Status == "running" && e.Name != "" {
			got = append(got, e.Name+" "+e.Kind+" "+e.Path)
		}
	}
	wantSteps(t, "claude", got, []string{
		"Agent agent ", "Agent agent ", "Read read /tmp/calc/src/calc.py", "Agent agent ",
		"Read read /tmp/calc/src/calc.py", "Read read /tmp/calc/test_calc.py", "Bash shell ", "Glob other ",
	})
	p := &claudeParser{}
	for name, want := range map[string]string{
		`"name":"Edit","input":{"file_path":"/w/a.go"}`:                "write /w/a.go",
		`"name":"Write","input":{"file_path":"/w/b.go"}`:               "write /w/b.go",
		`"name":"NotebookEdit","input":{"notebook_path":"/w/n.ipynb"}`: "write /w/n.ipynb",
		`"name":"WebSearch","input":{"query":"q"}`:                     "search ",
		`"name":"WebFetch","input":{"url":"https://a"}`:                "fetch ",
		`"name":"Skill","input":{"skill":"pdf"}`:                       "skill ",
	} {
		ev := p.Parse([]byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t",` + name + `}]}}`))
		if len(ev) != 1 || ev[0].Kind+" "+ev[0].Path != want {
			t.Errorf("%s: %+v", name, ev)
		}
	}
}

func TestShellSkill(t *testing.T) {
	for cmd, want := range map[string]string{
		"cat .agents/skills/night-sky/SKILL.md":               "night-sky",
		"sed -n '1,80p' '/home/u/.codex/skills/pdf/SKILL.md'": "pdf",
		"cat README.md":                                              "",
		"rg SKILL.md":                                                "",
		"cat SKILL.md":                                               "",
		"python3 skills/x/SKILL.md":                                  "",
		"cat docs/SKILL.md":                                          "",
		"cat > .agents/skills/x/SKILL.md":                            "",
		"cat a.txt >> .agents/skills/x/SKILL.md":                     "",
		"sed -i 's/a/b/' .agents/skills/x/SKILL.md":                  "",
		"sed -n -i '1p' skills/x/SKILL.md":                           "",
		"sed 's/a/b/' skills/x/SKILL.md":                             "",
		"tee skills/x/SKILL.md":                                      "",
		`sed -n '1,80p' '/home/u/My Work/skills/night sky/SKILL.md'`: "night sky",
		`cat "/home/u/a b/.agents/skills/acme/SKILL.md"`:             "acme",
		"cd /work/acme && cat .agents/skills/night-sky/SKILL.md":     "night-sky",
		"ls; head -40 ~/.codex/skills/.system/imagegen/SKILL.md":     "imagegen",
		"cat .agents/skills/x/SKILL.md | head -5":                    "x",
		"cat .agents/skills/x/SKILL.md 2>/dev/null":                  "x",
		"cat .agents/skills/x/SKILL.md > /tmp/copy":                  "",
		"cat .agents/skills/x/SKILL.md 2>&1 | head":                  "x",
	} {
		if got, _ := shellSkill(cmd); got != want {
			t.Errorf("%s: got %q, want %q", cmd, got, want)
		}
	}
}

// Each headless agent takes the canvas options its Canvas names, in its
// own flags; nothing else.
func TestCanvasArgsPerAgent(t *testing.T) {
	turn := Turn{Session: "s1", Auto: true, Effort: "max", AddDirs: []string{"/work/outside"}, NoWeb: true,
		Images: []string{"/work/red.png"}, Prompt: "hi"}
	codex, _ := Lookup("codex")
	got := codex.Headless.Args(turn)
	want := []string{"exec", "--add-dir", "/work/outside", "--sandbox", "workspace-write", "resume", "s1", "-i", "/work/red.png",
		"--json", "--skip-git-repo-check", "-c", `approval_policy="never"`, "-c", `model_reasoning_effort="xhigh"`,
		"-c", `web_search="disabled"`, "-c", "sandbox_workspace_write.network_access=false", "-"}
	wantSteps(t, "codex", got, want)
	gemini, _ := Lookup("gemini")
	wantSteps(t, "gemini", gemini.Headless.Args(turn), []string{"--prompt=hi", "-o", "stream-json", "--resume", "s1",
		"--yolo", "--include-directories", "/work/outside"})
	cursor, _ := Lookup("cursor")
	wantSteps(t, "cursor", cursor.Headless.Args(turn), []string{"-p", "--output-format", "stream-json", "--trust", "--resume", "s1",
		"--force", "--add-dir", "/work/outside", "--", "hi"})
	claude, _ := Lookup("claude")
	if a := strings.Join(claude.Headless.Args(Turn{NoWeb: true}), " "); !strings.HasSuffix(a, "--disallowedTools WebSearch,WebFetch") {
		t.Errorf("claude: %s", a)
	}
	for _, id := range []string{"claude", "codex", "gemini", "cursor"} {
		a, _ := Lookup(id)
		if c := a.Headless.Canvas; !c.Skills || (c.Images && c.ImagesNote == "") {
			t.Errorf("%s canvas %+v", id, c)
		}
	}
}

// A prompt that looks like an option stays the prompt: Gemini CLI gets it
// joined to --prompt=, Cursor Agent after --, Claude Code and Codex on stdin.
func TestPromptIsNeverAnOption(t *testing.T) {
	for _, prompt := range []string{"--version", "--yolo", "-e https://acme.example", "--force"} {
		for _, id := range []string{"claude", "codex", "gemini", "cursor"} {
			a, _ := Lookup(id)
			args := a.Headless.Args(Turn{Prompt: prompt})
			switch {
			case !a.Headless.PromptArg:
				if slices.Contains(args, prompt) {
					t.Errorf("%s: prompt in its arguments: %q", id, args)
				}
			case id == "gemini":
				if !slices.Contains(args, "--prompt="+prompt) || slices.Contains(args, prompt) {
					t.Errorf("gemini: %q", args)
				}
			case id == "cursor":
				if n := len(args); args[n-1] != prompt || args[n-2] != "--" {
					t.Errorf("cursor: %q", args)
				}
			}
		}
	}
}

// Sub-agent tools are agent steps; a Codex web search keeps its item's id
// (its line repeats "id", the search call's second).
func TestAgentKindsAndIDs(t *testing.T) {
	cursor := &cursorParser{}
	ev := cursor.Parse([]byte(`{"type":"tool_call","subtype":"started","call_id":"c1","tool_call":{"taskToolCall":{"args":{"description":"Explore"}}}}`))
	if len(ev) != 1 || ev[0].Kind != KindAgent {
		t.Errorf("cursor task: %+v", ev)
	}
	// A SKILL.md that is not in a skills folder is a plain read.
	ev = cursor.Parse([]byte(`{"type":"tool_call","subtype":"started","call_id":"c2","tool_call":{"readToolCall":{"args":{"path":"/work/acme/docs/SKILL.md"}}}}`))
	if len(ev) != 1 || ev[0].Kind != KindRead || ev[0].Input != "/work/acme/docs/SKILL.md" {
		t.Errorf("cursor read: %+v", ev)
	}
	gemini := &geminiParser{}
	for _, name := range []string{"invoke_agent", "codebase_investigator"} {
		if ev := gemini.Parse([]byte(`{"type":"tool_use","tool_name":"` + name + `","tool_id":"g","parameters":{}}`)); len(ev) != 1 || ev[0].Kind != KindAgent {
			t.Errorf("gemini %s: %+v", name, ev)
		}
	}
	var ids []string
	for _, e := range parseFile(t, "codex", "codex-canvas.jsonl") {
		if e.Name == "web_search" {
			ids = append(ids, e.ID)
		}
	}
	if !slices.Equal(ids, []string{"item_5", "item_5"}) {
		t.Errorf("web search ids %q", ids)
	}
}

// Codex's flags on a first and a resumed turn: --add-dir and --sandbox go
// before resume (exec resume has neither); research off swaps the
// unsandboxed full auto for the workspace-write sandbox without network and
// without approvals; research on keeps full auto.
func TestCodexArgsFirstAndResumed(t *testing.T) {
	codex, _ := Lookup("codex")
	for _, c := range []struct {
		turn Turn
		want string
	}{
		{Turn{Auto: true, Effort: "low"}, `exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort="low" -`},
		{Turn{Session: "th", Auto: true, Effort: "low"}, `exec resume th --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort="low" -`},
		{Turn{Auto: true, NoWeb: true, AddDirs: []string{"/o"}}, `exec --add-dir /o --sandbox workspace-write --json --skip-git-repo-check ` +
			`-c approval_policy="never" -c web_search="disabled" -c sandbox_workspace_write.network_access=false -`},
		{Turn{Session: "th", Auto: true, NoWeb: true, AddDirs: []string{"/o"}}, `exec --add-dir /o --sandbox workspace-write resume th --json ` +
			`--skip-git-repo-check -c approval_policy="never" -c web_search="disabled" -c sandbox_workspace_write.network_access=false -`},
		// Without full auto, Codex's own approval setting stays.
		{Turn{Session: "th", NoWeb: true}, `exec --sandbox workspace-write resume th --json --skip-git-repo-check ` +
			`-c web_search="disabled" -c sandbox_workspace_write.network_access=false -`},
	} {
		if got := strings.Join(codex.Headless.Args(c.turn), " "); got != c.want {
			t.Errorf("%+v:\n got %s\nwant %s", c.turn, got, c.want)
		}
	}
}

// Other agents' tool inputs show what a call looks for before where; an
// argument shape nobody knows shows its first short text. Claude Code's
// inputs stay as they were.
func TestToolInput(t *testing.T) {
	cursor := &cursorParser{}
	for line, want := range map[string]string{
		`{"grepToolCall":{"args":{"pattern":"def mul","path":"/work/acme/src"}}}`:            "def mul",
		`{"globToolCall":{"args":{"globPattern":"**/*.py","targetDirectory":"/work/acme"}}}`: "**/*.py",
		`{"lsToolCall":{"args":{"path":"/work/acme"}}}`:                                      "/work/acme",
		`{"oddToolCall":{"args":{"limit":3,"target":"night-sky","zone":"a"}}}`:               "night-sky",
		`{"oddToolCall":{"args":{"limit":3}}}`:                                               `{"limit":3}`,
	} {
		ev := cursor.Parse([]byte(`{"type":"tool_call","subtype":"started","call_id":"c","tool_call":` + line + `}`))
		if len(ev) != 1 || ev[0].Input != want {
			t.Errorf("%s: %+v", line, ev)
		}
	}
	gemini := &geminiParser{}
	if ev := gemini.Parse([]byte(`{"type":"tool_use","tool_name":"grep_search","tool_id":"g","parameters":{"pattern":"def mul","dir_path":"/work/acme"}}`)); ev[0].Input != "def mul" {
		t.Errorf("gemini grep: %+v", ev)
	}
	// Claude Code: unchanged, from the recorded runs.
	var got []string
	for _, e := range parseFile(t, "claude", "claude-subagents-parallel.jsonl") {
		if e.Type == "tool" && e.Status == "running" && e.Name != "" && e.Name != "Agent" {
			got = append(got, e.Name+" "+e.Input)
		}
	}
	wantSteps(t, "claude", got, []string{"Read /tmp/calc/src/calc.py", "Read /tmp/calc/src/calc.py", "Read /tmp/calc/test_calc.py",
		`Bash python3 test_calc.py && echo PASS; echo "EXIT_STATUS=$?"`, "Glob /tmp/calc"})
	p := &claudeParser{}
	if ev := p.Parse([]byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t","name":"Grep","input":{"pattern":"def","path":"/w"}}]}}`)); ev[0].Input != "/w" {
		t.Errorf("claude grep changed: %+v", ev)
	}
}
