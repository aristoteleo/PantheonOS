package pantheoncli

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// An agent hands a task to a coding agent on a node: the command starts the
// turn, follows the conversation until it ends and prints the reply.
func TestCodingAgentRunFollowsTheTurn(t *testing.T) {
	var requests []map[string]any
	polls := 0
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var got map[string]any
		json.NewDecoder(r.Body).Decode(&got)
		request := got["args"].(map[string]any)["request"].(map[string]any)
		requests = append(requests, request)
		params, _ := request["params"].(map[string]any)
		var result any
		switch {
		case request["op"] == "nodes.list":
			result = map[string]any{"success": true, "nodes": []any{map[string]any{"node_id": "n_mac", "name": "mac.local"}}}
		case params["method"] == "agent_send":
			result = map[string]any{"success": true, "conversation_id": "c1", "from_seq": 1}
		case params["method"] == "agent_conversation" && polls == 0:
			polls++
			result = map[string]any{"success": true, "conversation": map[string]any{"status": "running", "agent": "claude"},
				"events": []any{map[string]any{"seq": 1, "type": "user", "text": "fix it"},
					map[string]any{"seq": 2, "type": "tool", "status": "running", "name": "Bash", "input": "go test"}}}
		default:
			polls++
			if params["args"].(map[string]any)["after"] != float64(2) {
				t.Errorf("second poll must continue after seq 2: %v", params)
			}
			result = map[string]any{"success": true, "conversation": map[string]any{"status": "idle", "agent": "claude"},
				"events": []any{map[string]any{"seq": 3, "type": "text", "text": "All tests pass."},
					map[string]any{"seq": 4, "type": "done"}}}
		}
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": map[string]any{"success": true, "result": result}})
	}))
	defer server.Close()
	testTransport = server.Client().Transport
	defer func() { testTransport = nil }()
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_ENDPOINT", server.URL+"/rpc")
	t.Setenv("PANTHEON_GRANT", "grant-1")
	t.Setenv("PANTHEON_TOKEN", "tok")

	out, errOut, code := run(t, "coding-agent", "run", "--node", "mac", "--agent", "claude", "--cwd", "/repo", "--", "fix", "it")
	if code != 0 || !strings.Contains(out, "All tests pass.") || !strings.Contains(errOut, "→ Bash go test") ||
		!strings.Contains(errOut, "--conversation c1") {
		t.Fatalf("%d\n%s\n%s", code, out, errOut)
	}
	send := requests[1]["params"].(map[string]any)
	args := send["args"].(map[string]any)
	if requests[1]["op"] != "coding_agent" || send["node_id"] != "n_mac" || args["prompt"] != "fix it" ||
		args["agent"] != "claude" || args["cwd"] != "/repo" || args["auto"] != true || args["origin"] != "agent" {
		t.Fatalf("send %#v", requests[1])
	}
}
