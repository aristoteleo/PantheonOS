package codingagents

import (
	"slices"
	"strings"
	"testing"
)

// The pantheon-* fixtures are real runs of `pantheon agent chat --json` (see
// testdata/README.md), paths shortened to /work and the node renamed.

// A real turn that reads two files, edits one, runs it and searches the web
// (once with a tool the web App does not have). App tools the Agent reaches
// through the pantheon command come inside its run_command, and are its own.
func TestPantheonKinds(t *testing.T) {
	events := parseFile(t, PantheonAgent, "pantheon.jsonl")
	wantSteps(t, "pantheon", toolSteps(events), []string{
		"running read /work/acme/README.md /work/acme/README.md",
		"running read /work/acme/src/calc.py /work/acme/src/calc.py",
		"done   ",
		"done   ",
		"running write /work/acme/src/calc.py /work/acme/src/calc.py",
		`running shell  pantheon apps call web search query="current Python release version"`,
		"running search  current Python release version",
		"error   ",
		"done   ",
		"done   ",
		`running shell  python3 -c 'import sys; sys.path.insert(0,"src"); import calc; print(calc.sub(5,2))'`,
		`running shell  pantheon apps call web duckduckgo_search query="latest Python release version"`,
		"running search  latest Python release version",
		"done   ",
		"done   ",
		"done   ",
		"running read /work/acme/src/calc.py /work/acme/src/calc.py",
		"done   ",
	})
	if events[0].Type != "session" || events[0].SessionID != "8af2014a-0418-424a-8953-58311e5ec676" {
		t.Errorf("session %+v", events[0])
	}
	for _, e := range events {
		if e.Parent != "" || e.Subagent != "" {
			t.Errorf("a pantheon command's call is the agent's own: %+v", e)
		}
		switch e.ID {
		case "call_057add774761488baf24754b":
			// A file's content, not the JSON around it.
			if e.Status == "done" && e.Output != "# Night Sky\nA tiny calculator for acme.\n" {
				t.Errorf("read output %q", e.Output)
			}
		case "call_a1751af28d8f42ec99e0c295":
			if e.Status == "done" && e.Output != "3\n" {
				t.Errorf("command output %q", e.Output)
			}
		}
	}
	last := events[len(events)-1]
	if last.Type != "done" || last.Error != "" || last.InputTokens != 42849 || last.CachedTokens != 34816 ||
		last.OutputTokens != 1208 || last.ContextTokens != 10124 || last.ContextWindow != 1048576 ||
		last.Model != "openrouter/deepseek/deepseek-v4.1-flash" {
		t.Errorf("done %+v", last)
	}

	// The same chat resumed (--chat): it remembers the first turn.
	resumed := parseFile(t, PantheonAgent, "pantheon-resume.jsonl")
	if resumed[0].SessionID != events[0].SessionID || !strings.Contains(resumed[1].Text, "sub(a, b)") {
		t.Errorf("resumed %+v", resumed)
	}
}

// A real turn that runs a sub-agent with `pantheon agent run`: the command is
// the launch, the sub-agent's steps come inside it, and its answer ends it.
func TestPantheonSubagent(t *testing.T) {
	events := parseFile(t, PantheonAgent, "pantheon-subagent.jsonl")
	const launch = "call_ebbd59521e2b402ab5e8d357"
	var got []string
	for _, e := range events {
		if e.Type == "tool" || e.Parent != "" {
			got = append(got, strings.Join([]string{e.Type, e.ID, e.Status, e.Kind, e.Subagent, e.Parent, e.Text, e.Output}, "|"))
		}
	}
	wantSteps(t, "pantheon-subagent", got, []string{
		"tool|call_6619298cd7f446cbabb3843d|running|shell||||",
		"tool|call_6619298cd7f446cbabb3843d|done|||||" + events[3].Output,
		"tool|" + launch + "|running|agent|Worker||Reply with the single word ok.|",
		"text|||||" + launch + "|ok|",
		"tool|" + launch + "|done||Worker|||ok",
	})
	if !strings.HasPrefix(events[3].Output, "Worker\tGeneral-purpose sub-agent") {
		t.Errorf("agent list output %q", events[3].Output)
	}
}

// Lines the fixtures do not have: a sub-agent's own calls (and the App
// calls of its pantheon commands), a skill, a failed sub-agent, a turn that
// failed or could not start.
func TestPantheonLines(t *testing.T) {
	p := &pantheonParser{}
	parse := func(line string) Event {
		t.Helper()
		out := p.Parse([]byte(line))
		if len(out) != 1 {
			t.Fatalf("%s: %+v", line, out)
		}
		return out[0]
	}
	launch := parse(`{"type":"tool_call","id":"L","name":"shell__run_command","args":{"command":"cd /work/acme && pantheon agent run --profile Researcher --detach \"Find the brightest star\"","node":"n_1"}}`)
	if launch.Kind != KindAgent || launch.Subagent != "Researcher" || launch.Text != "Find the brightest star" {
		t.Errorf("launch %+v", launch)
	}
	inner := parse(`{"type":"tool_call","id":"S","name":"shell__run_command","args":{"command":"pantheon apps call web web_crawl urls='[\"https://night-sky.example\"]'"},"parent":"L","context":"c|d1|researcher|ab"}`)
	app := parse(`{"type":"tool_call","id":"S:1","name":"web__web_crawl","args":{"urls":["https://night-sky.example"]},"parent":"S","context":"c|d1|researcher|ab|cli|S"}`)
	if inner.Parent != "L" || app.Parent != "L" || app.Kind != KindFetch {
		t.Errorf("inside the sub-agent: %+v %+v", inner, app)
	}
	if r := parse(`{"type":"tool_result","id":"S:1","name":"web__web_crawl","output":"[\"Sirius\"]","parent":"S"}`); r.Parent != "L" {
		t.Errorf("result inside the sub-agent %+v", r)
	}
	end := parse(`{"type":"tool_result","id":"L","name":"shell__run_command","output":"{\"success\": false, \"output\": \"pantheon: the sub-agent failed\\n\", \"exit_code\": 1}","is_error":true}`)
	if end.Status != "error" || end.Subagent != "Researcher" || end.Error != "pantheon: the sub-agent failed" {
		t.Errorf("failed sub-agent %+v", end)
	}
	if r := parse(`{"type":"tool_call","id":"R","name":"shell__run_command","args":{"command":"pantheon agent run --resume c-1.d1.worker.f5d8 go on"}}`); r.Subagent != "worker" || r.Text != "go on" {
		t.Errorf("resumed run %+v", r)
	}

	for line, want := range map[string][3]string{
		`{"type":"tool_call","id":"a","name":"skills__skill_view","args":{"name":"night-sky"}}`:                                   {KindSkill, "", "night-sky"},
		`{"type":"tool_call","id":"b","name":"file_manager__read_file","args":{"file_path":"/work/skills/night-sky/SKILL.md"}}`:   {KindSkill, "/work/skills/night-sky/SKILL.md", "night-sky"},
		`{"type":"tool_call","id":"c","name":"shell__run_command","args":{"command":"cat /work/skills/night-sky/SKILL.md"}}`:      {KindSkill, "/work/skills/night-sky/SKILL.md", "night-sky"},
		`{"type":"tool_call","id":"d","name":"file_manager__apply_patch","args":{"patch":"…","file_path":"/work/acme/a.py"}}`:     {KindWrite, "/work/acme/a.py", "/work/acme/a.py"},
		`{"type":"tool_call","id":"e","name":"file_manager__delete_path","args":{"path":["/work/acme/old.py"]}}`:                  {KindWrite, "/work/acme/old.py", "/work/acme/old.py"},
		`{"type":"tool_call","id":"f","name":"file_manager__grep","args":{"pattern":"Sirius","path":"/work/acme","node":"n_1"}}`:  {KindOther, "", "Sirius"},
		`{"type":"tool_call","id":"g","name":"fleet__run_on_node","args":{"node_id":"n_2","code":"ls"}}`:                          {KindShell, "", "ls"},
		`{"type":"tool_call","id":"h","name":"web__duckduckgo_search","args":{"query":"Sirius","max_results":5}}`:                 {KindSearch, "", "Sirius"},
		`{"type":"tool_call","id":"i","name":"file_manager__read_pdf","args":{"pdf_path":"/work/acme/star.pdf"}}`:                 {KindRead, "/work/acme/star.pdf", "/work/acme/star.pdf"},
		`{"type":"tool_call","id":"j","name":"task__task_boundary","args":{"task_name":"Stars","mode":"execution","node":"n_1"}}`: {KindOther, "", "execution"},
	} {
		e := parse(line)
		if got := [3]string{e.Kind, e.Path, e.Input}; got != want {
			t.Errorf("%s: got %q, want %q", line, got, want)
		}
	}

	if e := parse(`{"type":"done","chat_id":"c","status":"error","error":"Model request failed: rate limited"}`); e.Type != "done" || e.Error != "Model request failed: rate limited" {
		t.Errorf("failed turn %+v", e)
	}
	if e := parse(`{"type":"done","chat_id":"c","status":"stopped"}`); e.Error != "" {
		t.Errorf("stopped turn %+v", e)
	}
	// The pantheon command could not start the turn: the error names the agent.
	if e := parse(`{"type":"error","error":"not signed in: run pantheon login"}`); e.Type != "done" ||
		e.Error != "Could not start Pantheon Agent: not signed in: run pantheon login" {
		t.Errorf("start error %+v", e)
	}
}

// A turn's arguments: the chat to resume, the node and folder it works in,
// a model tier; the message goes on stdin.
func TestPantheonTurnArgs(t *testing.T) {
	a, _ := Lookup(PantheonAgent)
	if a.Headless == nil || a.Headless.PromptArg {
		t.Fatal("the Pantheon Agent takes turns, the message on stdin")
	}
	got := a.Headless.Args(Turn{Prompt: "hi", Session: "chat-1", Node: "n_1", Cwd: "/work/acme", Model: "low",
		Effort: "high", AddDirs: []string{"/elsewhere"}, NoWeb: true, Images: []string{"/a.png"}})
	want := []string{"agent", "chat", "--json", "--chat", "chat-1", "--node", "n_1", "--workdir", "/work/acme", "--model", "low"}
	if !slices.Equal(got, want) {
		t.Errorf("got %q", got)
	}
	if got := a.Headless.Args(Turn{Prompt: "hi"}); !slices.Equal(got, []string{"agent", "chat", "--json"}) {
		t.Errorf("a new chat %q", got)
	}
	c := a.Headless.Canvas
	if c.Effort != nil || c.Research || c.Images || c.Skills || c.Roles || !c.Subagents ||
		!slices.Equal(c.Models, []string{"high", "normal", "low"}) {
		t.Errorf("canvas %+v", c)
	}
	// It knows PantheonOS: no note about it.
	if n := Note(Settings{Access: Access{Key: "pbk_x"}}, Launch{Agent: PantheonAgent}); n != "" {
		t.Errorf("note %q", n)
	}
}
