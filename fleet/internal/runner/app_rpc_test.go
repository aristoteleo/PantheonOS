package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAppRPCIsBoundedAndDoesNotFollowRedirects(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/rpc" || r.Method != "POST" {
			t.Error("RPC must use fixed endpoint")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"success":true,"result":{"node":"mac"}}`))
	}))
	defer server.Close()
	body, err := invokeAppRPC(context.Background(), server.URL, json.RawMessage(`{"method":"datasets"}`), 5)
	if err != nil || !strings.Contains(string(body), `"mac"`) {
		t.Fatalf("%s %v", body, err)
	}
	for _, bad := range []json.RawMessage{nil, []byte("{} garbage"), []byte(strings.Repeat(" ", maxAppRPC+1))} {
		if _, err := invokeAppRPC(context.Background(), server.URL, bad, 5); err == nil {
			t.Fatal("accepted bad payload")
		}
	}
	if calls != 1 {
		t.Fatal("invalid calls reached the backend")
	}
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, server.URL+"/rpc", 307) }))
	defer redirect.Close()
	if _, err := invokeAppRPC(context.Background(), redirect.URL, []byte(`{}`), 5); err == nil {
		t.Fatal("followed redirect")
	}
	if calls != 1 {
		t.Fatal("redirect reached another service")
	}
}
