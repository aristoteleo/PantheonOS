//go:build !windows

package codingagentsapp

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A Team Chat agent's reasoning effort reaches its runtime's command line
// (Claude Code --effort, Codex model_reasoning_effort), and a Team Chat turn
// gets none of the Canvas's per-turn settings lines (docs/apps/team-chat.md,
// "Reasoning effort").
func TestATeamTurnRunsWithTheAgentsEffort(t *testing.T) {
	dir := fakeAgents(t, map[string]string{"claude": claudeDone, "codex": codexDone, "gemini": ""})
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "ok"}, nil
	}
	for _, c := range []struct{ agent, effort, want string }{
		{"claude", "high", "--effort high"},
		{"codex", "xhigh", `model_reasoning_effort="xhigh"`},
	} {
		folder := filepath.Join(t.TempDir(), c.agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		res := a.send(SendArgs{Agent: c.agent, Cwd: folder, Prompt: "[Team Chat] hi", Auto: true, Origin: "team-chat",
			Canvas: &CanvasTurn{Effort: c.effort, Research: true},
			Team:   &TeamTurn{MID: "a1", Token: "tok", Handle: "w", Name: "W"}})
		if res["success"] != true {
			t.Fatal(c.agent, res)
		}
		waitIdle(t, a, res["conversation_id"].(string))
		calls, _ := os.ReadFile(filepath.Join(dir, "calls-"+c.agent+".txt"))
		if !strings.Contains(string(calls), c.want) {
			t.Fatalf("%s: the effort is not on its command line: %s", c.agent, calls)
		}
		for _, line := range []string{"Web research is on", "No sub-agent roles", "These settings are for this turn"} {
			if strings.Contains(string(calls), line) {
				t.Fatalf("%s: a Team Chat turn got the canvas line %q: %s", c.agent, line, calls)
			}
		}
	}
}
