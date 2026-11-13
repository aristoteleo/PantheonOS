package codingagents

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httptrace"
	"strings"
	"sync"
	"testing"
	"time"
)

// A model service may start its answer before it has read the whole request
// (it answers in full duplex), while the gateway's transport is still
// writing the request to it. The answer must reach the agent whole: if
// net/http drains and closes the request body when the answer's headers go
// out, the transport's next read of that body fails, it closes the model
// service's connection, and the agent gets a truncated answer.
func TestTurnModelCallEarlyAnswerIsNotCutOff(t *testing.T) {
	const head = "{\"model\":\"m1\",\"stream\":true}\n"
	rest := strings.Repeat("r", 128<<10)
	answer := strings.Repeat("data: {\"delta\":\"token\"}\n\n", 40000)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = http.NewResponseController(w).EnableFullDuplex()
		in := bufio.NewReader(r.Body)
		if line, err := in.ReadString('\n'); err != nil || line != head {
			http.Error(w, "the request's first line did not arrive", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		if got, err := io.ReadAll(in); err != nil || string(got) != rest {
			return
		}
		_, _ = io.WriteString(w, answer)
	}))
	defer upstream.Close()

	seen := make(chan struct{}) // the answer's headers reached the agent
	g := newRouteGateway("", func() Settings { return Settings{} })
	dialer := &net.Dialer{}
	g.client.Transport = &http.Transport{DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
		conn, err := dialer.DialContext(ctx, network, addr)
		if err != nil {
			return nil, err
		}
		return &holdWrite{Conn: conn, mark: []byte(head), hold: seen}, nil
	}}
	srv := httptest.NewServer(g)
	defer srv.Close()
	token := g.grantModel("claude", Platform{Base: upstream.URL, Key: "node-key"}, time.Now().Add(time.Hour))

	req, _ := http.NewRequest(http.MethodPost, srv.URL+"/v1/messages", strings.NewReader(head+rest))
	req.Header.Set("Authorization", "Bearer "+token)
	res, err := srv.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	close(seen)
	defer res.Body.Close()
	var got bytes.Buffer
	if _, err := io.Copy(&got, res.Body); err != nil {
		t.Fatalf("the answer broke off after %d bytes: %v", got.Len(), err)
	}
	if want := "data: first\n\n" + answer; got.String() != want {
		t.Fatalf("the answer was cut off: got %d bytes, want %d", got.Len(), len(want))
	}
}

// holdWrite is a slow link to the model service: the write that carries
// mark (the start of the request, all the service needs to answer) returns
// only once hold is closed, as if the link were still busy with it. The
// answer thus starts while the transport has not yet read the rest of the
// request.
type holdWrite struct {
	net.Conn
	mark []byte
	hold <-chan struct{}
	once sync.Once
}

func (c *holdWrite) Write(p []byte) (int, error) {
	n, err := c.Conn.Write(p)
	if bytes.Contains(p, c.mark) {
		c.once.Do(func() {
			select {
			case <-c.hold:
			case <-time.After(30 * time.Second):
			}
		})
	}
	return n, err
}

// syncBuffer is a log the server writes from its connections' goroutines.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// A model service that answers without reading the request (an error, say),
// or one the gateway cannot reach, leaves the request body unread when the
// gateway's handler returns. In full duplex net/http does not drain it while
// the answer is written; the gateway drains it before it returns, so the
// connection to the agent is not broken after the answer ("invalid
// concurrent Body.Read call") and stays usable for the next call.
func TestTurnModelCallUnreadRequestKeepsTheConnection(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":{"message":"over budget"}}`, http.StatusTooManyRequests)
	}))
	defer upstream.Close()
	g := newRouteGateway("", func() Settings { return Settings{} })
	srv := httptest.NewUnstartedServer(g)
	var errs syncBuffer
	srv.Config.ErrorLog = log.New(&errs, "", 0)
	srv.Start()
	defer srv.Close()
	token := g.grantModel("claude", Platform{Base: upstream.URL, Key: "node-key"}, time.Now().Add(time.Hour))
	gone := g.grantModel("claude", Platform{Base: "http://127.0.0.1:1", Key: "node-key"}, time.Now().Add(time.Hour))
	reused := 0
	for i, tok := range []string{token, gone, token} {
		trace := &httptrace.ClientTrace{GotConn: func(info httptrace.GotConnInfo) {
			if info.Reused {
				reused++
			}
		}}
		req, _ := http.NewRequestWithContext(httptrace.WithClientTrace(context.Background(), trace), http.MethodPost,
			srv.URL+"/v1/messages", strings.NewReader(strings.Repeat("r", 64<<10)))
		req.Header.Set("Authorization", "Bearer "+tok)
		res, err := srv.Client().Do(req)
		if err != nil {
			t.Fatal(i, err)
		}
		_, _ = io.Copy(io.Discard, res.Body)
		res.Body.Close()
		if res.StatusCode != http.StatusTooManyRequests && res.StatusCode != http.StatusBadGateway {
			t.Fatal(i, res.Status)
		}
	}
	if strings.Contains(errs.String(), "panic") || reused != 2 {
		t.Fatalf("the connection broke after an answer (reused %d of 2): %s", reused, errs.String())
	}
}
