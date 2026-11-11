//go:build linux

package lifecycle

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

// Real processes: one writing to an instance's component log that the ledger
// does not record is found by /proc and ended; a process of the node itself
// writing elsewhere is not touched.
func TestReapStraysEndsRealUnrecordedProcess(t *testing.T) {
	root := t.TempDir()
	m, err := Open(root, "owner", "node", proto.Capability{}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	id := "0123456789abcdef0123456789abcdef"
	data := filepath.Join(m.root, "data", id)
	os.MkdirAll(data, 0700)
	log, _ := os.OpenFile(filepath.Join(data, "backend.log"), os.O_CREATE|os.O_WRONLY, 0600)
	stray := exec.Command("sh", "-c", `trap "" TERM; while :; do sleep 0.1; done`)
	stray.Stdout, stray.Stderr = log, log
	stray.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	other := exec.Command("sleep", "30")
	for _, c := range []*exec.Cmd{stray, other} {
		if err := c.Start(); err != nil {
			t.Fatal(err)
		}
		go c.Wait()
	}
	log.Close()
	defer other.Process.Kill()
	time.Sleep(200 * time.Millisecond)
	defer func(g time.Duration) { strayGrace = g }(strayGrace)
	strayGrace = 500 * time.Millisecond
	m.serial.Lock()
	err = m.reapStrays(context.Background(), "")
	m.serial.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	if processExists(stray.Process.Pid) {
		t.Fatal("stray survived")
	}
	if !processExists(other.Process.Pid) {
		t.Fatal("unrelated process ended")
	}
}
