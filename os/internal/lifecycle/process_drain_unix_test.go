//go:build !windows

package lifecycle

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestStopWaitsForOwnedWorkerAfterLeaderExits(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("Python fixture unavailable")
	}
	dir := t.TempDir()
	script := `import os,signal,time
if os.fork() == 0:
 def stop(signum, frame):
  time.sleep(.7)
  open('worker-finished', 'w').close()
  os._exit(0)
 signal.signal(signal.SIGTERM, stop)
 open('worker-ready', 'w').close()
 while True: time.sleep(.1)
signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
while True: time.sleep(.1)
`
	c := Component{Name: "engine", Runtime: "process", Argv: []string{"python3", "-c", script}, StopSeconds: 3}
	d := NativeDriver{}
	resource, err := d.Start(context.Background(), c, Paths{dir, dir, dir}, "owned-tree")
	if err != nil {
		t.Fatal(err)
	}
	defer d.Stop(context.Background(), c, resource)
	for deadline := time.Now().Add(3 * time.Second); ; {
		if _, err := os.Stat(filepath.Join(dir, "worker-ready")); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("worker never ready")
		}
		time.Sleep(20 * time.Millisecond)
	}
	start := time.Now()
	if err := d.Stop(context.Background(), c, resource); err != nil {
		t.Fatal(err)
	}
	if time.Since(start) < 600*time.Millisecond {
		t.Fatal("released the group while its worker was draining")
	}
	if _, err := os.Stat(filepath.Join(dir, "worker-finished")); err != nil {
		t.Fatal("worker not drained", err)
	}
	if alive, err := d.Alive(context.Background(), resource); err != nil || alive {
		t.Fatal("owned group survived stop", err)
	}
}
