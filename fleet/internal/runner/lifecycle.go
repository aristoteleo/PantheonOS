package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
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
		Preparation    string                           `json:"preparation_id,omitempty"`
		Configuration  *lifecycle.AppConfiguration      `json:"configuration,omitempty"`
		GroupOverlay   *lifecycle.OverlayRequest        `json:"group_overlay,omitempty"`
		GroupTopology  json.RawMessage                  `json:"group_topology,omitempty"`
		GroupID        string                           `json:"group_id,omitempty"`
		TopologyHash   string                           `json:"topology_sha256,omitempty"`
		GroupClaim     *groupcredentials.Claim          `json:"group_claim,omitempty"`
		Certificate    string                           `json:"certificate_pem,omitempty"`
		Authority      string                           `json:"ca_pem,omitempty"`
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
	case "group_overlay_prepare", "group_overlay_pin", "group_overlay_status", "group_overlay_close":
		if q.GroupOverlay == nil {
			r.replyErr(m, "missing group overlay request")
			return
		}
		result, err := r.lifecycle.GroupOverlay(q.Method[len("group_overlay_"):], *q.GroupOverlay)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, result)
	case "group_authority_prepare", "group_authority_status", "group_authority_issue", "group_authority_close":
		result, err := r.lifecycle.GroupAuthority(q.Method[len("group_authority_"):], q.GroupID, q.TopologyHash, q.GroupTopology, q.GroupClaim)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, result)
	case "group_peer_enroll", "group_peer_install":
		enrollment, err := r.lifecycle.GroupPeer(q.Instance, q.Revision, q.Generation, q.Certificate, q.Authority, q.Method == "group_peer_install")
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, enrollment)
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
	case "check_instance", "configure", "lease", "keep_alive", "invoke", "stage", "submit", "fence_start", "status", "service":
		command := lifecycle.Command{Preparation: q.Preparation, Configuration: q.Configuration, Type: q.Type, Protocol: q.Protocol, Method: q.Method, Request: q.Request,
			Digest: q.Digest, Offset: q.Offset, Data: q.Data, Instance: q.Instance, Revision: q.Revision,
			Generation: q.Generation, Component: q.Component, Port: q.Port, AppID: q.AppID, Payload: q.Payload,
			Timeout: q.Timeout, Lease: q.Lease, Release: q.Release, KeepAlive: q.KeepAlive}
		dispatch := func() {
			result, err := r.lifecycle.Dispatch(context.Background(), command)
			if err != nil {
				r.replyErr(m, err.Error())
				return
			}
			r.reply(m, result)
		}
		if q.Method == "invoke" || q.Method == "check_instance" {
			select {
			case r.rpcSlots <- struct{}{}:
			default:
				r.replyErr(m, "App RPC concurrency limit reached")
				return
			}
			go func() { defer func() { <-r.rpcSlots }(); dispatch() }()
		} else {
			dispatch()
		}
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
