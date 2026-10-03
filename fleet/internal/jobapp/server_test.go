package jobapp

import (
	"archive/tar"
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func fixtureArtifact(t *testing.T) ([]byte, string) {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 required")
	}
	definition := map[string]any{"protocol": 1, "app_id": "ordinary-test", "version": "1.0.0",
		"requires": map[string]any{"os": []string{runtime.GOOS}, "caps": []string{"proc"}},
		"components": []any{map[string]any{"name": "backend", "runtime": "process",
			"argv": []string{"python3", "${PACKAGE}/server.py"}, "ports": map[string]int{"http": 0}, "stop_seconds": 1,
			"readiness": map[string]any{"argv": []string{"python3", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'])"}, "timeout_seconds": 10}}}}
	manifest, _ := json.Marshal(definition)
	files := map[string][]byte{"fleet.json": manifest, "app.json": []byte(`{"id":"ordinary-test","version":"1.0.0"}`), "server.py": []byte(`
import json,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  data=b'ORDINARY_APP';self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  reply=json.dumps({'echo':data,'pid':os.getpid()}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(reply)));self.end_headers();self.wfile.write(reply)
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
`)}
	var buffer bytes.Buffer
	archive := tar.NewWriter(&buffer)
	for name, data := range files {
		if err := archive.WriteHeader(&tar.Header{Name: name, Size: int64(len(data)), Mode: 0400}); err != nil {
			t.Fatal(err)
		}
		if _, err := archive.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(buffer.Bytes())
	return buffer.Bytes(), hex.EncodeToString(hash[:])
}

func TestOrdinaryPackageLifecycleRPCAndBoundStreams(t *testing.T) {
	payload, digest := fixtureArtifact(t)
	m, err := lifecycle.Open(t.TempDir(), "owner", "job-node", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, lifecycle.NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	handler, err := New(m, strings.Repeat("a", 64))
	if err != nil {
		t.Fatal(err)
	}
	defer handler.Close()
	server := httptest.NewServer(handler)
	defer server.Close()
	call := func(q lifecycle.Command, authorized bool) map[string]json.RawMessage {
		t.Helper()
		body, _ := json.Marshal(q)
		req, _ := http.NewRequest("POST", server.URL+"/control", bytes.NewReader(body))
		if authorized {
			req.Header.Set("Authorization", "Bearer "+handler.Token)
		}
		response, e := http.DefaultClient.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer response.Body.Close()
		if !authorized {
			if response.StatusCode != 403 {
				t.Fatal("missing authorization accepted")
			}
			return nil
		}
		var result map[string]json.RawMessage
		if e = json.NewDecoder(response.Body).Decode(&result); e != nil {
			t.Fatal(e)
		}
		return result
	}
	call(lifecycle.Command{Protocol: 1, Method: "status"}, false)
	for offset := 0; offset < len(payload); offset += lifecycle.MaxChunk {
		end := min(offset+lifecycle.MaxChunk, len(payload))
		out := call(lifecycle.Command{Protocol: 1, Method: "stage", Digest: digest, Offset: int64(offset), Data: payload[offset:end]}, true)
		if out["error"] != nil {
			t.Fatal(string(out["error"]))
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	id, err := StartInitial(ctx, m, digest, "app")
	if err != nil {
		t.Fatal(err)
	}
	in := m.Snapshot().Instances[id]
	pid := in.Resources[0].PID
	defer func() {
		current := m.Snapshot().Instances[id]
		if current != nil && current.State != "stopped" {
			_, _ = m.Submit(lifecycle.Request{Protocol: 1, OperationID: "cleanup", Action: "stop", Digest: digest, Scope: "app", Generation: current.Generation})
			for end := time.Now().Add(5 * time.Second); time.Now().Before(end); {
				if m.Snapshot().Instances[id].State == "stopped" {
					break
				}
				time.Sleep(20 * time.Millisecond)
			}
		}
	}()
	command := lifecycle.Command{Protocol: 1, Method: "invoke", AppID: in.AppID, Instance: id, Revision: digest, Generation: in.Generation, Timeout: 5, Payload: json.RawMessage(`{"method":"echo","args":{"value":"same-package"}}`)}
	if out := call(lifecycle.Command{Protocol: 1, Method: "check_instance", Instance: id, Revision: digest, Generation: in.Generation}, true); string(out["ok"]) != "true" {
		t.Fatal("job consumer identity unavailable", out)
	}
	result := call(command, true)
	if result["error"] != nil || !bytes.Contains(result["response"], []byte("same-package")) {
		t.Fatal(result)
	}
	command.Generation++
	if out := call(command, true); out["error"] == nil {
		t.Fatal("stale RPC generation accepted")
	}
	// Opening and closing the transport never restarts the application.
	for i := 0; i < 2; i++ {
		conn, e := net.DialTimeout("tcp", strings.TrimPrefix(server.URL, "http://"), time.Second)
		if e != nil {
			t.Fatal(e)
		}
		req, _ := http.NewRequest("CONNECT", server.URL+"/service", nil)
		req.URL.Opaque = "/service"
		req.Header.Set("Authorization", "Bearer "+handler.Token)
		for k, v := range map[string]string{"Instance": id, "Revision": digest, "Generation": fmt.Sprint(in.Generation), "Component": "backend", "Port": "http"} {
			req.Header.Set("X-App-"+k, v)
		}
		_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
		if e = req.Write(conn); e != nil {
			t.Fatal(e)
		}
		reader := bufio.NewReader(conn)
		response, e := http.ReadResponse(reader, req)
		if e != nil || response.StatusCode != 200 {
			t.Fatal(response, e)
		}
		_, _ = fmt.Fprint(conn, "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
		response, e = http.ReadResponse(reader, nil)
		if e != nil {
			t.Fatal(e)
		}
		body, e := io.ReadAll(response.Body)
		response.Body.Close()
		conn.Close()
		if e != nil || string(body) != "ORDINARY_APP" {
			t.Fatal(string(body), e)
		}
		if m.Snapshot().Instances[id].Resources[0].PID != pid {
			t.Fatal("transport reconnect restarted the App")
		}
	}

	// Long-lived streams must not occupy lifecycle control slots.
	for i := 0; i < cap(handler.StreamSlots); i++ {
		conn, err := net.DialTimeout("tcp", strings.TrimPrefix(server.URL, "http://"), time.Second)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		req, _ := http.NewRequest("CONNECT", server.URL+"/service", nil)
		req.URL.Opaque = "/service"
		req.Header.Set("Authorization", "Bearer "+handler.Token)
		for k, v := range map[string]string{"Instance": id, "Revision": digest, "Generation": fmt.Sprint(in.Generation), "Component": "backend", "Port": "http"} {
			req.Header.Set("X-App-"+k, v)
		}
		_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
		if err := req.Write(conn); err != nil {
			t.Fatal(err)
		}
		response, err := http.ReadResponse(bufio.NewReader(conn), req)
		if err != nil || response.StatusCode != 200 {
			t.Fatal(response, err)
		}
	}
	if status := call(lifecycle.Command{Protocol: 1, Method: "status"}, true); status["instances"] == nil {
		t.Fatal("streams blocked lifecycle status")
	}
	stop := call(lifecycle.Command{Protocol: 1, Method: "submit", Request: &lifecycle.Request{Protocol: 1, OperationID: "stop-through-standard-protocol", Action: "stop", Digest: digest, Scope: "app", Generation: in.Generation}}, true)
	if stop["error"] != nil {
		t.Fatal(string(stop["error"]))
	}
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end); {
		if m.Snapshot().Instances[id].State == "stopped" {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if m.Snapshot().Instances[id].State != "stopped" {
		t.Fatal("ordinary stop did not complete")
	}
	if _, err = m.Service(id, digest, in.Generation, "backend", "http"); err == nil {
		t.Fatal("stopped App remains accessible")
	}
}
