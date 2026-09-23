package lifecycle

// The node owns engine suspension. The connector only fences admission; it
// never receives Fleet process privileges or authority to release resources.
import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"time"
)

type ModelIdleBinding struct {
	InstanceID string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation uint64 `json:"generation"`
}
type ModelIdleRegistration struct {
	ID             string           `json:"id"`
	Revision       uint64           `json:"revision"`
	Connector      ModelIdleBinding `json:"connector"`
	Engine         ModelIdleBinding `json:"engine"`
	ConfigRevision string           `json:"config_revision"`
	IdleSeconds    int              `json:"idle_seconds"`
}
type ModelIdleManaged struct {
	Scope            string `json:"scope"`
	RecipeID         string `json:"recipe_id"`
	ContextLength    int    `json:"context_length"`
	Parallel         int    `json:"parallel"`
	KeepAliveSeconds int    `json:"keep_alive_seconds"`
	MemoryBytes      uint64 `json:"memory_bytes"`
	LoadPolicy       string `json:"load_policy"`
}
type ModelIdleConfig struct {
	Engine         string           `json:"engine"`
	Endpoint       string           `json:"endpoint"`
	CredentialFile string           `json:"credential_file"`
	Managed        ModelIdleManaged `json:"managed"`
}
type ModelIdle struct {
	ID             string           `json:"id"`
	Revision       uint64           `json:"revision"` // owner policy CAS; runtime phases do not change it
	Enabled        bool             `json:"enabled"`
	State          string           `json:"state"`
	Connector      ModelIdleBinding `json:"connector"`
	Engine         ModelIdleBinding `json:"engine"`
	ConfigRevision string           `json:"config_revision"`
	Configuration  ModelIdleConfig  `json:"configuration"`
	IdleSeconds    int              `json:"idle_seconds"`
	Cycle          uint64           `json:"cycle"`
	FenceID        string           `json:"fence_id,omitempty"`
	IdleEpoch      uint64           `json:"idle_epoch"`
	StopOperation  string           `json:"stop_operation,omitempty"`
	StartOperation string           `json:"start_operation,omitempty"`
	ResumeRevision string           `json:"resume_revision,omitempty"`
	WakeRequested  bool             `json:"wake_requested"`
	HoldUntil      time.Time        `json:"hold_until"`
	Error          string           `json:"error,omitempty"`
}
type modelIdleReceipt struct {
	Protocol       int    `json:"protocol"`
	Phase          string `json:"phase"`
	Epoch          uint64 `json:"idle_epoch"`
	SuspendID      string `json:"suspend_id"`
	ConfigRevision string `json:"config_revision"`
	ResumeRevision string `json:"resume_revision"`
	Fenced         bool   `json:"admission_fenced"`
}
type modelRPCResult struct {
	ConfigRevision string           `json:"config_revision"`
	Accepting      bool             `json:"accepting"`
	Status         string           `json:"status"`
	SafeToStop     bool             `json:"safe_to_stop"`
	EngineIdle     modelIdleReceipt `json:"engine_idle"`
	Idle           modelIdleReceipt `json:"idle"`
}

var errModelRPCRejected = errors.New("owned model connector rejected the idle operation; inspect recovery")

func (m *Manager) modelRPC(ctx context.Context, binding ModelIdleBinding, method string, args any) (modelRPCResult, error) {
	var out modelRPCResult
	endpoint, err := m.Service(binding.InstanceID, binding.Revision, binding.Generation, "backend", "http")
	if err != nil {
		return out, err
	}
	token, err := m.RPCCredential(binding.InstanceID, binding.Revision, binding.Generation)
	if err != nil {
		return out, err
	}
	release, err := m.BeginUse(binding.InstanceID, binding.Revision, binding.Generation)
	if err != nil {
		return out, err
	}
	defer release()
	body, err := json.Marshal(map[string]any{"method": method, "args": args})
	if err != nil {
		return out, err
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "POST", endpoint+"/rpc", bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Fleet-RPC-Token", token)
	// The endpoint comes only from the exact owned resource. Never forward a
	// node credential through environment HTTP proxies or vendor redirects.
	transport := &http.Transport{Proxy: nil, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return out, fmt.Errorf("owned connector observation unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		if response.StatusCode >= 400 && response.StatusCode < 500 {
			return out, errModelRPCRejected
		}
		return out, fmt.Errorf("owned connector observation unavailable")
	}
	b, err := io.ReadAll(io.LimitReader(response.Body, 32769))
	if err != nil || len(b) > 32768 || json.Unmarshal(b, &out) != nil {
		return out, errModelRPCRejected
	}
	return out, nil
}

func (m *Manager) modelBoundLocked(b ModelIdleBinding, scope string, ready bool) (*Instance, error) {
	in, err := m.boundLocked(b.InstanceID, b.Revision, b.Generation)
	if err != nil {
		return nil, err
	}
	if in.AppID != "model-service" || in.Scope != scope || in.ID != m.instanceID(b.Revision, scope) {
		return nil, fmt.Errorf("model idle binding does not match its owned deployment")
	}
	if ready && (in.State != "ready" || in.ReadyGeneration != in.Generation) {
		return nil, fmt.Errorf("model idle coordination requires a verified ready generation")
	}
	install := m.ledger.Installations[in.Digest]
	if install == nil || install.State != "installed" || install.Definition.AppID != in.AppID {
		return nil, fmt.Errorf("model idle installation unavailable")
	}
	return in, nil
}

func (m *Manager) modelPendingLocked(b ModelIdleBinding) bool {
	for _, op := range m.ledger.Operations {
		if (op.State == "running" || op.State == "queued") && op.Request.Digest == b.Revision &&
			m.instanceID(op.Request.Digest, op.Request.Scope) == b.InstanceID {
			return true
		}
	}
	return false
}

// Registration derives configuration from the installed immutable engine,
// never accepts an executable, endpoint, credential file or resource override.
func (m *Manager) RegisterModelIdle(ctx context.Context, q ModelIdleRegistration) (ModelIdle, error) {
	var empty ModelIdle
	if !nameRE.MatchString(q.ID) || len(q.ID) > 64 || q.Revision == ^uint64(0) || !digestRE.MatchString(q.ConfigRevision) || q.IdleSeconds < 1 || q.IdleSeconds > 86400 {
		return empty, fmt.Errorf("invalid model idle policy")
	}
	// A slow connector must not keep an expired management request waiting on
	// a mutex and later register a policy after its caller has given up.
	for !m.modelIdleSerial.TryLock() {
		select {
		case <-ctx.Done():
			return empty, ctx.Err()
		case <-m.ctx.Done():
			return empty, fmt.Errorf("Runner is shutting down")
		case <-time.After(20 * time.Millisecond):
		}
	}
	defer m.modelIdleSerial.Unlock()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	m.mu.Lock()
	check := func() error {
		old := m.ledger.ModelIdle[q.ID]
		if (old == nil && q.Revision != 0) || (old != nil && (old.Revision != q.Revision || old.State != "active" && old.State != "disabled")) {
			return fmt.Errorf("model idle policy changed or needs recovery")
		}
		if old == nil && len(m.ledger.ModelIdle) >= 128 {
			return fmt.Errorf("node model idle policy limit reached")
		}
		if _, e := m.modelBoundLocked(q.Connector, "model-"+q.ID, true); e != nil {
			return e
		}
		if _, e := m.modelBoundLocked(q.Engine, "engine-"+q.ID, true); e != nil {
			return e
		}
		if m.modelPendingLocked(q.Connector) || m.modelPendingLocked(q.Engine) {
			return fmt.Errorf("finish existing lifecycle operations before idle registration")
		}
		return nil
	}
	if err := check(); err != nil {
		m.mu.Unlock()
		return empty, err
	}
	definition := clone(m.ledger.Installations[q.Engine.Revision].Definition)
	m.mu.Unlock()
	if len(definition.Components) != 1 || definition.Components[0].Name != "backend" || definition.Components[0].Resources == nil || definition.Components[0].Runtime != "process" {
		return empty, fmt.Errorf("idle policy requires one budgeted native owned model engine")
	}
	b, err := os.ReadFile(filepath.Join(m.paths(q.Engine.Revision, "engine-"+q.ID).Package, "engine-config.json"))
	if err != nil || len(b) > 8192 {
		return empty, fmt.Errorf("owned engine configuration unavailable")
	}
	var managed ModelIdleManaged
	if json.Unmarshal(b, &managed) != nil || (managed.LoadPolicy != "warm" && managed.LoadPolicy != "on_demand") {
		return empty, fmt.Errorf("only explicitly warm or on-demand engines can idle")
	}
	if managed.LoadPolicy == "warm" && q.IdleSeconds < managed.KeepAliveSeconds {
		return empty, fmt.Errorf("engine idle policy must preserve model warm TTL")
	}
	managed.Scope, managed.MemoryBytes = "engine-"+q.ID, definition.Components[0].Resources.MemoryBytes
	engine := ""
	if strings.HasPrefix(managed.RecipeID, "ollama-") {
		engine = "ollama"
	}
	if strings.HasPrefix(managed.RecipeID, "llmster-") {
		engine = "lmstudio"
	}
	if engine == "" {
		return empty, fmt.Errorf("unsupported owned idle engine")
	}
	endpoint, err := m.Service(q.Engine.InstanceID, q.Engine.Revision, q.Engine.Generation, "backend", "http")
	if err != nil {
		return empty, err
	}
	config := ModelIdleConfig{Engine: engine, Endpoint: endpoint + "/v1", Managed: managed}
	preview, err := m.modelRPC(ctx, q.Connector, "preview_configuration", modelConfigArgs(config))
	if err != nil {
		return empty, err
	}
	status, err := m.modelRPC(ctx, q.Connector, "status", map[string]any{})
	if err != nil {
		return empty, err
	}
	if preview.ConfigRevision != q.ConfigRevision || status.ConfigRevision != q.ConfigRevision || !status.Accepting || status.EngineIdle.Protocol != 1 || status.EngineIdle.Fenced {
		return empty, fmt.Errorf("connector configuration or idle protocol does not match the owned engine")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if err = ctx.Err(); err != nil {
		return empty, err
	}
	if err = check(); err != nil {
		return empty, err
	}
	old := m.ledger.ModelIdle[q.ID]
	next := &ModelIdle{ID: q.ID, Revision: q.Revision + 1, Enabled: true, State: "active", Connector: q.Connector, Engine: q.Engine,
		ConfigRevision: q.ConfigRevision, Configuration: config, IdleSeconds: q.IdleSeconds, IdleEpoch: status.EngineIdle.Epoch,
		HoldUntil: time.Now().Add(time.Minute)}
	m.ledger.ModelIdle[q.ID] = next
	c, e := m.ledger.Instances[q.Connector.InstanceID], m.ledger.Instances[q.Engine.InstanceID]
	oldCKeep, oldEKeep := c.KeepAlive, e.KeepAlive
	c.KeepAlive, e.KeepAlive = true, true
	if err = m.persist(); err != nil {
		c.KeepAlive, e.KeepAlive = oldCKeep, oldEKeep
		if old == nil {
			delete(m.ledger.ModelIdle, q.ID)
		} else {
			m.ledger.ModelIdle[q.ID] = old
		}
		return empty, err
	}
	return clone(*next), nil
}

func modelConfigArgs(c ModelIdleConfig) map[string]any {
	return map[string]any{"config": map[string]string{"engine": c.Engine, "endpoint": c.Endpoint, "credential_file": ""}, "managed": c.Managed}
}

// Wake records demand before the caller obtains its frozen inference binding.
// It never submits/replays inference. Poll this same policy revision for active.
func (m *Manager) WakeModelIdle(id string, revision uint64) (ModelIdle, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.ledger.ModelIdle[id]
	if m.closed || p == nil || p.Revision != revision || !p.Enabled || p.State == "recovery_required" {
		return ModelIdle{}, fmt.Errorf("model idle policy is disabled, stale or requires recovery")
	}
	if _, err := m.modelBoundLocked(p.Connector, "model-"+p.ID, true); err != nil {
		return ModelIdle{}, err
	}
	if p.State == "active" {
		if _, err := m.modelBoundLocked(p.Engine, "engine-"+p.ID, true); err != nil {
			return ModelIdle{}, err
		}
	}
	next := clone(*p)
	next.HoldUntil = time.Now().Add(time.Minute)
	next.WakeRequested = p.State != "active"
	if err := m.saveModelIdleLocked(&next); err != nil {
		return ModelIdle{}, err
	}
	m.notifyModelIdle()
	return clone(next), nil
}

func (m *Manager) DisableModelIdle(id string, revision uint64) (ModelIdle, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.ledger.ModelIdle[id]
	if m.closed || p == nil || p.Revision != revision || revision == ^uint64(0) {
		return ModelIdle{}, fmt.Errorf("model idle policy changed")
	}
	next := clone(*p)
	next.Enabled, next.WakeRequested, next.State = false, false, "disabled"
	next.Revision++
	if err := m.saveModelIdleLocked(&next); err != nil {
		return ModelIdle{}, err
	}
	return clone(next), nil
}

func (m *Manager) saveModelIdleLocked(next *ModelIdle) error {
	old := m.ledger.ModelIdle[next.ID]
	if reflect.DeepEqual(old, next) {
		return nil
	}
	m.ledger.ModelIdle[next.ID] = next
	if err := m.persist(); err != nil {
		m.ledger.ModelIdle[next.ID] = old
		return err
	}
	return nil
}

func (m *Manager) modelIdleOwnsLocked(id string) bool {
	for _, p := range m.ledger.ModelIdle {
		if p.Enabled && (p.Connector.InstanceID == id || p.Engine.InstanceID == id) {
			return true
		}
	}
	return false
}
func (m *Manager) modelEngineFencedLocked(id string) bool {
	for _, p := range m.ledger.ModelIdle {
		if p.Enabled && p.Engine.InstanceID == id && p.State != "active" && p.State != "fencing" {
			return true
		}
	}
	return false
}

func (m *Manager) invalidateModelIdleLocked(req Request) {
	if req.Action == "install" {
		return
	}
	for _, p := range m.ledger.ModelIdle {
		if !p.Enabled {
			continue
		}
		for _, b := range []ModelIdleBinding{p.Connector, p.Engine} {
			in := m.ledger.Instances[b.InstanceID]
			if in == nil || req.Digest != in.Digest {
				continue
			}
			if req.Action != "uninstall" && (req.Scope != in.Scope || (req.Generation != in.Generation && req.Generation != b.Generation)) {
				continue
			}
			p.Enabled, p.WakeRequested, p.State = false, false, "disabled"
			if p.Revision < ^uint64(0) {
				p.Revision++
			}
			p.Error = "An explicit lifecycle action superseded automatic engine idle coordination"
			break
		}
	}
}

func (m *Manager) checkModelIdleOperationLocked(op *Operation) error {
	if op.ModelIdleID == "" {
		return nil
	}
	p := m.ledger.ModelIdle[op.ModelIdleID]
	if p == nil || !p.Enabled || ((op.Request.Action != "stop" || p.State != "stopping" || p.StopOperation != op.Request.OperationID) &&
		(op.Request.Action != "start" || p.State != "waking" || p.StartOperation != op.Request.OperationID)) {
		return fmt.Errorf("model idle operation was superseded; no new process action is authorized")
	}
	if _, err := m.modelBoundLocked(p.Connector, "model-"+p.ID, true); err != nil {
		return err
	}
	if _, err := m.modelBoundLocked(p.Engine, "engine-"+p.ID, false); err != nil {
		return err
	}
	if op.Request.Action == "stop" {
		u := m.usageLocked(p.Engine.InstanceID, time.Now())
		if u.calls > 0 || len(u.leases) > 0 {
			return fmt.Errorf("owned engine still has direct callers or windows")
		}
	}
	return nil
}

func (m *Manager) notifyModelIdle() {
	select {
	case m.modelIdleWake <- struct{}{}:
	default:
	}
}
func (m *Manager) observeModelIdle() {
	defer m.jobs.Done()
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-m.ctx.Done():
			return
		case <-ticker.C:
		case <-m.modelIdleWake:
		}
		m.mu.Lock()
		ids := []string{}
		for id, p := range m.ledger.ModelIdle {
			if p.Enabled && p.State != "recovery_required" {
				ids = append(ids, id)
			}
		}
		m.mu.Unlock()
		for _, id := range ids {
			m.driveModelIdle(id)
		}
	}
}

func (m *Manager) driveModelIdle(id string) {
	m.modelIdleSerial.Lock()
	defer m.modelIdleSerial.Unlock()
	for n := 0; n < 6; n++ {
		m.mu.Lock()
		p := m.ledger.ModelIdle[id]
		if m.closed || p == nil || !p.Enabled || p.State == "recovery_required" {
			m.mu.Unlock()
			return
		}
		copy := clone(*p)
		m.mu.Unlock()
		if !m.stepModelIdle(copy) {
			return
		}
	}
}

// Commit only to the policy/cycle that initiated the observation. Preserve any
// wake/hold arriving during HTTP; disabling/changing policy wins immediately.
func (m *Manager) advanceModelIdle(before ModelIdle, change func(*ModelIdle)) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.ledger.ModelIdle[before.ID]
	if m.closed || p == nil || !p.Enabled || p.Revision != before.Revision || p.Cycle != before.Cycle || p.State != before.State {
		return false
	}
	next := clone(*p)
	change(&next)
	return m.saveModelIdleLocked(&next) == nil
}

func (m *Manager) modelIdleFailure(p ModelIdle, err error) bool {
	m.advanceModelIdle(p, func(next *ModelIdle) {
		next.Error = "Idle coordination could not verify the owned service; inspect recovery"
		if errors.Is(err, errModelRPCRejected) {
			next.State = "recovery_required"
		}
	})
	return false
}

func (m *Manager) stepModelIdle(p ModelIdle) bool {
	m.mu.Lock()
	_, err := m.modelBoundLocked(p.Connector, "model-"+p.ID, true)
	engine, engineErr := m.modelBoundLocked(p.Engine, "engine-"+p.ID, false)
	if engine != nil {
		engine = clone(engine)
	}
	m.mu.Unlock()
	if err != nil {
		return m.modelIdleFailure(p, errModelRPCRejected)
	}
	// During a recorded start/stop only that exact operation may advance the
	// generation; all other generation changes require explicit reconciliation.
	if engineErr != nil && p.State != "stopping" && p.State != "waking" {
		return m.modelIdleFailure(p, errModelRPCRejected)
	}
	switch p.State {
	case "active":
		if time.Now().Before(p.HoldUntil) {
			return false
		}
		if engine == nil || engine.State != "ready" || engine.ReadyGeneration != engine.Generation {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		if p.Cycle == ^uint64(0) {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		status, e := m.modelRPC(m.ctx, p.Connector, "status", map[string]any{})
		if e != nil {
			return m.modelIdleFailure(p, e)
		}
		if status.ConfigRevision != p.ConfigRevision || !status.Accepting || status.EngineIdle.Protocol != 1 || status.EngineIdle.Fenced {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		return m.advanceModelIdle(p, func(next *ModelIdle) {
			if time.Now().Before(next.HoldUntil) {
				return
			}
			next.Cycle++
			sum := sha256.Sum256([]byte(fmt.Sprintf("%s/%d/%d", next.ID, next.Revision, next.Cycle)))
			next.FenceID = "model-idle-" + hex.EncodeToString(sum[:16])
			next.IdleEpoch = status.EngineIdle.Epoch
			next.State = "fencing"
			next.Error = ""
		})
	case "fencing":
		if engine == nil || engine.State != "ready" || engine.ReadyGeneration != engine.Generation {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		result, e := m.modelRPC(m.ctx, p.Connector, "idle_drain", map[string]any{"suspend_id": p.FenceID, "config_revision": p.ConfigRevision, "idle_seconds": p.IdleSeconds, "idle_epoch": p.IdleEpoch})
		if e != nil {
			return m.modelIdleFailure(p, e)
		}
		if result.ConfigRevision != p.ConfigRevision || result.Idle.Protocol != 1 {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		if !result.SafeToStop {
			// Maintenance and an idle model unload temporarily fence admission.
			// They are busy observations, never authorization to stop the engine.
			if result.Idle.Fenced || (!result.Accepting && result.Status != "busy") {
				return m.modelIdleFailure(p, errModelRPCRejected)
			}
			if result.Status != "waiting" && result.Status != "busy" && result.Status != "models_loaded_or_unknown" {
				return m.modelIdleFailure(p, errModelRPCRejected)
			}
			if p.WakeRequested || time.Now().Before(p.HoldUntil) {
				return m.advanceModelIdle(p, func(next *ModelIdle) { next.State = "active"; next.WakeRequested = false; next.Error = "" })
			}
			return false
		}
		if result.Accepting || result.Status != "fenced" || result.Idle.Phase != "fenced" || !result.Idle.Fenced || result.Idle.SuspendID != p.FenceID || result.Idle.Epoch != p.IdleEpoch+1 || result.Idle.ConfigRevision != p.ConfigRevision {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		m.mu.Lock()
		current := m.ledger.ModelIdle[p.ID]
		wake := current != nil && current.WakeRequested
		u := m.usageLocked(p.Engine.InstanceID, time.Now())
		busy := u.calls > 0 || len(u.leases) > 0
		m.mu.Unlock()
		if wake {
			return m.advanceModelIdle(p, func(next *ModelIdle) { next.State = "rebinding"; next.ResumeRevision = ""; next.Error = "" })
		}
		if busy {
			return false
		}
		return m.enqueueModelIdle(p, "stop")
	case "stopping", "waking":
		return m.finishModelIdleOperation(p)
	case "sleeping":
		if engine == nil || engine.State != "stopped" || len(engine.Resources) > 0 || len(engine.Reservations) > 0 {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		if !p.WakeRequested {
			return false
		}
		return m.enqueueModelIdle(p, "start")
	case "rebinding":
		if engine == nil || engine.State != "ready" || engine.ReadyGeneration != engine.Generation {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		endpoint, e := m.Service(p.Engine.InstanceID, p.Engine.Revision, p.Engine.Generation, "backend", "http")
		if e != nil {
			return m.modelIdleFailure(p, e)
		}
		config := p.Configuration
		config.Endpoint = endpoint + "/v1"
		args := modelConfigArgs(config)
		preview, e := m.modelRPC(m.ctx, p.Connector, "preview_configuration", args)
		if e != nil {
			return m.modelIdleFailure(p, e)
		}
		if !digestRE.MatchString(preview.ConfigRevision) || (p.ResumeRevision != "" && p.ResumeRevision != preview.ConfigRevision) {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		if p.ResumeRevision == "" {
			return m.advanceModelIdle(p, func(next *ModelIdle) { next.ResumeRevision = preview.ConfigRevision })
		}
		args["suspend_id"], args["config_revision"] = p.FenceID, p.ConfigRevision
		result, e := m.modelRPC(m.ctx, p.Connector, "idle_resume", args)
		if e != nil {
			return m.modelIdleFailure(p, e)
		}
		if result.Status == "waiting" {
			return false
		}
		if result.Status != "resumed" || !result.Accepting || result.SafeToStop || result.ConfigRevision != p.ResumeRevision || result.Idle.Phase != "resumed" || result.Idle.Fenced || result.Idle.SuspendID != p.FenceID || result.Idle.Epoch != p.IdleEpoch+1 || result.Idle.ResumeRevision != p.ResumeRevision {
			return m.modelIdleFailure(p, errModelRPCRejected)
		}
		return m.advanceModelIdle(p, func(next *ModelIdle) {
			next.State = "active"
			next.Configuration = config
			next.ConfigRevision = p.ResumeRevision
			next.WakeRequested = false
			next.IdleEpoch = result.Idle.Epoch
			next.HoldUntil = time.Now().Add(time.Minute)
			next.Error = ""
		})
	}
	return false
}

func (m *Manager) enqueueModelIdle(before ModelIdle, action string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.ledger.ModelIdle[before.ID]
	if m.closed || p == nil || !p.Enabled || p.Revision != before.Revision || p.State != before.State || p.Cycle != before.Cycle {
		return false
	}
	if action == "stop" && p.WakeRequested {
		return false
	}
	in, err := m.modelBoundLocked(p.Engine, "engine-"+p.ID, action == "stop")
	if err != nil || m.modelPendingLocked(p.Engine) || m.modelPendingLocked(p.Connector) {
		return false
	}
	if action == "start" && (in.State != "stopped" || len(in.Resources) > 0 || len(in.Reservations) > 0) {
		return false
	}
	u := m.usageLocked(in.ID, time.Now())
	if action == "stop" && (u.calls > 0 || len(u.leases) > 0) {
		return false
	}
	id := action + "-" + p.FenceID
	if m.ledger.Operations[id] != nil {
		return false
	} // never mint another ID after uncertainty
	next := clone(*p)
	if action == "stop" {
		next.State = "stopping"
		next.StopOperation = id
	} else {
		next.State = "waking"
		next.StartOperation = id
	}
	op := &Operation{ModelIdleID: p.ID, Request: Request{Protocol: 1, OperationID: id, Action: action, Digest: in.Digest, Scope: in.Scope, Generation: in.Generation}, State: "queued", Steps: []Step{}, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	m.ledger.ModelIdle[p.ID] = &next
	m.ledger.Operations[id] = op
	pruned := m.pruneModelIdleOperationsLocked(&next)
	if m.persist() != nil {
		m.ledger.ModelIdle[p.ID] = p
		delete(m.ledger.Operations, id)
		for id, previous := range pruned {
			m.ledger.Operations[id] = previous
		}
		return false
	}
	m.jobs.Add(1)
	go m.execute(id)
	return true
}

// Automatic idle cycles must not grow the ledger forever. Keep the current
// operation receipts plus eight recent successes. Unknown/failed/in-flight and
// all user operations remain available for explicit inspection/recovery.
func (m *Manager) pruneModelIdleOperationsLocked(p *ModelIdle) map[string]*Operation {
	var candidates []*Operation
	for id, op := range m.ledger.Operations {
		if op.ModelIdleID == p.ID && op.State == "succeeded" && id != p.StopOperation && id != p.StartOperation {
			candidates = append(candidates, op)
		}
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].CreatedAt.After(candidates[j].CreatedAt) })
	removed := map[string]*Operation{}
	for i := 8; i < len(candidates); i++ {
		op := candidates[i]
		removed[op.Request.OperationID] = op
		delete(m.ledger.Operations, op.Request.OperationID)
	}
	return removed
}

func (m *Manager) finishModelIdleOperation(before ModelIdle) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.ledger.ModelIdle[before.ID]
	if m.closed || p == nil || !p.Enabled || p.Revision != before.Revision || p.State != before.State || p.Cycle != before.Cycle {
		return false
	}
	id := p.StopOperation
	if p.State == "waking" {
		id = p.StartOperation
	}
	op := m.ledger.Operations[id]
	if op != nil && (op.State == "queued" || op.State == "running") {
		return false
	}
	next := clone(*p)
	in := m.ledger.Instances[p.Engine.InstanceID]
	expectedAction, expectedState, nextState := "stop", "stopped", "sleeping"
	if p.State == "waking" {
		expectedAction, expectedState, nextState = "start", "ready", "rebinding"
	}
	if op == nil || op.ModelIdleID != p.ID || op.State != "succeeded" || op.Request.Action != expectedAction || op.Request.Digest != p.Engine.Revision || op.Request.Scope != "engine-"+p.ID || op.Request.Generation != p.Engine.Generation || in == nil || in.Digest != p.Engine.Revision || in.Generation != p.Engine.Generation+1 || in.State != expectedState || (expectedAction == "stop" && (len(in.Resources) > 0 || len(in.Reservations) > 0)) || (expectedAction == "start" && in.ReadyGeneration != in.Generation) {
		next.State = "recovery_required"
		next.Error = "Owned engine operation outcome needs verification; it has not been replayed"
	} else {
		next.Engine.Generation = in.Generation
		next.State = nextState
		next.ResumeRevision = ""
		next.Error = ""
	}
	if m.saveModelIdleLocked(&next) != nil {
		return false
	}
	return next.State != "recovery_required"
}

func (m *Manager) ModelIdleStatus(id string) (ModelIdle, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.ledger.ModelIdle[id]
	if m.closed || p == nil {
		return ModelIdle{}, fmt.Errorf("model idle policy is unavailable")
	}
	return clone(*p), nil
}
