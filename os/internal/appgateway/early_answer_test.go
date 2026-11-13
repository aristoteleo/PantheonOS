package appgateway

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
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

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/gorilla/websocket"
)

// proxied is a gateway in front of one App (app) and a workload grant to it.
type proxied struct {
	server      *httptest.Server
	host, token string
	errs        *syncBuffer // the gateway server's error log
}

func proxyTo(t *testing.T, app http.Handler, appConn func(net.Conn) net.Conn) *proxied {
	t.Helper()
	const serviceToken = "controller-service-token-is-not-an-app-token"
	b := Binding{Fleet: "alice", Node: "node1", Instance: "instance1", Revision: strings.Repeat("a", 64), Generation: 1, Component: "office", Port: "http"}
	upstream := httptest.NewServer(app)
	t.Cleanup(upstream.Close)
	var server *httptest.Server
	g, err := New("apps.test", serviceToken, []string{"https://atrium.test"},
		func(ctx context.Context, _ Binding, id, secret string) error {
			ws, _, err := websocket.DefaultDialer.DialContext(ctx, "ws"+strings.TrimPrefix(server.URL, "http")+"/apps/tunnel/"+id, http.Header{"Authorization": {"Bearer " + secret}})
			if err != nil {
				return err
			}
			conn, err := net.Dial("tcp", strings.TrimPrefix(upstream.URL, "http://"))
			if err != nil {
				ws.Close()
				return err
			}
			go apptransport.Relay(apptransport.New(ws), conn)
			return nil
		}, func(context.Context, Binding) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	g.appConn = appConn
	mux := http.NewServeMux()
	g.Register(mux)
	server = httptest.NewUnstartedServer(g.Handler(mux))
	errs := &syncBuffer{}
	server.Config.ErrorLog = log.New(errs, "", 0)
	server.Start()
	t.Cleanup(server.Close)

	grantBody, _ := json.Marshal(AttachRequest{Binding: b, Credential: "hub-signed-instance-credential-not-login", Expires: time.Now().Add(time.Hour).Unix(), Workload: true})
	req, _ := http.NewRequest("POST", server.URL+"/apps/connect", bytes.NewReader(grantBody))
	req.Host = "controller.test"
	req.Header.Set("Authorization", "Bearer "+serviceToken)
	res, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var grant struct {
		AccessToken string `json:"access_token"`
	}
	if json.NewDecoder(res.Body).Decode(&grant) != nil || grant.AccessToken == "" {
		t.Fatal("no workload grant", res.Status)
	}
	return &proxied{server: server, host: Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test"), token: grant.AccessToken, errs: errs}
}

// post sends body to the App through the gateway.
func (p *proxied) post(ctx context.Context, body string) (*http.Response, error) {
	req, _ := http.NewRequestWithContext(ctx, "POST", p.server.URL+"/export", strings.NewReader(body))
	req.Host = p.host
	req.Header.Set("Authorization", "Bearer "+p.token)
	return p.server.Client().Do(req)
}

// An App may start its answer before it has read the whole request (it
// answers in full duplex), while the gateway's transport is still writing
// the request to it. The answer must reach the caller whole: if net/http
// drains and closes the request body when the answer's headers go out, the
// transport's next read of that body fails, it closes the App connection,
// and the caller gets a truncated answer (unexpected EOF).
func TestAppEarlyAnswerIsNotCutOff(t *testing.T) {
	const head = "{\"call\":\"export\"}\n"
	rest := strings.Repeat("r", 128<<10)
	answer := strings.Repeat("exported line\n", 80000)
	seen := make(chan struct{}) // the answer's headers reached the caller
	p := proxyTo(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = http.NewResponseController(w).EnableFullDuplex()
		in := bufio.NewReader(r.Body)
		if line, err := in.ReadString('\n'); err != nil || line != head {
			http.Error(w, "the request's first line did not arrive", http.StatusBadRequest)
			return
		}
		_, _ = io.WriteString(w, "first\n")
		w.(http.Flusher).Flush()
		if got, err := io.ReadAll(in); err != nil || string(got) != rest {
			return
		}
		_, _ = io.WriteString(w, answer)
	}), func(c net.Conn) net.Conn { return &holdWrite{Conn: c, mark: []byte(head), hold: seen} })

	res, err := p.post(context.Background(), head+rest)
	if err != nil {
		t.Fatal(err)
	}
	close(seen)
	defer res.Body.Close()
	var got bytes.Buffer
	if _, err := io.Copy(&got, res.Body); err != nil {
		t.Fatalf("the answer broke off after %d bytes: %v", got.Len(), err)
	}
	if want := "first\n" + answer; got.String() != want {
		t.Fatalf("the answer was cut off: got %d bytes, want %d", got.Len(), len(want))
	}
}

// An App that answers without reading the request (an error, say) leaves
// the request body unread when the gateway's handler returns. In full duplex
// net/http does not drain it while the answer is written; the gateway
// drains it before it returns, so the caller's connection is not broken
// after the answer ("invalid concurrent Body.Read call") and its next
// request on that connection goes through.
func TestAppUnreadRequestKeepsTheConnection(t *testing.T) {
	const body = "unread request body "
	// The link to the App takes the request's first bytes and then stays
	// busy, so the request is still unread when the App's answer is passed on.
	busy := make(chan struct{})
	t.Cleanup(func() { close(busy) })
	p := proxyTo(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "busy", http.StatusTooManyRequests)
	}), func(c net.Conn) net.Conn { return &holdWrite{Conn: c, mark: []byte(body), hold: busy} })
	reused := 0
	for i := range 3 {
		trace := &httptrace.ClientTrace{GotConn: func(info httptrace.GotConnInfo) {
			if info.Reused && i > 0 {
				reused++
			}
		}}
		res, err := p.post(httptrace.WithClientTrace(context.Background(), trace), strings.Repeat(body, 3200))
		if err != nil {
			t.Fatal(i, err)
		}
		_, _ = io.Copy(io.Discard, res.Body)
		res.Body.Close()
		if res.StatusCode != http.StatusTooManyRequests {
			t.Fatal(i, res.Status)
		}
	}
	if strings.Contains(p.errs.String(), "panic") || reused != 2 {
		t.Fatalf("the connection broke after an answer (reused %d of 2): %s", reused, p.errs.String())
	}
}

// holdWrite is a slow link to the App: the write that carries mark (the
// start of the request, all the App needs to answer) returns only once hold
// is closed, as if the link were still busy with it. The App's answer thus
// starts while the transport has not yet read the rest of the request.
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
