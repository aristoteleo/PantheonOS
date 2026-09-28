package runner

import (
	"context"
	"encoding/json"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/nats-io/nats.go"
)

// EnableHPC makes this Node a launcher for compute nodes on its Slurm cluster.
func (r *Runner) EnableHPC(l *hpc.Launcher) { r.hpc = l }

// handleHPC serves {"type":"hpc","method":"partitions"|"submit"|"jobs"|"cancel"}.
// Only the Fleet owner's credentials can publish to this subject; the join
// token in a submit is single-use and minted by the owner's Agent.
func (r *Runner) handleHPC(m *nats.Msg) {
	if r.hpc == nil {
		r.replyErr(m, "this node cannot start HPC compute nodes (no Slurm, or it runs inside a job)")
		return
	}
	var req struct {
		Method  string      `json:"method"`
		Request hpc.Request `json:"request"`
		JobID   string      `json:"job_id"`
	}
	if err := json.Unmarshal(m.Data, &req); err != nil {
		r.replyErr(m, "hpc: "+err.Error())
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		var (
			out any
			err error
		)
		switch req.Method {
		case "partitions":
			var parts []hpc.Partition
			parts, err = r.hpc.Partitions(ctx)
			out = map[string]any{"partitions": parts}
		case "submit":
			var job hpc.Job
			job, err = r.hpc.Submit(ctx, req.Request)
			out = map[string]any{"job": job}
		case "jobs":
			var jobs []hpc.Job
			jobs, err = r.hpc.Jobs(ctx)
			out = map[string]any{"jobs": jobs}
		case "cancel":
			err = r.hpc.Cancel(ctx, req.JobID)
			out = map[string]any{"ok": err == nil}
		default:
			r.replyErr(m, "hpc: unknown method "+req.Method)
			return
		}
		if err != nil {
			r.replyErr(m, "hpc "+req.Method+": "+err.Error())
			return
		}
		r.reply(m, out)
	}()
}
