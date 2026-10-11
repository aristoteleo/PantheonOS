package hosted

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"sync"
	"time"
)

// inventory is the credential-free view of an owner's fleet the Files and
// Fleet views read (formerly apps/fleet/inventory.py node_inventory).
func inventory(records []map[string]any, now time.Time) map[string]any {
	nodes, instances := []map[string]any{}, []map[string]any{}
	for _, record := range records {
		capability, _ := record["capability"].(map[string]any)
		state, _ := record["state"].(map[string]any)
		if capability == nil {
			capability = map[string]any{}
		}
		if state == nil {
			state = map[string]any{}
		}
		status := str(state["status"])
		if status == "" {
			status = "unknown"
		}
		if seen, err := time.Parse(time.RFC3339Nano, str(record["last_seen"])); err != nil {
			status = "unknown"
		} else if now.Sub(seen) > 90*time.Second {
			status = "offline"
		}
		apps, _ := state["instances"].([]any)
		caps := strings_(capability["caps"])
		hasFiles := slices.Contains(caps, "fs:workspace") || slices.Contains(caps, "fs:local")
		hasPTY := false
		for _, item := range apps {
			app, _ := item.(map[string]any)
			id := str(app["app_id"])
			hasFiles = hasFiles || id == "file-manager" || id == "node-files"
			hasPTY = hasPTY || id == "pty" && str(app["health"]) == "healthy"
		}
		name := str(record["name"])
		if name == "" {
			name = str(record["node_id"])
		}
		labels := []string{}
		for _, l := range orList(record["labels"]) {
			labels = append(labels, fmt.Sprint(l))
		}
		delegation := record["delegation"]
		node := map[string]any{
			"node_id": record["node_id"], "name": name, "kind": record["kind"], "last_seen": record["last_seen"],
			"version": record["version"], "delegation": delegation, "labels": labels, "status": status,
			"has_files": hasFiles, "os": capability["os"], "arch": capability["arch"], "cpu_cores": capability["cpu_cores"],
			"ram_gb": capability["ram_gb"], "disk_free_gb": capability["disk_free_gb"], "gpu": capability["gpu"],
			"resources": capability["resources"], "load": orEmpty(state["load"]), "caps": caps,
			"tools": orList(capability["tools"]), "file_roots": orList(capability["file_roots"]),
			"runtimes": orEmpty(capability["runtimes"]), "has_pty": hasPTY,
			"can_start_pty": !truthy(delegation) && slices.Contains(caps, "proc") && (capability["os"] == "linux" || capability["os"] == "darwin"),
		}
		nodes = append(nodes, node)
		for _, item := range apps {
			app, _ := item.(map[string]any)
			entry := map[string]any{}
			for _, key := range []string{"app_id", "scope", "version", "service_id", "health", "instance_id", "revision", "generation", "error"} {
				entry[key] = app[key]
			}
			entry["node_id"], entry["node_name"], entry["node_status"], entry["kind"] = record["node_id"], name, status, "service"
			instances = append(instances, entry)
		}
	}
	return map[string]any{"success": true, "nodes": nodes, "instances": instances, "observed_at": now.UTC().Format(time.RFC3339Nano)}
}

func orList(v any) []any {
	if list, ok := v.([]any); ok {
		return list
	}
	return []any{}
}

func truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case map[string]any:
		return len(t) > 0
	case []any:
		return len(t) > 0
	case float64:
		return t != 0
	}
	return true
}

func node(inv map[string]any, id string) map[string]any {
	for _, n := range inv["nodes"].([]map[string]any) {
		if n["node_id"] == id {
			return n
		}
	}
	return nil
}

func online(n map[string]any) bool {
	return n["status"] == "online" || n["status"] == "busy"
}

func runtime(n map[string]any, name string) string {
	runtimes, _ := n["runtimes"].(map[string]any)
	return str(runtimes[name])
}

// withDesktopApps adds the Apps each healthy desktop reports it runs
// (formerly inventory_from_records).
func withDesktopApps(ctx context.Context, bus Bus, fleet string, result map[string]any) map[string]any {
	instances := result["instances"].([]map[string]any)
	var desktops []map[string]any
	for _, i := range instances {
		if i["app_id"] == "desktop" && (i["node_status"] == "online" || i["node_status"] == "busy") && i["health"] == "healthy" {
			desktops = append(desktops, i)
		}
	}
	type answer struct {
		apps []map[string]any
		warn map[string]any
	}
	answers := make([]answer, len(desktops))
	var wg sync.WaitGroup
	for k, d := range desktops {
		wg.Add(1)
		go func(k int, d map[string]any) {
			defer wg.Done()
			c, cancel := context.WithTimeout(ctx, 4*time.Second)
			defer cancel()
			apps, err := desktopApps(c, bus, fleet, d, result)
			if err != nil {
				answers[k].warn = map[string]any{"node_id": d["node_id"], "error": err.Error()}
				return
			}
			answers[k].apps = apps
		}(k, d)
	}
	wg.Wait()
	for _, a := range answers {
		if a.warn != nil {
			warnings, _ := result["warnings"].([]map[string]any)
			result["warnings"] = append(warnings, a.warn)
		}
		instances = append(instances, a.apps...)
	}
	result["instances"] = instances
	return result
}

func desktopApps(ctx context.Context, bus Bus, fleet string, d map[string]any, inv map[string]any) ([]map[string]any, error) {
	envelope, err := lifecycle(ctx, bus, fleet, str(d["node_id"]), "invoke", map[string]any{
		"app_id": "desktop", "instance_id": d["instance_id"], "revision": d["revision"], "generation": d["generation"],
		"timeout_seconds": 4, "payload": map[string]any{"method": "fleet_instances", "args": map[string]any{}, "timeout_s": 4},
	})
	if err != nil {
		return nil, err
	}
	response, _ := envelope["response"].(map[string]any)
	if response == nil || response["success"] != true {
		return nil, fmt.Errorf("Desktop inventory unavailable")
	}
	reply, _ := response["result"].(map[string]any)
	if reply == nil || reply["success"] != true {
		reason := str(field(reply, "error"))
		if reason == "" {
			reason = "Desktop inventory unavailable"
		}
		return nil, fmt.Errorf("%s", reason)
	}
	var apps []map[string]any
	for _, item := range orList(reply["instances"]) {
		app, ok := item.(map[string]any)
		if !ok {
			continue
		}
		out := map[string]any{}
		for k, v := range app {
			out[k] = v
		}
		target := str(app["backend_node_id"])
		if target == "" {
			target = str(d["node_id"])
		}
		out["node_id"] = target
		if n := node(inv, target); n != nil {
			out["node_name"], out["node_status"] = n["name"], n["status"]
		} else {
			out["node_name"], out["node_status"] = app["backend_node_id"], "offline"
		}
		apps = append(apps, out)
	}
	return apps, nil
}

var _ = json.Valid
