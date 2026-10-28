package node

import (
	"sync/atomic"

	"github.com/aristoteleo/pantheonos/os/codingagents"
	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

var detectingAgents atomic.Bool

// CodingAgents is the last detection of coding-agent CLIs on this machine.
// When it is older than the detection interval a new one starts in the
// background, so a heartbeat never waits on the login-shell probe; the next
// heartbeat carries the result.
func CodingAgents() []proto.CodingAgent {
	found, fresh := codingagents.Cached()
	if !fresh && detectingAgents.CompareAndSwap(false, true) {
		go func() {
			defer detectingAgents.Store(false)
			codingagents.Detect(false)
		}()
	}
	out := make([]proto.CodingAgent, 0, len(found))
	for _, f := range codingagents.Apply(found, codingagents.LoadSettings()) {
		if f.Hidden || f.Missing {
			continue
		}
		out = append(out, proto.CodingAgent{ID: f.ID, Label: f.Label, Path: f.Path,
			Terminal: f.Terminal, Conversation: f.Headless})
	}
	return out
}
