//go:build !windows

package lifecycle

import (
	"context"
	"os/exec"
	"runtime"
	"testing"
	"time"
)

// A process group still running after its grace period is killed; the stop
// then succeeds instead of leaving it beside the next generation.
func TestStopKillsGroupAfterGrace(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh unavailable")
	}
	dir := t.TempDir()
	c := Component{Name: "backend", Runtime: "process", Argv: []string{"sh", "-c", `trap "" TERM; while :; do sleep 0.1; done`}, StopSeconds: 1}
	d := NativeDriver{}
	r, err := d.Start(context.Background(), c, Paths{dir, dir, dir}, "stubborn")
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(200 * time.Millisecond) // the trap is set
	start := time.Now()
	if err := d.Stop(context.Background(), c, r); err != nil {
		t.Fatal(err)
	}
	if took := time.Since(start); took < 900*time.Millisecond || took > 8*time.Second {
		t.Fatal("killed before or long after its grace", took)
	}
	if alive, err := d.Alive(context.Background(), r); err != nil || alive {
		t.Fatal("group survived SIGKILL", alive, err)
	}
}

// The identity check survives a boot-time shift (a stepped clock moves every
// boot-time-derived creation time) but not a different process.
func TestAliveSurvivesBootTimeShift(t *testing.T) {
	dir := t.TempDir()
	c := Component{Name: "backend", Runtime: "process", Argv: []string{"sleep", "30"}}
	d := NativeDriver{}
	r, err := d.Start(context.Background(), c, Paths{dir, dir, dir}, "identity")
	if err != nil {
		t.Fatal(err)
	}
	defer d.Stop(context.Background(), c, r)
	if runtime.GOOS == "linux" && r.StartTicks == 0 {
		t.Fatal("start ticks not recorded")
	}
	legacy := r
	legacy.StartTicks = 0
	for shift, want := range map[int64]bool{0: true, 1000: true, -1000: true, 10000: false} {
		shifted := legacy
		shifted.Birth += shift
		if alive, err := d.Alive(context.Background(), shifted); err != nil || alive != want {
			t.Fatal("legacy birth shift", shift, alive, err)
		}
	}
	if runtime.GOOS == "linux" {
		moved := r
		moved.Birth += 60000 // the wall clock moved a minute: still this process
		if alive, err := d.Alive(context.Background(), moved); err != nil || !alive {
			t.Fatal("start ticks identity lost to a clock step", alive, err)
		}
		other := r
		other.StartTicks++
		if alive, _ := d.Alive(context.Background(), other); alive {
			t.Fatal("another process taken for the recorded one")
		}
	}
}
