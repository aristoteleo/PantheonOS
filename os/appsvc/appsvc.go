// Package appsvc implements the Pantheon bus service protocol — the Go half
// of what pantheonos/remote/backend/nats.py's NATSRemoteWorker does in Python.
// It is what lets a Go builtin App answer the exact same wire surface a
// python ToolSet instance answers, so callers (ToolsetProxy, the frontend)
// cannot tell the implementations apart.
//
// The wire contract (mirrored, not shared):
//
//   - RPC: core NATS request/reply on `[prefix.]pantheon.service.<service_id>`.
//     Requests are JSON `{"method": ..., "parameters": {...}}`; responses are
//     JSON `{"result": ...}` or `{"error": "..."}`.
//   - Discovery: JetStream KV bucket `pantheon-service`, key = service_id,
//     value = the JSON registration (service_name, functions_description in
//     funcdesc shape, subject). This registration carries
//     `"serialization": "json"` — the marker the Python client negotiates on
//     to send JSON instead of its default cloudpickle.
//   - Built-ins every worker answers: `_ping` and `list_tools`.
package appsvc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// DiscoveryBucket is the global service-discovery KV bucket every Pantheon
// worker registers into (fixed name, deliberately not fleet-scoped — mirrors
// the Python side's forced `pantheon-service`).
const DiscoveryBucket = "pantheon-service"

// Param describes one tool parameter in funcdesc's JSON shape.
type Param struct {
	Type    string `json:"type"`
	Range   any    `json:"range"`
	Default any    `json:"default"` // "not_defined" when required
	Name    string `json:"name"`
	Doc     any    `json:"doc"`
}

// NotDefined is funcdesc's sentinel for "parameter has no default".
const NotDefined = "not_defined"

// Handler executes one tool call. params is the raw parameters object from
// the request — it may carry framework extras (session_id,
// context_variables) beyond the declared inputs; handlers must tolerate
// them. The returned value must be JSON-serializable.
type Handler func(ctx context.Context, params map[string]any) (any, error)

// Tool is one callable on the service. Hidden tools are answered on the wire
// but left out of list_tools — exactly how @tool(exclude=True) behaves.
type Tool struct {
	Name    string
	Doc     string
	Inputs  []Param
	Hidden  bool
	Handler Handler
}

// desc renders the funcdesc Description JSON for registration/list_tools.
func (t *Tool) desc() map[string]any {
	inputs := t.Inputs
	if inputs == nil {
		inputs = []Param{}
	}
	return map[string]any{
		"name":         t.Name,
		"doc":          t.Doc,
		"inputs":       inputs,
		"outputs":      []any{},
		"side_effects": []any{},
	}
}

// Service is one registered bus service (= one App instance's headless face).
type Service struct {
	nc          *nats.Conn
	serviceID   string
	serviceName string
	description string
	version     string
	prefix      string // optional subject prefix (NATS_SUBJECT_PREFIX)
	tools       map[string]*Tool
	order       []string
	sub         *nats.Subscription

	mu      sync.Mutex
	kv      jetstream.KeyValue
	stopped bool
}

// New builds a service. serviceID is the sha256 hex the control plane
// assigned (spec.service_id) — the same value generate_service_id produces
// on the Python side.
func New(nc *nats.Conn, serviceID, serviceName, description, version, prefix string) *Service {
	return &Service{
		nc: nc, serviceID: serviceID, serviceName: serviceName,
		description: description, version: version, prefix: prefix,
		tools: map[string]*Tool{},
	}
}

// Register adds a tool. Not safe after Start.
func (s *Service) Register(t *Tool) {
	if _, dup := s.tools[t.Name]; !dup {
		s.order = append(s.order, t.Name)
	}
	s.tools[t.Name] = t
}

func (s *Service) subject() string {
	base := "pantheon.service." + s.serviceID
	if s.prefix != "" {
		return s.prefix + "." + base
	}
	return base
}

// Start subscribes the RPC subject and writes the KV registration.
func (s *Service) Start(ctx context.Context) error {
	sub, err := s.nc.Subscribe(s.subject(), s.handle)
	if err != nil {
		return err
	}
	s.sub = sub
	// KV registration is discovery sugar, not the contract — clients
	// negotiate via _ping and discover tools via list_tools. Where the
	// JetStream API is reachable it takes milliseconds and is done before
	// Start returns, as before. Node and App credentials do not reach it, so
	// the request waits out its timeout: that part runs on in the background,
	// and quietly, instead of holding the App's start.
	done := make(chan struct{})
	go func() { s.register(); close(done) }()
	select {
	case <-done:
	case <-time.After(registerWait):
	case <-ctx.Done():
	}
	return nil
}

const (
	registerWait    = time.Second
	registerTimeout = 5 * time.Second
)

func (s *Service) register() {
	ctx, cancel := context.WithTimeout(context.Background(), registerTimeout)
	defer cancel()
	kv, err := s.registerKV(ctx)
	if err != nil {
		if !errors.Is(err, context.DeadlineExceeded) && !errors.Is(err, nats.ErrPermissionViolation) &&
			!errors.Is(err, nats.ErrNoResponders) && !errors.Is(err, jetstream.ErrJetStreamNotEnabled) {
			fmt.Printf("appsvc: kv registration skipped for %s: %v\n", s.serviceID[:12], err)
		}
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stopped {
		_ = kv.Delete(ctx, s.serviceID)
		return
	}
	s.kv = kv
}

// Stop unsubscribes and removes the KV registration.
func (s *Service) Stop(ctx context.Context) {
	if s.sub != nil {
		_ = s.sub.Unsubscribe()
		s.sub = nil
	}
	s.mu.Lock()
	kv := s.kv
	s.kv, s.stopped = nil, true
	s.mu.Unlock()
	if kv != nil {
		_ = kv.Delete(ctx, s.serviceID)
	}
}

func (s *Service) registerKV(ctx context.Context) (jetstream.KeyValue, error) {
	js, err := jetstream.New(s.nc)
	if err != nil {
		return nil, err
	}
	kv, err := js.CreateOrUpdateKeyValue(ctx, jetstream.KeyValueConfig{
		Bucket:      DiscoveryBucket,
		Description: "Pantheon service discovery",
	})
	if err != nil {
		return nil, err
	}
	funcs := map[string]any{}
	for name, t := range s.tools {
		funcs[name] = t.desc()
	}
	reg := map[string]any{
		"service_id":            s.serviceID,
		"service_name":          s.serviceName,
		"description":           s.description,
		"functions_description": funcs,
		"subject":               s.subject(),
		"registered_at":         float64(time.Now().UnixNano()) / 1e9,
		"serialization":         "json",
	}
	b, err := json.Marshal(reg)
	if err != nil {
		return nil, err
	}
	if _, err = kv.Put(ctx, s.serviceID, b); err != nil {
		return nil, err
	}
	return kv, nil
}

type request struct {
	Method     string         `json:"method"`
	Parameters map[string]any `json:"parameters"`
}

func (s *Service) handle(msg *nats.Msg) {
	go func() {
		data := s.dispatch(msg.Data)
		_ = msg.Respond(data)
	}()
}

func respondErr(format string, a ...any) []byte {
	b, _ := json.Marshal(map[string]any{"error": fmt.Sprintf(format, a...)})
	return b
}

func (s *Service) dispatch(data []byte) []byte {
	var req request
	if err := json.Unmarshal(data, &req); err != nil || req.Method == "" {
		// A cloudpickle payload lands here when a Python client skipped
		// negotiation (raced the KV registration). The error names the fix.
		return respondErr(
			"service %s speaks JSON only (a non-JSON request was received; "+
				"the caller should renegotiate serialization from the KV registration)",
			s.serviceID)
	}
	params := req.Parameters
	if params == nil {
		params = map[string]any{}
	}
	switch req.Method {
	case "_ping":
		// "serialization" is the negotiation marker: a Python client probes
		// with a JSON _ping and switches its wire format on seeing it.
		b, _ := json.Marshal(map[string]any{"result": map[string]any{
			"status": "ok", "service_id": s.serviceID, "version": s.version,
			"serialization": "json",
		}})
		return b
	case "list_tools":
		b, _ := json.Marshal(map[string]any{"result": s.listTools()})
		return b
	}
	tool, ok := s.tools[req.Method]
	if !ok {
		return respondErr("Method %s not found on service %s", req.Method, s.serviceID)
	}
	result, err := tool.Handler(context.Background(), params)
	if err != nil {
		return respondErr("Error processing %s: %s", req.Method, err)
	}
	b, err := json.Marshal(map[string]any{"result": result})
	if err != nil {
		return respondErr("Error encoding %s result: %s", req.Method, err)
	}
	return b
}

// listTools mirrors ToolSet.list_tools: visible tools only, funcdesc shape.
func (s *Service) listTools() map[string]any {
	tools := []any{}
	for _, name := range s.order {
		t := s.tools[name]
		if t.Hidden {
			continue
		}
		tools = append(tools, t.desc())
	}
	return map[string]any{"success": true, "tools": tools}
}
