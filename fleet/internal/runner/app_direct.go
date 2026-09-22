package runner

import (
	"context"
	"fmt"

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/nats-io/nats.go"
)

func (r *Runner) enableDirectServices(ctx context.Context) {
	if r.dp == nil || r.lifecycle == nil || r.nc == nil {
		return
	}
	r.direct = appdirect.New(ctx, r.dp, func(b apptransport.Binding) (string, func(), error) {
		if b.Fleet != r.fleet || b.Node != r.node {
			return "", nil, fmt.Errorf("wrong App owner or node")
		}
		release, err := r.lifecycle.BeginUse(b.Instance, b.Revision, b.Generation)
		if err != nil {
			return "", nil, err
		}
		endpoint, err := r.lifecycle.Service(b.Instance, b.Revision, b.Generation, b.Component, b.Port)
		if err != nil {
			release()
			return "", nil, err
		}
		return endpoint, release, nil
	}, r.nc.IsConnected)
	r.rec.Capability.Runtimes["app-direct-http"] = "1"
}

func (r *Runner) handleDirectGrant(m *nats.Msg) {
	var q struct {
		Type string `json:"type"`
		appdirect.Request
	}
	if r.direct == nil || lifecycle.StrictDecode(m.Data, &q) != nil {
		r.replyErr(m, "Direct App service protocol unavailable or invalid request")
		return
	}
	grant, err := r.direct.Issue(q.Request)
	if err != nil {
		r.replyErr(m, err.Error())
		return
	}
	r.reply(m, map[string]any{"ok": true, "grant": grant})
}
