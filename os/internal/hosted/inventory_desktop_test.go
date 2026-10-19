package hosted

import (
	"context"
	"encoding/json"
	"sync"
	"testing"
)

type desktopBus struct {
	mu    sync.Mutex
	calls []map[string]any
}

func (b *desktopBus) Records(context.Context, string) ([]map[string]any, error) { return nil, nil }
func (b *desktopBus) Subscribe(string, string, func([]byte)) (func(), error)    { return func() {}, nil }
func (b *desktopBus) Request(_ context.Context, _, _ string, payload any) (json.RawMessage, error) {
	b.mu.Lock()
	b.calls = append(b.calls, payload.(map[string]any))
	b.mu.Unlock()
	return json.RawMessage(`{"response":{"success":true,"result":{"success":true,"instances":[{"app_id":"browser","kind":"window"}]}}}`), nil
}

// A node lists a legacy desktop service record (no instance), stopped
// desktops and its running lifecycle instance as "ready": only the running
// instance is asked for its windows, and nothing is reported as unreadable.
func TestDesktopAppsReadOnlyTheRunningLifecycleInstance(t *testing.T) {
	bus := &desktopBus{}
	desktop := func(health, instance string) map[string]any {
		return map[string]any{"app_id": "desktop", "node_id": "ws", "node_status": "online", "health": health,
			"instance_id": instance, "revision": "r", "generation": 5}
	}
	legacy := desktop("healthy", "")
	delete(legacy, "instance_id")
	legacy["service_id"] = "legacy-service"
	result := map[string]any{"nodes": []map[string]any{{"node_id": "ws", "name": "workspace", "status": "online"}},
		"instances": []map[string]any{legacy, desktop("stopped", "old"), desktop("ready", "current")}}
	result = withDesktopApps(context.Background(), bus, "f_1", result)
	if _, warned := result["warnings"]; warned {
		t.Fatalf("no warning: %v", result["warnings"])
	}
	if len(bus.calls) != 1 || bus.calls[0]["instance_id"] != "current" {
		t.Fatalf("only the running instance is invoked: %v", bus.calls)
	}
	if n := len(result["instances"].([]map[string]any)); n != 4 {
		t.Fatalf("its window is added: %d instances", n)
	}
}
