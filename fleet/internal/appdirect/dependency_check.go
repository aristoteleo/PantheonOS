package appdirect

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ControllerCheck uses only the node's saved origin, never a grant-supplied URL.
// The private proof authenticates a read-only liveness check, not owner access.
func ControllerCheck(ctx context.Context, origin string) (DependencyCheck, error) {
	u, err := url.Parse(strings.TrimRight(origin, "/"))
	if err != nil || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" ||
		(u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost"))) {
		return nil, fmt.Errorf("dependency checks require a configured Controller origin")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.MaxIdleConnsPerHost = 16
	transport.IdleConnTimeout = 30 * time.Second
	client := &http.Client{Transport: transport, Timeout: 6 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	go func() { <-ctx.Done(); transport.CloseIdleConnections() }()
	endpoint := u.String() + "/apps/dependencies/check"
	return func(call context.Context, q Request) error {
		if ctx.Err() != nil || q.Dependency == nil || !q.Dependency.Valid(q) {
			return ErrGrantRejected
		}
		body, err := json.Marshal(q)
		if err != nil {
			return ErrGrantRejected
		}
		request, err := http.NewRequestWithContext(call, "POST", endpoint, bytes.NewReader(body))
		if err != nil {
			return ErrGrantRejected
		}
		request.Header.Set("Content-Type", "application/json")
		response, err := client.Do(request)
		if err != nil {
			return ErrGrantRejected
		}
		defer response.Body.Close()
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1024))
		if response.StatusCode != 204 {
			return ErrGrantRejected
		}
		return nil
	}, nil
}
