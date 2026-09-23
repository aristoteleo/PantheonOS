package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// Lifecycle uses its own versioned envelope so unsupported old Runners fail
// explicitly instead of interpreting an App package as a shell command.
func (r *Runner) handleLifecycle(m *nats.Msg) {
	if r.lifecycle == nil {
		r.replyErr(m, "App lifecycle protocol is not enabled on this node")
		return
	}
	var q struct {
		ModelIdle      *lifecycle.ModelIdleRegistration `json:"model_idle,omitempty"`
		ModelIdleID    string                           `json:"model_idle_id,omitempty"`
		PolicyRevision uint64                           `json:"policy_revision,omitempty"`
		Resources      *lifecycle.ResourceRequest       `json:"resources,omitempty"`
		Lease          string                           `json:"lease_id,omitempty"`
		Release        bool                             `json:"release,omitempty"`
		KeepAlive      bool                             `json:"keep_alive,omitempty"`
		AppID          string                           `json:"app_id,omitempty"`
		Payload        json.RawMessage                  `json:"payload,omitempty"`
		Timeout        int                              `json:"timeout_seconds,omitempty"`
		Type           string                           `json:"type"`
		Protocol       int                              `json:"protocol"`
		Method         string                           `json:"method"`
		Request        *lifecycle.Request               `json:"request,omitempty"`
		Digest         string                           `json:"digest,omitempty"`
		Offset         int64                            `json:"offset,omitempty"`
		Data           []byte                           `json:"data,omitempty"`
		Instance       string                           `json:"instance_id,omitempty"`
		Revision       string                           `json:"revision,omitempty"`
		Generation     uint64                           `json:"generation,omitempty"`
		Component      string                           `json:"component,omitempty"`
		Port           string                           `json:"port,omitempty"`
	}
	if err := lifecycle.StrictDecode(m.Data, &q); err != nil {
		r.replyErr(m, err.Error())
		return
	}
	if q.Protocol != lifecycle.Protocol {
		r.replyErr(m, fmt.Sprintf("unsupported App lifecycle protocol %d", q.Protocol))
		return
	}
	switch q.Method {
	case "model_idle_cancel":
		if q.ModelIdle == nil {
			r.replyErr(m, "missing model idle registration")
			return
		}
		policy, err := r.lifecycle.CancelModelIdleRegistration(*q.ModelIdle)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, policy)
	case "model_idle_register":
		if q.ModelIdle == nil {
			r.replyErr(m, "missing model idle registration")
			return
		}
		// Registration checks the exact connector over HTTP. Do not block the
		// NATS command callback (status, Stop and cancellation share it).
		select {
		case r.rpcSlots <- struct{}{}:
		default:
			r.replyErr(m, "node management is busy; retry registration")
			return
		}
		go func() {
			defer func() { <-r.rpcSlots }()
			ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
			defer cancel()
			policy, err := r.lifecycle.RegisterModelIdle(ctx, *q.ModelIdle)
			if err != nil {
				r.replyErr(m, err.Error())
				return
			}
			r.reply(m, policy)
		}()
	case "model_idle_status", "model_idle_wake", "model_idle_disable":
		var policy lifecycle.ModelIdle
		var err error
		switch q.Method {
		case "model_idle_status":
			policy, err = r.lifecycle.ModelIdleStatus(q.ModelIdleID)
		case "model_idle_wake":
			policy, err = r.lifecycle.WakeModelIdle(q.ModelIdleID, q.PolicyRevision)
		case "model_idle_disable":
			policy, err = r.lifecycle.DisableModelIdle(q.ModelIdleID, q.PolicyRevision)
		}
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, policy)
	case "resource_status":
		r.reply(m, r.lifecycle.ResourceStatus())
	case "resource_reserve":
		if q.Resources == nil {
			r.replyErr(m, "missing resource request")
			return
		}
		lease, err := r.lifecycle.ReserveResources(q.Instance, q.Revision, q.Generation, q.Lease, *q.Resources)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]any{"reservation": lease})
	case "resource_release":
		if err := r.lifecycle.ReleaseResources(q.Instance, q.Revision, q.Generation, q.Lease); err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]bool{"ok": true})
	case "lease", "keep_alive":
		var err error
		if q.Method == "lease" {
			err = r.lifecycle.WindowLease(q.Instance, q.Revision, q.Generation, q.Lease, q.Release)
		} else {
			err = r.lifecycle.SetKeepAlive(q.Instance, q.Revision, q.Generation, q.KeepAlive)
		}
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]bool{"ok": true})
	case "invoke":
		r.handleAppRPC(m, q.AppID, q.Instance, q.Revision, q.Generation, q.Payload, q.Timeout)
	case "stage":
		offset, err := r.lifecycle.Stage(q.Digest, q.Offset, q.Data)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]any{"offset": offset})
	case "submit":
		if q.Request == nil {
			r.replyErr(m, "missing lifecycle request")
			return
		}
		op, err := r.lifecycle.Submit(*q.Request)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]any{"operation": op})
	case "status":
		r.reply(m, r.lifecycle.Snapshot())
	case "service":
		_, err := r.lifecycle.Service(q.Instance, q.Revision, q.Generation, q.Component, q.Port)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]bool{"ready": true})
	default:
		r.replyErr(m, "unknown lifecycle method")
	}
}
func (r *Runner) instances() []proto.AppInstance {
	out := r.apps.List()
	if r.lifecycle != nil {
		for _, in := range r.lifecycle.Snapshot().Instances {
			out = append(out, proto.AppInstance{AppID: in.AppID, Version: in.Version, Scope: in.Scope, Health: in.State, InstanceID: in.ID, Revision: in.Digest, Generation: in.Generation, Error: in.Error})
		}
	}
	return out
}
