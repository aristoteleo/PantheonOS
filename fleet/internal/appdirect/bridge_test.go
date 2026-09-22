package appdirect

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestBridgeBootstrapAndParentDisconnect(t *testing.T) {
	for _, mode := range []string{"before-grant", "idle-http", "invalid-grant", "oversized-grant"} {
		t.Run(mode, func(t *testing.T) {
			f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("no HTTP authorized by this test") }))
			ctx, cancel := context.WithCancel(f.ctx)
			defer cancel()
			input, writer := io.Pipe()
			reader, output := io.Pipe()
			defer writer.Close()
			defer reader.Close()
			ended := make(chan error, 1)
			go func() { ended <- RunBridge(ctx, input, output) }()
			buffered := bufio.NewReader(reader)
			line, err := buffered.ReadBytes('\n')
			if err != nil {
				t.Fatal(err)
			}
			var hello struct {
				Protocol int    `json:"protocol"`
				Peer     string `json:"peer_id"`
			}
			if json.Unmarshal(line, &hello) != nil || hello.Protocol != 1 || hello.Peer == "" {
				t.Fatal("bad bootstrap")
			}
			switch mode {
			case "idle-http":
				q := f.request()
				q.Peer = hello.Peer
				g, err := f.server.Issue(q)
				if err != nil {
					t.Fatal(err)
				}
				if json.NewEncoder(writer).Encode(g) != nil {
					t.Fatal("grant write failed")
				}
				line, err := buffered.ReadBytes('\n')
				if err != nil || !strings.Contains(string(line), `"ready":true`) {
					t.Fatal("bridge not ready")
				}
			case "invalid-grant":
				_, _ = io.WriteString(writer, "{\"unknown\":\"do-not-log-this-secret\"}\n")
				line, err := buffered.ReadBytes('\n')
				if err != nil || !strings.Contains(string(line), "grant_rejected") || strings.Contains(string(line), "secret") {
					t.Fatal("invalid grant error leaked or was misclassified")
				}
			case "oversized-grant":
				_, _ = io.WriteString(writer, strings.Repeat("x", 65537)+"\n")
			}
			_ = writer.Close()
			select {
			case <-ended:
			case <-time.After(3 * time.Second):
				t.Fatal("bridge did not stop with parent input")
			}
		})
	}
}
