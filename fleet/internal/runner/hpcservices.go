package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/hpcservice"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/gorilla/websocket"
	"github.com/nats-io/nats.go"
)

func (c *delegatedNode) serviceCommand(r *Runner, m *nats.Msg, kind string) {
	reply := func(v any) { b, _ := json.Marshal(v); _ = m.Respond(b) }
	fail := func(err error) { reply(map[string]string{"error": err.Error()}) }
	var q struct {
		Type       string          `json:"type"`
		Protocol   int             `json:"protocol"`
		Method     string          `json:"method"`
		Spec       hpcservice.Spec `json:"spec"`
		Instance   string          `json:"instance_id"`
		Revision   string          `json:"revision"`
		Generation uint64          `json:"generation"`
		Component  string          `json:"component"`
		Port       string          `json:"port"`
		Stream     string          `json:"stream"`
		Secret     string          `json:"secret"`
	}
	if err := lifecycle.StrictDecode(m.Data, &q); err != nil {
		fail(err)
		return
	}
	if c.services == nil {
		fail(fmt.Errorf("HPC services unavailable"))
		return
	}
	if kind == "hpc_service" && q.Protocol == 1 && q.Method == "list" {
		reply(map[string]any{"services": c.services.List()})
		return
	}
	c.mu.Lock()
	ready := c.rec.Delegation.State == "ready" && time.Since(c.verifiedAt) <= 90*time.Second
	opctx, job := c.opctx, c.job
	c.mu.Unlock()
	if !ready || opctx == nil || opctx.Err() != nil || r.serviceOrigin == "" {
		fail(fmt.Errorf("HPC allocation or App gateway unavailable"))
		return
	}
	if kind == "hpc_service" {
		if q.Protocol != 1 {
			fail(fmt.Errorf("unsupported HPC service protocol"))
			return
		}
		select {
		case c.slot <- struct{}{}:
		default:
			fail(fmt.Errorf("allocation busy"))
			return
		}
		defer func() { <-c.slot }()
		var rec hpcservice.Receipt
		var err error
		switch q.Method {
		case "start":
			if job.Service != nil {
				fail(fmt.Errorf("This App is the Slurm job workload; submit a new HTTP App job to restart it"))
				return
			}
			rec, err = c.services.Start(opctx, q.Spec, q.Generation)
		case "stop":
			if job.Service != nil {
				// Validate the immutable primary binding before cancelling only
				// this recorded job. Slurm owns process cleanup and resources.
				expected := job.Service.Revision()
				if q.Instance != "hpcsvc_"+job.Service.Name || q.Revision != expected || q.Generation != 1 {
					fail(fmt.Errorf("stale service binding"))
					return
				}
				cancelCtx, cancel := context.WithTimeout(opctx, 10*time.Second)
				err = r.scheduler(c.cluster).Cancel(cancelCtx, job.JobID)
				cancel()
				if err != nil {
					fail(err)
					return
				}
			}
			rec, err = c.services.Stop(q.Instance, q.Revision, q.Generation)
		default:
			err = fmt.Errorf("unsupported service method")
		}
		if err != nil {
			fail(err)
		} else {
			reply(map[string]any{"service": rec})
		}
		return
	}
	if q.Component != "service" || q.Port != "http" {
		fail(fmt.Errorf("unknown service port"))
		return
	}
	if err := c.services.Ready(q.Instance, q.Revision, q.Generation); err != nil {
		fail(err)
		return
	}
	if kind == "app_lifecycle" {
		if q.Protocol != 1 || q.Method != "service" {
			fail(fmt.Errorf("HPC node supports service binding only; package lifecycle is not enabled"))
			return
		}
		reply(map[string]bool{"ready": true})
		return
	}
	if !streamToken.MatchString(q.Stream) || !streamToken.MatchString(q.Secret) {
		fail(fmt.Errorf("invalid gateway stream"))
		return
	}
	select {
	case r.serviceSlots <- struct{}{}:
	default:
		fail(fmt.Errorf("App connection limit reached"))
		return
	}
	go func() {
		defer func() { <-r.serviceSlots }()
		ctx, cancel := context.WithTimeout(opctx, 12*time.Second)
		defer cancel()
		local, err := c.services.Dial(ctx, q.Instance, q.Revision, q.Generation)
		if err != nil {
			fail(err)
			return
		}
		defer local.Close()
		ws, response, err := (&websocket.Dialer{HandshakeTimeout: 10 * time.Second}).DialContext(ctx, r.serviceOrigin+"/apps/tunnel/"+q.Stream, http.Header{"Authorization": {"Bearer " + q.Secret}})
		if err != nil {
			if response != nil && response.Body != nil {
				response.Body.Close()
			}
			fail(fmt.Errorf("App gateway unavailable"))
			return
		}
		defer ws.Close()
		reply(map[string]bool{"ok": true})
		finished := make(chan struct{})
		go func() {
			select {
			case <-opctx.Done():
				ws.Close()
				local.Close()
			case <-finished:
			}
		}()
		apptransport.Relay(apptransport.New(ws), local)
		close(finished)
	}()
}
