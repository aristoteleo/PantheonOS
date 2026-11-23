//go:build !windows

package hosted

import (
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// shellAgents is fakeAgents whose nodes are this machine: commands run here
// with sh, a transfer copies the file.
type shellAgents struct {
	*fakeAgents
	mu        sync.Mutex
	runs      []string
	transfers [][2]string
	failCopy  string
}

func (s *shellAgents) Run(_ context.Context, _, _, code string, _ int) map[string]any {
	s.mu.Lock()
	s.runs = append(s.runs, code)
	s.mu.Unlock()
	cmd := exec.Command("sh", "-c", code)
	var out, errb strings.Builder
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	return map[string]any{"success": err == nil, "stdout": out.String(), "stderr": errb.String()}
}

func (s *shellAgents) Transfer(_ context.Context, _, _, src, _, dst string, _ int) map[string]any {
	s.mu.Lock()
	s.transfers = append(s.transfers, [2]string{src, dst})
	fail := s.failCopy
	s.mu.Unlock()
	if fail != "" {
		return map[string]any{"state": "failed", "error": fail}
	}
	in, err := os.Open(src)
	if err != nil {
		return map[string]any{"state": "failed", "error": err.Error()}
	}
	defer in.Close()
	_ = os.MkdirAll(filepath.Dir(dst), 0o755)
	out, err := os.Create(dst)
	if err != nil {
		return map[string]any{"state": "failed", "error": err.Error()}
	}
	defer out.Close()
	if _, err := io.Copy(out, in); err != nil {
		return map[string]any{"state": "failed", "error": err.Error()}
	}
	return map[string]any{"state": "done"}
}

func agentOfResult(out map[string]any) map[string]any { return out["agent"].(map[string]any) }

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestMovingAnAgentCopiesItsFolderAndStartsANewSessionThere(t *testing.T) {
	url := jetStream(t)
	nodes := &shellAgents{fakeAgents: newFakeAgents()}
	nodes.online["n_box"] = true
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	old := filepath.Join(t.TempDir(), "it's mine") // a quote in the path
	write(t, filepath.Join(old, "MEMORY.md"), "answer in French")
	write(t, filepath.Join(old, "notes", "plan.txt"), "step 1")
	write(t, filepath.Join(old, ".team", "inbox"), "node-specific")
	mid := makeAgent(t, h, b, "Mover", "n_mac", "claude", map[string]any{"folder": old})
	// A first turn makes its session on n_mac.
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@mover hi"})
	eventually(t, "first turn", func() bool { return nodes.promptCount() == 1 })
	eventually(t, "its turn ends", func() bool {
		st := call(t, h, b, "space_get", map[string]any{})["agent_status"].(map[string]any)[mid].(map[string]any)
		return st["state"] == "ready" && st["queue"] == float64(0)
	})

	// Refused: the same computer, a computer not the owner's, an offline one.
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_mac"}); !strings.Contains(e, "on that computer already") {
		t.Fatal(e)
	}
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_nope"}); !strings.Contains(e, "not one of the agent owner's computers") {
		t.Fatal(e)
	}
	nodes.fakeAgents.mu.Lock()
	nodes.online["n_box"] = false
	nodes.fakeAgents.mu.Unlock()
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box"}); !strings.Contains(e, "is offline. Start it") {
		t.Fatal(e)
	}
	nodes.fakeAgents.mu.Lock()
	nodes.online["n_box"] = true
	// Its runtime not installed there.
	saved := nodes.detect
	nodes.detect = []any{map[string]any{"id": "codex", "conversation": true}}
	nodes.fakeAgents.mu.Unlock()
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box"}); !strings.Contains(e, "Claude Code is not installed on") || !strings.Contains(e, "open Coding Agents on") {
		t.Fatal(e)
	}
	nodes.fakeAgents.mu.Lock()
	nodes.detect = saved
	nodes.fakeAgents.mu.Unlock()
	nodes.mu.Lock()
	nodes.failCopy = "connect: no route"
	nodes.mu.Unlock()
	dst := filepath.Join(t.TempDir(), "PantheonAgents", "mover")
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box", "folder": dst}); !strings.Contains(e, "Could not copy") || !strings.Contains(e, "Nothing was moved") {
		t.Fatal(e)
	}
	nodes.mu.Lock()
	nodes.failCopy = ""
	nodes.mu.Unlock()

	out := call(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box", "folder": dst})
	a := agentOfResult(out)
	if a["node_id"] != "n_box" || a["folder"] != dst || a["session_conversation_id"] != nil && a["session_conversation_id"] != "" || out["copied"] != true {
		t.Fatalf("%v", out)
	}
	if st := out["status"].(map[string]any); st["state"] == "starting" || st["moving"] == true {
		t.Fatalf("the answer's status is still moving: %v", st)
	}
	if !strings.Contains(str(out["done"]), "the old one stays on") || out["old_folder"] != old {
		t.Fatalf("%v", out)
	}
	if b, _ := os.ReadFile(filepath.Join(dst, "MEMORY.md")); string(b) != "answer in French" {
		t.Fatalf("memory: %q", b)
	}
	if b, _ := os.ReadFile(filepath.Join(dst, "notes", "plan.txt")); string(b) != "step 1" {
		t.Fatalf("notes: %q", b)
	}
	if _, err := os.Stat(filepath.Join(dst, ".team", "inbox")); err == nil {
		t.Fatal(".team/ was copied")
	}
	if b, _ := os.ReadFile(filepath.Join(old, "MEMORY.md")); string(b) != "answer in French" {
		t.Fatal("the old folder was changed")
	}
	// The archives are gone from both temp folders.
	for _, tr := range nodes.transfers {
		if _, err := os.Stat(tr[0]); err == nil {
			t.Fatalf("source archive %s left", tr[0])
		}
		if _, err := os.Stat(tr[1]); err == nil {
			t.Fatalf("target archive %s left", tr[1])
		}
	}
	// Its next wake runs on the new computer, in the new folder, in a new session.
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@mover where are you"})
	eventually(t, "a turn after the move", func() bool { return nodes.promptCount() == 2 })
	nodes.fakeAgents.mu.Lock()
	last := nodes.sends[len(nodes.sends)-1]
	nodes.fakeAgents.mu.Unlock()
	if last["cwd"] != dst || last["conversation_id"] != nil {
		t.Fatalf("the turn after the move: %v", last)
	}
}

func TestMovingAnAgentWhoseComputerIsOffline(t *testing.T) {
	url := jetStream(t)
	nodes := &shellAgents{fakeAgents: newFakeAgents()}
	nodes.online["n_box"] = true
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	old := filepath.Join(t.TempDir(), "old")
	write(t, filepath.Join(old, "MEMORY.md"), "x")
	mid := makeAgent(t, h, b, "Mover", "n_mac", "codex", nil)
	makeAgent(t, h, b, "Cloud", "n_ws", "codex", nil)
	// A paused Workspace is not listed while it is away: an agent there makes it the owner's, offline.
	nodes.fakeAgents.mu.Lock()
	nodes.gone = map[string]bool{"n_ws": true}
	nodes.fakeAgents.mu.Unlock()
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_ws", "fresh": true}); !strings.Contains(e, "Your Cloud Workspace is offline. Start it") {
		t.Fatal(e)
	}
	nodes.fakeAgents.mu.Lock()
	nodes.online["n_mac"] = false
	nodes.fakeAgents.mu.Unlock()
	dst := filepath.Join(t.TempDir(), "new")
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box", "folder": dst}); !strings.Contains(e, "is offline, so Mover's folder cannot be copied") ||
		!strings.Contains(e, "start fresh") {
		t.Fatal(e)
	}
	out := call(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box", "folder": dst, "fresh": true})
	if agentOfResult(out)["node_id"] != "n_box" || out["copied"] != false || !strings.Contains(str(out["done"]), "starts fresh") {
		t.Fatalf("%v", out)
	}
	if len(nodes.runs) != 0 {
		t.Fatalf("a fresh start ran %v", nodes.runs)
	}
}

func TestAMoveWaitsForTheRunningTurn(t *testing.T) {
	url := jetStream(t)
	nodes := &shellAgents{fakeAgents: newFakeAgents()}
	nodes.online["n_box"] = true
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	mid := makeAgent(t, h, b, "Mover", "n_mac", "claude", nil)
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@mover work"})
	eventually(t, "its turn", func() bool { return nodes.promptCount() == 1 })
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": mid, "node_id": "n_box", "fresh": true}); !strings.Contains(e, "stop the turn first") {
		t.Fatal(e)
	}
	nodes.release <- struct{}{}
}

// agent_update's node_id is a computer of the owner's; a folder has its shape.
func TestAgentUpdateChecksTheComputerAndFolder(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	mid := makeAgent(t, h, b, "W", "n_mac", "claude", nil)
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": mid, "node_id": "n_someone_elses"}); !strings.Contains(e, "not one of the agent owner's computers") {
		t.Fatal(e)
	}
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": mid, "folder": "relative/x"}); !strings.Contains(e, "Choose a full folder") {
		t.Fatal(e)
	}
	a := agentOfResult(call(t, h, b, "agent_update", map[string]any{"mid": mid, "node_id": "n_ws"}))
	if a["node_id"] != "n_ws" || a["node_kind"] != "sandbox" {
		t.Fatalf("%v", a)
	}
}

// Effort: only levels the runtime takes; it reaches the turn.
func TestAnAgentsEffortIsCheckedAndReachesItsTurn(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	if e := refused(t, h, b, "agent_create", map[string]any{"spec": map[string]any{"name": "C", "node_id": "n_mac", "runtime": "codex", "effort": "max"}}); !strings.Contains(e, "low, medium, high, xhigh") {
		t.Fatal(e)
	}
	mid := makeAgent(t, h, b, "Coder", "n_mac", "codex", map[string]any{"effort": "high"})
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": mid, "effort": "turbo"}); !strings.Contains(e, "reasoning effort for Codex") {
		t.Fatal(e)
	}
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@coder hi"})
	eventually(t, "its turn", func() bool { return nodes.promptCount() == 1 })
	nodes.mu.Lock()
	sent := nodes.sends[0]
	nodes.mu.Unlock()
	if sent["effort"] != "high" {
		t.Fatalf("the turn's effort: %v", sent["effort"])
	}
	a := agentOfResult(call(t, h, b, "agent_update", map[string]any{"mid": mid, "effort": "xhigh"}))
	if a["effort"] != "xhigh" {
		t.Fatalf("%v", a)
	}
	// Claude Code takes max; a runtime that takes none drops the level.
	eventually(t, "the turn ends", func() bool {
		st := call(t, h, b, "space_get", map[string]any{})["agent_status"].(map[string]any)[mid].(map[string]any)
		return st["state"] == "ready"
	})
	if a := agentOfResult(call(t, h, b, "agent_update", map[string]any{"mid": mid, "runtime": "claude", "effort": "max"})); a["effort"] != "max" {
		t.Fatalf("%v", a)
	}
	if a := agentOfResult(call(t, h, b, "agent_update", map[string]any{"mid": mid, "runtime": "gemini"})); a["effort"] != "" && a["effort"] != nil {
		t.Fatalf("gemini kept the effort: %v", a)
	}
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": mid, "effort": "low"}); !strings.Contains(e, "Gemini CLI does not take a reasoning effort") {
		t.Fatal(e)
	}
}
