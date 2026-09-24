package runner

import (
	"context"
	"fmt"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appmedia"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/nats-io/nats.go"
)

func (r *Runner) enableMediaServices(ctx context.Context) {
	if r.lifecycle == nil || r.nc == nil {
		return
	}
	r.media = appmedia.New(ctx, func(b apptransport.Binding) (string, func(), error) {
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
	r.rec.Capability.Runtimes["model-browser-direct"] = "1"
}

func (r *Runner) handleMediaOffer(m *nats.Msg) {
	var q struct {
		Type string `json:"type"`
		appmedia.Request
	}
	if r.media == nil || len(m.Data) > 85000 || lifecycle.StrictDecode(m.Data, &q) != nil {
		r.replyErr(m, "Direct model media protocol unavailable or invalid request")
		return
	}
	// ICE gathering must not stall the node's sequential NATS subscription.
	go func() {
		ctx, cancel := context.WithTimeout(r.serviceContext, 8*time.Second)
		defer cancel()
		answer, err := r.media.Offer(ctx, q.Request)
		if err != nil {
			r.replyErr(m, "Direct model media unavailable")
			return
		}
		r.reply(m, map[string]any{"ok": true, "answer": answer})
	}()
}
