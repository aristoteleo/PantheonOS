//go:build !windows

package codingagentsapp

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// canvasFixture is a workspace (a git repository with a file to pin), a
// folder outside it and a reference image.
func canvasFixture(t *testing.T) (repo, outside, pinned, img string) {
	t.Helper()
	repo, outside = t.TempDir(), t.TempDir()
	gitRun(t, repo, "init", "-q", "-b", "main")
	pinned = filepath.Join(repo, "src", "calc.py")
	writeFile(t, pinned, "def add(a, b):\n    return a + b\n")
	gitRun(t, repo, "add", "-A")
	gitRun(t, repo, "commit", "-qm", "first")
	img = filepath.Join(repo, ".pantheon", "references", "night-sky.png")
	writeFile(t, img, "png")
	return
}

// fullCanvas asks for everything a canvas turn can.
func fullCanvas(outside, pinned, img string, research bool) *CanvasTurn {
	return &CanvasTurn{Effort: "max", Research: research, Focus: []string{outside}, Files: []string{pinned}, Images: []string{img},
		Subagents: []Subagent{{Name: "explorer", Description: "Explores the code"}}}
}

// A canvas turn reaches Codex as its own flags: --add-dir before resume,
// -i per image, effort (max as xhigh) and web search off by config, the
// settings as developer instructions. Roles are not sent.
func TestCanvasTurnCodex(t *testing.T) {
	record := fakeAgent(t, "codex", `echo '{"type":"thread.started","thread_id":"th-1"}'
echo '{"type":"turn.completed","usage":{}}'`)
	a := NewApp(nil, "", t.TempDir())
	repo, outside, pinned, img := canvasFixture(t)
	res := a.send(SendArgs{Agent: "codex", Cwd: repo, Prompt: "fix it", Auto: true, Origin: "canvas",
		Canvas: fullCanvas(outside, pinned, img, false)})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	id := res["conversation_id"].(string)
	waitIdle(t, a, id)
	calls, _ := os.ReadFile(record)
	first := string(calls)
	for _, want := range []string{
		"ARGS exec --add-dir " + outside + " -i " + img + " --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox " +
			`-c model_reasoning_effort="xhigh" -c web_search="disabled" -`,
		"-c developer_instructions=",
		"Focus on these folders:", outside + "\n\nWeb research is off",
		"Web research is off for this turn",
		"fix it\n\nFiles I pinned for this turn (start from them):\n- " + pinned,
		"Reference images (attached to this message):\n- " + img,
	} {
		if !strings.Contains(first, want) {
			t.Errorf("missing %q in:\n%s", want, first)
		}
	}
	for _, not := range []string{"--agents", "explorer", "Agent tool", "sub-agent"} {
		if strings.Contains(first, not) {
			t.Errorf("roles reached Codex (%q):\n%s", not, first)
		}
	}
	user := a.conversation(id, 0)["events"].([]codingagents.Event)[0]
	if user.Text != "fix it" || !slices.Equal(user.Images, []string{img}) || user.Branch != "main" {
		t.Errorf("user event %+v", user)
	}

	// The next turn resumes the thread; research on lifts the config.
	if r := a.send(SendArgs{ConversationID: id, Prompt: "again", Canvas: &CanvasTurn{Research: true}}); r["success"] != true {
		t.Fatal(r)
	}
	waitIdle(t, a, id)
	calls, _ = os.ReadFile(record)
	second := string(calls)[len(first):]
	if !strings.Contains(second, "ARGS exec resume th-1 --json") || strings.Contains(second, "web_search") ||
		strings.Contains(second, "model_reasoning_effort") || !strings.Contains(second, "Web research is on") {
		t.Errorf("second turn:\n%s", second)
	}
}

// Gemini CLI takes focus folders (--include-directories) and the settings in
// the message; effort, research, images and roles are left out.
func TestCanvasTurnGemini(t *testing.T) {
	record := fakeAgent(t, "gemini", `echo "TRUST $GEMINI_CLI_TRUST_WORKSPACE" >> "$(dirname "$0")/calls.txt"
echo '{"type":"result","status":"success","stats":{}}'`)
	a := NewApp(nil, "", t.TempDir())
	// The fake by its path: a login shell's PATH may hold another gemini.
	fake := filepath.Join(filepath.Dir(record), "gemini")
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{"gemini": {Command: fake}}}); r["success"] != true {
		t.Fatal(r)
	}
	repo, outside, pinned, img := canvasFixture(t)
	res := a.send(SendArgs{Agent: "gemini", Cwd: repo, Prompt: "fix it", Auto: true, Origin: "canvas",
		Canvas: fullCanvas(outside, pinned, img, false)})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	id := res["conversation_id"].(string)
	waitIdle(t, a, id)
	calls, _ := os.ReadFile(record)
	got := string(calls)
	wantPrompt := "[Focus on these folders:\n- " + outside + "\n\nThese settings are for this turn and replace any from earlier turns.]\n\n" +
		"fix it\n\nFiles I pinned for this turn (start from them):\n- " + pinned
	if !strings.Contains(got, "ARGS --prompt="+wantPrompt+" -o stream-json --yolo --include-directories "+outside+"\n") ||
		!strings.Contains(got, "TRUST true\n") {
		t.Errorf("args:\n%s", got)
	}
	for _, not := range []string{"Web research", "night-sky.png", "explorer", "effort", "--policy"} {
		if strings.Contains(got, not) {
			t.Errorf("%q reached Gemini CLI:\n%s", not, got)
		}
	}
	if user := a.conversation(id, 0)["events"].([]codingagents.Event)[0]; len(user.Images) != 0 {
		t.Errorf("images it never got: %+v", user)
	}
}

// Cursor Agent takes focus folders (--add-dir) and reads reference images
// named in the message.
func TestCanvasTurnCursor(t *testing.T) {
	record := fakeAgent(t, "cursor-agent", `echo '{"type":"result","is_error":false,"usage":{}}'`)
	a := NewApp(nil, "", t.TempDir())
	repo, outside, pinned, img := canvasFixture(t)
	res := a.send(SendArgs{Agent: "cursor", Cwd: repo, Prompt: "fix it", Auto: true, Origin: "canvas",
		Canvas: fullCanvas(outside, pinned, img, false)})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	calls, _ := os.ReadFile(record)
	got := string(calls)
	if !strings.Contains(got, "ARGS -p --output-format stream-json --trust --force --add-dir "+outside+" -- [Focus on these folders:") ||
		!strings.Contains(got, "Reference images (read each one):\n- "+img) || strings.Contains(got, "Web research") {
		t.Errorf("args:\n%s", got)
	}
}

// A conversation keeps its agent: a send naming another fails, naming both.
func TestConversationKeepsItsAgent(t *testing.T) {
	fakeAgent(t, "codex", `echo '{"type":"turn.completed","usage":{}}'`)
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "codex", Prompt: "hi"})["conversation_id"].(string)
	waitIdle(t, a, id)
	r := a.send(SendArgs{ConversationID: id, Agent: "gemini", Prompt: "again"})
	if r["success"] != false || r["error"] != "This conversation runs Codex; start a new one to use Gemini CLI" {
		t.Errorf("another agent: %v", r)
	}
	if r := a.send(SendArgs{ConversationID: id, Agent: "codex", Prompt: "again"}); r["success"] != true {
		t.Errorf("the same agent: %v", r)
	}
	waitIdle(t, a, id)
}

// A turn that fails at once says when the agent has no sign-in here.
func TestFailedTurnSaysNotSignedIn(t *testing.T) {
	fakeAgent(t, "codex", "echo 'Please log in.' >&2\nexit 2\n")
	for _, v := range []string{"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME"} {
		t.Setenv(v, "")
	}
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "codex", Prompt: "hi"})["conversation_id"].(string)
	if c := waitIdle(t, a, id); c["last_error"] != "Codex exited: exit status 2; it is not signed in on this node …" {
		t.Errorf("last error %q", c["last_error"])
	}
}

// agents_detect says what a canvas turn can ask of each headless agent.
func TestDetectReportsCanvas(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"claude", "codex", "gemini", "cursor-agent", "goose"} {
		writeFile(t, filepath.Join(dir, name), "#!/bin/sh\n")
		if err := os.Chmod(filepath.Join(dir, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("HOME", t.TempDir())
	t.Setenv("SHELL", "/bin/sh")
	t.Setenv("PATH", dir+":/usr/bin:/bin")
	codingagents.Detect(true)
	a := NewApp(nil, "", t.TempDir())
	b, _ := json.Marshal(a.detect(false)["agents"])
	var agents []struct {
		ID     string          `json:"id"`
		Canvas json.RawMessage `json:"canvas"`
	}
	if err := json.Unmarshal(b, &agents); err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, ag := range agents {
		got[ag.ID] = string(ag.Canvas)
	}
	want := map[string]string{
		"claude": `{"effort":["low","medium","high","xhigh","max"],"research":true,"images":true,"skills":true,"roles":true,"subagents":true,"models":["opus","sonnet","haiku"]}`,
		"codex":  `{"effort":["low","medium","high","xhigh"],"research":true,"images":true,"skills":true,"roles":false,"subagents":false,"models":null}`,
		"gemini": `{"effort":null,"research":false,"images":false,"skills":true,"roles":false,"subagents":false,"models":null}`,
		"cursor": `{"effort":null,"research":false,"images":true,"skills":true,"roles":false,"subagents":false,"models":null}`,
		"goose":  ``,
	}
	for id, w := range want {
		if got[id] != w {
			t.Errorf("%s canvas:\n got %s\nwant %s", id, got[id], w)
		}
	}
}

// agents_skills lists each agent's skills from the places it loads them,
// with the node's settings for the agent (its command, its environment).
func TestSkillsPerAgent(t *testing.T) {
	fakeAgent(t, "claude", "")
	home, proj, tmp := os.Getenv("HOME"), t.TempDir(), t.TempDir()
	skill := func(dir, name, desc string) {
		writeFile(t, filepath.Join(dir, name, "SKILL.md"), "---\nname: "+name+"\ndescription: "+desc+"\n---\n")
	}
	codexHome := filepath.Join(tmp, "codex-home")
	skill(filepath.Join(codexHome, "skills"), "night-sky", "Codex user")
	skill(filepath.Join(codexHome, "skills", ".system"), "skill-creator", "Codex system")
	skill(filepath.Join(home, ".codex", "skills"), "not-this-home", "Not the CODEX_HOME Codex runs with")
	skill(filepath.Join(home, ".agents", "skills"), "acme-shared", "Shared user")
	skill(filepath.Join(home, ".claude", "skills"), "pdf", "Claude user")
	skill(filepath.Join(home, ".cursor", "skills"), "cursor-only", "Cursor user")
	skill(filepath.Join(home, ".cursor", "skills-cursor"), "create-rule", "Cursor built in")
	skill(filepath.Join(proj, ".agents", "skills"), "acme-shared", "Project")
	skill(filepath.Join(proj, ".codex", "skills"), "release", "Codex project")

	// Gemini CLI: a bundle with a built-in skill, two extensions (one off
	// here), a disabled skill and one without front matter.
	bundle := filepath.Join(tmp, "gemini-cli", "bundle")
	writeFile(t, filepath.Join(bundle, "gemini.js"), "#!/bin/sh\n")
	if err := os.Chmod(filepath.Join(bundle, "gemini.js"), 0o755); err != nil {
		t.Fatal(err)
	}
	gemini := filepath.Join(tmp, "bin", "gemini")
	if err := os.MkdirAll(filepath.Dir(gemini), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(bundle, "gemini.js"), gemini); err != nil {
		t.Fatal(err)
	}
	skill(filepath.Join(bundle, "builtin"), "skill-creator", "Gemini built in")
	ext := filepath.Join(home, ".gemini", "extensions")
	writeFile(t, filepath.Join(ext, "stars", "gemini-extension.json"), `{"name":"stars"}`)
	skill(filepath.Join(ext, "stars", "skills"), "constellations", "From an extension")
	writeFile(t, filepath.Join(ext, "moons", "gemini-extension.json"), `{"name":"moons"}`)
	skill(filepath.Join(ext, "moons", "skills"), "phases", "From an extension off here")
	writeFile(t, filepath.Join(ext, "extension-enablement.json"), `{"moons":{"overrides":["!`+proj+`/*"]}}`)
	skill(filepath.Join(home, ".gemini", "skills"), "acme-shared", "Gemini user")
	skill(filepath.Join(home, ".gemini", "skills"), "hidden", "Disabled in settings")
	writeFile(t, filepath.Join(home, ".gemini", "skills", "bare", "SKILL.md"), "# No front matter\n")
	writeFile(t, filepath.Join(home, ".gemini", "settings.json"), `{"skills":{"disabled":["Hidden"]}}`)
	skill(filepath.Join(proj, ".gemini", "skills"), "gem", "Gemini project")

	a := NewApp(nil, "", t.TempDir())
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{
		"codex":  {Command: filepath.Join(tmp, "no-codex"), Env: map[string]string{"CODEX_HOME": codexHome}},
		"gemini": {Command: gemini},
	}}); r["success"] != true {
		t.Fatal(r)
	}
	list := func(agent string) []string {
		var out []string
		for _, s := range skills(proj, agent)["skills"].([]Skill) {
			out = append(out, strings.TrimSpace(s.Name+" "+s.Source+" "+s.Plugin))
		}
		return out
	}
	for agent, want := range map[string][]string{
		"codex": {"acme-shared project", "night-sky user", "release project", "skill-creator system"},
		// A later folder's skill replaces an earlier one of the same name.
		"gemini": {"acme-shared project", "constellations extension stars", "gem project", "skill-creator system"},
		"cursor": {"acme-shared project", "create-rule system", "cursor-only user", "not-this-home user", "pdf user", "release project"},
		"claude": {"pdf user"},
		"":       {"pdf user"},
	} {
		if got := list(agent); !slices.Equal(got, want) {
			t.Errorf("%q:\n got %q\nwant %q", agent, got, want)
		}
	}
	if r := skills(proj, "goose"); r["success"] != true || len(r["skills"].([]Skill)) != 0 {
		t.Errorf("an agent without skills: %v", r)
	}
	if r := skills(proj, "nonesuch"); r["success"] != false {
		t.Errorf("an unknown agent: %v", r)
	}
	// Skills off in Gemini CLI's settings: none.
	writeFile(t, filepath.Join(proj, ".gemini", "settings.json"), `{"skills":{"enabled":false}}`)
	if got := list("gemini"); len(got) != 0 {
		t.Errorf("skills off: %q", got)
	}

	// Through the tool, agent picks the list.
	tools, err := Tools(a)
	if err != nil {
		t.Fatal(err)
	}
	for _, tool := range tools {
		if tool.Name == "agents_skills" {
			out, _ := tool.Handler(context.Background(), map[string]any{"cwd": proj, "agent": "cursor"})
			if n := len(out.(map[string]any)["skills"].([]Skill)); n != 6 {
				t.Errorf("agents_skills(agent=cursor): %v", out)
			}
		}
	}
}

// Skills pinned for a turn are named in the message with their SKILL.md,
// found among the agent's own skills, and tick on the canvas as skill
// steps; an unknown one is refused. Claude Code's message is unchanged.
func TestPinnedSkills(t *testing.T) {
	record := fakeAgent(t, "cursor-agent", `echo '{"type":"result","is_error":false,"usage":{}}'`)
	repo := t.TempDir()
	skillPath := filepath.Join(repo, ".agents", "skills", "night-sky", "SKILL.md")
	writeFile(t, skillPath, "---\nname: night-sky\ndescription: Stars\n---\n")
	a := NewApp(nil, "", t.TempDir())
	res := a.send(SendArgs{Agent: "cursor", Cwd: repo, Prompt: "tonight?", Origin: "canvas",
		Canvas: &CanvasTurn{Research: true, Skills: []string{"night-sky", "night-sky"}}})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	id := res["conversation_id"].(string)
	c := waitIdle(t, a, id)
	calls, _ := os.ReadFile(record)
	want := "tonight?\n\nSkills I pinned for this turn (read each SKILL.md first and follow it):\n- night-sky: " + skillPath + "\n"
	if !strings.Contains(string(calls), want) || strings.Count(string(calls), "- night-sky:") != 1 {
		t.Errorf("message:\n%s", calls)
	}
	ev := a.conversation(id, 0)["events"].([]codingagents.Event)
	if ev[1].Type != "tool" || ev[1].Kind != codingagents.KindSkill || ev[1].Input != "night-sky" || ev[1].Path != skillPath || ev[1].Status != "done" {
		t.Errorf("pinned skill event %+v", ev[1])
	}
	if canvas := c["canvas"].(map[string]any); !slices.Equal(canvas["skills"].([]any), []any{"night-sky"}) {
		t.Errorf("canvas %v", canvas)
	}

	r := a.send(SendArgs{Agent: "cursor", Cwd: repo, Prompt: "hi", Canvas: &CanvasTurn{Research: true, Skills: []string{"moon"}}})
	if r["success"] != false || r["error"] != "moon is not a Cursor Agent skill on this node" {
		t.Errorf("unknown skill: %v", r)
	}

	// Claude Code: its skills are typed as /name; skills changes nothing.
	claude := fakeAgent(t, "claude", claudeTurn)
	b := NewApp(nil, "", t.TempDir())
	for _, skills := range [][]string{nil, {"night-sky"}} {
		id := b.send(SendArgs{Agent: "claude", Cwd: repo, Prompt: "tonight?", Origin: "canvas",
			Canvas: &CanvasTurn{Research: true, Skills: skills}})["conversation_id"].(string)
		waitIdle(t, b, id)
	}
	calls, _ = os.ReadFile(claude)
	turns := strings.Split(strings.TrimSpace(string(calls)), "ARGS ")
	if len(turns) != 3 || strings.TrimSpace(turns[1]) != strings.TrimSpace(turns[2]) || strings.Contains(string(calls), "Skills I pinned") {
		t.Errorf("claude turns differ:\n%s", calls)
	}
}

// What a conversation keeps of a canvas turn is what its agent took.
func TestCanvasKeepsWhatWasApplied(t *testing.T) {
	fakeAgent(t, "cursor-agent", `echo '{"type":"result","is_error":false,"usage":{}}'`)
	a := NewApp(nil, "", t.TempDir())
	repo, outside, pinned, img := canvasFixture(t)
	res := a.send(SendArgs{Agent: "cursor", Cwd: repo, Prompt: "hi", Canvas: fullCanvas(outside, pinned, img, false)})
	c := waitIdle(t, a, res["conversation_id"].(string))
	b, _ := json.Marshal(c["canvas"]) // a map: keys sorted
	want := `{"files":["` + pinned + `"],"focus":["` + outside + `"],"images":["` + img + `"],"research":true}`
	if string(b) != want {
		t.Errorf("canvas\n got %s\nwant %s", b, want)
	}
}

// Gemini CLI splits its folder list at commas: such a folder is refused.
// Its trust variable is a default the user's own setting overrides.
func TestGeminiFoldersAndTrust(t *testing.T) {
	record := fakeAgent(t, "gemini", `echo "TRUST $GEMINI_CLI_TRUST_WORKSPACE" >> "$(dirname "$0")/calls.txt"
echo '{"type":"result","status":"success","stats":{}}'`)
	a := NewApp(nil, "", t.TempDir())
	fake := filepath.Join(filepath.Dir(record), "gemini")
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{"gemini": {Command: fake,
		Env: map[string]string{"GEMINI_CLI_TRUST_WORKSPACE": "false"}}}}); r["success"] != true {
		t.Fatal(r)
	}
	repo := t.TempDir()
	comma := filepath.Join(t.TempDir(), "a,b")
	if err := os.Mkdir(comma, 0o755); err != nil {
		t.Fatal(err)
	}
	r := a.send(SendArgs{Agent: "gemini", Cwd: repo, Prompt: "hi", Canvas: &CanvasTurn{Research: true, Focus: []string{comma}}})
	if r["success"] != false || !strings.Contains(fmt.Sprint(r["error"]), "Gemini CLI cannot be given the folder "+comma) {
		t.Errorf("comma folder: %v", r)
	}
	id := a.send(SendArgs{Agent: "gemini", Cwd: repo, Prompt: "hi"})["conversation_id"].(string)
	waitIdle(t, a, id)
	if calls, _ := os.ReadFile(record); !strings.Contains(string(calls), "TRUST false\n") {
		t.Errorf("the user's setting was overridden:\n%s", calls)
	}
}

// Research off tells Codex the shell is off the network too.
func TestCodexResearchOffWording(t *testing.T) {
	codex, _ := codingagents.Lookup("codex")
	l, err := (&CanvasTurn{Research: false}).prepare(t.TempDir())
	if err == nil {
		l, err = l.forAgent(codex)
	}
	if err != nil || !strings.Contains(l.system, "no curl, wget or other network commands in the shell") {
		t.Errorf("%v %q", err, l.system)
	}
}

func TestSendNamesTheAgent(t *testing.T) {
	fakeAgent(t, "goose", "")
	a := NewApp(nil, "", t.TempDir())
	if r := a.send(SendArgs{Agent: "goose", Prompt: "hi"}); r["error"] != "Goose has no conversation mode; open it in a terminal" {
		t.Errorf("goose: %v", r)
	}
	if r := a.send(SendArgs{Agent: "nonesuch", Prompt: "hi"}); r["error"] != `Unknown coding agent "nonesuch"` {
		t.Errorf("unknown: %v", r)
	}
}
