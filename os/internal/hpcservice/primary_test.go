package hpcservice

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// Exercise the real batch program and both forwarding sessions, without Slurm.
// An ordinary HTTP App is the workload; reconnecting must retain its PID.
func TestPrimaryAppSurvivesTransportReconnect(t *testing.T) {
	m, ctx, disconnect, python := fixture(t)
	spec, err := serverSpec(python).Normalize()
	if err != nil {
		t.Fatal(err)
	}
	m.job.Service = &spec
	payload, _ := json.Marshal(map[string]any{"allocation": m.job.AllocationID, "service": spec, "revision": spec.Revision()})
	home := filepath.Dir(m.root)
	cmd := exec.Command(python, "../hpc/primary_http.py", base64.StdEncoding.EncodeToString(payload))
	cmd.Env = append(os.Environ(), "HOME="+home, "SLURM_JOB_ID=42")
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	stopped := false
	t.Cleanup(func() {
		if !stopped {
			_ = cmd.Process.Signal(syscall.SIGTERM)
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				_ = cmd.Process.Kill()
				<-done
			}
		}
	})
	status := filepath.Join(home, ".pantheon-fleet", "workspaces", m.job.AllocationID, ".primary-http.json")
	readState := func() map[string]any {
		t.Helper()
		b, e := os.ReadFile(status)
		if e != nil {
			t.Fatal(e)
		}
		var state map[string]any
		if e = json.Unmarshal(b, &state); e != nil {
			t.Fatal(e)
		}
		return state
	}
	if _, err = m.Start(ctx, spec, 1); err != nil {
		t.Fatal(err)
	}
	rec := await(t, m, "running")
	if !rec.Primary {
		t.Fatal("primary App not identified")
	}
	pid := readState()["pid"]
	request := func() {
		t.Helper()
		conn, e := m.Dial(context.Background(), rec.Instance, rec.Revision, 1)
		if e != nil {
			t.Fatal(e)
		}
		defer conn.Close()
		conn.SetDeadline(time.Now().Add(3 * time.Second))
		fmt.Fprint(conn, "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
		resp, e := http.ReadResponse(bufio.NewReader(conn), nil)
		if e != nil {
			t.Fatal(e)
		}
		b, e := io.ReadAll(resp.Body)
		resp.Body.Close()
		if e != nil || string(b) != "HPC_OK:42" {
			t.Fatal(string(b), e)
		}
	}
	request()
	disconnect()
	await(t, m, "interrupted")
	if readState()["pid"] != pid {
		t.Fatal("disconnect restarted App")
	}
	if _, err = m.Start(context.Background(), spec, 2); err == nil {
		t.Fatal("primary generation changed")
	}
	changed := spec
	changed.Argv = []string{python, "-c", "raise Exception('unexpected launch')"}
	if _, err = m.Start(context.Background(), changed, 1); err == nil {
		t.Fatal("accepted different primary workload")
	}
	if _, err = m.Start(context.Background(), spec, 1); err != nil {
		t.Fatal(err)
	}
	rec = await(t, m, "running")
	request()
	if readState()["pid"] != pid {
		t.Fatal("reconnect restarted App")
	}
	// Ending only the forwarding step must not terminate the batch workload.
	if _, err = m.Stop(rec.Instance, rec.Revision, 1); err != nil {
		t.Fatal(err)
	}
	await(t, m, "stopped")
	if state := readState(); state["state"] != "running" || state["pid"] != pid {
		t.Fatal(state)
	}
	_ = cmd.Process.Signal(syscall.SIGTERM)
	select {
	case <-done:
		stopped = true
	case <-time.After(5 * time.Second):
		t.Fatal("primary App did not stop")
	}
	if state := readState(); state["state"] != "stopped" {
		t.Fatal(state)
	}
}

func TestPrimaryAttachmentRejectsMismatchedAllocationState(t *testing.T) {
	m, ctx, _, python := fixture(t)
	spec, _ := serverSpec(python).Normalize()
	m.job.Service = &spec
	root := filepath.Join(filepath.Dir(m.root), ".pantheon-fleet", "workspaces", m.job.AllocationID)
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(map[string]any{"job_id": "other", "allocation": m.job.AllocationID, "revision": spec.Revision(), "state": "running", "port": 80})
	if err := os.WriteFile(filepath.Join(root, ".primary-http.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Start(ctx, spec, 1); err != nil {
		t.Fatal(err)
	}
	rec := await(t, m, "failed")
	if rec.Error != "primary App identity mismatch" {
		t.Fatal(rec)
	}
}
