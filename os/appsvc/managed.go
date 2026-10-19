package appsvc

// Managed Go Apps speak the same loopback HTTP protocol as portable Python
// Apps. Fleet owns routing, credentials, process identity and port allocation;
// this host owns admission and the lifetime of work actually accepted by it.
import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"sync"
	"time"
)

const ManagedMaxRPC = 512 * 1024

type ManagedIdentity struct {
	AppID      string `json:"app_id"`
	InstanceID string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation uint64 `json:"generation"`
}

type DrainReceipt struct {
	Status     string `json:"status"`
	SafeToStop bool   `json:"safe_to_stop"`
	Message    string `json:"message,omitempty"`
}

type ManagedOptions struct {
	Identity ManagedIdentity
	Token    string
	Tools    []*Tool
	// BeforeStop is a bounded, read-only resource guard. It runs with admission
	// locked and no accepted RPCs outstanding.
	BeforeStop func() DrainReceipt
	// DrainMethods are explicit completion/recovery operations still admitted
	// while stopping (e.g. collecting Shell output). New work stays rejected.
	DrainMethods []string
	// Close must be idempotent and report unsuccessful cleanup. It runs once
	// at a time, off the HTTP goroutine, after admission has permanently closed.
	Close         func() error
	MaxConcurrent int
}

type ManagedHost struct {
	opts         ManagedOptions
	tools        map[string]*Tool
	drainMethods map[string]bool
	mu           sync.Mutex
	active       int
	draining     bool
	closing      bool
	closed       bool
	closeError   bool
}

func NewManagedHost(opts ManagedOptions) (*ManagedHost, error) {
	if opts.Identity.AppID == "" || opts.Identity.InstanceID == "" ||
		!sessionLeaseID.MatchString(opts.Identity.Revision) || opts.Identity.Generation == 0 || len(opts.Token) < 32 {
		return nil, fmt.Errorf("managed App requires a Fleet identity and RPC credential")
	}
	if opts.MaxConcurrent == 0 {
		opts.MaxConcurrent = 32
	}
	if opts.MaxConcurrent < 1 || opts.MaxConcurrent > 256 {
		return nil, fmt.Errorf("invalid managed App concurrency limit")
	}
	h := &ManagedHost{opts: opts, tools: make(map[string]*Tool), drainMethods: make(map[string]bool)}
	for _, tool := range opts.Tools {
		if tool == nil || tool.Name == "" || tool.Handler == nil || h.tools[tool.Name] != nil || tool.Name == "_ping" || tool.Name == "list_tools" {
			return nil, fmt.Errorf("invalid or duplicate managed App tool")
		}
		copy := *tool
		copy.Inputs = append([]Param(nil), tool.Inputs...)
		h.tools[copy.Name] = &copy
	}
	for _, name := range opts.DrainMethods {
		if h.tools[name] == nil {
			return nil, fmt.Errorf("unknown drain completion method")
		}
		h.drainMethods[name] = true
	}
	return h, nil
}

func managedJSON(w http.ResponseWriter, status int, value any) {
	status, body := encodeManagedJSON(status, value)
	writeManagedJSON(w, status, body)
}

func encodeManagedJSON(status int, value any) (int, []byte) {
	body, err := json.Marshal(value)
	if err != nil || len(body) > ManagedMaxRPC {
		status = http.StatusInternalServerError
		body = []byte(`{"success":false,"error":"App returned an invalid or oversized result; outcome may be unknown"}`)
	}
	return status, body
}

func writeManagedJSON(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func managedError(w http.ResponseWriter, status int, message string) {
	managedJSON(w, status, map[string]any{"success": false, "error": message})
}

func (h *ManagedHost) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// This is a node-private listener, never a browser or cross-origin API.
	if r.Header.Get("Origin") != "" || r.Header.Get("X-Forwarded-Host") != "" ||
		subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Fleet-RPC-Token")), []byte(h.opts.Token)) != 1 {
		managedError(w, http.StatusForbidden, "App RPC authorization required")
		return
	}
	switch {
	case r.Method == "GET" && r.URL.Path == "/health":
		h.mu.Lock()
		ready, active := !h.draining && !h.closed, h.active
		h.mu.Unlock()
		managedJSON(w, http.StatusOK, struct {
			ManagedIdentity
			Ready  bool `json:"ready"`
			Active int  `json:"active"`
		}{h.opts.Identity, ready, active})
	case r.Method == "POST" && r.URL.Path == "/_fleet/drain":
		managedJSON(w, http.StatusOK, h.Drain())
	case r.Method == "POST" && r.URL.Path == "/rpc":
		h.invoke(w, r)
	default:
		managedError(w, http.StatusNotFound, "Unknown App endpoint")
	}
}

func (h *ManagedHost) invoke(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Method  string         `json:"method"`
		Args    map[string]any `json:"args"`
		Timeout *int           `json:"timeout_s"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, ManagedMaxRPC)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil || decoder.Decode(new(any)) != io.EOF || req.Method == "" {
		managedError(w, http.StatusBadRequest, "Invalid App RPC payload")
		return
	}
	timeout := 60
	if req.Timeout != nil {
		timeout = *req.Timeout
	}
	if timeout < 1 || timeout > 600 {
		managedError(w, http.StatusBadRequest, "App RPC timeout must be 1..600 seconds")
		return
	}
	tool := h.tools[req.Method]
	if tool == nil && req.Method != "_ping" && req.Method != "list_tools" {
		managedError(w, http.StatusNotFound, "Unknown App method")
		return
	}
	if req.Args == nil {
		req.Args = make(map[string]any)
	}
	h.mu.Lock()
	if h.closed || h.closing || h.closeError || (h.draining && !h.drainMethods[req.Method]) {
		h.mu.Unlock()
		managedError(w, http.StatusConflict, "App is stopping; call was not accepted")
		return
	}
	if h.active >= h.opts.MaxConcurrent {
		h.mu.Unlock()
		managedError(w, http.StatusTooManyRequests, "App concurrency limit reached; call was not accepted")
		return
	}
	h.active++
	h.mu.Unlock()
	// Client disconnect does not release work ownership. A handler can honor
	// its deadline, but only its actual return releases admission/drain state.
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), time.Duration(timeout)*time.Second)
	defer cancel()
	type reply struct {
		status int
		body   []byte
	}
	completed := make(chan reply, 1)
	go func() {
		var out reply
		defer func() {
			if recover() != nil {
				out.status, out.body = encodeManagedJSON(http.StatusInternalServerError, map[string]any{"success": false, "error": "App call failed; outcome may be unknown"})
			}
			h.mu.Lock()
			h.active--
			h.mu.Unlock()
			completed <- out
		}()
		var result any
		var err error
		switch req.Method {
		case "_ping":
			result = h.opts.Identity
		case "list_tools":
			result = map[string]any{"success": true, "tools": h.toolDescriptions()}
		default:
			result, err = tool.Handler(ctx, req.Args)
		}
		if err != nil {
			out.status, out.body = encodeManagedJSON(http.StatusInternalServerError, map[string]any{"success": false, "error": "App call failed; outcome may be unknown"})
		} else {
			// Snapshot results before releasing the call's resource lifetime.
			// Cleanup may invalidate provider-owned values returned by handlers.
			out.status, out.body = encodeManagedJSON(http.StatusOK, map[string]any{"success": true, "result": result})
		}
	}()
	select {
	case out := <-completed:
		writeManagedJSON(w, out.status, out.body)
	case <-ctx.Done():
		managedError(w, http.StatusGatewayTimeout, "App call timed out; outcome may be unknown")
	case <-r.Context().Done():
		return
	}
}

func (h *ManagedHost) toolDescriptions() []map[string]any {
	names := make([]string, 0, len(h.tools))
	for name, t := range h.tools {
		if !t.Hidden {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	out := make([]map[string]any, 0, len(names))
	for _, name := range names {
		out = append(out, h.tools[name].desc())
	}
	return out
}

// Drain never mistakes an HTTP timeout for completed backend work. Cleanup
// errors remain unsafe; another owner-requested drain may retry cleanup.
func (h *ManagedHost) Drain() DrainReceipt {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.closed {
		return DrainReceipt{Status: "succeeded", SafeToStop: true}
	}
	h.draining = true
	if h.active != 0 {
		return DrainReceipt{Status: "waiting", Message: "App calls are still running"}
	}
	if !h.closing && !h.closeError && h.opts.BeforeStop != nil {
		guard := h.opts.BeforeStop()
		if guard.Status != "succeeded" || !guard.SafeToStop {
			return guard
		}
	}
	if h.closing {
		return DrainReceipt{Status: "waiting", Message: "App resources are closing"}
	}
	if h.closeError {
		h.closeError = false
		return DrainReceipt{Status: "failed", Message: "App cleanup failed; resources are retained for recovery"}
	}
	h.closing = true
	go func() {
		var err error
		defer func() {
			if recover() != nil {
				err = fmt.Errorf("App cleanup panicked")
			}
			h.mu.Lock()
			h.closing = false
			h.closed, h.closeError = err == nil, err != nil
			h.mu.Unlock()
		}()
		if h.opts.Close != nil {
			err = h.opts.Close()
		}
	}()
	return DrainReceipt{Status: "waiting", Message: "App resources are closing"}
}
