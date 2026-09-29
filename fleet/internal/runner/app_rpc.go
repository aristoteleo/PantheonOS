package runner

import (
	"context"
	"encoding/json"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/nats-io/nats.go"
)

const maxAppRPC = 512 * 1024

// Only the fixed App RPC endpoint is exposed. Clients cannot proxy arbitrary
// URLs, host paths, headers or lifecycle endpoints through this operation.
func invokeAppRPC(ctx context.Context, endpoint string, payload json.RawMessage, timeout int, credential ...string) (json.RawMessage, error) {
	token := ""
	if len(credential) > 0 {
		token = credential[0]
	}
	return lifecycle.InvokeHTTP(ctx, endpoint, payload, timeout, token)
}

func (r *Runner) handleAppRPC(m *nats.Msg, appID, instance, revision string, generation uint64, payload json.RawMessage, timeout int) {
	if r.lifecycle == nil {
		r.replyErr(m, "App lifecycle unavailable")
		return
	}
	in := r.lifecycle.Snapshot().Instances[instance]
	if in == nil || in.AppID != appID {
		r.replyErr(m, "App binding does not belong to this App")
		return
	}
	endpoint, err := r.lifecycle.Service(instance, revision, generation, "backend", "http")
	if err != nil {
		r.replyErr(m, err.Error())
		return
	}
	credential, err := r.lifecycle.RPCCredential(instance, revision, generation)
	if err != nil {
		r.replyErr(m, err.Error())
		return
	}
	select {
	case r.rpcSlots <- struct{}{}:
	default:
		r.replyErr(m, "App RPC concurrency limit reached")
		return
	}
	release, err := r.lifecycle.BeginUse(instance, revision, generation)
	if err != nil {
		<-r.rpcSlots
		r.replyErr(m, err.Error())
		return
	}
	go func() {
		defer release()
		defer func() { <-r.rpcSlots }()
		result, err := invokeAppRPC(context.Background(), endpoint, payload, timeout, credential)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]any{"response": result})
	}()
}
