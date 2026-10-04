package apptransport

import (
	"net/http"
	"path"
	"strings"
)

type HTTPRule struct {
	Method string `json:"method"`
	Path   string `json:"path"`
	Prefix bool   `json:"prefix,omitempty"`
}

type HTTPDependency struct {
	Rules      []HTTPRule        `json:"rules"`
	Headers    map[string]string `json:"headers,omitempty"`
	Credential string            `json:"credential"` // Hub-signed exact provider identity, never returned to consumer
}

func cleanDependencyPath(value string) bool {
	return strings.HasPrefix(value, "/") && len(value) <= 1024 &&
		!strings.ContainsAny(value, "\\%?#\r\n\x00") && path.Clean(value) == value
}

func (p *HTTPDependency) Valid() bool {
	if len(p.Rules) == 0 || len(p.Rules) > 64 || len(p.Headers) > 16 || len(p.Credential) < 32 || len(p.Credential) > 8192 {
		return false
	}
	seen := map[string]bool{}
	for _, rule := range p.Rules {
		if !cleanDependencyPath(rule.Path) || rule.Path == "/rpc" || strings.HasPrefix(rule.Path, "/__fleet") || strings.HasPrefix(rule.Path, "/_fleet") || rule.Prefix && rule.Path == "/" {
			return false
		}
		switch rule.Method {
		case "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE":
		default:
			return false
		}
		key := rule.Method + " " + rule.Path
		if seen[key] {
			return false
		}
		seen[key] = true
	}
	for name, value := range p.Headers {
		lower := strings.ToLower(name)
		if name == "" || name != http.CanonicalHeaderKey(name) || len(name) > 128 || len(value) > 4096 || strings.ContainsAny(value, "\r\n\x00") {
			return false
		}
		for _, c := range name {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-') {
				return false
			}
		}
		if strings.HasPrefix(lower, "x-fleet-") || strings.HasPrefix(lower, "x-pantheon-") || strings.HasPrefix(lower, "x-forwarded-") {
			return false
		}
		switch lower {
		case "authorization", "cookie", "host", "connection", "upgrade", "transfer-encoding", "content-length", "te", "trailer", "proxy-authorization", "proxy-connection", "forwarded":
			return false
		}
	}
	return true
}

func (p *HTTPDependency) Permits(r *http.Request) bool {
	if !cleanDependencyPath(r.URL.Path) || r.URL.RawPath != "" || r.Header.Get("Origin") != "" || r.Header.Get("Sec-Fetch-Site") != "" || r.Header.Get("Upgrade") != "" || r.Header.Get("Content-Encoding") != "" {
		return false
	}
	for _, rule := range p.Rules {
		if rule.Method == r.Method && (rule.Path == r.URL.Path || rule.Prefix && strings.HasPrefix(r.URL.Path, rule.Path+"/")) {
			return true
		}
	}
	return false
}
