package hosted

import (
	"context"
	"encoding/json"
	"fmt"
)

// Bus reaches one owner's fleet with the controller's authority for it.
type Bus interface {
	// Records returns every node's registry record as published.
	Records(ctx context.Context, fleet string) ([]map[string]any, error)
	// Request sends one command on a node's cmd subject and returns its reply.
	Request(ctx context.Context, fleet, node string, payload any) (json.RawMessage, error)
	// Subscribe delivers messages on a fleet subject until the returned stop.
	Subscribe(fleet, subject string, fn func([]byte)) (func(), error)
}

// lifecycle sends one app_lifecycle command; a reply carrying {"error"} fails.
func lifecycle(ctx context.Context, bus Bus, fleet, node, method string, data map[string]any) (map[string]any, error) {
	payload := map[string]any{}
	for k, v := range data {
		payload[k] = v
	}
	payload["type"], payload["protocol"], payload["method"] = "app_lifecycle", 1, method
	raw, err := bus.Request(ctx, fleet, node, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("node returned an invalid reply")
	}
	if e, ok := out["error"].(string); ok && e != "" {
		return nil, fmt.Errorf("%s", e)
	}
	return out, nil
}

// command sends a typed node command and decodes its reply.
func command(ctx context.Context, bus Bus, fleet, node string, payload map[string]any) (map[string]any, error) {
	raw, err := bus.Request(ctx, fleet, node, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("node returned an invalid reply")
	}
	return out, nil
}

// field walks nested maps: field(rec, "capability", "os").
func field(m map[string]any, path ...string) any {
	var v any = m
	for _, key := range path {
		next, ok := v.(map[string]any)
		if !ok {
			return nil
		}
		v = next[key]
	}
	return v
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

func num(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case json.Number:
		f, _ := t.Float64()
		return f
	}
	return 0
}

func strings_(v any) []string {
	list, _ := v.([]any)
	out := []string{}
	for _, item := range list {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func orEmpty(v any) any {
	if v == nil {
		return map[string]any{}
	}
	return v
}
