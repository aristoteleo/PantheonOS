package apptransport

import (
	"bytes"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// A close-delimited body relayed from a node must reach the gateway as data
// followed by a clean EOF, not a connection error.
func TestRelayedCloseDelimitedBodyEndsWithEOF(t *testing.T) {
	body := bytes.Repeat([]byte("embedding-bytes-"), 20000) // several WebSocket chunks
	local, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer local.Close()
	go func() {
		c, err := local.Accept()
		if err != nil {
			return
		}
		_, _ = c.Write(body)
		_ = c.Close() // HTTP/1.0 style: the close ends the body
	}()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			return
		}
		app, err := net.Dial("tcp", local.Addr().String())
		if err != nil {
			return
		}
		Relay(New(ws), app) // node side
	}))
	defer server.Close()
	ws, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	gateway := New(ws)
	defer gateway.Close()
	_ = gateway.SetReadDeadline(time.Now().Add(5 * time.Second))
	got, err := io.ReadAll(gateway) // ReadAll treats io.EOF as success
	if err != nil {
		t.Fatalf("relayed body ended with an error instead of EOF: %v", err)
	}
	if !bytes.Equal(got, body) {
		t.Fatalf("relayed %d of %d bytes", len(got), len(body))
	}
}
