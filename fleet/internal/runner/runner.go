// Package runner ties the Runner together: it serves the Agent's commands
// (run_task / transfer / ping) over the control plane, drives the data plane
// for Transfers, and heartbeats the Node record into the Registry.
package runner

import (
	"context"
	"encoding/json"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
	fexec "github.com/aristoteleo/pantheon-fleet/internal/exec"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/nats-io/nats.go"
)

// Runner holds everything a Node needs to serve the Agent.
type Runner struct {
	nc    *nats.Conn
	fleet string
	node  string
	reg   *registry.Registry
	dp    *dataplane.Plane // may be nil if the data plane is disabled
	rec   *proto.Node
}

// New builds a Runner. dp may be nil (control-plane-only mode).
func New(nc *nats.Conn, fleet, node string, reg *registry.Registry, dp *dataplane.Plane, rec *proto.Node) *Runner {
	return &Runner{nc: nc, fleet: fleet, node: node, reg: reg, dp: dp, rec: rec}
}

// Serve subscribes to this Node's cmd subject and dispatches commands. Tasks
// and Transfers run in their own goroutine so the subscription never blocks.
func (r *Runner) Serve() (*nats.Subscription, error) {
	return r.nc.Subscribe(proto.SubjNodeCmd(r.fleet, r.node), func(m *nats.Msg) {
		var cmd proto.Command
		if err := json.Unmarshal(m.Data, &cmd); err != nil {
			r.replyErr(m, "bad command: "+err.Error())
			return
		}
		switch cmd.Type {
		case "run_task":
			if cmd.Task == nil {
				r.replyErr(m, "run_task without task")
				return
			}
			t := *cmd.Task
			go func() { r.reply(m, fexec.Run(context.Background(), t)) }()
		case "transfer":
			if cmd.Transfer == nil {
				r.replyErr(m, "transfer without request")
				return
			}
			req := *cmd.Transfer
			go r.handleTransfer(m, req)
		case "ping":
			r.reply(m, map[string]string{"pong": r.node})
		default:
			r.replyErr(m, "unknown command type: "+cmd.Type)
		}
	})
}

// handleTransfer runs on the *source* Node: it looks up the destination's
// data-plane addresses in the Registry and streams the file, publishing
// progress and replying with the final state.
func (r *Runner) handleTransfer(m *nats.Msg, req proto.TransferRequest) {
	ctx := context.Background()
	progSubj := proto.SubjTransferProgress(r.fleet, req.TransferID)
	pub := func(p proto.TransferProgress) {
		if b, err := json.Marshal(p); err == nil {
			_ = r.nc.Publish(progSubj, b)
		}
	}
	fail := func(msg string) {
		p := proto.TransferProgress{TransferID: req.TransferID, State: "failed", Error: msg}
		pub(p)
		r.reply(m, p)
	}

	pub(proto.TransferProgress{TransferID: req.TransferID, State: "connecting"})
	if r.dp == nil {
		fail("data plane disabled on source node")
		return
	}
	dst, err := r.reg.Get(ctx, req.DstNode)
	if err != nil {
		fail("destination node not found: " + err.Error())
		return
	}
	if len(dst.Net.Multiaddrs) == 0 {
		fail("destination has no data-plane addresses")
		return
	}

	start := time.Now()
	onProg := func(done, total int64) {
		rate := int64(0)
		if d := time.Since(start).Seconds(); d > 0 {
			rate = int64(float64(done) / d)
		}
		pub(proto.TransferProgress{
			TransferID: req.TransferID, State: "transferring",
			BytesDone: done, BytesTotal: total, RateBps: rate, Path: "direct",
		})
	}
	sum, err := r.dp.Send(ctx, dst.Net.Multiaddrs, req.SrcPath, req.DstPath, onProg)
	if err != nil {
		fail(err.Error())
		return
	}
	done := proto.TransferProgress{TransferID: req.TransferID, State: "done", Path: "direct", SHA256: sum}
	pub(done)
	r.reply(m, done)
}

// Heartbeat refreshes the Node record until ctx is cancelled.
func (r *Runner) Heartbeat(ctx context.Context, interval time.Duration) {
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			r.rec.State.Load = node.LiveLoad()
			// Refresh data-plane addresses: a relay (circuit) address only
			// appears after AutoRelay reserves a slot, so the Registry must
			// pick it up on a later heartbeat for peers to reach this Node.
			if r.dp != nil {
				r.rec.Net.Multiaddrs = r.dp.Multiaddrs()
				r.rec.Net.Reachability = r.dp.Reachability()
			}
			_ = r.reg.Put(ctx, *r.rec)
		}
	}
}

func (r *Runner) reply(m *nats.Msg, v any) {
	if b, err := json.Marshal(v); err == nil {
		_ = m.Respond(b)
	}
}

func (r *Runner) replyErr(m *nats.Msg, msg string) {
	r.reply(m, map[string]string{"error": msg})
}
