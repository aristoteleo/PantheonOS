package codingagents

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// The claude-subagents-* fixtures are real Claude Code 2.1 runs (paths
// shortened) on a tiny repository with sub-agents defined by --agents:
//
//   - parallel: explorer, reviewer and tester launched at once, in the
//     background;
//   - twice-fail: explorer launched twice, and a sub-agent whose model does
//     not exist, so it fails;
//   - stop: three sub-agents, interrupted (SIGINT) while two of them run.

const (
	explorerID = "toolu_012EycaQTSvN4D46aPBDe7H3"
	reviewerID = "toolu_013RnTPmkFU8fYJxqXoTtUw7"
	testerID   = "toolu_016CumKjJCky5isfDLfphuPr"
)

// parseLines parses a fixture, keeping which raw line each event came from
// and that line's parent_tool_use_id.
func parseLines(t *testing.T, name string) (events []Event, parents []string) {
	t.Helper()
	f, err := os.Open(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	a, _ := Lookup("claude")
	p := a.Headless.Parser()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var raw struct {
			Type   string  `json:"type"`
			Parent *string `json:"parent_tool_use_id"`
		}
		if err := json.Unmarshal(sc.Bytes(), &raw); err != nil {
			t.Fatalf("fixture line: %v", err)
		}
		for _, e := range p.Parse(sc.Bytes()) {
			events = append(events, e)
			parent := ""
			if raw.Parent != nil && (raw.Type == "assistant" || raw.Type == "user") {
				parent = *raw.Parent
			}
			parents = append(parents, parent)
		}
	}
	return events, parents
}

// toolEvents is every event for one tool id, in order.
func toolEvents(events []Event, id string) []Event {
	var out []Event
	for _, e := range events {
		if e.Type == "tool" && e.ID == id {
			out = append(out, e)
		}
	}
	return out
}

// Every event carries exactly the parent of the line it came from: none for
// the main agent, the launch's tool id for a sub-agent's.
func TestSubagentAttributionIsExact(t *testing.T) {
	for _, name := range []string{"claude-subagents-parallel.jsonl", "claude-subagents-twice-fail.jsonl", "claude-subagents-stop.jsonl"} {
		events, parents := parseLines(t, name)
		inside := 0
		for i, e := range events {
			if e.Subagent != "" && e.Status != "running" {
				continue // a launch's end: reported by the main agent's side
			}
			if e.Type == "tool" && e.Activity != "" {
				continue // progress reports name the launch by id
			}
			if e.Parent != parents[i] {
				t.Errorf("%s: event %d %+v: parent %q, the line's %q", name, i, e, e.Parent, parents[i])
			}
			if e.Parent != "" {
				inside++
			}
		}
		if inside < 4 {
			t.Errorf("%s: only %d events from inside sub-agents", name, inside)
		}
	}
}

func TestParallelSubagents(t *testing.T) {
	events, _ := parseLines(t, "claude-subagents-parallel.jsonl")
	launches := map[string]string{explorerID: "explorer", reviewerID: "reviewer", testerID: "tester"}
	// What each sub-agent did, as the stream reports it (its thinking
	// comes summarised away, empty).
	steps := map[string][]string{
		explorerID: {"Read", "text"},
		reviewerID: {"Read", "Read", "Glob", "text"},
		testerID:   {"Bash", "text"},
	}
	for id, kind := range launches {
		evs := toolEvents(events, id)
		launch := evs[0]
		if launch.Name != "Agent" || launch.Subagent != kind || launch.Status != "running" || launch.Parent != "" ||
			launch.Input == "" || launch.Text == "" {
			t.Errorf("%s launch: %+v", kind, launch)
		}
		end := evs[len(evs)-1]
		if end.Status != "done" || end.Subagent != kind || end.Output == "" || end.Error != "" {
			t.Errorf("%s end: %+v", kind, end)
		}
		// The background launch's "started" result is not an end.
		for _, e := range evs[1 : len(evs)-1] {
			if e.Status != "running" || e.Activity == "" {
				t.Errorf("%s: between launch and end only progress, got %+v", kind, e)
			}
		}
		var got []string
		for _, e := range events {
			if e.Parent != id {
				continue
			}
			if e.Type == "tool" {
				if e.Status == "running" {
					got = append(got, e.Name)
				}
			} else {
				got = append(got, e.Type)
			}
		}
		if !slices.Equal(got, steps[id]) {
			t.Errorf("%s steps: %v, want %v", kind, got, steps[id])
		}
	}
	if evs := toolEvents(events, explorerID); !strings.Contains(evs[len(evs)-1].Output, "add(a, b)") {
		t.Errorf("explorer report: %q", evs[len(evs)-1].Output)
	}
	// Sub-agents run while the main agent goes on: the tester's Bash starts
	// after the reviewer's first Read and before the explorer ends.
	order := func(pred func(Event) bool) int { return slices.IndexFunc(events, pred) }
	bash := order(func(e Event) bool { return e.Name == "Bash" })
	read := order(func(e Event) bool { return e.Name == "Read" && e.Parent == reviewerID })
	explorerEnd := order(func(e Event) bool { return e.ID == explorerID && e.Status == "done" })
	if !(read < bash && bash < explorerEnd) {
		t.Errorf("interleaving: reviewer read %d, tester bash %d, explorer end %d", read, bash, explorerEnd)
	}
	// One result per follow-up of the main agent; the last sums the turn.
	var dones []Event
	for _, e := range events {
		if e.Type == "done" {
			dones = append(dones, e)
		}
	}
	last := dones[len(dones)-1]
	if len(dones) != 4 || last.DurationMs != 0 || last.CostUSD != 0.01039794 || last.OutputTokens <= dones[0].OutputTokens {
		t.Errorf("done events: %d, last %+v", len(dones), last)
	}
}

func TestSubagentInvokedTwiceAndFailing(t *testing.T) {
	events, _ := parseLines(t, "claude-subagents-twice-fail.jsonl")
	byKind := map[string][]Event{}
	for _, e := range events {
		if e.Subagent != "" && e.Name != "" {
			byKind[e.Subagent] = append(byKind[e.Subagent], e)
		}
	}
	if len(byKind["explorer"]) != 2 || byKind["explorer"][0].ID == byKind["explorer"][1].ID {
		t.Fatalf("explorer launches: %+v", byKind["explorer"])
	}
	for _, l := range byKind["explorer"] {
		evs := toolEvents(events, l.ID)
		ends := 0
		for _, e := range evs {
			if e.Status != "running" {
				ends++
			}
		}
		// The foreground launch's tool result repeats the notification's end.
		if ends != 1 || evs[len(evs)-1].Status != "done" {
			t.Errorf("explorer %s: %d ends, last %+v", l.ID, ends, evs[len(evs)-1])
		}
		if reads := slices.IndexFunc(events, func(e Event) bool { return e.Name == "Read" && e.Parent == l.ID }); reads < 0 {
			t.Errorf("explorer %s: no Read of its own", l.ID)
		}
	}
	broken := byKind["broken"]
	if len(broken) != 1 {
		t.Fatalf("broken launches: %+v", broken)
	}
	evs := toolEvents(events, broken[0].ID)
	end := evs[len(evs)-1]
	if end.Status != "error" || end.Stopped || !strings.Contains(end.Error, "claude-nonexistent-model-9") {
		t.Errorf("failed sub-agent end: %+v", end)
	}
}

func TestSubagentsStoppedMidway(t *testing.T) {
	events, _ := parseLines(t, "claude-subagents-stop.jsonl")
	ends := map[string]Event{}
	for _, e := range events {
		if e.Subagent != "" && e.Status != "running" {
			if _, dup := ends[e.Subagent]; dup {
				t.Errorf("%s ended twice", e.Subagent)
			}
			ends[e.Subagent] = e
		}
	}
	if e := ends["explorer"]; e.Status != "done" || e.Stopped {
		t.Errorf("explorer finished before the stop: %+v", e)
	}
	for _, kind := range []string{"tester", "sleeper"} {
		if e := ends[kind]; e.Status != "error" || !e.Stopped {
			t.Errorf("%s stopped: %+v", kind, e)
		}
	}
	// Background shell commands a sub-agent started also report their end;
	// those are not sub-agents.
	if n := len(toolEvents(events, "toolu_012hQPBTvvMmu7DZFv9NHtYY")); n != 2 {
		t.Errorf("a background command has %d events, want its call and result", n)
	}
	if last := events[len(events)-1]; last.Type != "done" || last.Error == "" {
		t.Errorf("last event %+v", last)
	}
}

func TestClaudeCanvasArgs(t *testing.T) {
	a, _ := Lookup("claude")
	got := a.Headless.Args(Turn{Session: "s1", Model: "opus", Auto: true, Effort: "xhigh",
		Subagents: `{"explorer":{}}`, AddDirs: []string{"/a", "/b c"}, NoWeb: true})
	want := []string{"-p", "--output-format", "stream-json", "--verbose", "--resume", "s1", "--model", "opus",
		"--dangerously-skip-permissions", "--effort", "xhigh", "--agents", `{"explorer":{}}`,
		"--add-dir", "/a", "--add-dir", "/b c", "--disallowedTools", "WebSearch,WebFetch"}
	if !slices.Equal(got, want) {
		t.Errorf("args:\n got %q\nwant %q", got, want)
	}
	plain := a.Headless.Args(Turn{})
	if !slices.Equal(plain, []string{"-p", "--output-format", "stream-json", "--verbose"}) {
		t.Errorf("a plain turn: %q", plain)
	}
}

// fixtureLine is the first line of a fixture that contains all of marks.
func fixtureLine(t *testing.T, name string, marks ...string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	for _, l := range strings.Split(string(b), "\n") {
		ok := true
		for _, m := range marks {
			ok = ok && strings.Contains(l, m)
		}
		if ok {
			return []byte(l)
		}
	}
	t.Fatalf("no line with %v in %s", marks, name)
	return nil
}

// A foreground sub-agent's tool result, without a notification before it,
// gives only the report, not the frame Claude Code wraps it in for the
// model; from the structured result or, failing that, from the text.
func TestForegroundReportWithoutFrame(t *testing.T) {
	const fx, id = "claude-subagents-twice-fail.jsonl", "toolu_01SQByfF7tf8T2tG7MfTkPCF"
	launch := fixtureLine(t, fx, `"type":"tool_use","id":"`+id+`"`)
	result := fixtureLine(t, fx, `"tool_use_id":"`+id+`","type":"tool_result"`)
	var withoutStructured map[string]any
	_ = json.Unmarshal(result, &withoutStructured)
	delete(withoutStructured, "tool_use_result")
	plain, _ := json.Marshal(withoutStructured)

	want := "Here is what `src/calc.py` contains: a minimal six-line module with two functions, `add(a, b)` returning " +
		"`a + b` and `sub(a, b)` returning `a - b`. It has no classes, imports, docstrings, or input validation, so it " +
		"works with any operands that support `+` and `-`.\n\nFile: /tmp/calc/src/calc.py"
	for name, line := range map[string][]byte{"structured": result, "text": plain} {
		p := &claudeParser{}
		p.Parse(launch)
		out := p.Parse(line)
		if len(out) != 1 || out[0].Status != "done" || out[0].Output != want {
			t.Errorf("%s: %+v\n got %q", name, out, out[0].Output)
		}
	}
	if !strings.Contains(string(plain), "[Subagent hand-back]") || !strings.Contains(string(plain), "agentId: ") {
		t.Error("the fixture's tool result no longer has the frame")
	}
}

// Claude Code repeats its init line after each background sub-agent ends;
// a turn reports its session once.
func TestOneSessionEventPerTurn(t *testing.T) {
	for _, name := range []string{"claude-subagents-parallel.jsonl", "claude-subagents-twice-fail.jsonl", "claude-subagents-stop.jsonl"} {
		events, _ := parseLines(t, name)
		n := 0
		for _, e := range events {
			if e.Type == "session" {
				n++
			}
		}
		if n != 1 {
			t.Errorf("%s: %d session events", name, n)
		}
	}
	if raw, _ := os.ReadFile(filepath.Join("testdata", "claude-subagents-parallel.jsonl")); strings.Count(string(raw), `"subtype":"init"`) < 2 {
		t.Error("the fixture no longer repeats init")
	}
}
