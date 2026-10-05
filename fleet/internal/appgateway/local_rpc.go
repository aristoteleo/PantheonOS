package appgateway

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

func NewLocalRPC(origin, serviceToken string, dispatch Dispatch, verify Verify) (*Gateway, error) {
	if !apptransport.ValidLocalRPCOrigin(origin) || len(serviceToken) < 24 || dispatch == nil || verify == nil {
		return nil, fmt.Errorf("local RPC gateway requires an explicit HTTPS loopback origin and node transport")
	}
	return &Gateway{localRPCOrigin: origin, serviceToken: serviceToken, dispatch: dispatch, verify: verify,
		origins: map[string]bool{}, grants: map[string]*grant{}, pending: map[string]*pending{}, slots: make(chan struct{}, 256)}, nil
}

// EnableLocalHTTP enables server-to-server data paths before Register/serving.
// Browser sessions, cookies and direct grants are intentionally not enabled.
func (g *Gateway) EnableLocalHTTP() error {
	if g.localRPCOrigin == "" {
		return fmt.Errorf("local HTTP dependencies require a private local authority")
	}
	g.localHTTP = true
	return nil
}

func (g *Gateway) acceptsHTTPDependency(policy *HTTPDependency) bool {
	if policy == nil {
		return true
	}
	if g.localRPCOrigin != "" {
		return g.localHTTP && policy.NodeBound && policy.Credential == ""
	}
	return !policy.NodeBound
}

func localInteger(raw json.RawMessage, fallback, low, high int) (int, bool) {
	if len(raw) == 0 {
		return fallback, true
	}
	var value int
	if json.Unmarshal(raw, &value) != nil || value < low || value > high {
		return 0, false
	}
	return value, true
}

// RegisterLocalAuthority exposes the same RPC-grant contract used by the Hub,
// with owner authentication resolved locally. It never accepts an asserted
// Fleet identity or gives a consumer an owner/service key. HTTP authority
// requires separate explicit opt-in; browser authority remains absent.
func (g *Gateway) RegisterLocalAuthority(mux *http.ServeMux, resolve func(string) (string, bool)) error {
	if g.localRPCOrigin == "" || resolve == nil {
		return fmt.Errorf("local RPC authority is not configured")
	}
	const rpcBase = "/api/fleet/apps/dependency-grants"
	const httpBase = "/api/fleet/apps/dependency-http-grants"
	handler := func(w http.ResponseWriter, r *http.Request) {
		base := rpcBase
		isHTTP := r.URL.Path == httpBase || strings.HasPrefix(r.URL.Path, httpBase+"/")
		if isHTTP {
			base = httpBase
		}
		w.Header().Set("Cache-Control", "no-store")
		if r.TLS == nil || "https://"+r.Host != g.localRPCOrigin || r.URL.RawQuery != "" || r.URL.ForceQuery ||
			r.URL.RawPath != "" || r.Header.Get("Origin") != "" || r.Header.Get("Sec-Fetch-Site") != "" ||
			r.Header.Get("Upgrade") != "" || r.Header.Get("Content-Encoding") != "" {
			http.Error(w, "owner control request required", 403)
			return
		}
		header := r.Header.Get("Authorization")
		if !strings.HasPrefix(header, "Bearer ") || len(header) > 8192 {
			http.Error(w, "owner authorization required", 401)
			return
		}
		fleet, ok := resolve(strings.TrimPrefix(header, "Bearer "))
		if !ok || fleet == "" {
			http.Error(w, "owner authorization required", 401)
			return
		}
		raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64*1024))
		if err != nil {
			http.Error(w, "invalid dependency request", 400)
			return
		}
		decode := func(value any) bool {
			if uniqueJSON(raw) != nil {
				return false
			}
			d := json.NewDecoder(bytes.NewReader(raw))
			d.DisallowUnknownFields()
			return d.Decode(value) == nil
		}
		var body any
		if r.Method == "POST" && r.URL.Path == base {
			var q struct {
				Operation   string                        `json:"operation_id,omitempty"`
				Consumer    apptransport.InstanceIdentity `json:"consumer"`
				Provider    Binding                       `json:"provider"`
				AppID       string                        `json:"app_id"`
				Preparation string                        `json:"preparation_id,omitempty"`
				Methods     map[string]RPCMethod          `json:"methods,omitempty"`
				Rules       []HTTPRule                    `json:"rules,omitempty"`
				Headers     map[string]string             `json:"headers,omitempty"`
				TTL         json.RawMessage               `json:"ttl_seconds"`
				Timeout     json.RawMessage               `json:"timeout_seconds"`
			}
			if !decode(&q) || q.Consumer.Fleet != "" || q.Provider.Fleet != "" {
				http.Error(w, "invalid dependency request", 400)
				return
			}
			// Keep the two public contracts disjoint, including explicit nulls.
			var fields map[string]json.RawMessage
			_ = json.Unmarshal(raw, &fields)
			for _, name := range []string{"methods", "rules", "headers"} {
				if _, exists := fields[name]; exists && ((isHTTP && name == "methods") || (!isHTTP && name != "methods")) {
					http.Error(w, "mixed dependency protocols", 400)
					return
				}
			}
			ttl, validTTL := localInteger(q.TTL, 300, 30, 900)
			timeout, validTimeout := localInteger(q.Timeout, 60, 1, 600)
			if !validTTL || !validTimeout {
				http.Error(w, "invalid dependency lifetime", 400)
				return
			}
			q.Consumer.Fleet, q.Provider.Fleet = fleet, fleet
			request := DependencyRequest{Operation: q.Operation, Consumer: q.Consumer, Provider: q.Provider,
				AppID: q.AppID, Preparation: q.Preparation, Methods: q.Methods, Timeout: timeout, Expires: time.Now().Unix() + int64(ttl)}
			if isHTTP {
				request.HTTP = &HTTPDependency{Rules: q.Rules, Headers: q.Headers, NodeBound: true}
			}
			body = request
		} else if strings.HasPrefix(r.URL.Path, base+"/") {
			id := strings.TrimPrefix(r.URL.Path, base+"/")
			if len(id) != 64 || strings.Trim(id, "0123456789abcdef") != "" {
				http.Error(w, "invalid grant id", 400)
				return
			}
			switch r.Method {
			case "PATCH":
				var q struct {
					TTL json.RawMessage `json:"ttl_seconds"`
				}
				if !decode(&q) {
					http.Error(w, "invalid renewal", 400)
					return
				}
				ttl, valid := localInteger(q.TTL, 900, 30, 900)
				if !valid {
					http.Error(w, "invalid renewal lifetime", 400)
					return
				}
				body = map[string]any{"fleet_id": fleet, "grant_id": id, "expires": time.Now().Unix() + int64(ttl)}
			case "DELETE":
				if len(raw) != 0 && string(raw) != "null" {
					http.Error(w, "revocation takes no body", 400)
					return
				}
				body = map[string]string{"fleet_id": fleet, "grant_id": id}
			default:
				http.Error(w, "method not allowed", 405)
				return
			}
		} else {
			http.Error(w, "method not allowed", 405)
			return
		}
		encoded, err := json.Marshal(body)
		if err != nil {
			http.Error(w, "invalid dependency request", 400)
			return
		}
		request := r.Clone(r.Context())
		request.Body = io.NopCloser(bytes.NewReader(encoded))
		request.ContentLength = int64(len(encoded))
		g.manageAuthorizedDependency(w, request)
	}
	mux.HandleFunc(rpcBase, handler)
	mux.HandleFunc(rpcBase+"/", handler)
	if g.localHTTP {
		mux.HandleFunc(httpBase, handler)
		mux.HandleFunc(httpBase+"/", handler)
	}
	return nil
}
