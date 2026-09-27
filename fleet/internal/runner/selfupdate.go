package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/selfupdate"
	"github.com/nats-io/nats.go"
)

// EnableSelfUpdate lets this node accept "self_update" commands (a release tag
// from the owner's Agent) and apply them when idle. restart replaces the
// process with the updated binary.
func (r *Runner) EnableSelfUpdate(u *selfupdate.Updater, restart func(string) error) {
	u.Busy = r.busyReason
	r.updater, r.restart = u, restart
}

// busyReason says why restarting now would interrupt work ("" when it would not).
// Running App instances are not work in flight: they keep running across the
// node's restart and are re-adopted by its recovery.
func (r *Runner) busyReason() string {
	if n := r.active.Load(); n > 0 {
		return fmt.Sprintf("%d task(s) or transfer(s) in progress", n)
	}
	if len(r.serviceSlots) > 0 || len(r.rpcSlots) > 0 {
		return "App requests are in progress"
	}
	if r.lifecycle != nil {
		for _, op := range r.lifecycle.Snapshot().Operations {
			if op.State == "queued" || op.State == "running" {
				return "an App operation is in progress"
			}
		}
	}
	return ""
}

// ApplyUpdate installs tag when newer and idle, then restarts. The node's own
// periodic check and the self_update command both come here.
func (r *Runner) ApplyUpdate(ctx context.Context, tag string, reply func(selfupdate.Result, error)) {
	if r.updater == nil {
		reply(selfupdate.Result{}, fmt.Errorf("self-update is turned off on this node"))
		return
	}
	res, executable, err := r.updater.Apply(ctx, tag)
	reply(res, err)
	if err != nil || res.Status != "updated" {
		return
	}
	fmt.Printf("%s Updated Fleet %s → %s; restarting…\n", time.Now().Format("[15:04]"), res.From, res.To)
	time.Sleep(500 * time.Millisecond) // let the reply leave before the process image changes
	if err := r.restart(executable); err != nil {
		fmt.Printf("Could not restart into the update (%v); it takes effect at the next start.\n", err)
	}
}

func (r *Runner) handleSelfUpdate(m *nats.Msg) {
	var req struct {
		Tag string `json:"tag"`
	}
	if err := json.Unmarshal(m.Data, &req); err != nil || req.Tag == "" {
		r.replyErr(m, "self_update needs a release tag")
		return
	}
	go r.ApplyUpdate(context.Background(), req.Tag, func(res selfupdate.Result, err error) {
		if err != nil {
			r.replyErr(m, "self_update: "+err.Error())
			return
		}
		r.reply(m, res)
	})
}
