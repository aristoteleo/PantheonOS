package lifecycle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

type Paths struct{ Package, Install, Data string }
type Driver interface {
	Prepare(context.Context, Component) error
	Start(context.Context, Component, Paths, string) (Resource, error)
	Probe(context.Context, Component, Paths, Resource) error
	Stop(context.Context, Component, Resource) error
	Release(context.Context, Resource) error
	Alive(context.Context, Resource) (bool, error)
	Hook(context.Context, Hook, Paths, map[string]any) (Receipt, error)
}
type dependencyDriver interface {
	PrepareDependencies(context.Context, Dependencies) (Receipt, error)
}
type containerHookDriver interface {
	ContainerHook(context.Context, Hook, Resource, map[string]any) (Receipt, error)
}
type Manager struct {
	mu                sync.Mutex
	serial            sync.Mutex
	root, owner, node string
	ledger            Ledger
	driver            Driver
	caps              proto.Capability
	lock              *os.File
	closed            bool
	jobs              sync.WaitGroup
	ctx               context.Context
	cancel            context.CancelFunc
	closeOnce         sync.Once
	closeErr          error
	usage             map[string]*instanceUsage
	resourceSampler   func() proto.ResourceInventory
	resourcePolicy    ResourcePolicy
	rpcSecret         []byte
	modelIdleSerial   sync.Mutex
	modelIdleWake     chan struct{}
	platform          node.PlatformNetwork
	platformDetect    func(string) (node.PlatformNetwork, error) // tests only
}

func Open(root, owner, node string, caps proto.Capability, driver Driver) (*Manager, error) {
	for _, p := range []string{root, filepath.Join(root, "artifacts"), filepath.Join(root, "packages"), filepath.Join(root, "installations"), filepath.Join(root, "data")} {
		if err := os.MkdirAll(p, 0700); err != nil {
			return nil, err
		}
	}
	var err error
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return nil, err
	}
	lock, err := lockRoot(filepath.Join(root, "lock"))
	if err != nil {
		return nil, err
	}
	m := &Manager{root: root, owner: owner, node: node, caps: caps, driver: driver, lock: lock, ledger: Ledger{Protocol: Protocol, Owner: owner, Node: node, Installations: map[string]*Installation{}, Instances: map[string]*Instance{}, Operations: map[string]*Operation{}}}
	m.platform = advertisedPlatformNetwork(caps)
	switch native := driver.(type) {
	case NativeDriver:
		native.groupIngress = newGroupIngressRegistry()
		m.driver = native
	case *NativeDriver:
		copy := *native
		copy.groupIngress = newGroupIngressRegistry()
		m.driver = &copy
	}
	if err = m.readResourcePolicy(); err != nil {
		lock.Close()
		return nil, err
	}
	if err = m.loadRPCSecret(); err != nil {
		lock.Close()
		return nil, err
	}
	b, err := os.ReadFile(filepath.Join(root, "ledger.json"))
	if err == nil {
		err = json.Unmarshal(b, &m.ledger)
	} else if errors.Is(err, os.ErrNotExist) {
		err = nil
	}
	if err != nil || (m.ledger.Protocol < Protocol || m.ledger.Protocol > 5) || m.ledger.ModelIdleProtocol > 1 || m.ledger.Owner != owner || m.ledger.Node != node || m.ledger.Installations == nil || m.ledger.Instances == nil || m.ledger.Operations == nil {
		lock.Close()
		return nil, fmt.Errorf("cannot read lifecycle ledger: %v", err)
	}
	if m.ledger.ModelIdle == nil {
		m.ledger.ModelIdle = map[string]*ModelIdle{}
	}
	for id, op := range m.ledger.Operations {
		if op == nil || (op.State == "cancelled" && (m.ledger.Protocol < 2 || op.Request.OperationID != id || validateStartFence(op.Request) != nil)) {
			lock.Close()
			return nil, fmt.Errorf("invalid start cancellation in lifecycle ledger")
		}
	}
	for _, in := range m.ledger.Instances {
		if in.State != "prepared" {
			continue
		}
		install := m.ledger.Installations[in.Digest]
		if m.ledger.Protocol < 2 || install == nil || install.State != "installed" ||
			in.ID != m.instanceID(in.Digest, in.Scope) || in.Generation == 0 ||
			!nameRE.MatchString(in.StartPreparationID) || checkPreparedReservations(in, install.Definition) != nil {
			lock.Close()
			return nil, fmt.Errorf("invalid prepared start in lifecycle ledger")
		}
	}
	m.ledger.ModelIdleProtocol = 1
	m.modelIdleWake = make(chan struct{}, 1)
	// Uncertain hooks are never replayed after a lost acknowledgement. Existing
	// resources remain recorded; an explicit reconcile checks actual liveness.
	for _, op := range m.ledger.Operations {
		if op.State == "running" || op.State == "queued" {
			op.State = "unknown"
			op.Error = "Runner restarted before operation acknowledgement; reconcile before retrying"
		}
	}
	for _, in := range m.ledger.Instances {
		// Migrate a legacy ready record before obscuring its previous state.
		if in.State == "ready" {
			in.ReadyGeneration = in.Generation
		}
		if in.State != "stopped" && in.State != "prepared" {
			in.State = "unknown"
		}
	}
	for _, install := range m.ledger.Installations {
		if install.State == "installing" || install.State == "removing" {
			install.State = "unknown"
		}
	}
	if err = m.persist(); err != nil {
		lock.Close()
		return nil, err
	}
	m.usage = map[string]*instanceUsage{}
	// Window leases live in memory. Give existing clients one lease TTL to
	// reconnect after a Runner restart before reclaiming an opted-in backend.
	for id, in := range m.ledger.Instances {
		if in.State == "unknown" && in.AutoStop {
			m.usageLocked(id, time.Now()).reconnectUntil = time.Now().Add(windowLeaseTTL)
		}
	}
	m.ledger.UsageProtocol = 1
	m.ledger.ResourceProtocol = 1
	m.ctx, m.cancel = context.WithCancel(context.Background())
	m.jobs.Add(1)
	go m.observe()
	m.jobs.Add(1)
	go m.observeModelIdle()
	return m, nil
}

func (m *Manager) Close() error {
	m.closeOnce.Do(func() {
		m.mu.Lock()
		m.closed = true
		m.cancel()
		m.mu.Unlock()
		m.jobs.Wait()
		if closer, ok := m.driver.(interface{ CloseGroupIngress() }); ok {
			closer.CloseGroupIngress()
		}
		// Cancel control operations, never kill App processes on Runner exit.
		m.closeErr = m.lock.Close()
	})
	return m.closeErr
}

func (m *Manager) observe() {
	defer m.jobs.Done()
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-m.ctx.Done():
			return
		case <-ticker.C:
			m.observeOnce()
		}
	}
}
func (m *Manager) observeOnce() {
	if !m.serial.TryLock() {
		return
	}
	defer m.serial.Unlock()
	for _, in := range m.Snapshot().Instances {
		if in.State == "unknown" || in.State == "recovered" {
			// Observe ownership/liveness only: never replay interrupted hooks or
			// declare a live process ready. Dead records must not stay Unknown
			// forever, and recovered idle backends must still be reclaimable.
			ctx, cancel := context.WithTimeout(m.ctx, 5*time.Second)
			m.mu.Lock()
			current := m.ledger.Instances[in.ID]
			m.mu.Unlock()
			_ = m.reconcile(ctx, nil, current)
			cancel()
			continue
		}
		if in.State != "ready" {
			continue
		}
		for _, r := range in.Resources {
			ctx, cancel := context.WithTimeout(m.ctx, 2*time.Second)
			alive, err := m.driver.Alive(ctx, r)
			cancel()
			if !alive || err != nil {
				message := "Component " + r.Component + " exited; working copies are retained"
				if err != nil {
					message = "Cannot verify component " + r.Component + ": " + err.Error()
				}
				_ = m.update(func() { current := m.ledger.Instances[in.ID]; current.State = "degraded"; current.Error = message })
				break
			}
		}
	}
	m.stopIdle(time.Now())
}
func (m *Manager) persist() error {
	b, err := json.MarshalIndent(m.ledger, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.OpenFile(filepath.Join(m.root, "ledger.tmp"), os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	if _, err = f.Write(b); err == nil {
		err = f.Sync()
	}
	ce := f.Close()
	if err != nil {
		return err
	}
	if ce != nil {
		return ce
	}
	return commitLedger(f.Name(), filepath.Join(m.root, "ledger.json"), m.root)
}
func clone[T any](v T) T { b, _ := json.Marshal(v); var out T; _ = json.Unmarshal(b, &out); return out }
func (m *Manager) Snapshot() Ledger {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := clone(m.ledger)
	out.Protocol = Protocol // On-disk v2 fences old Runners; the RPC remains v1.
	for id, in := range out.Instances {
		u := m.usageLocked(id, time.Now())
		in.Usage = &Usage{Windows: len(u.leases), Calls: u.calls, GraceSeconds: int(idleGrace / time.Second)}
		if !u.idleSince.IsZero() {
			t := u.idleSince
			in.Usage.IdleSince = &t
		}
	}
	return out
}
func (m *Manager) instanceID(digest, scope string) string {
	sum := sha256.Sum256([]byte(m.owner + "\x00" + m.node + "\x00" + digest + "\x00" + scope))
	return hex.EncodeToString(sum[:16])
}
func (m *Manager) paths(digest, scope string) Paths {
	return Paths{filepath.Join(m.root, "packages", digest), filepath.Join(m.root, "installations", digest), filepath.Join(m.root, "data", m.instanceID(digest, scope))}
}

func validateRequest(req Request) error {
	if req.Protocol != Protocol || !nameRE.MatchString(req.OperationID) || !digestRE.MatchString(req.Digest) || !nameRE.MatchString(req.Scope) {
		return fmt.Errorf("invalid operation identity/protocol")
	}
	switch req.Action {
	case "install", "start", "prepare_start", "stop", "uninstall", "reconcile", "recover", "clone_data":
	default:
		return fmt.Errorf("unsupported lifecycle action")
	}
	if req.StartPreparationID != "" && (req.Action != "start" || !nameRE.MatchString(req.StartPreparationID)) {
		return fmt.Errorf("start_preparation_id is only valid for a prepared start")
	}
	if req.Action == "clone_data" {
		if req.DataSource == nil || !digestRE.MatchString(req.DataSource.Digest) || req.DataSource.Digest == req.Digest || req.DataSource.Generation == 0 {
			return fmt.Errorf("state copy requires a different exact source revision/generation")
		}
	} else if req.DataSource != nil {
		return fmt.Errorf("data_source is only valid for clone_data")
	}
	return nil
}

func (m *Manager) Submit(req Request) (Operation, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return Operation{}, fmt.Errorf("Runner is shutting down")
	}
	if err := validateRequest(req); err != nil {
		return Operation{}, err
	}
	if op := m.ledger.Operations[req.OperationID]; op != nil {
		if !reflect.DeepEqual(op.Request, req) {
			return Operation{}, fmt.Errorf("operation_id already used for a different request")
		}
		return clone(*op), nil
	}
	previousPolicies := clone(m.ledger.ModelIdle)
	m.invalidateModelIdleLocked(req)
	op := &Operation{Request: req, State: "queued", Steps: []Step{}, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	m.ledger.Operations[req.OperationID] = op
	if err := m.persist(); err != nil {
		delete(m.ledger.Operations, req.OperationID)
		m.ledger.ModelIdle = previousPolicies
		return Operation{}, err
	}
	m.jobs.Add(1)
	go m.execute(req.OperationID)
	return clone(*op), nil
}

func (m *Manager) update(fn func()) error { m.mu.Lock(); defer m.mu.Unlock(); fn(); return m.persist() }
func (m *Manager) step(op *Operation, name string, fn func() (Receipt, error)) error {
	if err := m.update(func() {
		op.Steps = append(op.Steps, Step{Name: name, State: "running", StartedAt: time.Now().UTC()})
		op.UpdatedAt = time.Now().UTC()
	}); err != nil {
		return err
	}
	receipt, err := fn()
	if err != nil {
		receipt.Status = "failed"
		if receipt.Message == "" {
			receipt.Message = err.Error()
		}
	}
	now := time.Now().UTC()
	pe := m.update(func() {
		s := &op.Steps[len(op.Steps)-1]
		s.FinishedAt = &now
		s.Receipt = &receipt
		s.State = "succeeded"
		if err != nil {
			s.State = "failed"
		}
		op.UpdatedAt = now
	})
	return errors.Join(err, pe)
}
func (m *Manager) hook(ctx context.Context, op *Operation, def Definition, stage string, paths Paths) error {
	h, ok := def.Hooks[stage]
	if !ok {
		return nil
	}
	return m.step(op, stage, func() (Receipt, error) {
		c, cancel := context.WithTimeout(ctx, time.Duration(h.TimeoutSeconds)*time.Second)
		defer cancel()
		input := map[string]any{"protocol": Protocol, "operation_id": op.Request.OperationID, "instance_id": m.instanceID(op.Request.Digest, op.Request.Scope), "owner": m.owner, "node_id": m.node, "digest": op.Request.Digest, "stage": stage, "package_dir": paths.Package, "install_dir": paths.Install, "data_dir": paths.Data}
		var receipt Receipt
		var err error
		if h.Component == "" {
			receipt, err = m.driver.Hook(c, h, paths, input)
		} else {
			driver, supported := m.driver.(containerHookDriver)
			if !supported {
				return Receipt{}, fmt.Errorf("Runner cannot execute container hooks")
			}
			instance := m.Snapshot().Instances[m.instanceID(op.Request.Digest, op.Request.Scope)]
			var resource Resource
			if instance != nil {
				input["generation"] = instance.Generation
				for _, r := range instance.Resources {
					if r.Component == h.Component {
						resource = r
					}
				}
			}
			if resource.ID == "" {
				return Receipt{}, fmt.Errorf("hook component has not started; inspect recovery before stopping")
			}
			receipt, err = driver.ContainerHook(c, h, resource, input)
		}
		if err != nil {
			return receipt, err
		}
		if receipt.Status != "succeeded" {
			return receipt, fmt.Errorf("%s: %s: %s", stage, receipt.Status, receipt.Message)
		}
		if stage == "before_stop" {
			if !receipt.SafeToStop {
				return receipt, fmt.Errorf("stop blocked: App has not confirmed a durable checkpoint")
			}
			for _, p := range receipt.Checkpoints {
				if !relative(p) {
					return receipt, fmt.Errorf("invalid checkpoint reference")
				}
				actual, e := filepath.EvalSymlinks(filepath.Join(paths.Data, p))
				if e != nil {
					return receipt, e
				}
				rel, e := filepath.Rel(paths.Data, actual)
				if e != nil || !relative(filepath.ToSlash(rel)) {
					return receipt, fmt.Errorf("checkpoint outside instance data")
				}
				st, e := os.Stat(actual)
				if e != nil || !st.Mode().IsRegular() {
					return receipt, fmt.Errorf("checkpoint missing/not a file")
				}
			}
		}
		return receipt, nil
	})
}
func (m *Manager) eligibility(def Definition) error {
	contains := func(xs []string, x string) bool {
		for _, a := range xs {
			if a == x {
				return true
			}
		}
		return false
	}
	r := def.Requires
	for _, c := range def.Components {
		if c.Runtime == "container" && c.Resources != nil && len(c.Resources.Devices) > 0 && m.caps.OS != "linux" {
			return fmt.Errorf("GPU containers currently require a Linux NVIDIA node")
		}
	}
	if len(r.OS) > 0 && !contains(r.OS, m.caps.OS) {
		return fmt.Errorf("unsupported OS %s", m.caps.OS)
	}
	if len(r.Arch) > 0 && !contains(r.Arch, m.caps.Arch) {
		return fmt.Errorf("unsupported architecture %s", m.caps.Arch)
	}
	if m.caps.RAMGB < r.MemoryGB || m.caps.DiskFreeGB < r.DiskGB {
		return fmt.Errorf("node has insufficient memory/disk capacity")
	}
	for _, c := range r.Caps {
		if !contains(m.caps.Caps, c) {
			return fmt.Errorf("node missing capability %s", c)
		}
	}
	if consumesGroupPlatformNetwork(def) && m.platform.Mode == "" {
		return fmt.Errorf("node missing capability %s", node.PlatformNetworkCap)
	}
	return nil
}
func (m *Manager) execute(id string) {
	defer m.jobs.Done()
	m.serial.Lock()
	defer m.serial.Unlock()
	m.mu.Lock()
	op := m.ledger.Operations[id]
	m.mu.Unlock()
	if op.ModelIdleID != "" {
		defer m.notifyModelIdle()
	}
	if err := m.update(func() { op.State = "running" }); err != nil {
		return
	}
	ctx, cancel := context.WithTimeout(m.ctx, 30*time.Minute)
	defer cancel()
	err := m.perform(ctx, op)
	_ = m.update(func() {
		op.State = "succeeded"
		op.Error = ""
		if err != nil {
			op.State = "failed"
			op.Error = err.Error()
			if install := m.ledger.Installations[op.Request.Digest]; install != nil {
				if install.State == "installing" {
					install.State = "failed"
				}
				if install.State == "removing" {
					install.State = "remove_failed"
				}
			}
		}
		op.UpdatedAt = time.Now().UTC()
	})
}
func (m *Manager) perform(ctx context.Context, op *Operation) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	req := op.Request
	key := m.instanceID(req.Digest, req.Scope)
	paths := m.paths(req.Digest, req.Scope)
	m.mu.Lock()
	if err := m.checkModelIdleOperationLocked(op); err != nil {
		m.mu.Unlock()
		return err
	}
	installation := m.ledger.Installations[req.Digest]
	in := m.ledger.Instances[key]
	m.mu.Unlock()
	instanceAction := req.Action != "install" && req.Action != "uninstall"
	if instanceAction && in != nil && req.Generation != in.Generation {
		return fmt.Errorf("stale generation: expected %d", in.Generation)
	}
	if instanceAction && in == nil && req.Generation != 0 {
		return fmt.Errorf("instance does not exist")
	}
	if req.Action == "prepare_start" {
		return m.prepareStart(op, installation, in)
	}
	prepared := in != nil && in.State == "prepared"
	if prepared {
		switch req.Action {
		case "start":
			if req.StartPreparationID == "" || req.StartPreparationID != in.StartPreparationID {
				return fmt.Errorf("start requires the exact preparation id and generation")
			}
		case "stop":
			return m.cancelPreparedStart(in)
		case "reconcile":
			return nil // A durable, unconsumed hold survives observation/reconnect.
		default:
			return fmt.Errorf("instance has a prepared start; explicitly start or stop it first")
		}
	} else if req.StartPreparationID != "" {
		return fmt.Errorf("start preparation is no longer available; inspect the original operation")
	}
	if req.Action == "clone_data" {
		return m.cloneData(ctx, op, installation, in)
	}
	if req.Action == "recover" {
		return m.recover(ctx, op, installation, in, paths)
	}
	if req.Action == "reconcile" {
		if installation != nil && installation.State == "unknown" {
			return m.settleInterruptedInstallation(ctx, op, installation)
		}
		if installation != nil {
			if err := m.dependencies(ctx, op, installation.Definition); err != nil {
				return err
			}
		}
		return m.reconcile(ctx, op, in)
	}
	if in != nil && in.State == "unknown" {
		return fmt.Errorf("instance outcome unknown; reconcile before lifecycle changes")
	}
	if req.Action == "stop" {
		if in == nil {
			return nil
		}
		if err := m.dependencies(ctx, op, installation.Definition); err != nil {
			return err
		}
		return m.stop(ctx, op, installation.Definition, in, paths)
	}
	if req.Action == "uninstall" {
		return m.uninstall(ctx, op, installation)
	}
	if installation != nil && (installation.State == "unknown" || installation.State == "removing" || installation.State == "remove_failed") {
		return fmt.Errorf("installation has an unfinished operation; reconcile/complete removal before installing again")
	}
	if installation == nil || installation.State != "installed" {
		var def Definition
		if err := m.step(op, "verify_artifact", func() (Receipt, error) {
			m.mu.Lock()
			defer m.mu.Unlock()
			var e error
			def, e = m.unpack(req.Digest)
			return Receipt{Status: "succeeded"}, e
		}); err != nil {
			return err
		}
		if err := m.eligibility(def); err != nil {
			return err
		}
		installation = &Installation{Digest: req.Digest, Definition: def, State: "installing"}
		if err := m.update(func() {
			if consumesGroupPeer(def) {
				// Older Runners must not ignore the persisted credential-consumer field.
				if m.ledger.Protocol < 3 {
					m.ledger.Protocol = 3
				}
			}
			if consumesGroupNetwork(def) && m.ledger.Protocol < 4 {
				m.ledger.Protocol = 4
			}
			if consumesGroupPlatformNetwork(def) {
				m.ledger.Protocol = 5
			}
			m.ledger.Installations[req.Digest] = installation
		}); err != nil {
			return err
		}
		if err := os.MkdirAll(paths.Install, 0700); err != nil {
			return err
		}
		if err := m.dependencies(ctx, op, def); err != nil {
			return err
		}
		if err := m.hook(ctx, op, def, "before_install", paths); err != nil {
			return err
		}
		for _, c := range def.Components {
			if err := m.step(op, "prepare:"+c.Name, func() (Receipt, error) { return Receipt{Status: "succeeded"}, m.driver.Prepare(ctx, c) }); err != nil {
				return err
			}
		}
		if err := m.hook(ctx, op, def, "after_install", paths); err != nil {
			return err
		}
		if err := m.update(func() { installation.State = "installed" }); err != nil {
			return err
		}
	}
	if req.Action == "install" {
		return nil
	}
	def := installation.Definition
	// A node restart may have stopped the managed dependency, while its App
	// installation remains valid. Restore the same engine, never switch sockets.
	if err := m.dependencies(ctx, op, def); err != nil {
		return err
	}
	if err := m.eligibility(def); err != nil {
		return err
	}
	if in != nil && in.State == "ready" {
		return m.checkReady(ctx, op, def, in, paths)
	}
	if in != nil && (len(in.Resources) > 0 || (len(in.Reservations) > 0 && !prepared)) {
		return fmt.Errorf("instance still owns resources; stop before restarting")
	}
	generation := uint64(1)
	if in != nil {
		generation = in.Generation + 1
	}
	autoStop, keepAlive := false, false
	var dataSource *DataSource
	var preparedReservations map[string]ResourceReservation
	if in != nil {
		autoStop, keepAlive = in.AutoStop, in.KeepAlive
		dataSource = in.DataSource
		if prepared {
			if err := checkPreparedReservations(in, def); err != nil {
				return err
			}
			preparedReservations = clone(in.Reservations)
		}
	}
	if err := m.checkPreparedGroupNetwork(def, in, paths.Package); err != nil {
		return err
	}
	if err := m.checkGroupPlatformNetwork(def, key); err != nil {
		return err
	}
	if err := m.materializeGroupPeer(def, in, paths.Package); err != nil {
		return err
	}
	in = &Instance{Reservations: preparedReservations, DataSource: dataSource, AutoStop: autoStop, KeepAlive: keepAlive, ID: key, AppID: def.AppID, Version: def.Version, Digest: req.Digest, Scope: req.Scope, Generation: generation, State: "starting", Resources: []Resource{}}
	if err := m.update(func() { m.ledger.Instances[key] = in; delete(m.usage, key) }); err != nil {
		return err
	}
	if err := os.MkdirAll(paths.Data, 0700); err != nil {
		return err
	}
	fail := func(e error) error { _ = m.update(func() { in.State = "failed"; in.Error = e.Error() }); return e }
	if !prepared {
		if err := m.reserveComponents(in, def); err != nil {
			return fail(err)
		}
	}
	if err := m.hook(ctx, op, def, "before_start", paths); err != nil {
		return fail(err)
	}
	for _, c := range def.Components {
		c = m.boundComponent(c, in)
		// Persist resource intent BEFORE an external process/container is created.
		rid := fmt.Sprintf("pa-%s-%d-%s", key, generation, c.Name)
		intent := Resource{Component: c.Name, Runtime: c.Runtime, ID: rid}
		if err := m.update(func() { in.Resources = append(in.Resources, intent) }); err != nil {
			return fail(err)
		}
		err := m.step(op, "start:"+c.Name, func() (Receipt, error) {
			res, e := m.driver.Start(ctx, c, paths, rid)
			// Even failed starts can own a created resource. Keep its identity.
			pe := m.update(func() { in.Resources[len(in.Resources)-1] = res })
			if e != nil {
				return Receipt{}, errors.Join(e, pe)
			}
			return Receipt{Status: "succeeded"}, pe
		})
		if err != nil {
			return fail(err)
		}
		if err = m.probe(ctx, op, c, paths, in.Resources[len(in.Resources)-1]); err != nil {
			return fail(err)
		}
	}
	if err := m.hook(ctx, op, def, "after_start", paths); err != nil {
		return fail(err)
	}
	if err := m.checkReady(ctx, op, def, in, paths); err != nil {
		return fail(err)
	}
	return m.update(func() { in.State = "ready"; in.ReadyGeneration = in.Generation; in.Error = "" })
}

// Use the same Runner-owned identity for startup and subsequent readiness.
// Manifest values never override the live instance or its management token.
func (m *Manager) boundComponent(c Component, in *Instance) Component {
	c.Env = clone(c.Env)
	if c.Env == nil {
		c.Env = map[string]string{}
	}
	c.Env["PANTHEON_FLEET_ID"] = m.owner
	c.Env["PANTHEON_NODE_ID"] = m.node
	c.Env["PANTHEON_INSTANCE_ID"] = in.ID
	c.Env["PANTHEON_APP_REVISION"] = in.Digest
	c.Env["PANTHEON_INSTANCE_GENERATION"] = fmt.Sprint(in.Generation)
	c.Env["PANTHEON_APP_RPC_TOKEN"] = m.rpcCredential(in.ID, in.Digest, in.Generation)
	c.Env["PANTHEON_APP_CACHE"] = filepath.Join(m.root, "cache", in.AppID)
	c.Env["PANTHEON_APP_SCOPE"] = in.Scope
	delete(c.Env, "PANTHEON_MODEL_CREDENTIALS")
	for key := range c.Env {
		if strings.HasPrefix(key, "PANTHEON_GROUP_") {
			delete(c.Env, key)
		}
	}
	if c.GroupNetwork {
		c.groupOverlayRoot = filepath.Join(m.root, "group-overlays")
	}
	if c.GroupPlatformNetwork != "" && m.platform.Mode != "" {
		c.Env["PANTHEON_GROUP_PLATFORM_ADDRESS"] = m.platform.Address
		c.Env["PANTHEON_GROUP_PLATFORM_INTERFACE"] = m.platform.Interface
	}
	if c.GroupPeer {
		c.groupPeerDir, _ = groupcredentials.RuntimePath(m.groupRuntimeRoot(), m.groupRuntimeBinding(in, in.Generation))
		c.Env["PANTHEON_GROUP_CREDENTIALS"] = c.groupPeerDir
		if c.Runtime == "container" {
			c.Env["PANTHEON_GROUP_CREDENTIALS"] = groupPeerContainerPath
		}
	}
	if in.AppID == "model-service" && !c.GroupPeer {
		if root, err := filepath.Abs(filepath.Join(m.root, "model-credentials")); err == nil {
			c.Env["PANTHEON_MODEL_CREDENTIALS"] = root
		}
	}
	return c
}
func (m *Manager) probe(ctx context.Context, op *Operation, c Component, p Paths, r Resource) error {
	return m.step(op, "readiness:"+c.Name, func() (Receipt, error) {
		cc, cancel := context.WithTimeout(ctx, time.Duration(c.Readiness.TimeoutSeconds)*time.Second)
		defer cancel()
		return Receipt{Status: "succeeded"}, m.driver.Probe(cc, c, p, r)
	})
}
func (m *Manager) dependencies(ctx context.Context, op *Operation, def Definition) error {
	if def.Dependencies.ContainerEngine == nil {
		return nil
	}
	d, ok := m.driver.(dependencyDriver)
	if !ok {
		return fmt.Errorf("Runner does not support App dependencies")
	}
	return m.step(op, "dependency:container-engine", func() (Receipt, error) {
		return d.PrepareDependencies(ctx, def.Dependencies)
	})
}
func (m *Manager) checkReady(ctx context.Context, op *Operation, d Definition, in *Instance, p Paths) error {
	if len(in.Resources) != len(d.Components) {
		return fmt.Errorf("incomplete component set")
	}
	for n, c := range d.Components {
		if err := m.probe(ctx, op, m.boundComponent(c, in), p, in.Resources[n]); err != nil {
			_ = m.update(func() { in.State = "degraded"; in.Error = err.Error() })
			return err
		}
	}
	return nil
}
func (m *Manager) stop(ctx context.Context, op *Operation, d Definition, in *Instance, p Paths) error {
	if in.State == "stopped" && len(in.Resources) == 0 && len(in.Reservations) == 0 {
		return nil
	}
	// The running generation must remain reachable while the App drains. A
	// blocked stop may need the existing editor to finish syncing its document.
	m.mu.Lock()
	if op.Request.IfIdle && !m.idleDueLocked(in, time.Now()) {
		m.mu.Unlock()
		return nil // Another window/call arrived while this stop was queued.
	}
	in.State = "draining"
	in.ReadyGeneration = 0
	m.usageLocked(in.ID, time.Now()).stopping = op.Request.IfIdle
	err := m.persist()
	m.mu.Unlock()
	if err != nil {
		return err
	}
	defer func() { m.mu.Lock(); m.usageLocked(in.ID, time.Now()).stopping = false; m.mu.Unlock() }()
	fail := func(e error) error {
		_ = m.update(func() { in.State = "stop_blocked"; in.Error = e.Error() })
		return e
	}
	if err := m.hook(ctx, op, d, "before_stop", p); err != nil {
		return fail(err)
	}
	for len(in.Resources) > 0 {
		n := len(in.Resources) - 1
		r := in.Resources[n]
		var c Component
		for _, part := range d.Components {
			if part.Name == r.Component {
				c = part
			}
		}
		err := m.step(op, "stop:"+r.Component, func() (Receipt, error) {
			if e := m.driver.Stop(ctx, c, r); e != nil {
				return Receipt{}, e
			}
			alive, e := m.driver.Alive(ctx, r)
			if e != nil {
				return Receipt{}, e
			}
			if alive {
				return Receipt{}, fmt.Errorf("component has not exited")
			}
			if e := m.driver.Release(ctx, r); e != nil {
				return Receipt{}, e
			}
			return Receipt{Status: "succeeded"}, nil
		})
		if err != nil {
			return fail(err)
		}
		if err = m.update(func() { in.Resources = in.Resources[:n] }); err != nil {
			return err
		}
	}
	if err := m.hook(ctx, op, d, "after_stop", p); err != nil {
		return fail(err)
	}
	if err := m.clearGroupNetwork(ctx, in); err != nil {
		return fail(err)
	}
	if err := m.clearGroupPeerRuntime(in, in.Generation); err != nil {
		return fail(err)
	}
	return m.update(func() { in.State = "stopped"; in.Error = ""; in.Generation++; in.Reservations = nil })
}
func (m *Manager) uninstall(ctx context.Context, op *Operation, inst *Installation) error {
	if inst == nil || inst.State == "absent" {
		return nil
	}
	// Shared install is retained while ANY scope still uses it; no implicit kill.
	snapshot := m.Snapshot()
	for _, in := range snapshot.Instances {
		if in.Digest == inst.Digest && (in.State != "stopped" || len(in.Resources) > 0 || len(in.Reservations) > 0) {
			return fmt.Errorf("installation in use by instance %s; stop it first", in.ID)
		}
	}
	p := m.paths(inst.Digest, op.Request.Scope)
	if err := m.update(func() { inst.State = "removing" }); err != nil {
		return err
	}
	if err := m.hook(ctx, op, inst.Definition, "before_uninstall", p); err != nil {
		return err
	}
	// Artifact cache is retained, with no installation reference. Data is never
	// removed. Cache GC is deliberately separate from user-facing uninstall.
	if err := os.RemoveAll(p.Install); err != nil {
		return err
	}
	if err := m.hook(ctx, op, inst.Definition, "after_uninstall", p); err != nil {
		return err
	}
	return m.update(func() { inst.State = "absent" })
}

// settleInterruptedInstallation finishes an install/uninstall whose Runner
// restarted mid-flight. Install hooks are never replayed: the partial
// installation is removed through the ordinary uninstall path, then the
// interrupted operations become terminal failures so callers can abort cleanly
// or install again under a new operation.
func (m *Manager) settleInterruptedInstallation(ctx context.Context, op *Operation, inst *Installation) error {
	if err := m.uninstall(ctx, op, inst); err != nil {
		return err
	}
	return m.update(func() {
		for _, other := range m.ledger.Operations {
			if other.State == "unknown" && other.Request.Digest == inst.Digest &&
				(other.Request.Action == "install" || other.Request.Action == "uninstall") {
				other.State = "failed"
				other.Error = "Runner restarted during installation; the partial installation was removed without replaying install hooks"
				other.UpdatedAt = time.Now().UTC()
			}
		}
	})
}

func (m *Manager) reconcile(ctx context.Context, op *Operation, in *Instance) error {
	if in == nil {
		return nil
	}
	if in.State == "stopped" && len(in.Resources) == 0 && len(in.Reservations) == 0 {
		return nil
	}
	allStopped := true
	for _, r := range in.Resources {
		alive, err := m.driver.Alive(ctx, r)
		if err != nil {
			return err
		}
		if alive {
			allStopped = false
		}
	}
	if allStopped {
		for _, r := range in.Resources {
			if err := m.driver.Release(ctx, r); err != nil {
				return err
			}
		}
		if err := m.clearGroupNetwork(ctx, in); err != nil {
			return err
		}
		if err := m.clearGroupPeerRuntime(in, in.Generation); err != nil {
			return err
		}
		return m.update(func() {
			in.State = "stopped"
			in.Resources = []Resource{}
			in.Error = ""
			in.Generation++
			in.ReadyGeneration = 0
			in.Reservations = nil
		})
	}
	// A live component does not prove an interrupted after_start/stop hook ran.
	// Mark recoverable but never advertise ready without an explicit start probe.
	if in.State == "recovered" {
		return nil
	}
	return m.update(func() {
		in.State = "recovered"
		in.Error = "Live resources recovered; stop safely before starting a new generation"
		// These are still the original processes, with their original injected
		// identity. Preserve it so an editor can reconnect and flush before stop.
	})
}
