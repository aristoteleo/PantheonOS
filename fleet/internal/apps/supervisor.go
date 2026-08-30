// Package apps supervises long-running App instances on a Node (the App
// model's P3: the runner is the sole supervisor everywhere).
//
// The supervisor is deliberately manifest-agnostic: it runs a concrete
// process spec the control plane sends. Resolving an App's manifest into a
// command line (for a python App: the `python -m pantheon.apphost …`
// invocation) is the control plane's job — the Go side stays free of
// manifest parsing, and the cross-language boundary remains a JSON shape.
package apps

import (
	"context"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Spec tells the supervisor what to run and how to report it.
type Spec struct {
	AppID     string            `json:"app_id"`
	Version   string            `json:"version,omitempty"`
	Scope     string            `json:"scope,omitempty"` // app|window|node; keys the instance with AppID
	ServiceID string            `json:"service_id,omitempty"`
	Command   []string          `json:"command"` // argv; Command[0] resolved on PATH
	Dir       string            `json:"dir,omitempty"`
	Env       map[string]string `json:"env,omitempty"` // per-instance additions (e.g. scoped NATS creds)
}

// Restart policy: exponential backoff, capped; after maxCrashes consecutive
// fast failures the instance is marked crashed and left down.
const (
	backoffStart = 1 * time.Second
	backoffMax   = 30 * time.Second
	maxCrashes   = 5
	// A process that lived this long resets the crash counter: it was
	// running, not crash-looping.
	healthyAfter = 20 * time.Second
)

type instance struct {
	spec    Spec
	cancel  context.CancelFunc
	mu      sync.Mutex
	health  string // starting|healthy|degraded|stopped|crashed
	crashes int
}

func (i *instance) setHealth(h string) {
	i.mu.Lock()
	i.health = h
	i.mu.Unlock()
}

// Supervisor owns every App instance on this Node.
type Supervisor struct {
	mu        sync.Mutex
	instances map[string]*instance // key: app_id + "\x00" + scope
	onChange  func()               // optional: poked on any health transition
}

func New(onChange func()) *Supervisor {
	return &Supervisor{instances: map[string]*instance{}, onChange: onChange}
}

func key(appID, scope string) string {
	if scope == "" {
		scope = "app"
	}
	return appID + "\x00" + scope
}

// Start launches (or reports) an instance. Idempotent per (app_id, scope):
// starting a running instance is a no-op, matching how toolset starts behave.
func (s *Supervisor) Start(spec Spec) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	k := key(spec.AppID, spec.Scope)
	if inst, ok := s.instances[k]; ok {
		inst.mu.Lock()
		h := inst.health
		inst.mu.Unlock()
		if h != "stopped" && h != "crashed" {
			return nil
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	inst := &instance{spec: spec, cancel: cancel, health: "starting"}
	s.instances[k] = inst
	go s.supervise(ctx, inst)
	return nil
}

// Stop terminates an instance and marks it stopped (no restart).
func (s *Supervisor) Stop(appID, scope string) {
	s.mu.Lock()
	inst, ok := s.instances[key(appID, scope)]
	s.mu.Unlock()
	if !ok {
		return
	}
	inst.setHealth("stopped")
	inst.cancel()
	s.changed()
}

// List reports every instance in the registry's wire shape.
func (s *Supervisor) List() []proto.AppInstance {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]proto.AppInstance, 0, len(s.instances))
	for _, inst := range s.instances {
		inst.mu.Lock()
		out = append(out, proto.AppInstance{
			AppID:     inst.spec.AppID,
			Version:   inst.spec.Version,
			Scope:     inst.spec.Scope,
			ServiceID: inst.spec.ServiceID,
			Health:    inst.health,
		})
		inst.mu.Unlock()
	}
	return out
}

// StopAll terminates everything (runner shutdown).
func (s *Supervisor) StopAll() {
	s.mu.Lock()
	insts := make([]*instance, 0, len(s.instances))
	for _, i := range s.instances {
		insts = append(insts, i)
	}
	s.mu.Unlock()
	for _, i := range insts {
		i.setHealth("stopped")
		i.cancel()
	}
}

func (s *Supervisor) changed() {
	if s.onChange != nil {
		s.onChange()
	}
}

// supervise runs the process, restarting with backoff until stopped or
// crash-looped out.
func (s *Supervisor) supervise(ctx context.Context, inst *instance) {
	backoff := backoffStart
	for {
		if ctx.Err() != nil {
			return
		}
		started := time.Now()
		err := s.runOnce(ctx, inst)
		if ctx.Err() != nil || health(inst) == "stopped" || health(inst) == "crashed" {
			return
		}
		alive := time.Since(started)
		if alive >= healthyAfter {
			inst.mu.Lock()
			inst.crashes = 0
			inst.mu.Unlock()
			backoff = backoffStart
		} else {
			inst.mu.Lock()
			inst.crashes++
			crashes := inst.crashes
			inst.mu.Unlock()
			if crashes >= maxCrashes {
				inst.setHealth("crashed")
				s.changed()
				return
			}
		}
		inst.setHealth("degraded")
		s.changed()
		_ = err
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff *= 2
		if backoff > backoffMax {
			backoff = backoffMax
		}
	}
}

func health(inst *instance) string {
	inst.mu.Lock()
	defer inst.mu.Unlock()
	return inst.health
}

// runOnce starts the process and waits for it to exit.
func (s *Supervisor) runOnce(ctx context.Context, inst *instance) error {
	spec := inst.spec
	if len(spec.Command) == 0 {
		inst.setHealth("crashed")
		return nil
	}
	cmd := exec.CommandContext(ctx, spec.Command[0], spec.Command[1:]...)
	cmd.Dir = spec.Dir
	cmd.Env = os.Environ()
	for k, v := range spec.Env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	// Logs: inherit the runner's stdio for now; per-instance files land with
	// the log-collection capability.
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	// Liveness IS health in this cut; a bus-level ping refines it later.
	go func(i *instance) {
		t := time.NewTimer(2 * time.Second)
		defer t.Stop()
		select {
		case <-ctx.Done():
		case <-t.C:
			i.mu.Lock()
			if i.health == "starting" {
				i.health = "healthy"
			}
			i.mu.Unlock()
			s.changed()
		}
	}(inst)
	return cmd.Wait()
}
