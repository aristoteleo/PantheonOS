package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/nats-io/nats.go"
)

const maxAppRPC = 512 * 1024

// Only the fixed App RPC endpoint is exposed. Clients cannot proxy arbitrary
// URLs, host paths, headers or lifecycle endpoints through this operation.
func invokeAppRPC(ctx context.Context, endpoint string, payload json.RawMessage, timeout int) (json.RawMessage, error) {
	if len(payload) == 0 || len(payload) > maxAppRPC || !json.Valid(payload) {
		return nil, fmt.Errorf("invalid or oversized App RPC payload")
	}
	if timeout < 1 || timeout > 600 {
		return nil, fmt.Errorf("App RPC timeout must be 1..600 seconds")
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(timeout)*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "POST", endpoint+"/rpc", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("App call failed; outcome may be unknown: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxAppRPC+1))
	if err != nil {
		return nil, err
	}
	if len(body) > maxAppRPC {
		return nil, fmt.Errorf("App RPC result exceeds 512 KiB; return served file URLs for large data")
	}
	if !json.Valid(body) {
		return nil, fmt.Errorf("App RPC returned an invalid response (HTTP %d)", response.StatusCode)
	}
	return body, nil
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
		result, err := invokeAppRPC(context.Background(), endpoint, payload, timeout)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		r.reply(m, map[string]any{"response": result})
	}()
}
