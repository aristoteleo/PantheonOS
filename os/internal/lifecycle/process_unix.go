//go:build !windows

package lifecycle

import (
	"os/exec"
	"syscall"

	"github.com/shirou/gopsutil/v4/process"
)

func configureProcess(c *exec.Cmd)   { c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }
func terminateProcess(pid int) error { return syscall.Kill(-pid, syscall.SIGTERM) }
func killProcessGroup(pid int) error {
	if err := syscall.Kill(-pid, syscall.SIGKILL); err != nil && err != syscall.ESRCH {
		return err
	}
	return nil
}
func killCommandGroup(c *exec.Cmd) error { return syscall.Kill(-c.Process.Pid, syscall.SIGKILL) }

// The group outlives its leader while owned workers are still draining. Do
// not release memory reservations or instance data before those workers exit.
func processGroupAlive(pid int) (bool, error) {
	if err := syscall.Kill(-pid, 0); err != nil {
		if err == syscall.ESRCH {
			return false, nil
		}
		return false, err
	}
	// Containers can leave already exited orphan workers as zombies until PID
	// 1 reaps them. They cannot run or hold model memory; don't retain a lease
	// forever for that case. Any live member keeps the group owned and active.
	pids, err := process.Pids()
	if err != nil {
		return false, err
	}
	for _, member := range pids {
		group, err := syscall.Getpgid(int(member))
		if err == syscall.ESRCH || group != pid {
			continue
		}
		if err != nil {
			return false, err
		}
		p, err := process.NewProcess(member)
		if err != nil {
			if exists, e := process.PidExists(member); e == nil && !exists {
				continue
			}
			return false, err
		}
		states, err := p.Status()
		if err != nil {
			if exists, e := process.PidExists(member); e == nil && !exists {
				continue
			}
			return false, err
		}
		zombie := false
		for _, state := range states {
			zombie = zombie || state == process.Zombie
		}
		if !zombie {
			return true, nil
		}
	}
	return false, nil
}

// signalProcess ends one stray process: SIGTERM, or SIGKILL when kill.
func signalProcess(pid int, kill bool) error {
	sig := syscall.SIGTERM
	if kill {
		sig = syscall.SIGKILL
	}
	return syscall.Kill(pid, sig)
}

func processExists(pid int) bool {
	if err := syscall.Kill(pid, 0); err == syscall.ESRCH {
		return false
	}
	// A zombie is gone for every purpose here (it holds no files or locks).
	if p, err := process.NewProcess(int32(pid)); err == nil {
		if states, err := p.Status(); err == nil {
			for _, s := range states {
				if s == process.Zombie {
					return false
				}
			}
		}
	}
	return true
}
