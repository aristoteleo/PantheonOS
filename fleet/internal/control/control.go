// Package control is the Runner's control-plane side: it serves the Agent's
// commands (currently run_task / ping) over the Node's NATS cmd subject.
package control

import (
	"context"
	"encoding/json"

	fexec "github.com/aristoteleo/pantheon-fleet/internal/exec"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// Conn is a NATS connection scoped to one Fleet/Node.
type Conn struct {
	nc    *nats.Conn
	fleet string
	node  string
}

// New wraps an established NATS connection.
func New(nc *nats.Conn, fleet, node string) *Conn {
	return &Conn{nc: nc, fleet: fleet, node: node}
}

// ServeCommands subscribes to this Node's cmd subject and dispatches commands.
// Tasks run in their own goroutine so the subscription is never blocked; the
// reply is sent when the Task finishes (the caller sets its own timeout).
func (c *Conn) ServeCommands(ctx context.Context) (*nats.Subscription, error) {
	subj := proto.SubjNodeCmd(c.fleet, c.node)
	return c.nc.Subscribe(subj, func(m *nats.Msg) {
		var cmd proto.Command
		if err := json.Unmarshal(m.Data, &cmd); err != nil {
			replyErr(m, "bad command: "+err.Error())
			return
		}
		switch cmd.Type {
		case "run_task":
			if cmd.Task == nil {
				replyErr(m, "run_task without task")
				return
			}
			task := *cmd.Task
			go func() { reply(m, fexec.Run(ctx, task)) }()
		case "ping":
			reply(m, map[string]string{"pong": c.node})
		default:
			replyErr(m, "unknown command type: "+cmd.Type)
		}
	})
}

func reply(m *nats.Msg, v any) {
	b, _ := json.Marshal(v)
	_ = m.Respond(b)
}

func replyErr(m *nats.Msg, msg string) {
	reply(m, map[string]string{"error": msg})
}
