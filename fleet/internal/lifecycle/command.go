package lifecycle

// The App protocol is independent of node transport. Native NATS runners and
// job-scoped workers dispatch these same operations to the same Manager.
import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Command struct {
	Preparation    string                 `json:"preparation_id,omitempty"`
	Configuration  *AppConfiguration      `json:"configuration,omitempty"`
	Resources      *ResourceRequest       `json:"resources,omitempty"`
	ModelIdle      *ModelIdleRegistration `json:"model_idle,omitempty"`
	ModelIdleID    string                 `json:"model_idle_id,omitempty"`
	PolicyRevision uint64                 `json:"policy_revision,omitempty"`
	Type           string                 `json:"type"`
	Protocol       int                    `json:"protocol"`
	Method         string                 `json:"method"`
	Request        *Request               `json:"request,omitempty"`
	Digest         string                 `json:"digest,omitempty"`
	Offset         int64                  `json:"offset,omitempty"`
	Data           []byte                 `json:"data,omitempty"`
	Instance       string                 `json:"instance_id,omitempty"`
	Revision       string                 `json:"revision,omitempty"`
	Generation     uint64                 `json:"generation,omitempty"`
	Component      string                 `json:"component,omitempty"`
	Port           string                 `json:"port,omitempty"`
	AppID          string                 `json:"app_id,omitempty"`
	Payload        json.RawMessage        `json:"payload,omitempty"`
	Timeout        int                    `json:"timeout_seconds,omitempty"`
	Lease          string                 `json:"lease_id,omitempty"`
	Release        bool                   `json:"release,omitempty"`
	KeepAlive      bool                   `json:"keep_alive,omitempty"`
}

func (m *Manager) Dispatch(ctx context.Context, q Command) (any, error) {
	if q.Protocol != Protocol || (q.Type != "" && q.Type != "app_lifecycle") {
		return nil, fmt.Errorf("unsupported App lifecycle protocol")
	}
	switch q.Method {
	case "check_instance":
		err := m.CheckInstance(ctx, q.Instance, q.Revision, q.Generation, q.Preparation)
		return map[string]bool{"ok": err == nil}, err
	case "configure":
		if q.Configuration == nil {
			return nil, fmt.Errorf("missing App configuration")
		}
		err := m.ConfigureApp(q.Instance, q.Revision, q.Generation, *q.Configuration)
		return map[string]bool{"ok": err == nil}, err
	case "resource_status":
		return m.ResourceStatus(), nil
	case "resource_reserve":
		if q.Resources == nil {
			return nil, fmt.Errorf("missing resource request")
		}
		lease, err := m.ReserveResources(q.Instance, q.Revision, q.Generation, q.Lease, *q.Resources)
		return map[string]any{"reservation": lease}, err
	case "resource_release":
		err := m.ReleaseResources(q.Instance, q.Revision, q.Generation, q.Lease)
		return map[string]bool{"ok": err == nil}, err
	case "model_idle_register", "model_idle_cancel":
		if q.ModelIdle == nil {
			return nil, fmt.Errorf("missing model idle registration")
		}
		if q.Method == "model_idle_cancel" {
			return m.CancelModelIdleRegistration(*q.ModelIdle)
		}
		return m.RegisterModelIdle(ctx, *q.ModelIdle)
	case "model_idle_status":
		return m.ModelIdleStatus(q.ModelIdleID)
	case "model_idle_wake":
		return m.WakeModelIdle(q.ModelIdleID, q.PolicyRevision)
	case "model_idle_disable":
		return m.DisableModelIdle(q.ModelIdleID, q.PolicyRevision)
	case "stage":
		offset, err := m.Stage(q.Digest, q.Offset, q.Data)
		return map[string]any{"offset": offset}, err
	case "submit", "fence_start":
		if q.Request == nil {
			return nil, fmt.Errorf("missing lifecycle request")
		}
		var op Operation
		var err error
		if q.Method == "submit" {
			op, err = m.Submit(*q.Request)
		} else {
			op, err = m.FenceStart(*q.Request)
		}
		return map[string]any{"operation": op}, err
	case "status":
		return m.Snapshot(), nil
	case "service":
		_, err := m.Service(q.Instance, q.Revision, q.Generation, q.Component, q.Port)
		return map[string]bool{"ready": err == nil}, err
	case "lease":
		err := m.WindowLease(q.Instance, q.Revision, q.Generation, q.Lease, q.Release)
		return map[string]bool{"ok": err == nil}, err
	case "keep_alive":
		err := m.SetKeepAlive(q.Instance, q.Revision, q.Generation, q.KeepAlive)
		return map[string]bool{"ok": err == nil}, err
	case "invoke":
		return m.Invoke(ctx, q.AppID, q.Instance, q.Revision, q.Generation, q.Payload, q.Timeout)
	default:
		return nil, fmt.Errorf("unsupported App lifecycle method %q", q.Method)
	}
}

func (m *Manager) Invoke(ctx context.Context, appID, instance, revision string, generation uint64, payload json.RawMessage, timeout int) (any, error) {
	in := m.Snapshot().Instances[instance]
	if in == nil || in.AppID != appID {
		return nil, fmt.Errorf("App binding does not belong to this App")
	}
	endpoint, err := m.Service(instance, revision, generation, "backend", "http")
	if err != nil {
		return nil, err
	}
	credential, err := m.RPCCredential(instance, revision, generation)
	if err != nil {
		return nil, err
	}
	release, err := m.BeginUse(instance, revision, generation)
	if err != nil {
		return nil, err
	}
	defer release()
	result, err := InvokeHTTP(ctx, endpoint, payload, timeout, credential)
	return map[string]any{"response": result}, err
}

const MaxRPC = 512 * 1024

// InvokeHTTP cannot select headers or arbitrary paths supplied by a caller.
func InvokeHTTP(ctx context.Context, endpoint string, payload json.RawMessage, timeout int, credential string) (json.RawMessage, error) {
	if len(payload) == 0 || len(payload) > MaxRPC || !json.Valid(payload) {
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
	if credential != "" {
		req.Header.Set("X-Fleet-RPC-Token", credential)
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("App call failed; outcome may be unknown: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, MaxRPC+1))
	if err != nil {
		return nil, err
	}
	if len(body) > MaxRPC {
		return nil, fmt.Errorf("App RPC result exceeds 512 KiB; return served file URLs for large data")
	}
	if !json.Valid(body) {
		return nil, fmt.Errorf("App RPC returned an invalid response (HTTP %d)", response.StatusCode)
	}
	return body, nil
}
