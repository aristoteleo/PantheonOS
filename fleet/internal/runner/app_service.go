package runner

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/gorilla/websocket"
	"github.com/nats-io/nats.go"
)

var streamToken = regexp.MustCompile(`^[a-f0-9]{64}$`)

// EnableServices trusts only the saved Controller origin, never a URL received
// in a command. The same outbound TLS path works on cloud and user-owned nodes.
func (r *Runner) EnableServices(ctx context.Context, controller string) error {
	u, err := url.Parse(strings.TrimRight(controller, "/"))
	if err != nil || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" || u.Host == "" {
		return fmt.Errorf("App gateway requires a Controller origin")
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost")) {
		return fmt.Errorf("App gateway requires HTTPS")
	}
	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else {
		u.Scheme = "ws"
	}
	r.serviceOrigin, r.serviceContext = u.String(), ctx
	r.serviceSlots = make(chan struct{}, 64)
	r.rec.Capability.Runtimes["app-services"] = "1"
	r.enableDirectServices(ctx)
	return nil
}

type serviceRequest struct {
	Type       string `json:"type"`
	Instance   string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation uint64 `json:"generation"`
	Component  string `json:"component"`
	Port       string `json:"port"`
	Stream     string `json:"stream"`
	Secret     string `json:"secret"`
}

func (r *Runner) handleService(m *nats.Msg) {
	var q serviceRequest
	if r.lifecycle == nil || r.serviceOrigin == "" || lifecycle.StrictDecode(m.Data, &q) != nil || !streamToken.MatchString(q.Stream) || !streamToken.MatchString(q.Secret) {
		r.replyErr(m, "App service protocol unavailable or invalid request")
		return
	}
	endpoint, err := r.lifecycle.Service(q.Instance, q.Revision, q.Generation, q.Component, q.Port)
	if err != nil {
		r.replyErr(m, err.Error())
		return
	}
	select {
	case r.serviceSlots <- struct{}{}:
	default:
		r.replyErr(m, "App connection limit reached")
		return
	}
	release, err := r.lifecycle.BeginUse(q.Instance, q.Revision, q.Generation)
	if err != nil {
		<-r.serviceSlots
		r.replyErr(m, err.Error())
		return
	}
	go func() {
		defer release()
		defer func() { <-r.serviceSlots }()
		ctx, cancel := context.WithTimeout(r.serviceContext, 15*time.Second)
		defer cancel()
		u, _ := url.Parse(endpoint)
		local, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp", u.Host)
		if err != nil {
			r.replyErr(m, "App endpoint is unavailable")
			return
		}
		defer local.Close()
		ws, response, err := (&websocket.Dialer{HandshakeTimeout: 10 * time.Second}).DialContext(ctx, r.serviceOrigin+"/apps/tunnel/"+q.Stream, http.Header{"Authorization": {"Bearer " + q.Secret}})
		if err != nil && response != nil && response.Body != nil {
			response.Body.Close()
		}
		if err != nil {
			r.replyErr(m, "App gateway is unavailable")
			return
		}
		defer ws.Close()
		r.reply(m, map[string]bool{"ok": true})
		done := make(chan struct{})
		go func() {
			select {
			case <-r.serviceContext.Done():
				_ = ws.Close()
				_ = local.Close()
			case <-done:
			}
		}()
		apptransport.Relay(apptransport.New(ws), local)
		close(done)
	}()
}
