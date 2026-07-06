package node

import (
	"context"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/mem"
)

// knownTools are the executables we probe for, to advertise what a Node can run.
var knownTools = []string{"python3", "python", "bash", "sh", "rsync", "git", "zstd", "nvidia-smi", "docker", "uv"}

// DetectCapability gathers the (mostly static) capability of this machine.
// workDir selects the filesystem whose free space we report.
func DetectCapability(workDir string) proto.Capability {
	c := proto.Capability{
		OS:       runtime.GOOS,
		Arch:     runtime.GOARCH,
		CPUCores: runtime.NumCPU(),
	}
	if vm, err := mem.VirtualMemory(); err == nil {
		c.RAMGB = bytesToGB(vm.Total)
	}
	if workDir == "" {
		workDir = "."
	}
	if du, err := disk.Usage(workDir); err == nil {
		c.DiskFreeGB = bytesToGB(du.Free)
	}
	c.GPU = detectGPU()
	c.Tools = detectTools()
	return c
}

// LiveLoad samples normalized [0,1] CPU and memory usage.
func LiveLoad() proto.Load {
	l := proto.Load{}
	if pcts, err := cpu.Percent(0, false); err == nil && len(pcts) > 0 {
		l.CPU = pcts[0] / 100.0
	}
	if vm, err := mem.VirtualMemory(); err == nil {
		l.Mem = vm.UsedPercent / 100.0
	}
	return l
}

func detectTools() []string {
	var out []string
	for _, t := range knownTools {
		if _, err := exec.LookPath(t); err == nil {
			out = append(out, t)
		}
	}
	return out
}

// detectGPU is best-effort: count NVIDIA GPUs via nvidia-smi, else "".
func detectGPU() string {
	if _, err := exec.LookPath("nvidia-smi"); err != nil {
		return ""
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	b, err := exec.CommandContext(ctx, "nvidia-smi",
		"--query-gpu=name", "--format=csv,noheader").Output()
	if err != nil {
		return ""
	}
	lines := strings.Split(strings.TrimSpace(string(b)), "\n")
	if len(lines) == 0 || strings.TrimSpace(lines[0]) == "" {
		return ""
	}
	name := strings.TrimSpace(lines[0])
	if len(lines) > 1 {
		return strconv.Itoa(len(lines)) + "x " + name
	}
	return name
}

func bytesToGB(b uint64) float64 { return float64(b) / (1024 * 1024 * 1024) }
