package hpcservice

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
)

func fixture(t *testing.T) (*Manager, context.Context, context.CancelFunc, string) {
	t.Helper()
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 required")
	}
	home := t.TempDir()
	job := hpc.Job{JobID: "42", AllocationID: strings.Repeat("a", 32), State: "RUNNING", CPUs: 1, MemGB: 1}
	stream := func(ctx context.Context, _ string, activity bool, in io.Reader, out, stderr io.Writer, argv ...string) error {
		if !activity || argv[0] != "srun" || argv[1] != "--jobid=42" || argv[6] != "--cpus-per-task=1" || argv[8] != "--gres=none" {
			return fmt.Errorf("unexpected compute step: %v", argv[:10])
		}
		cmd := exec.Command(python, argv[len(argv)-4:]...)
		cmd.Env = append(os.Environ(), "HOME="+home, "SLURM_JOB_ID=42")
		cmd.Stdin = in
		cmd.Stdout = out
		cmd.Stderr = stderr
		return cmd.Run()
	}
	m, err := Open(filepath.Join(home, "receipts"), stream, "cluster", job)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(func() { cancel(); m.Close() })
	return m, ctx, cancel, python
}
func await(t *testing.T, m *Manager, state string) Receipt {
	t.Helper()
	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		for _, r := range m.List() {
			if r.State == state {
				return r
			}
		}
		time.Sleep(25 * time.Millisecond)
	}
	t.Fatalf("expected %s, got %+v", state, m.List())
	return Receipt{}
}
func serverSpec(python string) Spec {
	return Spec{Name: "test", Argv: []string{python, "-c", `import http.server,os
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(('HPC_OK:'+os.environ['SLURM_JOB_ID']).encode())
http.server.ThreadingHTTPServer(('127.0.0.1',int(os.environ['PORT'])),Handler).serve_forever()`}, StartupSeconds: 5}
}
func TestServiceLifecycleAndMultiplexedHTTP(t *testing.T) {
	m, ctx, _, python := fixture(t)
	spec := serverSpec(python)
	rec, err := m.Start(ctx, spec, 1)
	if err != nil {
		t.Fatal(err)
	}
	again, err := m.Start(ctx, spec, 1)
	if err != nil || again.Revision != rec.Revision {
		t.Fatal("start not idempotent", again, err)
	}
	rec = await(t, m, "running")
	if _, err = m.Start(ctx, spec, 2); err == nil {
		t.Fatal("started second service")
	}
	if _, err = m.Dial(ctx, rec.Instance, strings.Repeat("0", 64), 1); err == nil {
		t.Fatal("accepted stale revision")
	}
	for i := 0; i < 20; i++ {
		dialctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		conn, err := m.Dial(dialctx, rec.Instance, rec.Revision, rec.Generation)
		cancel()
		if err != nil {
			t.Fatal(i, err)
		}
		conn.SetDeadline(time.Now().Add(3 * time.Second))
		fmt.Fprint(conn, "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
		resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
		if err != nil {
			t.Fatal(i, err)
		}
		data, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		conn.Close()
		if err != nil || string(data) != "HPC_OK:42" {
			t.Fatal(string(data), err)
		}
	}
	if _, err = m.Stop(rec.Instance, rec.Revision, 2); err == nil {
		t.Fatal("accepted stale stop")
	}
	if _, err = m.Stop(rec.Instance, rec.Revision, 1); err != nil {
		t.Fatal(err)
	}
	await(t, m, "stopped")
	if err = m.Ready(rec.Instance, rec.Revision, 1); err == nil {
		t.Fatal("stopped service still ready")
	}
	_, err = m.Start(ctx, spec, 2)
	if err != nil {
		t.Fatal(err)
	}
	rec = await(t, m, "running")
	if rec.Generation != 2 {
		t.Fatal(rec)
	}
	m.Stop(rec.Instance, rec.Revision, 2)
	await(t, m, "stopped")
	restored, err := Open(m.root, m.stream, m.cluster, m.job)
	if err != nil {
		t.Fatal(err)
	}
	if restored.List()[0].Generation != 2 || restored.List()[0].State != "stopped" {
		t.Fatal(restored.List())
	}
}
func TestServiceCancelClosesConnectionsAndRefusesNewWork(t *testing.T) {
	m, ctx, cancel, python := fixture(t)
	m.Start(ctx, serverSpec(python), 1)
	rec := await(t, m, "running")
	conn, err := m.Dial(ctx, rec.Instance, rec.Revision, 1)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	cancel()
	await(t, m, "interrupted")
	conn.SetReadDeadline(time.Now().Add(time.Second))
	if _, err = conn.Read(make([]byte, 1)); err == nil {
		t.Fatal("connection survived allocation loss")
	}
	if _, err = m.Start(context.Background(), serverSpec(python), 2); err == nil {
		t.Fatal("restarted before remote lease expiry")
	}
}
func TestServiceStartupTimeoutAndValidation(t *testing.T) {
	m, ctx, _, python := fixture(t)
	invalid := serverSpec(python)
	invalid.Cwd = "../other"
	if _, err := m.Start(ctx, invalid, 1); err == nil {
		t.Fatal("escaped workspace")
	}
	if _, err := m.Start(ctx, serverSpec(python), 5); err == nil {
		t.Fatal("skipped generation")
	}
	spec := Spec{Name: "timeout", Argv: []string{python, "-c", "import time;time.sleep(60)"}, StartupSeconds: 1}
	m.Start(ctx, spec, 1)
	rec := await(t, m, "failed")
	if !strings.Contains(rec.Error, "startup timeout") {
		t.Fatal(rec)
	}
}

func TestLargeResponseWithSlowConsumer(t *testing.T) {
	m, ctx, _, python := fixture(t)
	spec := serverSpec(python)
	spec.Argv[2] = strings.Replace(spec.Argv[2], "('HPC_OK:'+os.environ['SLURM_JOB_ID']).encode()", "b'x'*2097152", 1)
	if _, err := m.Start(ctx, spec, 1); err != nil {
		t.Fatal(err)
	}
	rec := await(t, m, "running")
	conn, err := m.Dial(ctx, rec.Instance, rec.Revision, 1)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(10 * time.Second))
	fmt.Fprint(conn, "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
	time.Sleep(300 * time.Millisecond) // Exercise per-stream flow control, not an unbounded queue.
	response, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		t.Fatal(err)
	}
	n, err := io.Copy(io.Discard, response.Body)
	response.Body.Close()
	if err != nil || n != 2097152 {
		t.Fatal(n, err)
	}
	m.Stop(rec.Instance, rec.Revision, 1)
	await(t, m, "stopped")
}
