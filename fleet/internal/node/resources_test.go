package node

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func TestResourceDeviceParsers(t *testing.T) {
	gpus := parseNVIDIA([]byte("GPU-a, NVIDIA A100, 580.1, 40960, 40000\nGPU-b, NVIDIA A100, 580.1, 40960, 20000\nGPU-c, Unknown, 580.1, [N/A], [N/A]\n"))
	if len(gpus) != 2 || gpus[1].ID != "GPU-b" || gpus[0].Memory.TotalBytes != 40960<<20 || *gpus[1].Memory.AvailableBytes != 20000<<20 {
		t.Fatal(gpus)
	}
	if got := parseNVIDIA([]byte("GPU-a, GPU, 1, 4, 9\n")); len(got) != 0 {
		t.Fatal("impossible available memory accepted")
	}
	free := uint64(40 << 30)
	memory := proto.MemoryPool{TotalBytes: 64 << 30, AvailableBytes: &free}
	apple := parseApple([]byte(`{"SPDisplaysDataType":[{"sppci_model":"Apple M4 Max"},{"sppci_model":"Apple M4 Max"}]}`), memory)
	if len(apple) != 1 || !apple[0].SharedMemory || apple[0].Memory.TotalBytes != memory.TotalBytes {
		t.Fatal("unified memory counted per display", apple)
	}
	if len(parseApple([]byte(`{"SPDisplaysDataType":[{"sppci_model":"Intel Iris"}]}`), memory)) != 0 {
		t.Fatal("non-Apple memory assumed unified")
	}
}

func TestAMDInventoryUsesStablePCIAndMeasuredBytes(t *testing.T) {
	root := t.TempDir()
	p := filepath.Join(root, "0000:03:00.0")
	if err := os.MkdirAll(p, 0700); err != nil {
		t.Fatal(err)
	}
	for k, v := range map[string]string{"vendor": "0x1002", "device": "0x744c", "mem_info_vram_total": "24000000000", "mem_info_vram_used": "4000000000"} {
		if err := os.WriteFile(filepath.Join(p, k), []byte(v), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, card := range []string{"card0", "card1"} {
		if err := os.Mkdir(filepath.Join(root, card), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(p, filepath.Join(root, card, "device")); err != nil {
			t.Skip("symlinks unavailable", err)
		}
	}
	gpus := detectAMD(root)
	if len(gpus) != 1 || gpus[0].ID != "pci-0000:03:00.0" || *gpus[0].Memory.AvailableBytes != 20000000000 {
		t.Fatal(gpus)
	}
}

func TestContainerMemoryRespectsHostPressure(t *testing.T) {
	free := uint64(512 << 20)
	host := proto.MemoryPool{TotalBytes: 64 << 30, AvailableBytes: &free}
	got := constrainedMemory(host, 8<<30, 1<<30, true)
	if got.TotalBytes != 8<<30 || *got.AvailableBytes != free {
		t.Fatal("container overstates physical headroom", got)
	}
	if constrainedMemory(host, 8<<30, 0, false).AvailableBytes != nil {
		t.Fatal("invented current cgroup usage")
	}
	if *constrainedMemory(host, 8<<30, 9<<30, true).AvailableBytes != 0 {
		t.Fatal("usage overflow reported free RAM")
	}
}

func TestLiveResourceInventory(t *testing.T) {
	if os.Getenv("FLEET_TEST_RESOURCE_INVENTORY") != "1" {
		t.Skip("opt-in real machine inventory")
	}
	inv := DetectResources()
	if inv.Protocol != 1 || inv.Memory.TotalBytes == 0 || inv.Memory.AvailableBytes == nil {
		t.Fatal(inv)
	}
	b, _ := json.Marshal(inv)
	t.Log(string(b))
	if expected, err := strconv.ParseUint(os.Getenv("FLEET_EXPECT_MEMORY_BYTES"), 10, 64); err == nil && inv.Memory.TotalBytes > expected {
		for _, p := range []string{"/proc/self/cgroup", "/proc/self/mountinfo", "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"} {
			b, err := os.ReadFile(p)
			t.Logf("%s: %s (%v)", p, b, err)
		}
		t.Fatalf("reported host capacity %d above sandbox hard limit %d", inv.Memory.TotalBytes, expected)
	}
}
