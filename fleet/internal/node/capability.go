package node

import (
	"context"
	"os"
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
	// In a runc container /proc is the host's; the cgroup carries what
	// this node actually owns. Report the allocation when one is set.
	if cores := cgroupCPULimitCores(); cores > 0 {
		c.CPUCores = int(cores + 0.5)
		if c.CPUCores < 1 {
			c.CPUCores = 1
		}
	}
	if vm, err := mem.VirtualMemory(); err == nil {
		c.RAMGB = bytesToGB(vm.Total)
	}
	if limit := cgroupMemLimitBytes(); limit > 0 {
		c.RAMGB = bytesToGB(limit)
	}
	if workDir == "" {
		workDir = "."
	}
	if du, err := disk.Usage(workDir); err == nil {
		c.DiskFreeGB = bytesToGB(du.Free)
	}
	c.GPU = detectGPU()
	c.Tools = detectTools()
	c.Kernel = detectKernel()
	c.Runtimes = detectRuntimes()
	return c
}

// DefaultCaps derives the App placement capabilities for a node kind, from
// what the machine actually offers. Explicit --caps overrides this entirely —
// especially fs:workspace, which means "this node holds THE user's workspace
// filesystem" and cannot be detected, only declared (the sandbox entrypoint
// declares it).
func DefaultCaps(kind string, cap proto.Capability) []string {
	switch kind {
	case proto.KindSandbox:
		return []string{"proc", "fs:workspace", "display", "net"}
	case proto.KindPod:
		return []string{"net"}
	case proto.KindFrontend:
		return []string{"dom"}
	default: // machine: a user's own box runs processes and reaches the net
		caps := []string{"proc", "net"}
		if os.Getenv("DISPLAY") != "" || runtime.GOOS == "darwin" {
			caps = append(caps, "display")
		}
		if cap.GPU != "" {
			caps = append(caps, "gpu")
		}
		return caps
	}
}

// detectKernel reports the OS kernel release (uname -r style), best-effort.
func detectKernel() string {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	b, err := exec.CommandContext(ctx, "uname", "-r").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// detectRuntimes probes versions of the runtimes Apps care about. The
// runner's own version is stamped by the caller (it knows its build).
func detectRuntimes() map[string]string {
	out := map[string]string{}
	probe := func(name string, args []string, trim func(string) string) {
		path, err := exec.LookPath(args[0])
		if err != nil {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		b, err := exec.CommandContext(ctx, path, args[1:]...).CombinedOutput()
		if err != nil {
			return
		}
		if v := trim(strings.TrimSpace(string(b))); v != "" {
			out[name] = v
		}
	}
	lastField := func(s string) string {
		f := strings.Fields(strings.SplitN(s, "\n", 2)[0])
		if len(f) == 0 {
			return ""
		}
		return f[len(f)-1]
	}
	probe("python", []string{"python3", "--version"}, lastField)
	probe("git", []string{"git", "--version"}, lastField)
	probe("pantheon", []string{"python3", "-c", "import pantheon; print(pantheon.__version__)"},
		func(s string) string { return strings.SplitN(s, "\n", 2)[0] })
	return out
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
	// Container-truthful overrides: in a runc pod the numbers above are
	// the HOST's. When the cgroup can answer, its answer wins.
	if cpuLoad, ok := cgroupCPULoad(runtime.NumCPU()); ok {
		l.CPU = cpuLoad
	}
	if memLoad, ok := cgroupMemLoad(); ok {
		l.Mem = memLoad
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
