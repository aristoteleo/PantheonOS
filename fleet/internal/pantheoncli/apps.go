package pantheoncli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Deployment is the owner's Agent deployment as the controller reports it.
const Deployment = "general-team"

// AppStatus is one App of the deployment.
type AppStatus struct {
	State      string `json:"state"`
	NodeID     string `json:"node_id"`
	InstanceID string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation int    `json:"generation"`
	Reason     string `json:"reason,omitempty"`
}

// DeploymentStatus is the part of the deployment a client uses.
type DeploymentStatus struct {
	Revision int `json:"revision"`
	Status   struct {
		Apps       map[string]AppStatus `json:"apps"`
		Conditions []struct {
			Type   string `json:"type"`
			Status bool   `json:"status"`
			Reason string `json:"reason"`
		} `json:"conditions"`
	} `json:"status"`
}

// Deployment reads the deployment's status from the controller.
func (s *Session) Deployment(ctx context.Context) (*DeploymentStatus, error) {
	controller, err := s.controller(ctx)
	if err != nil {
		return nil, err
	}
	var dep DeploymentStatus
	if err := s.request(ctx, http.MethodGet, controller+"/deployments/"+Deployment, s.Creds.Key, nil, &dep, 20*time.Second); err != nil {
		var httpErr *HTTPError
		if errors.As(err, &httpErr) && httpErr.Status == 404 {
			return nil, errors.New("this account has no Agent deployment yet; set it up in the PantheonOS web interface")
		}
		return nil, fmt.Errorf("reading the deployment: %w", err)
	}
	return &dep, nil
}

// Binding names one App instance exactly.
type Binding struct {
	NodeID     string `json:"node_id"`
	InstanceID string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation int    `json:"generation"`
	Component  string `json:"component"`
	Port       string `json:"port"`
}

func (b Binding) key() string {
	return fmt.Sprintf("%s/%s/%s/%d/%s/%s", b.NodeID, b.InstanceID, b.Revision, b.Generation, b.Component, b.Port)
}

// AppBinding is a deployment App's backend, if it is ready.
func (d *DeploymentStatus) AppBinding(name string) (Binding, error) {
	app, ok := d.Status.Apps[name]
	if !ok {
		return Binding{}, fmt.Errorf("the deployment has no App %q", name)
	}
	if app.State != "ready" || app.InstanceID == "" {
		reason := app.State
		if app.Reason != "" {
			reason += ": " + app.Reason
		}
		return Binding{}, fmt.Errorf("App %q is not ready (%s); check it in Fleet", name, reason)
	}
	return Binding{NodeID: app.NodeID, InstanceID: app.InstanceID, Revision: app.Revision,
		Generation: app.Generation, Component: "backend", Port: "http"}, nil
}

// AppNames lists the deployment's Apps.
func (d *DeploymentStatus) AppNames() []string {
	names := make([]string, 0, len(d.Status.Apps))
	for name := range d.Status.Apps {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

type grant struct {
	Origin      string `json:"origin"`
	AccessToken string `json:"access_token"`
	Expires     int64  `json:"expires"`
}

// Grants are cached per exact instance until shortly before they expire, so a
// command does not ask the Hub for a new one each time.
func grantCachePath() (string, error) {
	dir, err := ConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "grants.json"), nil
}

func (s *Session) cachedGrant(b Binding) (grant, bool) {
	path, err := grantCachePath()
	if err != nil {
		return grant{}, false
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return grant{}, false
	}
	var cache map[string]grant
	if json.Unmarshal(data, &cache) != nil {
		return grant{}, false
	}
	g, ok := cache[s.Creds.Hub+"|"+b.key()]
	return g, ok && g.Expires > time.Now().Add(2*time.Minute).Unix()
}

func (s *Session) storeGrant(b Binding, g grant) {
	path, err := grantCachePath()
	if err != nil {
		return
	}
	cache := map[string]grant{}
	if data, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(data, &cache)
	}
	now := time.Now().Unix()
	for k, v := range cache {
		if v.Expires <= now {
			delete(cache, k)
		}
	}
	cache[s.Creds.Hub+"|"+b.key()] = g
	data, err := json.Marshal(cache)
	if err != nil || os.MkdirAll(filepath.Dir(path), 0o700) != nil {
		return
	}
	tmp := path + ".tmp"
	if os.WriteFile(tmp, data, 0o600) == nil {
		_ = os.Rename(tmp, path)
	}
}

// Connect returns a grant to call one App instance.
func (s *Session) Connect(ctx context.Context, b Binding) (grant, error) {
	if g, ok := s.cachedGrant(b); ok {
		return g, nil
	}
	var g grant
	err := s.request(ctx, http.MethodPost, s.Creds.Hub+"/api/fleet/apps/workload-connect", s.Creds.Key, b, &g, 30*time.Second)
	if err != nil {
		var httpErr *HTTPError
		if errors.As(err, &httpErr) && httpErr.Status == 409 {
			return grant{}, errors.New("the App is not ready at its node; check it in Fleet")
		}
		return grant{}, fmt.Errorf("connecting to the App: %w", err)
	}
	u, err := url.Parse(g.Origin)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.Path != "" || g.AccessToken == "" {
		return grant{}, errors.New("the Hub returned an invalid App grant")
	}
	s.storeGrant(b, g)
	return g, nil
}

// RPCError is an App method that answered with success false.
type RPCError struct{ Message string }

func (e *RPCError) Error() string { return e.Message }

// Call invokes one method of an App instance and returns its result.
func (s *Session) Call(ctx context.Context, b Binding, method string, args any, timeout time.Duration) (json.RawMessage, error) {
	if args == nil {
		args = map[string]any{}
	}
	g, err := s.Connect(ctx, b)
	if err != nil {
		return nil, err
	}
	body := map[string]any{"method": method, "args": args, "timeout_s": int(timeout.Seconds())}
	var reply struct {
		Success bool            `json:"success"`
		Result  json.RawMessage `json:"result"`
		Error   any             `json:"error"`
	}
	if err := s.request(ctx, http.MethodPost, g.Origin+"/rpc", g.AccessToken, body, &reply, timeout+30*time.Second); err != nil {
		return nil, fmt.Errorf("calling %s: %w", method, err)
	}
	if !reply.Success {
		message := "the App reported a failure"
		if reply.Error != nil {
			message = fmt.Sprint(reply.Error)
		}
		return nil, &RPCError{Message: message}
	}
	return reply.Result, nil
}

// DeploymentAppCall calls a method of a deployment App by its name.
func (s *Session) DeploymentAppCall(ctx context.Context, app, method string, args any, timeout time.Duration) (json.RawMessage, error) {
	dep, err := s.Deployment(ctx)
	if err != nil {
		return nil, err
	}
	b, err := dep.AppBinding(app)
	if err != nil {
		return nil, err
	}
	return s.Call(ctx, b, method, args, timeout)
}

// Succeeded reports whether a result object says success: false, and returns
// its error then. Many App methods report failures in their result.
func Succeeded(result json.RawMessage) error {
	var r struct {
		Success *bool `json:"success"`
		Error   any   `json:"error"`
	}
	if json.Unmarshal(result, &r) == nil && r.Success != nil && !*r.Success {
		if r.Error != nil {
			return &RPCError{Message: strings.TrimSpace(fmt.Sprint(r.Error))}
		}
		return &RPCError{Message: "the App reported a failure"}
	}
	return nil
}
