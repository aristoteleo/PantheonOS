package runner

import (
	"fmt"

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
		Type       string             `json:"type"`
		Protocol   int                `json:"protocol"`
		Method     string             `json:"method"`
		Request    *lifecycle.Request `json:"request,omitempty"`
		Digest     string             `json:"digest,omitempty"`
		Offset     int64              `json:"offset,omitempty"`
		Data       []byte             `json:"data,omitempty"`
		Instance   string             `json:"instance_id,omitempty"`
		Revision   string             `json:"revision,omitempty"`
		Generation uint64             `json:"generation,omitempty"`
		Component  string             `json:"component,omitempty"`
		Port       string             `json:"port,omitempty"`
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
