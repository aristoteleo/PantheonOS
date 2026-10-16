package pantheoncli

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func tarball(t *testing.T, files map[string]string) []byte {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for name, body := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		tw.Write([]byte(body))
	}
	tw.Close()
	gz.Close()
	return buf.Bytes()
}

// The Agent serves its own terminal chat; the command keeps it per revision
// and serves the chat's RPC on loopback with the owner's grant.
func TestAgentChatFetchesItsTerminalAndProxiesItsCalls(t *testing.T) {
	bundle := tarball(t, map[string]string{"terminal.py": "print('hi')", "requirements.txt": "six==1",
		"app.json": "{}", "backend/_vendor/pantheon/__init__.py": ""})
	sum := sha256.Sum256(bundle)
	digest := hex.EncodeToString(sum[:])
	fetches := 0
	var server *httptest.Server
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/fleet/apps/workload-identity", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"fleet_id": "f_test", "controller_url": server.URL})
	})
	mux.HandleFunc("POST /api/fleet/apps/workload-connect", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"origin": server.URL, "access_token": "grant-agent",
			"expires": time.Now().Add(time.Hour).Unix()})
	})
	mux.HandleFunc("POST /rpc", func(w http.ResponseWriter, r *http.Request) {
		var call struct {
			Method string         `json:"method"`
			Args   map[string]any `json:"args"`
		}
		json.NewDecoder(r.Body).Decode(&call)
		var result any
		switch call.Method {
		case "agent_terminal_frontend":
			fetches++
			offset := int(call.Args["offset"].(float64))
			end := min(offset+40, len(bundle))
			result = map[string]any{"sha256": digest, "size": len(bundle), "offset": offset,
				"data": base64.StdEncoding.EncodeToString(bundle[offset:end])}
		case "list_chats":
			result = map[string]any{"success": true, "chats": []any{}}
		}
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": result})
	})
	server = httptest.NewTLSServer(mux)
	defer server.Close()
	testTransport = server.Client().Transport
	defer func() { testTransport = nil }()
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_KEY", "")
	t.Setenv("PANTHEON_HUB_URL", "")
	if out, errOut, code := run(t, "login", "--hub", server.URL, "--key", "pbk_test"); code != 0 {
		t.Fatalf("login: %s %s", out, errOut)
	}
	creds, err := LoadCredentials()
	if err != nil {
		t.Fatal(err)
	}
	s, err := NewSession(creds)
	if err != nil {
		t.Fatal(err)
	}
	var progress []string
	r := &runner{ctx: context.Background(), s: s, stderr: &bytes.Buffer{}}
	b := Binding{NodeID: "brain", InstanceID: "agent", Revision: strings.Repeat("b", 64), Generation: 1,
		Component: "backend", Port: "http"}
	dir := filepath.Join(t.TempDir(), "terminals", b.Revision)
	if err := r.fetchTerminal(b, dir, func(s string) { progress = append(progress, s) }); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "terminal.py")); string(got) != "print('hi')" || fetches < 2 {
		t.Fatalf("%q after %d reads", got, fetches)
	}
	// Kept per revision: not fetched again.
	before := fetches
	if err := r.fetchTerminal(b, dir, nil); err != nil || fetches != before {
		t.Fatal(err, fetches)
	}

	proxy := httptest.NewServer(r.agentProxy(b, "local-token"))
	defer proxy.Close()
	post := func(token string) (*http.Response, map[string]any) {
		req, _ := http.NewRequest(http.MethodPost, proxy.URL+"/rpc",
			strings.NewReader(`{"method":"list_chats","args":{},"timeout_s":30}`))
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		resp.Body.Close()
		return resp, body
	}
	if resp, _ := post("someone-else"); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("another local process reached the Agent: %d", resp.StatusCode)
	}
	if resp, body := post("local-token"); resp.StatusCode != 200 || body["success"] != true ||
		body["result"].(map[string]any)["chats"] == nil {
		t.Fatalf("%d %#v", resp.StatusCode, body)
	}
}

func TestATerminalArchiveCannotWriteOutsideItsFolder(t *testing.T) {
	for _, name := range []string{"../escape.py", "/etc/escape.py", "a/../../escape.py"} {
		if err := untar(tarball(t, map[string]string{name: "x"}), t.TempDir()); err == nil {
			t.Fatalf("%s was extracted", name)
		}
	}
}
