package main

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

func makeAppGateway(domain, token string, origins []string, authority *auth.Authority, natsURL string) (*appgateway.Gateway, error) {
	if authority == nil {
		return nil, fmt.Errorf("App gateway requires authenticated Fleet")
	}
	type cached struct {
		nc      *nats.Conn
		expires time.Time
	}
	cache := map[string]cached{}
	var mu sync.Mutex
	connect := func(fid string) (*nats.Conn, error) {
		mu.Lock()
		defer mu.Unlock()
		for key, entry := range cache {
			if entry.expires.Before(time.Now()) || entry.nc.IsClosed() {
				entry.nc.Close()
				delete(cache, key)
			}
		}
		if entry, ok := cache[fid]; ok {
			return entry.nc, nil
		}
		if len(cache) >= 256 {
			return nil, fmt.Errorf("gateway capacity reached")
		}
		creds, err := authority.MintFleetUser(fid)
		if err != nil {
			return nil, err
		}
		nc, err := nats.Connect(natsURL, nats.UserCredentialBytes(creds), nats.Name("fleet-app-gateway"), nats.CustomInboxPrefix("_INBOX_"+fid), nats.Timeout(5*time.Second))
		if err != nil {
			return nil, err
		}
		cache[fid] = cached{nc, time.Now().Add(auth.AccessTTL / 2)}
		return nc, nil
	}
	request := func(ctx context.Context, b appgateway.Binding, payload map[string]any) error {
		nc, err := connect(b.Fleet)
		if err != nil {
			return err
		}
		data, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		response, err := nc.RequestWithContext(ctx, proto.SubjNodeCmd(b.Fleet, b.Node), data)
		if err != nil {
			return err
		}
		var out struct {
			Error string `json:"error"`
			OK    bool   `json:"ok"`
			Ready bool   `json:"ready"`
		}
		if json.Unmarshal(response.Data, &out) != nil || out.Error != "" || (!out.OK && !out.Ready) {
			return fmt.Errorf("node rejected App service")
		}
		return nil
	}
	payload := func(b appgateway.Binding) map[string]any {
		return map[string]any{"instance_id": b.Instance, "revision": b.Revision, "generation": b.Generation, "component": b.Component, "port": b.Port}
	}
	gateway, err := appgateway.New(domain, token, origins,
		func(ctx context.Context, b appgateway.Binding, id, secret string) error {
			q := payload(b)
			q["type"] = "app_service"
			q["stream"] = id
			q["secret"] = secret
			return request(ctx, b, q)
		},
		func(ctx context.Context, b appgateway.Binding) error {
			q := payload(b)
			q["type"] = "app_lifecycle"
			q["protocol"] = 1
			q["method"] = "service"
			return request(ctx, b, q)
		},
	)
	if err != nil {
		return nil, err
	}
	gateway.SetModelIdleDispatch(func(ctx context.Context, q appgateway.ModelIdleRequest) (appgateway.ModelIdleSnapshot, error) {
		nc, err := connect(q.Fleet)
		if err != nil {
			return appgateway.ModelIdleSnapshot{}, err
		}
		query := func(method string) (appgateway.ModelIdleSnapshot, error) {
			payload, _ := json.Marshal(map[string]any{"type": "app_lifecycle", "protocol": 1,
				"method": method, "model_idle_id": q.ID, "policy_revision": q.Revision})
			response, err := nc.RequestWithContext(ctx, proto.SubjNodeCmd(q.Fleet, q.Node), payload)
			if err != nil {
				return appgateway.ModelIdleSnapshot{}, err
			}
			var out appgateway.ModelIdleSnapshot
			if len(response.Data) > 32768 || json.Unmarshal(response.Data, &out) != nil || !out.Matches(q) {
				return out, fmt.Errorf("node model idle binding changed")
			}
			return out, nil
		}
		// Check connector identity before changing demand. A concurrent policy
		// replacement increments revision, so the subsequent wake CAS fails.
		out, err := query("model_idle_status")
		if err != nil || q.Action == "status" {
			return out, err
		}
		return query("model_idle_wake")
	})
	gateway.SetDirectDispatch(func(ctx context.Context, q appdirect.Request) (appdirect.Grant, error) {
		nc, err := connect(q.Fleet)
		if err != nil {
			return appdirect.Grant{}, err
		}
		payload, err := json.Marshal(struct {
			Type string `json:"type"`
			appdirect.Request
		}{"app_direct_grant", q})
		if err != nil {
			return appdirect.Grant{}, err
		}
		response, err := nc.RequestWithContext(ctx, proto.SubjNodeCmd(q.Fleet, q.Node), payload)
		if err != nil {
			return appdirect.Grant{}, err
		}
		var out struct {
			OK    bool            `json:"ok"`
			Grant appdirect.Grant `json:"grant"`
		}
		if json.Unmarshal(response.Data, &out) != nil || !out.OK || out.Grant.Transport != "fleet_direct" || out.Grant.Expires != q.Expires {
			return appdirect.Grant{}, fmt.Errorf("node rejected direct App grant")
		}
		return out.Grant, nil
	})
	return gateway, nil
}
