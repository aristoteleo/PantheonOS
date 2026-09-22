//go:build !linux

package node

func cgroupCPULimitCores() float64          { return 0 }
func cgroupMemLimitBytes() uint64           { return 0 }
func cgroupMemCurrentBytes() (uint64, bool) { return 0, false }
func cgroupCPULoad(int) (float64, bool)     { return 0, false }
func cgroupMemLoad() (float64, bool)        { return 0, false }
