package lifecycle

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func resourceInventory() proto.ResourceInventory {
	free := uint64(8 << 30)
	return proto.ResourceInventory{Protocol: 1, SampledAt: time.Now(), Memory: proto.MemoryPool{TotalBytes: free, AvailableBytes: &free}, Accelerators: []proto.Accelerator{}}
}
func readyResourceInstance(t *testing.T) (*Manager, *fakeDriver, *Instance) {
	t.Helper()
	m, driver, digest := setup(t)
	m.SetResourceSampler(resourceInventory)
	if op := submit(t, m, digest, "ready", "start", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	return m, driver, m.Snapshot().Instances[m.instanceID(digest, "app")]
}
func TestConcurrentResourceReservationsAreAtomicAndImmutable(t *testing.T) {
	m, _, in := readyResourceInstance(t)
	var wg sync.WaitGroup
	results := make(chan error, 32)
	for i := range 32 {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, err := m.ReserveResources(in.ID, in.Digest, in.Generation, fmt.Sprintf("model-%d", i), ResourceRequest{MemoryBytes: 1 << 30})
			results <- err
		}(i)
	}
	wg.Wait()
	close(results)
	count := 0
	for err := range results {
		if err == nil {
			count++
		}
	}
	if count != 6 {
		t.Fatalf("expected exactly six 1 GiB leases in 6 GiB budget, got %d", count)
	}
	leases := m.Snapshot().Instances[in.ID].Reservations
	for id := range leases {
		if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, id, ResourceRequest{MemoryBytes: 1 << 30}); err != nil {
			t.Fatal("lost idempotent lease", err)
		}
		if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, id, ResourceRequest{MemoryBytes: 2 << 30}); err == nil {
			t.Fatal("changed reserved budget without unloading")
		}
	}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation+1, "stale", ResourceRequest{MemoryBytes: 1}); err == nil {
		t.Fatal("stale generation reserved")
	}
	if err := m.ReleaseResources(in.ID, in.Digest, in.Generation+1, "model-0"); err == nil {
		t.Fatal("stale generation released")
	}
}
func TestUnifiedAndDedicatedMemoryAdmission(t *testing.T) {
	m, _, in := readyResourceInstance(t)
	inv := resourceInventory()
	inv.Accelerators = []proto.Accelerator{{ID: "apple-metal", Backend: "metal", SharedMemory: true, Memory: inv.Memory}}
	m.SetResourceSampler(func() proto.ResourceInventory { return inv })
	request := ResourceRequest{MemoryBytes: 5 << 30, Devices: []DeviceBudget{{ID: "apple-metal", Backend: "metal", MemoryBytes: 5 << 30}}}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "metal", request); err != nil {
		t.Fatal("unified RAM double-counted", err)
	}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "too-much", ResourceRequest{MemoryBytes: 2 << 30}); err == nil {
		t.Fatal("unified pool overbooked")
	}
	if err := m.ReleaseResources(in.ID, in.Digest, in.Generation, "metal"); err != nil {
		t.Fatal(err)
	}
	inv.Accelerators = []proto.Accelerator{{ID: "GPU-a", Backend: "cuda", Memory: inv.Memory}, {ID: "GPU-b", Backend: "cuda", Memory: inv.Memory}}
	request = ResourceRequest{MemoryBytes: 1 << 30, Devices: []DeviceBudget{{ID: "GPU-a", Backend: "cuda", MemoryBytes: 2 << 30, Exclusive: true}}}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "gpu", request); err != nil {
		t.Fatal(err)
	}
	request.Devices[0].Exclusive = false
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "collision", request); err == nil {
		t.Fatal("exclusive GPU reused")
	}
	request.Devices[0].ID = "GPU-b"
	request.Devices[0].MemoryBytes = 8 << 30
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "oversized", request); err == nil {
		t.Fatal("VRAM safety budget ignored")
	}
	request.Devices[0].ID = "GPU-missing"
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "missing", request); err == nil {
		t.Fatal("missing device accepted")
	}
}
func TestReservationSurvivesRestartUntilExitVerified(t *testing.T) {
	m, driver, in := readyResourceInstance(t)
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "loaded", ResourceRequest{MemoryBytes: 5 << 30}); err != nil {
		t.Fatal(err)
	}
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
	restarted.SetResourceSampler(resourceInventory)
	retained := restarted.Snapshot().Instances[in.ID]
	if len(retained.Reservations) != 1 || retained.State != "unknown" {
		t.Fatal("live lease lost on restart", retained)
	}
	if op := submit(t, restarted, in.Digest, "reconcile-alive", "reconcile", "app", in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if len(restarted.Snapshot().Instances[in.ID].Reservations) != 1 {
		t.Fatal("live recovered lease lost")
	}
	driver.mu.Lock()
	for id := range driver.alive {
		driver.alive[id] = false
	}
	driver.mu.Unlock()
	if op := submit(t, restarted, in.Digest, "reconcile-dead", "reconcile", "app", in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if len(restarted.Snapshot().Instances[in.ID].Reservations) != 0 {
		t.Fatal("confirmed dead lease not released")
	}
}
func TestStaticResourceBudgetPreventsSecondStartAndDrainRetainsLease(t *testing.T) {
	m, driver, _ := setup(t)
	m.SetResourceSampler(resourceInventory)
	def := definition()
	def.Components[0].Resources = &ResourceRequest{MemoryBytes: 4 << 30}
	b, digest := bundle(t, def, nil)
	if _, err := m.Stage(digest, 0, b); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "first", "start", "one", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "second", "start", "two", 0); op.State != "failed" || !strings.Contains(op.Error, "memory") {
		t.Fatal(op)
	}
	if driver.starts != 1 {
		t.Fatal("overcommitted component started")
	}
	driver.blocked = true
	if op := submit(t, m, digest, "blocked-stop", "stop", "one", 1); op.State != "failed" {
		t.Fatal(op)
	}
	in := m.Snapshot().Instances[m.instanceID(digest, "one")]
	if len(in.Reservations) != 1 {
		t.Fatal("draining model budget freed")
	}
	if err := m.ReleaseResources(in.ID, digest, 1, "component-backend"); err == nil {
		t.Fatal("static budget released before process exited")
	}
	driver.blocked = false
	if op := submit(t, m, digest, "stop", "stop", "one", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "retry", "start", "two", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
}
func TestMissingStaleOrExternalPressureDoesNotGrantResources(t *testing.T) {
	m, _, in := readyResourceInstance(t)
	for _, modify := range []func(*proto.ResourceInventory){
		func(i *proto.ResourceInventory) { i.SampledAt = time.Now().Add(-time.Minute) },
		func(i *proto.ResourceInventory) { i.Memory.AvailableBytes = nil },
		func(i *proto.ResourceInventory) { n := uint64(1 << 30); i.Memory.AvailableBytes = &n },
	} {
		inv := resourceInventory()
		modify(&inv)
		m.SetResourceSampler(func() proto.ResourceInventory { return inv })
		if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "load", ResourceRequest{MemoryBytes: 1 << 30}); err == nil {
			t.Fatal("unsafe telemetry accepted", inv)
		}
	}
}
func TestInvalidNodeResourcePolicyFailsClosed(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "resource-policy.json"), []byte(`{"system_reserve_byte": 0}`), 0600); err != nil {
		t.Fatal(err)
	}
	m, err := Open(root, "owner", "node", proto.Capability{}, &fakeDriver{})
	if err == nil {
		m.Close()
		t.Fatal("misspelled safety setting ignored")
	}
}
func TestContainerDeviceArgumentsAreConstrained(t *testing.T) {
	request := &ResourceRequest{MemoryBytes: 8 << 30, Devices: []DeviceBudget{{ID: "GPU-a", Backend: "cuda", MemoryBytes: 1 << 30}, {ID: "GPU-b", Backend: "cuda", MemoryBytes: 1 << 30}}}
	args, err := containerResourceArgs(request)
	if err != nil || strings.Join(args, " ") != `--memory 8589934592 --memory-swap 8589934592 --gpus "device=GPU-a,GPU-b" --env NVIDIA_DRIVER_CAPABILITIES=compute,utility` {
		t.Fatal(args, err)
	}
	request.Devices[0].ID = "all"
	if _, err := containerResourceArgs(request); err == nil {
		t.Fatal("unbounded devices accepted")
	}
	request.Devices[0].ID = "GPU-a,privileged=true"
	if _, err := containerResourceArgs(request); err == nil {
		t.Fatal("CSV injection accepted")
	}
	request.Devices = []DeviceBudget{{ID: "apple-metal", Backend: "metal", MemoryBytes: 1 << 30}}
	if _, err := containerResourceArgs(request); err == nil {
		t.Fatal("unsupported GPU container accepted")
	}
}

func TestNativeDeviceBudgetOverridesManifestEnvironment(t *testing.T) {
	// Exercise the real subprocess driver; shell only belongs to this fixture.
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("POSIX shell fixture")
	}
	dir := t.TempDir()
	c := Component{Name: "engine", Runtime: "process", Argv: []string{"sh", "-c", `printf '%s' "$CUDA_VISIBLE_DEVICES" > device; sleep 2`}, Env: map[string]string{"CUDA_VISIBLE_DEVICES": "all"}, Resources: &ResourceRequest{MemoryBytes: 1 << 30, Devices: []DeviceBudget{{ID: "GPU-owned", Backend: "cuda", MemoryBytes: 1 << 30}}}}
	d := NativeDriver{}
	r, err := d.Start(context.Background(), c, Paths{dir, dir, dir}, "owned")
	if err != nil {
		t.Fatal(err)
	}
	defer d.Stop(context.Background(), c, r)
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); {
		b, err := os.ReadFile(filepath.Join(dir, "device"))
		if err == nil {
			if string(b) != "GPU-owned" {
				t.Fatal(string(b))
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("child did not report device binding")
}

func TestReadinessUsesThisProcessesAssignedPort(t *testing.T) {
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("POSIX shell fixture")
	}
	dir := t.TempDir()
	c := Component{Name: "engine", Runtime: "process", Ports: map[string]int{"http": 0},
		Env:       map[string]string{"PANTHEON_APP_CACHE": "${DATA}/owned cache", "PANTHEON_APP_SCOPE": "engine-owned"},
		Argv:      []string{"sh", "-c", `printf '%s' "$PANTHEON_PORT_HTTP" > assigned-port; sleep 10`},
		Readiness: Probe{Argv: []string{"sh", "-c", `test "$PANTHEON_PORT_HTTP" = "$(cat assigned-port)" && test "$PANTHEON_APP_CACHE" = "$HOME/owned cache" && test "$PANTHEON_APP_SCOPE" = engine-owned`}, TimeoutSeconds: 2}}
	d := NativeDriver{}
	r, err := d.Start(context.Background(), c, Paths{dir, dir, dir}, "probe-owned")
	if err != nil {
		t.Fatal(err)
	}
	defer d.Stop(context.Background(), c, r)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := d.Probe(ctx, c, Paths{dir, dir, dir}, r); err != nil {
		t.Fatal(err)
	}
	r.Endpoints = nil
	if err := d.Probe(ctx, c, Paths{dir, dir, dir}, r); err == nil {
		t.Fatal("probe accepted missing port ownership")
	}
}

func TestLiveResourceAdmission(t *testing.T) {
	if os.Getenv("FLEET_TEST_RESOURCE_INVENTORY") != "1" {
		t.Skip("opt-in real hardware admission")
	}
	inv := node.DetectResources()
	if len(inv.Accelerators) == 0 {
		t.Fatal("GPU acceptance needs an actual device")
	}
	m, _, in := readyResourceInstance(t)
	m.SetResourceSampler(node.DetectResources)
	device := inv.Accelerators[0]
	request := ResourceRequest{MemoryBytes: 512 << 20, Devices: []DeviceBudget{{ID: device.ID, Backend: device.Backend, MemoryBytes: 512 << 20, Exclusive: true}}}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "real-hardware", request); err != nil {
		t.Fatal(err)
	}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "competing-model", request); err == nil {
		t.Fatal("actual GPU granted twice")
	}
	if op := submit(t, m, in.Digest, "stop-live", "stop", in.Scope, in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if len(m.Snapshot().Instances[in.ID].Reservations) != 0 {
		t.Fatal("live admission lease leaked after stop")
	}
	t.Logf("measured %s / %s: exclusive admission, competing-load rejection and stop release passed", device.Name, device.Backend)
}

func TestGpuGroupAdmissionDoesNotLeavePartialReservations(t *testing.T) {
	m, _, in := readyResourceInstance(t)
	inv := resourceInventory()
	for _, id := range []string{"GPU-a", "GPU-b"} {
		inv.Accelerators = append(inv.Accelerators, proto.Accelerator{ID: id, Backend: "cuda", Memory: inv.Memory})
	}
	m.SetResourceSampler(func() proto.ResourceInventory { return inv })
	single := ResourceRequest{MemoryBytes: 1 << 30, Devices: []DeviceBudget{{ID: "GPU-b", Backend: "cuda", MemoryBytes: 2 << 30, Exclusive: true}}}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "existing", single); err != nil {
		t.Fatal(err)
	}
	group := ResourceRequest{MemoryBytes: 1 << 30, Devices: []DeviceBudget{
		{ID: "GPU-a", Backend: "cuda", MemoryBytes: 2 << 30, Exclusive: true},
		{ID: "GPU-b", Backend: "cuda", MemoryBytes: 2 << 30, Exclusive: true},
	}}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "group", group); err == nil {
		t.Fatal("occupied rank admitted")
	}
	if len(m.Snapshot().Instances[in.ID].Reservations) != 1 {
		t.Fatal("partial group persisted")
	}
	single.Devices[0].ID = "GPU-a"
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "free-rank", single); err != nil {
		t.Fatal("failed group consumed free rank", err)
	}
	for _, id := range []string{"existing", "free-rank"} {
		if err := m.ReleaseResources(in.ID, in.Digest, in.Generation, id); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := m.ReserveResources(in.ID, in.Digest, in.Generation, "group", group); err != nil {
		t.Fatal(err)
	}
	if len(m.Snapshot().Instances[in.ID].Reservations["group"].Request.Devices) != 2 {
		t.Fatal("incomplete group reservation")
	}
	if err := m.ReleaseResources(in.ID, in.Digest, in.Generation, "group"); err != nil {
		t.Fatal(err)
	}
	if len(m.Snapshot().Instances[in.ID].Reservations) != 0 {
		t.Fatal("group release retained resources")
	}
}
