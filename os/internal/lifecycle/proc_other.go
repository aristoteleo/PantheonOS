//go:build !linux

package lifecycle

// Start ticks and the process scan need /proc. Elsewhere identity falls back
// to the creation time and strays are not scanned for (stop still kills the
// recorded process group after its grace period).
func processStartTicks(int) (uint64, error) { return 0, errNoStartTicks }

func scanProcesses(string) (processTable, error) { return processTable{}, nil }
