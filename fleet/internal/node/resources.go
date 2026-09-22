package node

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/shirou/gopsutil/v4/mem"
)

func resourceCommand(name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, name, args...).Output()
}

// DetectResources is also sampled before admission. It installs no drivers and
// does not ask for privileges. A failed device query removes that device from
// admission until a fresh successful probe; it never invents free VRAM.
func DetectResources() proto.ResourceInventory {
	inv := proto.ResourceInventory{Protocol: 1, SampledAt: time.Now().UTC(), Accelerators: []proto.Accelerator{}}
	if vm, err := mem.VirtualMemory(); err == nil {
		inv.Memory = proto.MemoryPool{TotalBytes: vm.Total, AvailableBytes: &vm.Available}
	}
	if limit := cgroupMemLimitBytes(); limit > 0 && (inv.Memory.TotalBytes == 0 || limit < inv.Memory.TotalBytes) {
		current, ok := cgroupMemCurrentBytes()
		inv.Memory = constrainedMemory(inv.Memory, limit, current, ok)
	}
	if b, err := resourceCommand("nvidia-smi", "--query-gpu=uuid,name,driver_version,memory.total,memory.free", "--format=csv,noheader,nounits"); err == nil {
		inv.Accelerators = append(inv.Accelerators, parseNVIDIA(b)...)
	}
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		if b, err := resourceCommand("system_profiler", "SPDisplaysDataType", "-json"); err == nil {
			inv.Accelerators = append(inv.Accelerators, parseApple(b, inv.Memory)...)
		}
	}
	if runtime.GOOS == "linux" {
		inv.Accelerators = append(inv.Accelerators, detectAMD("/sys/class/drm")...)
	}
	return inv
}

func constrainedMemory(host proto.MemoryPool, limit, current uint64, currentKnown bool) proto.MemoryPool {
	result := proto.MemoryPool{TotalBytes: limit}
	if currentKnown {
		available := uint64(0)
		if current < limit {
			available = limit - current
		}
		// Container headroom cannot make exhausted host RAM available again.
		if host.AvailableBytes != nil {
			available = min(available, *host.AvailableBytes)
		}
		result.AvailableBytes = &available
	}
	return result
}

func parseNVIDIA(b []byte) []proto.Accelerator {
	r := csv.NewReader(strings.NewReader(string(b)))
	r.TrimLeadingSpace = true
	var out []proto.Accelerator
	seen := map[string]bool{}
	for {
		fields, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil
		}
		if len(fields) != 5 {
			continue
		}
		for i := range fields {
			fields[i] = strings.TrimSpace(fields[i])
		}
		total, e1 := strconv.ParseUint(fields[3], 10, 40)
		free, e2 := strconv.ParseUint(fields[4], 10, 40)
		if !strings.HasPrefix(fields[0], "GPU-") || seen[fields[0]] || e1 != nil || e2 != nil || total == 0 || free > total {
			continue
		}
		seen[fields[0]] = true
		free <<= 20
		out = append(out, proto.Accelerator{ID: fields[0], Name: fields[1], Vendor: "nvidia", Backend: "cuda", Driver: fields[2], Memory: proto.MemoryPool{TotalBytes: total << 20, AvailableBytes: &free}})
	}
	return out
}

func parseApple(b []byte, memory proto.MemoryPool) []proto.Accelerator {
	var data struct {
		Displays []map[string]any `json:"SPDisplaysDataType"`
	}
	if json.Unmarshal(b, &data) != nil {
		return nil
	}
	for _, d := range data.Displays {
		name, _ := d["sppci_model"].(string)
		if !strings.HasPrefix(name, "Apple ") {
			continue
		}
		// Apple silicon has one unified system pool. Do not sum it once per
		// GPU core/display. CPU and Metal estimates both refer to this RAM.
		return []proto.Accelerator{{ID: "apple-metal", Name: name, Vendor: "apple", Backend: "metal", SharedMemory: true, Memory: memory}}
	}
	return nil
}

func detectAMD(root string) []proto.Accelerator {
	entries, _ := filepath.Glob(filepath.Join(root, "card[0-9]*", "device"))
	var out []proto.Accelerator
	seen := map[string]bool{}
	for _, p := range entries {
		vendor, err := os.ReadFile(filepath.Join(p, "vendor"))
		if err != nil || strings.TrimSpace(string(vendor)) != "0x1002" {
			continue
		}
		real, err := filepath.EvalSymlinks(p)
		if err != nil || seen[real] {
			continue
		}
		seen[real] = true
		read := func(name string) (uint64, error) {
			b, err := os.ReadFile(filepath.Join(p, name))
			if err != nil {
				return 0, err
			}
			return strconv.ParseUint(strings.TrimSpace(string(b)), 10, 60)
		}
		total, e1 := read("mem_info_vram_total")
		used, e2 := read("mem_info_vram_used")
		if e1 != nil || e2 != nil || total == 0 || used > total {
			continue
		}
		free := total - used
		device, _ := os.ReadFile(filepath.Join(p, "device"))
		out = append(out, proto.Accelerator{ID: "pci-" + filepath.Base(real), Name: "AMD " + strings.TrimSpace(string(device)), Vendor: "amd", Backend: "rocm", Memory: proto.MemoryPool{TotalBytes: total, AvailableBytes: &free}})
	}
	return out
}
