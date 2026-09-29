package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/hpcproxy"
	"github.com/aristoteleo/pantheon-fleet/internal/hpcservice"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/nats-io/jwt/v2"
	"github.com/nats-io/nats.go"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

type DelegateAuth func(context.Context, string) (proto.DelegateResponse, error)
type delegatedNode struct {
	mu              sync.Mutex
	job             hpc.Job
	cluster         string
	rec             proto.Node
	nc              *nats.Conn
	reg             *registry.Registry
	creds           atomic.Value
	renewAt         time.Time
	nextProbe       time.Time
	nextAttach      time.Time
	verifiedAt      time.Time
	heartbeatCancel context.CancelFunc
	heartbeatDone   chan struct{}
	opctx           context.Context
	cancel          context.CancelFunc
	slot            chan struct{}
	services        *hpcservice.Manager
	appToken        string
}

// ServeHPCDelegates keeps child credentials and subscriptions on the connector.
// No Fleet executable or credential is sent to the cluster.
func (r *Runner) ServeHPCDelegates(ctx context.Context, authorize DelegateAuth) {
	children := map[string]*delegatedNode{}
	defer func() {
		for _, c := range children {
			c.close()
		}
	}()
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		if ctx.Err() != nil {
			return
		}
		seen := map[string]bool{}
		for _, st := range r.clusters.List() {
			launcher := r.scheduler(st.ID)
			jobs := launcher.Recorded()
			var pollErr error
			if st.State == "connected" {
				pollctx, cancel := context.WithTimeout(ctx, 70*time.Second)
				jobs, pollErr = launcher.Jobs(pollctx)
				cancel()
				if pollErr != nil {
					jobs = launcher.Recorded()
				}
			}
			for _, j := range jobs {
				if j.AllocationID == "" {
					continue
				}
				if ctx.Err() != nil {
					return
				}
				seen[j.AllocationID] = true
				c := children[j.AllocationID]
				if endedAllocation(j.State) {
					if c != nil {
						c.close()
						delete(children, j.AllocationID)
					}
					continue
				}
				if c == nil {
					var err error
					c, err = r.newDelegate(ctx, st.ID, j, authorize)
					if err != nil {
						r.setProxyError(j.AllocationID, err.Error())
						continue
					}
					children[j.AllocationID] = c
					r.setProxyError(j.AllocationID, "")
				}
				if time.Now().After(c.renewAt) {
					authctx, cancel := context.WithTimeout(ctx, 12*time.Second)
					grant, err := authorize(authctx, j.AllocationID)
					cancel()
					if err != nil || grant.NodeID != c.rec.NodeID {
						c.unavailable("authorization_required", "Cannot renew delegated node authorization")
						r.setProxyError(j.AllocationID, "Cannot renew delegated node authorization")
						continue
					}
					c.creds.Store([]byte(grant.Creds))
					c.renewAt = refreshAt(grant.ExpiresAt)
					_ = c.nc.ForceReconnect()
				}
				if st.State != "connected" {
					c.unavailable("sign_in_required", "Sign in to the cluster again")
				} else if pollErr != nil {
					c.unavailable("unknown", "Unable to verify Slurm allocation")
				} else {
					c.update(ctx, r, j)
				}

			}
		}
		present := map[string]bool{}
		for _, st := range r.clusters.List() {
			present[st.ID] = true
		}
		for key, c := range children {
			if !present[c.cluster] || !seen[key] {
				c.close()
				delete(children, key)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
func refreshAt(exp int64) time.Time { return time.Now().Add(time.Until(time.Unix(exp, 0)) * 3 / 4) }
func (r *Runner) setProxyError(allocation, msg string) {
	r.hpcMu.Lock()
	defer r.hpcMu.Unlock()
	if r.proxyErrors == nil {
		r.proxyErrors = map[string]string{}
	}
	r.proxyErrors[allocation] = msg
}
func (r *Runner) newDelegate(ctx context.Context, cluster string, j hpc.Job, authorize DelegateAuth) (*delegatedNode, error) {
	authctx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	grant, err := authorize(authctx, j.AllocationID)
	if err != nil {
		return nil, err
	}
	want := proto.DelegatedNodeID(r.fleet, r.node, j.AllocationID)
	if grant.NodeID != want {
		return nil, fmt.Errorf("unexpected delegated identity")
	}
	c := &delegatedNode{cluster: cluster, job: j, renewAt: refreshAt(grant.ExpiresAt), slot: make(chan struct{}, 1)}
	if j.App != nil {
		token, e := os.ReadFile(filepath.Join(r.clusters.Root, "app-tokens", cluster, j.AllocationID))
		if e != nil || len(token) != 64 {
			return nil, fmt.Errorf("private App job transport credential unavailable")
		}
		c.appToken = string(token)
	}
	c.services, err = hpcservice.Open(filepath.Join(r.clusters.Root, "services", cluster, j.AllocationID), r.clusters.Stream, cluster, j)
	if err != nil {
		return nil, err
	}
	c.creds.Store([]byte(grant.Creds))
	c.rec = proto.Node{NodeID: want, Name: j.NodeName, Kind: proto.KindMachine, Version: r.rec.Version,
		Labels:     []string{"hpc", "connector:" + r.node, "cluster:" + cluster, "slurm-job:" + j.JobID},
		Capability: proto.Capability{OS: "linux", CPUCores: j.CPUs, RAMGB: float64(j.MemGB), Runtimes: map[string]string{"hpc-proxy": "1"}},
		State:      proto.State{Status: proto.StatusOffline},
		Delegation: &proto.Delegation{ConnectorID: r.node, ClusterID: cluster, JobID: j.JobID, Allocation: j.AllocationID, State: "preparing"}}
	opts := []nats.Option{nats.Name("fleet-hpc/" + want), nats.CustomInboxPrefix("_INBOX_" + r.fleet), nats.Timeout(5 * time.Second), nats.MaxReconnects(-1),
		nats.UserJWT(func() (string, error) { return jwt.ParseDecoratedJWT(c.creds.Load().([]byte)) }, func(nonce []byte) ([]byte, error) {
			kp, err := jwt.ParseDecoratedNKey(c.creds.Load().([]byte))
			if err != nil {
				return nil, err
			}
			defer kp.Wipe()
			return kp.Sign(nonce)
		})}
	c.nc, err = nats.Connect(r.nc.ConnectedUrl(), opts...)
	if err != nil {
		return nil, err
	}
	c.reg, err = registry.Open(ctx, c.nc, r.fleet, want, 30*time.Second)
	if err != nil {
		c.nc.Close()
		return nil, err
	}
	_, err = c.nc.Subscribe(proto.SubjNodeCmd(r.fleet, want), func(m *nats.Msg) { c.command(r, m) })
	if err == nil {
		err = c.nc.FlushTimeout(5 * time.Second)
	}
	if err != nil {
		c.close()
		return nil, err
	}
	heartbeatCtx, stopHeartbeat := context.WithCancel(ctx)
	c.heartbeatCancel = stopHeartbeat
	c.heartbeatDone = make(chan struct{})
	go func() {
		defer close(c.heartbeatDone)
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			putctx, cancel := context.WithTimeout(heartbeatCtx, 3*time.Second)
			_ = c.reg.Put(putctx, c.snapshot())
			cancel()
			select {
			case <-heartbeatCtx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	return c, nil
}
func (c *delegatedNode) snapshot() proto.Node {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rec.Delegation.State == "ready" && time.Since(c.verifiedAt) > 90*time.Second {
		if c.cancel != nil {
			c.cancel()
			c.cancel = nil
		}
		c.rec.State.Status = proto.StatusOffline
		c.rec.Delegation.State, c.rec.Delegation.Reason = "unknown", "Allocation status is stale"
	}
	n := c.rec
	n.Capability.Runtimes = make(map[string]string, len(c.rec.Capability.Runtimes))
	for k, v := range c.rec.Capability.Runtimes {
		n.Capability.Runtimes[k] = v
	}
	n.Capability.Caps = append([]string(nil), c.rec.Capability.Caps...)
	n.Capability.FileRoots = append([]string(nil), c.rec.Capability.FileRoots...)
	d := *n.Delegation
	n.Delegation = &d
	return n
}
func (c *delegatedNode) unavailable(state, reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cancel != nil {
		c.cancel()
		c.cancel = nil
	}
	c.rec.State.Status = proto.StatusOffline
	c.rec.Delegation.State, c.rec.Delegation.Reason = state, reason
}
func (c *delegatedNode) update(ctx context.Context, r *Runner, j hpc.Job) {
	if j.State != "RUNNING" {
		c.unavailable(j.State, j.Reason)
		return
	}
	c.mu.Lock()
	ready := c.rec.Delegation.State == "ready"
	c.job = j
	c.verifiedAt = time.Now()
	c.mu.Unlock()
	if ready {
		c.attachPrimary()
		c.refreshAppCapabilities(ctx)
		return
	}
	if time.Now().Before(c.nextProbe) {
		return
	}
	c.nextProbe = time.Now().Add(time.Minute)
	probeCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	out, err := hpcproxy.Execute(probeCtx, r.clusters.Stream, c.cluster, j, hpcproxy.Request{Operation: "probe"})
	cancel()
	var probe struct{ OS, Arch, Python, Root, Hostname, Error string }
	if err == nil {
		err = json.Unmarshal(out, &probe)
	}
	if err == nil && probe.Error != "" {
		err = fmt.Errorf("%s", probe.Error)
	}
	if err != nil {
		c.unavailable("preparing", fmt.Sprintf("Compute probe: %v", err))
		return
	}
	arch := map[string]string{"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[probe.Arch]
	if arch == "" || probe.OS != "linux" || probe.Root == "" {
		c.unavailable("unsupported", "Compute environment is unsupported")
		return
	}
	c.mu.Lock()
	c.opctx, c.cancel = context.WithCancel(ctx)
	c.rec.Capability.Arch = arch
	c.rec.Capability.Caps = []string{"proc"}
	c.rec.Capability.Runtimes["python"] = probe.Python
	c.rec.Capability.Runtimes["hpc-files"] = "1"
	if r.serviceOrigin != "" {
		c.rec.Capability.Runtimes["hpc-services"] = "1"
		c.rec.Capability.Runtimes["app-services"] = "1"
	}
	c.rec.Capability.FileRoots = []string{probe.Root}
	c.rec.State.Status = proto.StatusOnline
	c.rec.Delegation.State, c.rec.Delegation.Reason = "ready", probe.Hostname
	if j.Service != nil {
		c.rec.Capability.Runtimes["hpc-job-service"] = "1"
		c.rec.Capability.Caps = nil
	}

	c.mu.Unlock()
	c.attachPrimary()
	c.refreshAppCapabilities(ctx)
}

// Reconnect only the transport of a primary batch App, never its process.
func (c *delegatedNode) attachPrimary() {
	c.mu.Lock()
	if c.job.Service == nil || c.services == nil || c.opctx == nil || c.opctx.Err() != nil || time.Now().Before(c.nextAttach) {
		c.mu.Unlock()
		return
	}
	spec, ctx := *c.job.Service, c.opctx
	c.nextAttach = time.Now().Add(time.Minute)
	c.mu.Unlock()
	select {
	case c.slot <- struct{}{}:
	default:
		return
	}
	defer func() { <-c.slot }()
	if !c.services.Busy() {
		_, _ = c.services.Start(ctx, spec, 1)
	}
}
func (c *delegatedNode) close() {
	c.unavailable("offline", "Connector stopped")
	if c.services != nil {
		c.services.Close()
	}
	if c.heartbeatCancel != nil {
		c.heartbeatCancel()
		<-c.heartbeatDone
	}
	if c.reg != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = c.reg.Delete(ctx)
		cancel()
	}
	if c.nc != nil {
		c.nc.Close()
	}
}
func (c *delegatedNode) command(r *Runner, m *nats.Msg) {
	reply := func(v any) { b, _ := json.Marshal(v); _ = m.Respond(b) }
	if len(m.Data) > 768*1024 {
		reply(map[string]string{"error": "request too large"})
		return
	}
	var req struct {
		Type string            `json:"type"`
		Task *proto.Task       `json:"task"`
		File *hpcproxy.Request `json:"file"`
	}
	if json.Unmarshal(m.Data, &req) != nil {
		reply(map[string]string{"error": "invalid request"})
		return
	}
	c.mu.Lock()
	ordinaryApp := c.job.App != nil
	c.mu.Unlock()
	if ordinaryApp {
		switch req.Type {
		case "app_lifecycle", "app_list":
			c.appCommand(r, m, req.Type)
			return
		case "app_service":
			c.appService(r, m)
			return
		case "hpc_service":
			r.replyErr(m, "Use the standard App lifecycle for this job")
			return
		}
	}
	if req.Type == "hpc_service" || req.Type == "app_service" || req.Type == "app_lifecycle" {
		c.serviceCommand(r, m, req.Type)
		return
	}
	if req.Type == "app_list" {
		reply(map[string]any{"instances": []any{}})
		return
	}
	c.mu.Lock()
	ready := c.rec.Delegation.State == "ready" && time.Since(c.verifiedAt) <= 90*time.Second
	opctx, j := c.opctx, c.job
	c.mu.Unlock()
	if !ready {
		reply(map[string]string{"error": "HPC allocation unavailable; check its job and sign-in state"})
		return
	}
	if req.Type == "ping" {
		reply(map[string]string{"pong": c.rec.NodeID})
		return
	}
	var op hpcproxy.Request
	switch req.Type {
	case "run_task":
		if req.Task == nil {
			reply(map[string]string{"error": "missing task"})
			return
		}
		op = hpcproxy.Request{Operation: "task", Task: req.Task}
	case "hpc_file":
		if req.File == nil {
			reply(map[string]string{"error": "missing file operation"})
			return
		}
		op = *req.File
		op.Task = nil
		switch op.Operation {
		case "list", "read", "write", "mkdir":
		default:
			reply(map[string]string{"error": "unsupported file operation"})
			return
		}
	default:
		reply(map[string]string{"error": "This HPC proxy currently supports tasks and workspace files; app lifecycle is not enabled"})
		return
	}
	select {
	case c.slot <- struct{}{}:
	default:
		reply(map[string]string{"error": "Allocation is busy; retry after the current operation finishes"})
		return
	}
	// Service starts use this same slot. Checking after acquiring it prevents a
	// simultaneous start from slipping between the busy check and task launch.
	if req.Type == "run_task" && (j.Service != nil || (c.services != nil && c.services.Busy())) {
		<-c.slot
		reply(map[string]string{"error": "Stop the allocation service before running a separate task"})
		return
	}
	go func() {
		defer func() { <-c.slot }()
		out, err := hpcproxy.Execute(opctx, r.clusters.Stream, c.cluster, j, op)
		if err != nil {
			reply(map[string]string{"error": err.Error()})
			return
		}
		reply(out)
	}()
}

func endedAllocation(state string) bool {
	switch state {
	case "COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "REVOKED", "ENDED":
		return true
	}
	return false
}
