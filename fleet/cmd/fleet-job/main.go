// fleet-job is a bounded Slurm workload, not a Fleet node daemon. Discovery and
// owner authentication remain on the attended SSH connector.
package main

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/jobapp"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

type config struct{ Root, Artifact, Digest, Scope, Owner, Node, Token string }

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("usage: fleet-job private-job-config.json")
	}
	if _, err := strconv.ParseUint(os.Getenv("SLURM_JOB_ID"), 10, 64); err != nil {
		return fmt.Errorf("fleet-job requires a Slurm allocation")
	}
	b, err := os.ReadFile(os.Args[1])
	if err != nil {
		return err
	}
	var c config
	if err = lifecycle.StrictDecode(b, &c); err != nil {
		return err
	}
	if !filepath.IsAbs(c.Root) || !filepath.IsAbs(c.Artifact) {
		return fmt.Errorf("job paths must be absolute")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	caps := proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}, Runtimes: map[string]string{"app-lifecycle": "1", "app-services": "1"}}
	// Only allocation/module configuration is inherited, never connector secrets.
	var env []string
	env = append(env, "PANTHEON_PYTHON_CACHE="+filepath.Join(filepath.Dir(filepath.Dir(c.Root)), "python-environments"))
	for _, key := range []string{"LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH", "MODULEPATH", "LOADEDMODULES", "SLURM_JOB_ID", "SLURM_CPUS_PER_TASK", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "GROUP_HOME", "GROUP_SCRATCH", "SCRATCH", "TMPDIR"} {
		if value := os.Getenv(key); value != "" {
			env = append(env, key+"="+value)
		}
	}
	driver := lifecycle.NativeDriver{Environment: env}
	m, err := lifecycle.Open(c.Root, c.Owner, c.Node, caps, driver)
	if err != nil {
		return err
	}
	defer m.Close()
	file, err := os.Open(c.Artifact)
	if err != nil {
		return err
	}
	offset := int64(0)
	chunk := make([]byte, lifecycle.MaxChunk)
	for {
		n, e := file.Read(chunk)
		if n > 0 {
			if _, err = m.Stage(c.Digest, offset, chunk[:n]); err != nil {
				file.Close()
				return err
			}
			offset += int64(n)
		}
		if e == io.EOF {
			break
		}
		if e != nil {
			file.Close()
			return e
		}
	}
	file.Close()
	handler, err := jobapp.New(m, c.Token)
	if err != nil {
		return err
	}
	defer handler.Close()
	port, err := strconv.Atoi(os.Getenv("PORT"))
	if err != nil || port < 1 || port > 65535 {
		return fmt.Errorf("missing assigned worker port")
	}
	listener, err := net.Listen("tcp4", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return err
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 << 10}
	go func() { _ = server.Serve(listener) }()
	defer server.Close()
	initial := make(chan error, 1)
	go func() {
		startup, stop := context.WithTimeout(ctx, 15*time.Minute)
		defer stop()
		_, e := jobapp.StartInitial(startup, m, c.Digest, c.Scope)
		initial <- e
	}()
	select {
	case err = <-initial:
		if err != nil {
			return err
		}
	case <-ctx.Done():
		return ctx.Err()
	}
	ready := false
	for _, in := range m.Snapshot().Instances {
		ready = ready || (in.Digest == c.Digest && in.Scope == c.Scope && in.State == "ready")
	}
	if ready {
		fmt.Println("Fleet App ready")
	} else {
		fmt.Println("Fleet App control ready; awaiting authorized App startup")
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
		live := false
		state := m.Snapshot()
		for _, op := range state.Operations {
			if op.State == "queued" || op.State == "running" {
				live = true
			}
		}
		for _, in := range state.Instances {
			if in.State == "degraded" || in.State == "failed" {
				// Keep ambiguous/live resources; release the allocation once
				// every owned process is known to have exited.
				for _, resource := range in.Resources {
					probe, stop := context.WithTimeout(ctx, 2*time.Second)
					alive, err := driver.Alive(probe, resource)
					stop()
					if alive || err != nil {
						live = true
					}
				}
			} else if in.State != "stopped" && in.State != "installed" {
				live = true
			}
		}
		if !live {
			return nil
		}
	}
}
