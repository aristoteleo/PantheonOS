//go:build windows

package lifecycle

import (
	"context"
	"fmt"
	"golang.org/x/sys/windows"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"time"
)

const consoleHelper = "--pantheon-private-console-stop"

// Each App has its own hidden console, including when Fleet runs as a service.
// A short-lived helper attaches only to that console to request graceful exit.
// Fleet's own terminal and unrelated applications never receive CTRL_BREAK.
func configureProcess(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NEW_CONSOLE, HideWindow: true}
}
func init() {
	if len(os.Args) != 3 || os.Args[1] != consoleHelper {
		return
	}
	pid, err := strconv.ParseUint(os.Args[2], 10, 32)
	if err != nil || pid == 0 {
		os.Exit(2)
	}
	kernel := windows.NewLazySystemDLL("kernel32.dll")
	// Detach any inherited console; the helper never sends an event until its
	// attachment to the selected App's console has succeeded.
	kernel.NewProc("FreeConsole").Call()
	if ok, _, _ := kernel.NewProc("AttachConsole").Call(uintptr(pid)); ok == 0 {
		os.Exit(3)
	}
	ignore := syscall.NewCallback(func(uint32) uintptr { return 1 })
	if ok, _, _ := kernel.NewProc("SetConsoleCtrlHandler").Call(ignore, 1); ok == 0 {
		os.Exit(4)
	}
	if err := windows.GenerateConsoleCtrlEvent(windows.CTRL_BREAK_EVENT, 0); err != nil {
		os.Exit(5)
	}
	// Keep the helper attached until Windows has delivered the asynchronous event.
	time.Sleep(100 * time.Millisecond)
	os.Exit(0)
}
func terminateProcess(pid int) error {
	if pid <= 0 {
		return fmt.Errorf("invalid process identity")
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, self, consoleHelper, strconv.Itoa(pid))
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW, HideWindow: true}
	return cmd.Run()
}
func killCommandGroup(c *exec.Cmd) error {
	if c.Process == nil {
		return os.ErrProcessDone
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "taskkill.exe", "/PID", strconv.Itoa(c.Process.Pid), "/T", "/F")
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW, HideWindow: true}
	return cmd.Run()
}

// Windows console shutdown has no POSIX group identity. Real child-process
// acceptance and persistent Job Object ownership require a Windows node.
func processGroupAlive(pid int) (bool, error) { return false, nil }
