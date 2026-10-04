package main

// Opt-in paired release acceptance. Real native Managers, authenticated NATS,
// packaged owner/consumer processes and the production dependency gateway.
// Only Hub's directory/auth wrapper, DNS routing and inference output are fixtures.
import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/gorilla/websocket"
	"github.com/nats-io/nats.go"
)

type agentDeploymentFixture struct {
	root, shim, cert string
	binary           string
	mu               sync.RWMutex
	tunnel           string
	tlsConfig        *tls.Config
}

func newAgentDeploymentFixture(t *testing.T, root string) *agentDeploymentFixture {
	if os.Getenv("FLEET_TEST_AGENT_DEPLOYMENT") != "1" {
		return nil
	}
	for _, key := range []string{"FLEET_TEST_PYTHON", "AGENT_RELEASE_TRANSPORT", "AGENT_APP_BUILD_DIR"} {
		if os.Getenv(key) == "" {
			t.Fatalf("native release acceptance requires %s", key)
		}
	}
	f := &agentDeploymentFixture{root: root, shim: filepath.Join(root, "test-dns"), cert: filepath.Join(root, "test-ca.pem")}
	if err := os.MkdirAll(f.shim, 0700); err != nil {
		t.Fatal(err)
	}
	// The supervisor normally supplies its own Fleet binary for the local vault
	// pipe. A Go test executable cannot implement that command; build the real
	// binary rather than replacing the credential reader with a fixture.
	f.binary = filepath.Join(root, "fleet-credential-reader")
	build := exec.Command("go", "build", "-o", f.binary, "./cmd/fleet")
	build.Dir = filepath.Join("..", "..")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build Fleet credential reader: %v: %s", err, output)
	}
	// Test-only routing at process startup, not a product TLS bypass. Certificate
	// verification still checks the issued hostname and fixture trust root.
	source := `import json, pathlib, socket
original = socket.getaddrinfo
routing = pathlib.Path(__file__).with_name('routing.json')
def mapped(host, port, *args, **kwargs):
 if isinstance(host,bytes): host=host.decode('ascii')
 if isinstance(host,str) and host.endswith('.apps.test') and routing.exists():
  port=json.loads(routing.read_text())['port']; host='127.0.0.1'
 return original(host,port,*args,**kwargs)
socket.getaddrinfo=mapped
# AnyIO retains the requested port after resolving addresses; route that socket
# too. This affects only loopback:443 in these isolated fixture processes.
for method in ('connect','connect_ex'):
 original_connect=getattr(socket.socket,method)
 def connect(self,address,_original=original_connect):
  if isinstance(address,tuple) and address[:2]==('127.0.0.1',443) and routing.exists():
   address=('127.0.0.1',json.loads(routing.read_text())['port'])
  return _original(self,address)
 setattr(socket.socket,method,connect)
`
	if err := os.WriteFile(filepath.Join(f.shim, "sitecustomize.py"), []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	return f
}
func (f *agentDeploymentFixture) environment() []string {
	if f == nil {
		return nil
	}
	env := []string{"PYTHONPATH=" + f.shim, "SSL_CERT_FILE=" + f.cert, "PANTHEON_FLEET_EXECUTABLE=" + f.binary}
	if cache := os.Getenv("FLEET_TEST_AGENT_CACHE"); cache != "" {
		env = append(env, "PANTHEON_PYTHON_CACHE="+cache)
	}
	return env
}
func (f *agentDeploymentFixture) service(m *lifecycle.Manager, msg *nats.Msg, calls *sync.WaitGroup) bool {
	if f == nil {
		return false
	}
	var q struct {
		Type       string `json:"type"`
		Instance   string `json:"instance_id"`
		Revision   string `json:"revision"`
		Generation uint64 `json:"generation"`
		Component  string `json:"component"`
		Port       string `json:"port"`
		Stream     string `json:"stream"`
		Secret     string `json:"secret"`
	}
	if json.Unmarshal(msg.Data, &q) != nil || q.Type != "app_service" {
		return false
	}
	go func() {
		defer calls.Done()
		fail := func() { _ = msg.Respond([]byte(`{"error":"native fixture service unavailable"}`)) }
		endpoint, err := m.Service(q.Instance, q.Revision, q.Generation, q.Component, q.Port)
		if err != nil {
			fail()
			return
		}
		release, err := m.BeginUse(q.Instance, q.Revision, q.Generation)
		if err != nil {
			fail()
			return
		}
		defer release()
		u, _ := url.Parse(endpoint)
		conn, err := net.DialTimeout("tcp", u.Host, 5*time.Second)
		if err != nil {
			fail()
			return
		}
		defer conn.Close()
		f.mu.RLock()
		origin, tc := f.tunnel, f.tlsConfig
		f.mu.RUnlock()
		ws, response, err := (&websocket.Dialer{TLSClientConfig: tc, HandshakeTimeout: 5 * time.Second}).Dial(origin+"/apps/tunnel/"+q.Stream, http.Header{"Authorization": {"Bearer " + q.Secret}})
		if err != nil {
			if response != nil && response.Body != nil {
				response.Body.Close()
			}
			fail()
			return
		}
		defer ws.Close()
		_ = msg.Respond([]byte(`{"ok":true}`))
		apptransport.Relay(apptransport.New(ws), conn)
	}()
	return true
}
func (f *agentDeploymentFixture) run(t *testing.T, owner, address string, authority *auth.Authority, g *appgateway.Gateway, key string) {
	creds, err := authority.MintFleetUser(owner)
	if err != nil {
		t.Fatal(err)
	}
	nc, err := nats.Connect("nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix("_INBOX_"+owner))
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	private, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Native Agent test"}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), DNSNames: []string{"*.apps.test"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, cert, cert, &private.PublicKey, private)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	if err = os.WriteFile(f.cert, certPEM, 0600); err != nil {
		t.Fatal(err)
	}
	pair, err := tls.X509KeyPair(certPEM, pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(private)}))
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AppendCertsFromPEM(certPEM)
	mux := http.NewServeMux()
	g.Register(mux)
	var directory json.RawMessage = []byte(`{"deployments":[]}`)
	var directoryMu sync.RWMutex
	var joins atomic.Int32
	var inference atomic.Int32
	var startup json.RawMessage
	var startupReads atomic.Int32
	var budgetReads atomic.Int32
	var budgetEndpoint atomic.Value
	mux.HandleFunc("/api/users/me/llm-proxy", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.Header.Get("Authorization") != "Bearer "+key+"-owner-login" {
			w.WriteHeader(403)
			return
		}
		budgetReads.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]string{"fleet_id": owner,
			"api_base_url": budgetEndpoint.Load().(string), "model_mode": "direct", "virtual_key": key + "-budget"})
	})
	mux.HandleFunc("/fixture/startup", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		directoryMu.Lock()
		defer directoryMu.Unlock()
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 64*1024)).Decode(&startup) != nil {
			w.WriteHeader(400)
			return
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	})
	mux.HandleFunc("/api/fleet/apps/startup/default", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		startupReads.Add(1)
		directoryMu.RLock()
		defer directoryMu.RUnlock()
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_ = json.NewEncoder(w).Encode(map[string]any{"protocol": 1, "revision": 1, "recipe": startup})
	})
	mux.HandleFunc("/controller/join", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		if json.NewDecoder(r.Body).Decode(&body) != nil || body["key"] != key {
			w.WriteHeader(403)
			return
		}
		joins.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]string{"fleet_id": owner, "nats_url": "nats://" + address, "creds": string(creds)})
	})
	mux.HandleFunc("/hub/api/model-services", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		directoryMu.RLock()
		defer directoryMu.RUnlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(directory)
	})
	// Owner registration uses the public directory contract, including create CAS.
	mux.HandleFunc("/hub/api/model-services/native-model", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "PUT" || r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		var row map[string]any
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 64*1024)).Decode(&row) != nil || row["deployment_id"] != "native-model" || row["revision"] != float64(0) {
			w.WriteHeader(400)
			return
		}
		directoryMu.Lock()
		defer directoryMu.Unlock()
		var listing struct {
			Deployments []json.RawMessage `json:"deployments"`
		}
		if json.Unmarshal(directory, &listing) != nil || len(listing.Deployments) != 0 {
			w.WriteHeader(409)
			return
		}
		row["revision"] = 1
		directory, _ = json.Marshal(map[string]any{"deployments": []any{row}})
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(row)
	})
	mux.HandleFunc("/hub/api/model-services/routes", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		_, _ = w.Write([]byte(`{"routes":[]}`))
	})
	mux.HandleFunc("/hub/api/fleet/apps/", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		path := strings.TrimPrefix(r.URL.Path, "/hub/api/fleet/apps/")
		if path == "workload-identity" && r.Method == "GET" {
			w.Header().Set("Cache-Control", "no-store")
			_ = json.NewEncoder(w).Encode(map[string]any{"protocol": 1, "fleet_id": owner,
				"controller_url": "https://" + r.Host + "/controller"})
			return
		}
		target := "/apps/dependencies"
		if strings.HasPrefix(path, "dependency-grants/") && (r.Method == "DELETE" || r.Method == "PATCH") {
			body := map[string]any{"fleet_id": owner, "grant_id": strings.TrimPrefix(path, "dependency-grants/")}
			if r.Method == "PATCH" {
				var renewal struct {
					TTL int64 `json:"ttl_seconds"`
				}
				if json.NewDecoder(r.Body).Decode(&renewal) != nil {
					w.WriteHeader(400)
					return
				}
				body["expires"] = time.Now().Unix() + renewal.TTL
			}
			raw, _ := json.Marshal(body)
			req := httptest.NewRequest(r.Method, "http://controller.test"+target, bytes.NewReader(raw))
			req.Header.Set("Authorization", "Bearer "+key)
			record := httptest.NewRecorder()
			mux.ServeHTTP(record, req)
			w.WriteHeader(record.Code)
			_, _ = w.Write(record.Body.Bytes())
			return
		}
		// Both Hub contracts map to the same gateway issuance endpoint.
		if path != "dependency-http-grants" && path != "dependency-grants" {
			w.WriteHeader(404)
			return
		}
		var body map[string]any
		if json.NewDecoder(io.LimitReader(r.Body, 64<<10)).Decode(&body) != nil {
			w.WriteHeader(400)
			return
		}
		consumer, cok := body["consumer"].(map[string]any)
		provider, pok := body["provider"].(map[string]any)
		ttl, tok := body["ttl_seconds"].(float64)
		if !cok || !pok || !tok || r.Method != "POST" {
			w.WriteHeader(400)
			return
		}
		if path == "dependency-http-grants" {
			directoryMu.RLock()
			var listing struct {
				Deployments []struct {
					ConfigRevision string `json:"config_revision"`
				}
			}
			_ = json.Unmarshal(directory, &listing)
			directoryMu.RUnlock()
			if len(listing.Deployments) != 1 {
				w.WriteHeader(409)
				return
			}
			body["http"] = map[string]any{"rules": body["rules"], "headers": map[string]string{"X-Model-Config": listing.Deployments[0].ConfigRevision}, "credential": strings.Repeat("test-model-key", 4)}
			delete(body, "rules")
			body["timeout_seconds"] = 60
		}
		consumer["fleet_id"] = owner
		provider["fleet_id"] = owner
		body["expires"] = time.Now().Unix() + int64(ttl)
		delete(body, "ttl_seconds")
		raw, _ := json.Marshal(body)
		req := httptest.NewRequest("POST", "http://controller.test"+target, bytes.NewReader(raw))
		req.Header.Set("Authorization", "Bearer "+key)
		record := httptest.NewRecorder()
		mux.ServeHTTP(record, req)
		if record.Code != 200 {
			w.WriteHeader(record.Code)
			_, _ = w.Write(record.Body.Bytes())
			return
		}
		var result map[string]any
		_ = json.Unmarshal(record.Body.Bytes(), &result)
		result["consumer"] = consumer
		result["provider"] = provider
		_ = json.NewEncoder(w).Encode(result)
	})
	// This wrapper replaces Hub authentication/directory only. Commands and grant
	// admission below remain real Controller/NATS/Manager/gateway operations.
	mux.HandleFunc("/fixture/", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+key {
			w.WriteHeader(403)
			return
		}
		path := strings.TrimPrefix(r.URL.Path, "/fixture/")
		raw, err := io.ReadAll(io.LimitReader(r.Body, 400<<10))
		if err != nil {
			w.WriteHeader(400)
			return
		}
		if strings.HasPrefix(path, "node/") {
			node := strings.TrimPrefix(path, "node/")
			if node != "consumer-node" && node != "provider-node" {
				w.WriteHeader(400)
				return
			}
			reply, err := nc.Request(proto.SubjNodeCmd(owner, node), raw, 90*time.Second)
			if err != nil {
				http.Error(w, err.Error(), 503)
				return
			}
			_, _ = w.Write(reply.Data)
			return
		}

		w.WriteHeader(404)
	})
	server := httptest.NewUnstartedServer(g.Handler(mux))
	server.TLS = &tls.Config{Certificates: []tls.Certificate{pair}, MinVersion: tls.VersionTLS12}
	server.StartTLS()
	defer server.Close()
	f.mu.Lock()
	f.tunnel = "wss" + strings.TrimPrefix(server.URL, "https")
	f.tlsConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	f.mu.Unlock()
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(server.URL, "https://"))
	routing := fmt.Sprintf(`{"port":%s}`, port)
	if err := os.WriteFile(filepath.Join(f.shim, "routing.json"), []byte(routing), 0600); err != nil {
		t.Fatal(err)
	}
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+key+"-budget" {
			w.WriteHeader(403)
			return
		}
		switch r.URL.Path {
		case "/v1/models":
			_, _ = w.Write([]byte(`{"data":[{"id":"example:8b","capabilities":["completion","tools"],"context_length":8192}]}`))
		case "/api/show":
			_, _ = w.Write([]byte(`{"capabilities":["completion","tools"],"model_info":{"general.architecture":"llama","llama.context_length":8192}}`))
		case "/v1/chat/completions":
			round := inference.Add(1)
			var request struct {
				Messages []struct {
					Role    string          `json:"role"`
					Content json.RawMessage `json:"content"`
				} `json:"messages"`
				Tools []struct {
					Function struct {
						Name string `json:"name"`
					} `json:"function"`
				} `json:"tools"`
			}
			if json.NewDecoder(r.Body).Decode(&request) != nil {
				w.WriteHeader(400)
				return
			}
			if round > 27 {
				http.Error(w, "unexpected extra inference round", 400)
				return
			}
			lastUser, lastTool := -1, -1
			for i, message := range request.Messages {
				if message.Role == "user" {
					lastUser = i
				}
				if message.Role == "tool" {
					lastTool = i
				}
			}
			delta := map[string]any{"content": "native fleet reply"}
			reason := "stop"
			if lastTool > lastUser {
				var content string
				if json.Unmarshal(request.Messages[lastTool].Content, &content) != nil {
					w.WriteHeader(400)
					return
				}
				delta["content"] = "native tool result: " + content
			} else if lastUser >= 0 && strings.Contains(string(request.Messages[lastUser].Content), "NATIVE_SHELL_") {
				name := ""
				for _, tool := range request.Tools {
					if tool.Function.Name == "shell__run_command_in_shell" {
						name = tool.Function.Name
					}
				}
				if name == "" {
					w.WriteHeader(400)
					return
				}
				command := `printf 'SHELL_VALUE=%s\n' "${NATIVE_SHELL_OWNER-unset}"`
				if strings.Contains(string(request.Messages[lastUser].Content), "NATIVE_SHELL_SET") {
					command = "export NATIVE_SHELL_OWNER=owner-a; " + command
				}
				arguments, _ := json.Marshal(map[string]string{"command": command})
				delta = map[string]any{"tool_calls": []any{map[string]any{"index": 0, "id": fmt.Sprintf("native-shell-call-%d", round), "type": "function",
					"function": map[string]string{"name": name, "arguments": string(arguments)}}}}
				reason = "tool_calls"
			} else if lastUser >= 0 && strings.Contains(string(request.Messages[lastUser].Content), "NATIVE_MCP_CHECK") {
				found := false
				for _, tool := range request.Tools {
					if tool.Function.Name == "mcp__docs_check" {
						found = true
					}
				}
				if !found {
					http.Error(w, "MCP tool missing", 400)
					return
				}
				delta = map[string]any{"tool_calls": []any{map[string]any{"index": 0, "id": fmt.Sprintf("native-mcp-call-%d", round), "type": "function",
					"function": map[string]string{"name": "mcp__docs_check", "arguments": "{}"}}}}
				reason = "tool_calls"
			} else if lastUser >= 0 && strings.Contains(string(request.Messages[lastUser].Content), "NATIVE_FILES_") {
				name := "file_manager__read_file"
				args := map[string]string{}
				if strings.Contains(string(request.Messages[lastUser].Content), "NATIVE_FILES_WRITE") {
					name = "file_manager__write_file"
					args["content"] = "shared-by-owner-a"
				}
				found := false
				for _, tool := range request.Tools {
					if tool.Function.Name == name {
						found = true
					}
				}
				if !found {
					http.Error(w, "Files tool missing", 400)
					return
				}
				arguments, _ := json.Marshal(args)
				delta = map[string]any{"tool_calls": []any{map[string]any{"index": 0, "id": fmt.Sprintf("native-files-call-%d", round), "type": "function",
					"function": map[string]string{"name": name, "arguments": string(arguments)}}}}
				reason = "tool_calls"
			}
			w.Header().Set("Content-Type", "text/event-stream")
			chunk, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": reason}}})
			_, _ = fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", chunk)
		default:
			w.WriteHeader(404)
		}
	}))
	defer engine.Close()
	budgetEndpoint.Store(engine.URL + "/v1")
	repo, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Getenv("FLEET_TEST_PYTHON"), filepath.Join(repo, "fleet/scripts/verify-agent-deployment.py"), server.URL, key, owner, engine.URL, filepath.Join(f.root, "agent-acceptance"))
	cmd.Dir = repo
	cmd.Env = append(os.Environ(), "PYTHONPATH="+repo+string(os.PathListSeparator)+f.shim, "SSL_CERT_FILE="+f.cert)
	output, err := cmd.CombinedOutput()
	t.Log(string(output))
	if err != nil {
		_ = filepath.WalkDir(f.root, func(path string, entry os.DirEntry, walkErr error) error {
			if walkErr == nil && !entry.IsDir() && strings.HasSuffix(path, ".log") {
				data, _ := os.ReadFile(path)
				if len(data) > 6000 {
					data = data[len(data)-6000:]
				}
				t.Log(filepath.Base(path), string(data))
			}
			return nil
		})
		t.Fatal("native Agent deployment:", err)
	}
	if budgetReads.Load() != 1 {
		t.Fatalf("expected one owner budget acquisition across startup polls and replay, got %d", budgetReads.Load())
	}
	if startupReads.Load() != 1 {
		t.Fatalf("expected one authenticated startup read, got %d", startupReads.Load())
	}
	// First delivery, idempotent replay and conflict probe each open a separate
	// provisioning connection; original and restarted allocators each join once.
	if joins.Load() != 5 || inference.Load() != 27 {
		t.Fatalf("expected three provisioning joins, two allocator joins and twenty-seven inference rounds (thirteen real tool calls), got %d/%d", joins.Load(), inference.Load())
	}
}
