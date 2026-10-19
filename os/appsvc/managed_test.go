package appsvc

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func managedOptions() ManagedOptions {
	return ManagedOptions{Identity: ManagedIdentity{AppID: "test-app", InstanceID: "instance",
		Revision: strings.Repeat("a", 64), Generation: 1}, Token: strings.Repeat("b", 64)}
}

func managedCall(t *testing.T, h http.Handler, method, path, body, token, origin string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("X-Fleet-RPC-Token", token)
	req.Header.Set("Origin", origin)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

func awaitDrained(t *testing.T, h *ManagedHost) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		r := h.Drain()
		if r.Status == "succeeded" && r.SafeToStop {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("host failed to drain")
}

func TestManagedHTTPAdmission(t *testing.T) {
	var calls atomic.Int32
	opts := managedOptions()
	opts.Tools = []*Tool{{Name: "echo", Handler: func(_ context.Context, args map[string]any) (any, error) {
		calls.Add(1)
		return args, nil
	}}}
	h, err := NewManagedHost(opts)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/rpc", "/_fleet/drain", "/health"} {
		method := "POST"
		if path == "/health" {
			method = "GET"
		}
		for _, token := range []string{"", "wrong"} {
			if got := managedCall(t, h, method, path, `{"method":"echo"}`, token, ""); got.Code != 403 {
				t.Fatalf("unauthorized %s: %d", path, got.Code)
			}
		}
		if got := managedCall(t, h, method, path, `{"method":"echo"}`, opts.Token, "https://example.org"); got.Code != 403 {
			t.Fatalf("browser origin accepted: %d", got.Code)
		}
	}
	for _, body := range []string{`{`, `{"method":"echo","args":[]}`, `{"method":"echo","extra":1}`,
		`{"method":"echo","timeout_s":0}`, `{"method":"echo","timeout_s":1.5}`, `{"method":"echo"} {}`,
		`{"method":"echo","args":{"large":"` + strings.Repeat("x", ManagedMaxRPC) + `"}}`} {
		if got := managedCall(t, h, "POST", "/rpc", body, opts.Token, ""); got.Code != 400 {
			t.Fatalf("invalid payload accepted: %d", got.Code)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("rejected request invoked handler")
	}
	got := managedCall(t, h, "POST", "/rpc", `{"method":"echo","args":{"value":"ok"}}`, opts.Token, "")
	if got.Code != 200 || !strings.Contains(got.Body.String(), `"value":"ok"`) {
		t.Fatal(got)
	}
	awaitDrained(t, h)
	if got := managedCall(t, h, "POST", "/rpc", `{"method":"echo"}`, opts.Token, ""); got.Code != 409 {
		t.Fatal("accepted call after drain", got)
	}
	if calls.Load() != 1 {
		t.Fatal("call replayed")
	}
}

func TestManagedTimeoutAndDisconnectRetainActualWork(t *testing.T) {
	for _, mode := range []string{"timeout", "disconnect"} {
		t.Run(mode, func(t *testing.T) {
			started, release := make(chan struct{}), make(chan struct{})
			defer close(release)
			opts := managedOptions()
			opts.MaxConcurrent = 1
			var closed atomic.Bool
			opts.Close = func() error { closed.Store(true); return nil }
			opts.Tools = []*Tool{{Name: "slow", Handler: func(context.Context, map[string]any) (any, error) {
				close(started)
				<-release // deliberately ignores cancellation
				return "done", nil
			}}}
			h, _ := NewManagedHost(opts)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			req := httptest.NewRequest("POST", "/rpc", strings.NewReader(`{"method":"slow","timeout_s":1}`)).WithContext(ctx)
			req.Header.Set("X-Fleet-RPC-Token", opts.Token)
			response := httptest.NewRecorder()
			done := make(chan struct{})
			go func() { h.ServeHTTP(response, req); close(done) }()
			<-started
			if mode == "disconnect" {
				cancel()
			}
			<-done
			if mode == "timeout" && response.Code != 504 {
				t.Fatal(response)
			}
			if got := managedCall(t, h, "POST", "/rpc", `{"method":"slow"}`, opts.Token, ""); got.Code != 429 {
				t.Fatal("released concurrency before handler returned", got.Code)
			}
			if r := h.Drain(); r.SafeToStop || closed.Load() {
				t.Fatal("lost actual work", r)
			}
			if got := managedCall(t, h, "POST", "/rpc", `{"method":"slow"}`, opts.Token, ""); got.Code != 409 {
				t.Fatal("accepted new work during drain", got.Code)
			}
			// Deliver one value rather than close, allowing the deferred close.
			release <- struct{}{}
			awaitDrained(t, h)
			if !closed.Load() {
				t.Fatal("cleanup never ran")
			}
		})
	}
}

func TestManagedResourceGuardAndCleanupFailure(t *testing.T) {
	opts := managedOptions()
	var guardReady atomic.Bool
	var cleanups atomic.Int32
	opts.Tools = []*Tool{{Name: "progress", Handler: func(context.Context, map[string]any) (any, error) { return nil, nil }}}
	opts.DrainMethods = []string{"progress"}
	opts.BeforeStop = func() DrainReceipt {
		if !guardReady.Load() {
			return DrainReceipt{Status: "waiting"}
		}
		return DrainReceipt{Status: "succeeded", SafeToStop: true}
	}
	opts.Close = func() error {
		if cleanups.Add(1) == 1 {
			return fmt.Errorf("private credential must not be exposed")
		}
		return nil
	}
	h, _ := NewManagedHost(opts)
	if r := h.Drain(); r.Status != "waiting" || cleanups.Load() != 0 {
		t.Fatal(r)
	}
	if got := managedCall(t, h, "POST", "/rpc", `{"method":"progress"}`, opts.Token, ""); got.Code != 200 {
		t.Fatal(got)
	}
	guardReady.Store(true)
	deadline := time.Now().Add(time.Second)
	for {
		r := h.Drain()
		if r.Status == "failed" {
			if r.SafeToStop || strings.Contains(r.Message, "credential") {
				t.Fatal(r)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("cleanup failure disappeared", r)
		}
		time.Sleep(time.Millisecond)
	}
	awaitDrained(t, h)
	if cleanups.Load() != 2 {
		t.Fatal("cleanup did not retry exactly once")
	}
}

func TestManagedProbePinsIdentity(t *testing.T) {
	opts := managedOptions()
	h, _ := NewManagedHost(opts)
	server := httptest.NewServer(h)
	defer server.Close()
	address := strings.TrimPrefix(server.URL, "http://")
	var out bytes.Buffer
	if err := managedProbe(context.Background(), "ready", opts.Identity, opts.Token, address, &out); err != nil {
		t.Fatal(err)
	}
	wrong := opts.Identity
	wrong.Generation++
	if err := managedProbe(context.Background(), "drain", wrong, opts.Token, address, &out); err == nil {
		t.Fatal("stale generation could drain")
	}
	if err := managedProbe(context.Background(), "ready", opts.Identity, opts.Token, address, &out); err != nil {
		t.Fatal("stale probe changed readiness", err)
	}
	if err := managedProbe(context.Background(), "drain", opts.Identity, opts.Token, address, &out); err != nil {
		t.Fatal(err)
	}
	var receipt DrainReceipt
	if err := json.Unmarshal(out.Bytes(), &receipt); err != nil || !receipt.SafeToStop {
		t.Fatal(out.String(), err)
	}
	if err := managedProbe(context.Background(), "ready", opts.Identity, opts.Token, address, io.Discard); err == nil {
		t.Fatal("closed app ready")
	}
}

func TestManagedPanicsAndOversizedResults(t *testing.T) {
	opts := managedOptions()
	opts.Tools = []*Tool{
		{Name: "panic", Handler: func(context.Context, map[string]any) (any, error) { panic("secret") }},
		{Name: "large", Handler: func(context.Context, map[string]any) (any, error) { return strings.Repeat("x", ManagedMaxRPC), nil }},
	}
	h, _ := NewManagedHost(opts)
	for _, method := range []string{"panic", "large"} {
		got := managedCall(t, h, "POST", "/rpc", `{"method":"`+method+`"}`, opts.Token, "")
		if got.Code != 500 || strings.Contains(got.Body.String(), "secret") || got.Body.Len() > 1024 {
			t.Fatal(got)
		}
	}
	awaitDrained(t, h)
}
