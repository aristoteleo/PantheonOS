package runner

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/jobapp"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/gorilla/websocket"
	"github.com/nats-io/nats.go"
)

func (c *delegatedNode) appDial(ctx context.Context) (net.Conn, error) {
	c.mu.Lock()
	j := c.job
	ready := c.rec.Delegation.State == "ready" && time.Since(c.verifiedAt) <= 90*time.Second
	c.mu.Unlock()
	if !ready || j.App == nil || j.Service == nil || c.appToken == "" {
		return nil, fmt.Errorf("HPC App allocation is unavailable")
	}
	return c.services.Dial(ctx, "hpcsvc_"+j.Service.Name, j.Service.Revision(), 1)
}

func (c *delegatedNode) appControl(ctx context.Context, data []byte) (json.RawMessage, error) {
	connection, err := c.appDial(ctx)
	if err != nil {
		return nil, err
	}
	defer connection.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = connection.SetDeadline(deadline)
	}
	req, _ := http.NewRequest("POST", "http://localhost/control", bytes.NewReader(data))
	req.Header.Set("Authorization", "Bearer "+c.appToken)
	req.Header.Set("Content-Type", "application/json")
	req.Close = true
	if err = req.Write(connection); err != nil {
		return nil, err
	}
	response, err := http.ReadResponse(bufio.NewReader(connection), req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20+1))
	if err != nil {
		return nil, err
	}
	if response.StatusCode != 200 || len(body) > 4<<20 || !json.Valid(body) {
		return nil, fmt.Errorf("invalid job App response (HTTP %d)", response.StatusCode)
	}
	return body, nil
}

func (c *delegatedNode) appCommand(r *Runner, m *nats.Msg, kind string) {
	select {
	case r.rpcSlots <- struct{}{}:
	default:
		r.replyErr(m, "App RPC concurrency limit reached")
		return
	}
	go func() {
		defer func() { <-r.rpcSlots }()
		c.mu.Lock()
		ctx := c.opctx
		c.mu.Unlock()
		if ctx == nil {
			r.replyErr(m, "HPC allocation unavailable")
			return
		}
		var q lifecycle.Command
		data := m.Data
		if kind == "app_list" {
			data = []byte(`{"type":"app_lifecycle","protocol":1,"method":"status"}`)
		}
		if err := lifecycle.StrictDecode(data, &q); err != nil {
			r.replyErr(m, err.Error())
			return
		}
		seconds := 20
		if q.Method == "invoke" && q.Timeout >= 1 && q.Timeout <= 600 {
			seconds = q.Timeout + 5
		}
		ctx, cancel := context.WithTimeout(ctx, time.Duration(seconds)*time.Second)
		defer cancel()
		result, err := c.appControl(ctx, data)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		if kind == "app_list" {
			var state lifecycle.Ledger
			if err = json.Unmarshal(result, &state); err != nil || state.Protocol != 1 {
				r.replyErr(m, "App worker status unavailable")
				return
			}
			list := []proto.AppInstance{}
			for _, in := range state.Instances {
				list = append(list, proto.AppInstance{AppID: in.AppID, Version: in.Version, Scope: in.Scope, Health: in.State, InstanceID: in.ID, Revision: in.Digest, Generation: in.Generation, Error: in.Error})
			}
			r.reply(m, map[string]any{"instances": list})
			return
		}
		_ = m.Respond(result)
	}()
}

func (c *delegatedNode) appService(r *Runner, m *nats.Msg) {
	var q serviceRequest
	if r.serviceOrigin == "" || lifecycle.StrictDecode(m.Data, &q) != nil || !streamToken.MatchString(q.Stream) || !streamToken.MatchString(q.Secret) {
		r.replyErr(m, "invalid App service request")
		return
	}
	select {
	case r.serviceSlots <- struct{}{}:
	default:
		r.replyErr(m, "App connection limit reached")
		return
	}
	go func() {
		defer func() { <-r.serviceSlots }()
		c.mu.Lock()
		opctx := c.opctx
		c.mu.Unlock()
		if opctx == nil {
			r.replyErr(m, "HPC allocation unavailable")
			return
		}
		ctx, cancel := context.WithTimeout(opctx, 15*time.Second)
		defer cancel()
		local, err := c.appDial(ctx)
		if err != nil {
			r.replyErr(m, err.Error())
			return
		}
		defer local.Close()
		_ = local.SetDeadline(time.Now().Add(15 * time.Second))
		req, _ := http.NewRequest("CONNECT", "http://localhost/service", nil)
		req.URL.Opaque = "/service"
		req.Header.Set("Authorization", "Bearer "+c.appToken)
		for key, value := range map[string]string{"Instance": q.Instance, "Revision": q.Revision, "Generation": strconv.FormatUint(q.Generation, 10), "Component": q.Component, "Port": q.Port} {
			req.Header.Set("X-App-"+key, value)
		}
		if err = req.Write(local); err != nil {
			r.replyErr(m, "App stream request failed")
			return
		}
		reader := bufio.NewReader(local)
		response, err := http.ReadResponse(reader, req)
		if err != nil || response.StatusCode != 200 {
			if response != nil {
				response.Body.Close()
			}
			r.replyErr(m, "App binding is not available")
			return
		}
		_ = local.SetDeadline(time.Time{})
		ws, resp, err := (&websocket.Dialer{HandshakeTimeout: 10 * time.Second}).DialContext(ctx, r.serviceOrigin+"/apps/tunnel/"+q.Stream, http.Header{"Authorization": {"Bearer " + q.Secret}})
		if err != nil {
			if resp != nil {
				resp.Body.Close()
			}
			r.replyErr(m, "App gateway unavailable")
			return
		}
		defer ws.Close()
		r.reply(m, map[string]bool{"ok": true})
		done := make(chan struct{})
		defer close(done)
		go func() {
			select {
			case <-opctx.Done():
				local.Close()
				ws.Close()
			case <-done:
			}
		}()
		apptransport.Relay(apptransport.New(ws), &jobapp.BufferedConn{Conn: local, Reader: reader})
	}()
}

// Probe the standard authenticated control protocol before advertising Apps.
// A RUNNING allocation alone does not establish that its worker is reachable.
func (c *delegatedNode) refreshAppCapabilities(ctx context.Context) {
	c.mu.Lock()
	ordinary := c.job.App != nil
	c.mu.Unlock()
	if !ordinary {
		return
	}
	probe, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	data, err := c.appControl(probe, []byte(`{"type":"app_lifecycle","protocol":1,"method":"status"}`))
	var ledger lifecycle.Ledger
	valid := err == nil && json.Unmarshal(data, &ledger) == nil && ledger.Protocol == lifecycle.Protocol
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, key := range []string{"app-lifecycle", "app-rpc", "app-rpc-auth"} {
		if valid {
			c.rec.Capability.Runtimes[key] = "1"
		} else {
			delete(c.rec.Capability.Runtimes, key)
		}
	}
	if valid && ledger.AppConfigProtocol == 1 {
		c.rec.Capability.Runtimes["app-configuration"] = "1"
	} else {
		delete(c.rec.Capability.Runtimes, "app-configuration")
	}
	if valid {
		c.rec.Capability.Caps = []string{"proc"}
	} else {
		c.rec.Capability.Caps = nil
	}
}
