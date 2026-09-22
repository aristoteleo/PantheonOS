package lifecycle

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// ResourceRequest describes a conservative peak estimate, including weights,
// KV cache, workspace and concurrency. MemoryBytes INCLUDES unified GPU memory;
// dedicated VRAM is separate. It is never silently reduced to fit the node.
type ResourceRequest struct {
	MemoryBytes uint64         `json:"memory_bytes"`
	Devices     []DeviceBudget `json:"devices,omitempty"`
}
type DeviceBudget struct {
	ID          string `json:"id"`
	Backend     string `json:"backend"`
	MemoryBytes uint64 `json:"memory_bytes"`
	Exclusive   bool   `json:"exclusive,omitempty"`
}
type ResourceReservation struct {
	Request   ResourceRequest `json:"request"`
	CreatedAt time.Time       `json:"created_at"`
}
type ResourcePolicy struct {
	SystemReserveBytes *uint64           `json:"system_reserve_bytes,omitempty"`
	DeviceReserveBytes map[string]uint64 `json:"device_reserve_bytes,omitempty"`
}
type ResourceStatus struct {
	Protocol  int                     `json:"protocol"`
	Inventory proto.ResourceInventory `json:"inventory"`
	Policy    ResourcePolicy          `json:"policy"`
}

var deviceIDRE = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$`)

func (r ResourceRequest) Validate() error {
	if r.MemoryBytes == 0 || r.MemoryBytes > 1<<60 || len(r.Devices) > 64 {
		return fmt.Errorf("resource request needs a positive system memory budget and at most 64 devices")
	}
	seen := map[string]bool{}
	for _, d := range r.Devices {
		if !deviceIDRE.MatchString(d.ID) || seen[d.ID] || d.MemoryBytes == 0 || d.MemoryBytes > 1<<60 || (d.Backend != "cuda" && d.Backend != "rocm" && d.Backend != "metal") {
			return fmt.Errorf("invalid or duplicate accelerator budget")
		}
		seen[d.ID] = true
		if d.Backend == "metal" && d.MemoryBytes > r.MemoryBytes {
			return fmt.Errorf("system memory budget must include unified Metal memory")
		}
	}
	return nil
}

// SetResourceSampler is configured by the Runner, not a manifest. The sampler
// runs outside the ledger lock; reservations then serialize against the fresh
// snapshot. Persistence occurs before the caller is permitted to start loading.
func (m *Manager) SetResourceSampler(sample func() proto.ResourceInventory) {
	m.mu.Lock()
	m.resourceSampler = sample
	m.mu.Unlock()
}
func (m *Manager) sampleResources() proto.ResourceInventory {
	m.mu.Lock()
	sample := m.resourceSampler
	var inv proto.ResourceInventory
	if m.caps.Resources != nil {
		inv = clone(*m.caps.Resources)
	}
	m.mu.Unlock()
	if sample != nil {
		inv = sample()
	}
	return inv
}
func (m *Manager) ResourceStatus() ResourceStatus {
	return ResourceStatus{Protocol: 1, Inventory: m.sampleResources(), Policy: clone(m.resourcePolicy)}
}
func (m *Manager) readResourcePolicy() error {
	b, err := os.ReadFile(filepath.Join(m.root, "resource-policy.json"))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if err = StrictDecode(b, &m.resourcePolicy); err != nil {
		return fmt.Errorf("invalid resource policy: %w", err)
	}
	if p := m.resourcePolicy.SystemReserveBytes; p != nil && *p > 1<<60 {
		return fmt.Errorf("invalid system reserve")
	}
	for k, v := range m.resourcePolicy.DeviceReserveBytes {
		if !deviceIDRE.MatchString(k) || v > 1<<60 {
			return fmt.Errorf("invalid device reserve")
		}
	}
	return nil
}
func availableBudget(p proto.MemoryPool, reserve uint64) uint64 {
	// Conservative admission: account for external processes AND all declared
	// leases (including loads not yet visible in telemetry). This can refuse an
	// optimistic fit; it cannot turn a missing sample into permission to overbook.
	if p.AvailableBytes == nil {
		return 0
	}
	b := min(p.TotalBytes, *p.AvailableBytes)
	if b <= reserve {
		return 0
	}
	return b - reserve
}

// admitLocked protects all managed Apps on this Runner. Claims of unknown or
// failed generations still count until actual process exit is verified.
func (m *Manager) admitLocked(inv proto.ResourceInventory, requests []ResourceRequest) error {
	if inv.Protocol != 1 || time.Since(inv.SampledAt) > 45*time.Second || inv.SampledAt.After(time.Now().Add(5*time.Second)) {
		return fmt.Errorf("resource telemetry unavailable or stale; refresh the node")
	}
	systemReserve := max(uint64(256<<20), inv.Memory.TotalBytes/4)
	if m.resourcePolicy.SystemReserveBytes != nil {
		systemReserve = *m.resourcePolicy.SystemReserveBytes
	}
	systemFree := availableBudget(inv.Memory, systemReserve)
	deviceFree := map[string]uint64{}
	devices := map[string]proto.Accelerator{}
	for _, d := range inv.Accelerators {
		devices[d.ID] = d
		reserve := max(uint64(256<<20), d.Memory.TotalBytes/20)
		if n, ok := m.resourcePolicy.DeviceReserveBytes[d.ID]; ok {
			reserve = n
		}
		deviceFree[d.ID] = availableBudget(d.Memory, reserve)
	}
	used := map[string]bool{}
	exclusive := map[string]bool{}
	subtract := func(r ResourceRequest, enforce bool) error {
		if enforce && r.MemoryBytes > systemFree {
			return fmt.Errorf("insufficient reserved system memory: need %d bytes, admission budget %d", r.MemoryBytes, systemFree)
		}
		if r.MemoryBytes > systemFree {
			systemFree = 0
		} else {
			systemFree -= r.MemoryBytes
		}
		for _, request := range r.Devices {
			d, ok := devices[request.ID]
			if enforce && (!ok || d.Backend != request.Backend) {
				return fmt.Errorf("accelerator %s (%s) is unavailable", request.ID, request.Backend)
			}
			if enforce && (exclusive[request.ID] || (request.Exclusive && used[request.ID])) {
				return fmt.Errorf("accelerator %s already reserved", request.ID)
			}
			if enforce && d.SharedMemory && request.MemoryBytes > r.MemoryBytes {
				return fmt.Errorf("unified memory must be included in the system budget")
			}
			if !d.SharedMemory {
				remaining := deviceFree[request.ID]
				if enforce && request.MemoryBytes > remaining {
					return fmt.Errorf("insufficient reserved memory on accelerator %s", request.ID)
				}
				if request.MemoryBytes > remaining {
					remaining = 0
				} else {
					remaining -= request.MemoryBytes
				}
				deviceFree[request.ID] = remaining
			}
			used[request.ID] = true
			exclusive[request.ID] = exclusive[request.ID] || request.Exclusive
		}
		return nil
	}
	for _, in := range m.ledger.Instances {
		for _, lease := range in.Reservations {
			_ = subtract(lease.Request, false)
		}
	}
	for _, request := range requests {
		if err := subtract(request, true); err != nil {
			return err
		}
	}
	return nil
}

// ReserveResources is an owner-only lifecycle control operation, never a model
// inference permission. IDs are idempotent, with immutable budgets. No TTL frees
// a model while it might still occupy memory; managers release after unload or
// Fleet clears reservations once the owning process generation is confirmed dead.
func (m *Manager) ReserveResources(id, revision string, generation uint64, lease string, request ResourceRequest) (ResourceReservation, error) {
	if !nameRE.MatchString(lease) || strings.HasPrefix(lease, "component-") {
		return ResourceReservation{}, fmt.Errorf("invalid resource lease id")
	}
	if err := request.Validate(); err != nil {
		return ResourceReservation{}, err
	}
	inv := m.sampleResources()
	m.mu.Lock()
	defer m.mu.Unlock()
	in := m.ledger.Instances[id]
	if m.closed || in == nil || in.Digest != revision || in.Generation != generation || in.State != "ready" {
		return ResourceReservation{}, fmt.Errorf("resource lease requires the ready instance generation")
	}
	if old, ok := in.Reservations[lease]; ok {
		if !reflect.DeepEqual(old.Request, request) {
			return ResourceReservation{}, fmt.Errorf("resource lease budget is immutable; unload before replacing it")
		}
		return clone(old), nil
	}
	if len(in.Reservations) >= 128 {
		return ResourceReservation{}, fmt.Errorf("too many resource leases")
	}
	if err := m.admitLocked(inv, []ResourceRequest{request}); err != nil {
		return ResourceReservation{}, err
	}
	if in.Reservations == nil {
		in.Reservations = map[string]ResourceReservation{}
	}
	res := ResourceReservation{Request: clone(request), CreatedAt: time.Now().UTC()}
	in.Reservations[lease] = res
	if err := m.persist(); err != nil {
		delete(in.Reservations, lease)
		return ResourceReservation{}, err
	}
	return clone(res), nil
}
func (m *Manager) ReleaseResources(id, revision string, generation uint64, lease string) error {
	if !nameRE.MatchString(lease) || strings.HasPrefix(lease, "component-") {
		return fmt.Errorf("invalid resource lease id")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	in := m.ledger.Instances[id]
	if m.closed || in == nil || in.Digest != revision || in.Generation != generation {
		return fmt.Errorf("stale resource lease generation")
	}
	old, found := in.Reservations[lease]
	delete(in.Reservations, lease)
	if err := m.persist(); err != nil {
		if found {
			in.Reservations[lease] = old
		}
		return err
	}
	return nil
}

func (m *Manager) reserveComponents(in *Instance, def Definition) error {
	var requests []ResourceRequest
	for _, c := range def.Components {
		if c.Resources != nil {
			requests = append(requests, *c.Resources)
		}
	}
	if len(requests) == 0 {
		return nil
	}
	inv := m.sampleResources()
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.admitLocked(inv, requests); err != nil {
		return err
	}
	in.Reservations = map[string]ResourceReservation{}
	for _, c := range def.Components {
		if c.Resources != nil {
			in.Reservations["component-"+c.Name] = ResourceReservation{Request: clone(*c.Resources), CreatedAt: time.Now().UTC()}
		}
	}
	if err := m.persist(); err != nil {
		in.Reservations = nil
		return err
	}
	return nil
}
