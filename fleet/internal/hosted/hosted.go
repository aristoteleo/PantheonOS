// Package hosted serves Apps of an owner's deployment inside the Fleet
// controller instead of as processes on a node (docs/fleet-orchestration.md
// §15). A hosted App is part of its deployment like any other: it has a fixed
// identity on node "controller", other Apps bind to it, and it answers the
// ordinary App RPC ({method, args, timeout_s} -> {success, result}).
package hosted

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
)

// Node is the node id every hosted App reports; no Runner has it.
const Node = "controller"

//go:embed manifests/fleet.json
var fleetManifest []byte

//go:embed manifests/dependency-binding.json
var allocatorManifest []byte

//go:embed manifests/model-services-control.json
var modelAccessManifest []byte

// catalog holds each hosted service's App manifest, the contract consumers
// bind against. They are the manifests the Python packages were built with.
var catalog = map[string][]byte{"fleet": fleetManifest, "dependency-binding": allocatorManifest,
	"model-services-control": modelAccessManifest}

// Manifest is a hosted service's App manifest.
func Manifest(service string) ([]byte, bool) {
	raw, ok := catalog[service]
	return raw, ok
}

// Revision identifies a hosted service's contract: consumers pinned to it
// restart only when its manifest changes.
func Revision(service string) (string, bool) {
	raw, ok := catalog[service]
	if !ok {
		return "", false
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), true
}

// ByRevision returns the manifest a hosted revision names.
func ByRevision(revision string) ([]byte, bool) {
	for service, raw := range catalog {
		if rev, _ := Revision(service); rev == revision {
			return raw, true
		}
	}
	return nil, false
}

// InstanceID is a hosted App's stable instance id, distinct per owner so its
// gateway origin is too.
func InstanceID(fleet, deployment, app string) string {
	sum := sha256.Sum256([]byte(fleet + "/" + deployment + "/" + app))
	return "h" + hex.EncodeToString(sum[:])[:24]
}

// Binding is the identity a running hosted App reports.
func Binding(fleet, deployment, app, service string) (apptransport.Binding, bool) {
	revision, ok := Revision(service)
	if !ok {
		return apptransport.Binding{}, false
	}
	return apptransport.Binding{Fleet: fleet, Node: Node, Instance: InstanceID(fleet, deployment, app),
		Revision: revision, Generation: 1, Component: "backend", Port: "http"}, true
}

// Call is one App RPC to a hosted App.
type Call struct {
	Fleet, Deployment, App string
	Spec                   deployments.AppSpec
	Method                 string
	Args                   json.RawMessage
	// Owner is set for the owner's own browser (an App connection), unset for
	// an App calling through a dependency grant (e.g. the Agent's tools).
	Owner bool
}

// ArgumentError is a malformed call: unknown method or arguments.
type ArgumentError struct{ Reason string }

func (e *ArgumentError) Error() string { return e.Reason }

// Decode reads the call's arguments into v, refusing unknown ones (as the
// Python App host did).
func (c *Call) Decode(v any) error {
	args := c.Args
	if len(args) == 0 || string(args) == "null" {
		args = []byte("{}")
	}
	decoder := json.NewDecoder(bytes.NewReader(args))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(v); err != nil {
		return &ArgumentError{fmt.Sprintf("%s: invalid arguments: %v", c.Method, err)}
	}
	return nil
}

// Service is one hosted App implementation.
type Service interface {
	Call(ctx context.Context, c *Call) (any, error)
}

// Host routes App RPCs of hosted bindings to their services.
type Host struct {
	Store    *deployments.Store
	services map[string]Service
}

func NewHost(store *deployments.Store) *Host {
	return &Host{Store: store, services: map[string]Service{}}
}

// Register installs a service for an id in the catalog.
func (h *Host) Register(service string, s Service) {
	if _, ok := catalog[service]; !ok {
		panic("hosted: " + service + " is not in the catalog")
	}
	h.services[service] = s
}

// Resolve finds the deployment App a hosted binding names, if it is one the
// owner's deployment currently runs.
func (h *Host) Resolve(b apptransport.Binding) (deployments.Deployment, string, error) {
	if b.Node != Node || h.Store == nil {
		return deployments.Deployment{}, "", fmt.Errorf("not a hosted App")
	}
	for _, d := range h.Store.List(b.Fleet) {
		for name, app := range d.Spec.Apps {
			if app.Hosted == "" || InstanceID(b.Fleet, d.Name, name) != b.Instance {
				continue
			}
			want, ok := Binding(b.Fleet, d.Name, name, app.Hosted)
			if !ok || app.Intent != deployments.Running || want != b || h.services[app.Hosted] == nil {
				return d, name, fmt.Errorf("hosted App %s is not running at this binding", name)
			}
			return d, name, nil
		}
	}
	return deployments.Deployment{}, "", fmt.Errorf("unknown hosted App")
}

// Verify is the gateway's readiness check for a hosted binding.
func (h *Host) Verify(b apptransport.Binding) error {
	_, _, err := h.Resolve(b)
	return err
}

// Invoke serves one App RPC body for a hosted binding, returning the status
// and body the Python App host would have.
func (h *Host) Invoke(ctx context.Context, b apptransport.Binding, payload json.RawMessage) (int, json.RawMessage) {
	return h.invoke(ctx, b, payload, false)
}

func (h *Host) invoke(ctx context.Context, b apptransport.Binding, payload json.RawMessage, owner bool) (int, json.RawMessage) {
	var q struct {
		Method  string          `json:"method"`
		Args    json.RawMessage `json:"args"`
		Timeout float64         `json:"timeout_s"`
	}
	if json.Unmarshal(payload, &q) != nil || q.Method == "" {
		return fail(http.StatusBadRequest, "invalid App RPC")
	}
	d, name, err := h.Resolve(b)
	if err != nil {
		return fail(http.StatusNotFound, err.Error())
	}
	app := d.Spec.Apps[name]
	timeout := time.Duration(q.Timeout * float64(time.Second))
	if timeout <= 0 || timeout > 10*time.Minute {
		timeout = 10 * time.Minute
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	call := &Call{Fleet: b.Fleet, Deployment: d.Name, App: name, Spec: app, Method: q.Method, Args: q.Args, Owner: owner}
	result, err := h.services[app.Hosted].Call(ctx, call)
	if err != nil {
		var arg *ArgumentError
		if errors.As(err, &arg) {
			return fail(http.StatusBadRequest, arg.Reason)
		}
		if ctx.Err() != nil {
			return fail(http.StatusGatewayTimeout, "timed out")
		}
		log.Printf("[hosted] %s %s.%s: %v", b.Fleet, name, q.Method, err)
		return fail(http.StatusBadRequest, err.Error())
	}
	body, err := json.Marshal(map[string]any{"success": true, "result": result})
	if err != nil {
		return fail(http.StatusInternalServerError, "unencodable result")
	}
	return http.StatusOK, body
}

// ServeRPC answers the owner's browser App RPC through the gateway.
func (h *Host) ServeRPC(w http.ResponseWriter, r *http.Request, b apptransport.Binding) {
	if r.Method != http.MethodPost || r.URL.Path != "/rpc" {
		http.Error(w, "this App answers App RPC only", http.StatusNotFound)
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 512*1024))
	if err != nil {
		http.Error(w, "invalid RPC body", http.StatusBadRequest)
		return
	}
	status, body := h.invoke(r.Context(), b, raw, true)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func fail(status int, reason string) (int, json.RawMessage) {
	body, _ := json.Marshal(map[string]any{"success": false, "error": reason})
	return status, body
}

// Failure is a tool's own failed result ({success: false, error}), which the
// Python tools returned rather than raised.
func Failure(err error) map[string]any {
	return map[string]any{"success": false, "error": strings.TrimSpace(err.Error())}
}
