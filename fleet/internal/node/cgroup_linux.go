//go:build linux

// Container-truthful capacity and load.
//
// In a runc container (a k8s pod) /proc is the HOST's: gopsutil reports
// the node's cores, the node's RAM and the node's load — so a 1-vCPU
// agent pod on a 2-core droplet advertised "2 cores · 8GB · 51% used",
// none of which was its own. cgroup v2 carries the container's actual
// allocation and usage; when limits are present they are the truth this
// node should report. (gVisor sandboxes virtualize /proc and need none
// of this — every reader falls back to the psutil numbers when a file
// is absent or unlimited.)

package node

import (
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

const cgroupRoot = "/sys/fs/cgroup"

func readCgroupFile(name string) (string, bool) {
	b, err := os.ReadFile(cgroupRoot + "/" + name)
	if err != nil {
		return "", false
	}
	return strings.TrimSpace(string(b)), true
}

// cgroupCPULimitCores returns the cpu.max quota in cores, or 0 when
// unlimited or not on cgroup v2.
func cgroupCPULimitCores() float64 {
	s, ok := readCgroupFile("cpu.max")
	if !ok {
		return 0
	}
	fields := strings.Fields(s) // "200000 100000" or "max 100000"
	if len(fields) != 2 || fields[0] == "max" {
		return 0
	}
	quota, err1 := strconv.ParseFloat(fields[0], 64)
	period, err2 := strconv.ParseFloat(fields[1], 64)
	if err1 != nil || err2 != nil || period <= 0 {
		return 0
	}
	return quota / period
}

// cgroupMemLimitBytes returns memory.max, or 0 when unlimited/absent.
func cgroupMemLimitBytes() uint64 {
	s, ok := readCgroupFile("memory.max")
	if !ok || s == "max" {
		return 0
	}
	v, err := strconv.ParseUint(s, 10, 64)
	if err != nil {
		return 0
	}
	return v
}

func cgroupMemCurrentBytes() (uint64, bool) {
	s, ok := readCgroupFile("memory.current")
	if !ok {
		return 0, false
	}
	v, err := strconv.ParseUint(s, 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// cgroupCPUUsageUsec reads usage_usec from cpu.stat.
func cgroupCPUUsageUsec() (uint64, bool) {
	s, ok := readCgroupFile("cpu.stat")
	if !ok {
		return 0, false
	}
	for _, line := range strings.Split(s, "\n") {
		if rest, found := strings.CutPrefix(line, "usage_usec "); found {
			v, err := strconv.ParseUint(strings.TrimSpace(rest), 10, 64)
			if err != nil {
				return 0, false
			}
			return v, true
		}
	}
	return 0, false
}

var cpuSampleMu sync.Mutex
var lastCPUUsageUsec uint64
var lastCPUSampleAt time.Time

// cgroupCPULoad returns normalized [0,1] CPU use of THIS container since
// the previous call, against its own quota (or the host cores when
// unlimited). ok=false on the first call and off-cgroup systems.
func cgroupCPULoad(hostCores int) (float64, bool) {
	usage, ok := cgroupCPUUsageUsec()
	if !ok {
		return 0, false
	}
	cpuSampleMu.Lock()
	defer cpuSampleMu.Unlock()
	now := time.Now()
	defer func() { lastCPUUsageUsec, lastCPUSampleAt = usage, now }()
	if lastCPUSampleAt.IsZero() || usage < lastCPUUsageUsec {
		return 0, false
	}
	wallUsec := float64(now.Sub(lastCPUSampleAt).Microseconds())
	if wallUsec <= 0 {
		return 0, false
	}
	cores := cgroupCPULimitCores()
	if cores <= 0 {
		cores = float64(hostCores)
	}
	if cores <= 0 {
		return 0, false
	}
	load := float64(usage-lastCPUUsageUsec) / wallUsec / cores
	if load < 0 {
		load = 0
	}
	if load > 1 {
		load = 1
	}
	return load, true
}

// cgroupMemLoad returns normalized [0,1] memory use of this container
// against its own limit. ok=false when unlimited or off-cgroup.
func cgroupMemLoad() (float64, bool) {
	limit := cgroupMemLimitBytes()
	if limit == 0 {
		return 0, false
	}
	cur, ok := cgroupMemCurrentBytes()
	if !ok {
		return 0, false
	}
	load := float64(cur) / float64(limit)
	if load > 1 {
		load = 1
	}
	return load, true
}
