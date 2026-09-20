package lifecycle

import (
	"github.com/aristoteleo/pantheon-fleet/internal/nativecapture"

	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/shirou/gopsutil/v4/process"
)

// NativeDriver resolves executables on THIS node. A controller cannot select
// another machine's Python, workdir, Docker socket or arbitrary host mounts.
type NativeDriver struct{ Engine *ContainerEngine }

func (d NativeDriver) PrepareDependencies(ctx context.Context, deps Dependencies) (Receipt, error) {
	if deps.ContainerEngine == nil {
		return Receipt{Status: "succeeded"}, nil
	}
	if d.Engine == nil {
		return Receipt{}, fmt.Errorf("this Runner has no dependency manager")
	}
	return d.Engine.Ensure(ctx, *deps.ContainerEngine)
}

func (d NativeDriver) docker(ctx context.Context, args ...string) ([]byte, error) {
	if d.Engine == nil {
		return nil, fmt.Errorf("container engine not configured")
	}
	return d.Engine.Command(ctx, args...)
}

type boundedOutput struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (b *boundedOutput) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	left := (64 << 10) - b.b.Len()
	if left > 0 {
		if len(p) > left {
			p = p[:left]
		}
		_, _ = b.b.Write(p)
	}
	return n, nil
}
func (b *boundedOutput) Bytes() []byte {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]byte{}, b.b.Bytes()...)
}

func run(ctx context.Context, argv []string, dir string, env []string, input []byte) ([]byte, error) {
	if len(argv) == 0 {
		return nil, fmt.Errorf("missing command")
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	configureProcess(cmd)
	cmd.Cancel = func() error { return killCommandGroup(cmd) }
	cmd.WaitDelay = 2 * time.Second
	cmd.Dir = dir
	cmd.Env = env
	cmd.Stdin = bytes.NewReader(input)
	var out boundedOutput
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	if err != nil {
		return nil, fmt.Errorf("%s: %w", filepath.Base(argv[0]), err)
	}
	return out.Bytes(), nil
}
func cleanEnv() []string {
	// No inherited Hub keys, model keys, fleet credentials or PYTHONPATH.
	out := []string{"PATH=" + os.Getenv("PATH"), "LANG=C.UTF-8", "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1", "PYTHONUTF8=1"}
	for _, k := range []string{"SYSTEMROOT", "TMPDIR", "TEMP", "TMP"} {
		if v := os.Getenv(k); v != "" {
			out = append(out, k+"="+v)
		}
	}
	if path := nativecapture.Path(); path != "" {
		out = append(out, "PANTHEON_NATIVE_CAPTURE_HELPER="+path)
	}
	if path := nativecapture.QuPath(); path != "" {
		out = append(out, "PANTHEON_QUPATH_EXECUTABLE="+path)
	}
	return out
}
func expand(argv []string, p Paths) []string {
	r := strings.NewReplacer("${PACKAGE}", p.Package, "${INSTALL}", p.Install, "${DATA}", p.Data)
	out := make([]string, len(argv))
	for i, v := range argv {
		out[i] = r.Replace(v)
	}
	return out
}
func (d NativeDriver) Prepare(ctx context.Context, c Component) error {
	if c.Runtime == "container" {
		// Finding the CLI alone is not a capability check: contact the daemon.
		cc, cancel := context.WithTimeout(ctx, 10*time.Second)
		_, err := d.docker(cc, "info", "--format", "{{.OSType}}")
		cancel()
		if err != nil {
			return fmt.Errorf("container runtime unavailable: %w", err)
		}
		// Immutable repository digests are safe to reuse. First installation
		// pulls missing images; reinstalling cached App code can work offline.
		if _, cached := d.docker(ctx, "image", "inspect", c.Image); cached == nil {
			return nil
		}
		_, err = d.docker(ctx, "pull", c.Image)
		return err
	}
	if strings.Contains(c.Argv[0], "${") {
		return nil
	}
	if filepath.IsAbs(c.Argv[0]) {
		return fmt.Errorf("process executable must be node-local PATH name or package/install placeholder")
	}
	_, err := exec.LookPath(c.Argv[0])
	return err
}
func (d NativeDriver) Start(ctx context.Context, c Component, p Paths, id string) (Resource, error) {
	r := Resource{Component: c.Name, Runtime: c.Runtime, ID: id}
	if err := ctx.Err(); err != nil {
		return r, err
	}
	if c.Runtime == "container" {
		return d.startContainer(ctx, c, p, r)
	}
	argv := expand(c.Argv, p)
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = p.Package
	cmd.Env = append(cleanEnv(), "HOME="+p.Data)
	for k, v := range c.Env {
		cmd.Env = append(cmd.Env, k+"="+expand([]string{v}, p)[0])
	}
	// Reserve distinct ephemeral loopback ports for this launch. The child must
	// bind them before passing readiness; a bind failure is a failed start, never
	// an instruction to attach to the service already occupying that port.
	var reservations []net.Listener
	defer func() {
		for _, listener := range reservations {
			_ = listener.Close()
		}
	}()
	r.Endpoints = map[string]string{}
	for name := range c.Ports {
		listener, e := net.Listen("tcp4", "127.0.0.1:0")
		if e != nil {
			return r, e
		}
		reservations = append(reservations, listener)
		port := listener.Addr().(*net.TCPAddr).Port
		r.Endpoints[name] = fmt.Sprintf("http://127.0.0.1:%d", port)
		cmd.Env = append(cmd.Env, "PANTHEON_PORT_"+strings.ToUpper(strings.ReplaceAll(name, "-", "_"))+"="+strconv.Itoa(port))
	}
	configureProcess(cmd)
	log, err := os.OpenFile(filepath.Join(p.Data, c.Name+".log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return r, err
	}
	cmd.Stdout = log
	cmd.Stderr = log
	for _, listener := range reservations {
		_ = listener.Close()
	}
	if err = cmd.Start(); err != nil {
		log.Close()
		return r, err
	}
	r.PID = cmd.Process.Pid
	proc, err := process.NewProcess(int32(r.PID))
	if err == nil {
		r.Birth, err = proc.CreateTime()
	}
	go func() { _ = cmd.Wait(); _ = log.Close() }()
	if err != nil {
		return r, fmt.Errorf("cannot identify child process: %w", err)
	}
	return r, nil
}
func (d NativeDriver) startContainer(ctx context.Context, c Component, p Paths, r Resource) (Resource, error) {
	// Do not reuse/adopt a name collision. The deterministic name and ownership
	// label make a creation with a lost reply inspectable after a restart.
	argv := []string{"create", "--name", r.ID, "--label", "pantheon.resource=" + r.ID, "--security-opt", "no-new-privileges:true", "--log-opt", "max-size=10m", "--log-opt", "max-file=2"}
	for _, port := range c.Ports {
		argv = append(argv, "-p", fmt.Sprintf("127.0.0.1::%d", port))
	}
	for rel, target := range c.Mounts {
		path := filepath.Join(p.Data, rel)
		if err := os.MkdirAll(path, 0700); err != nil {
			return r, err
		}
		real, err := filepath.EvalSymlinks(path)
		if err != nil {
			return r, err
		}
		sub, err := filepath.Rel(p.Data, real)
		if err != nil || !relative(filepath.ToSlash(sub)) {
			return r, fmt.Errorf("mount escapes instance data")
		}
		argv = append(argv, "--mount", "type=bind,src="+real+",dst="+target)
	}
	for k, v := range c.Env {
		argv = append(argv, "--env", k+"="+expand([]string{v}, p)[0])
	}
	argv = append(argv, c.Image)
	argv = append(argv, c.Argv...)
	if _, err := d.docker(ctx, argv...); err != nil {
		return r, err
	}
	if _, err := d.docker(ctx, "start", r.ID); err != nil {
		return r, err
	}
	info, err := d.inspectContainer(ctx, r)
	if err != nil {
		return r, err
	}
	r.Endpoints = map[string]string{}
	for name, port := range c.Ports {
		bindings := info.NetworkSettings.Ports[strconv.Itoa(port)+"/tcp"]
		if len(bindings) != 1 || bindings[0].HostIP != "127.0.0.1" {
			return r, fmt.Errorf("missing loopback port binding")
		}
		r.Endpoints[name] = "http://" + net.JoinHostPort("127.0.0.1", bindings[0].HostPort)
	}
	return r, nil
}

type containerInfo struct {
	Config          struct{ Labels map[string]string } `json:"Config"`
	State           struct{ Running bool }             `json:"State"`
	NetworkSettings struct {
		Ports map[string][]struct {
			HostIP   string
			HostPort string
		}
	} `json:"NetworkSettings"`
}

func (d NativeDriver) inspectContainer(ctx context.Context, r Resource) (containerInfo, error) {
	var info containerInfo
	b, e := d.docker(ctx, "container", "inspect", r.ID)
	if e != nil {
		// Distinguish an absent owned name from an unreachable daemon. This is
		// needed when removal succeeded but its ledger acknowledgement was lost.
		list, le := d.docker(ctx, "container", "ls", "-a", "--filter", "name=^/"+r.ID+"$", "--format", "{{.Names}}")
		if le == nil && strings.TrimSpace(string(list)) == "" {
			return info, nil
		}
		return info, e
	}
	var all []containerInfo
	if e = json.Unmarshal(b, &all); e != nil {
		return info, e
	}
	if len(all) != 1 || all[0].Config.Labels["pantheon.resource"] != r.ID {
		return info, fmt.Errorf("container ownership mismatch")
	}
	return all[0], nil
}
func (d NativeDriver) Alive(ctx context.Context, r Resource) (alive bool, err error) {
	if r.Runtime == "container" {
		info, e := d.inspectContainer(ctx, r)
		return info.State.Running, e
	}
	if r.PID <= 0 || r.Birth <= 0 {
		return false, fmt.Errorf("process creation outcome unknown; cannot safely identify or signal it")
	}
	// ps/CreateTime can race a child exiting on macOS; only turn that error
	// into 'stopped' after independently confirming the PID no longer exists.
	defer func() {
		if err != nil {
			if exists, e := process.PidExists(int32(r.PID)); e == nil && !exists {
				alive, err = false, nil
			}
		}
	}()
	p, e := process.NewProcess(int32(r.PID))
	if errors.Is(e, process.ErrorProcessNotRunning) {
		return false, nil
	}
	if e != nil {
		return false, e
	}
	birth, e := p.CreateTime()
	if e != nil {
		return false, e
	}
	if birth != r.Birth {
		return false, nil
	}
	// Windows has no Unix zombie state and gopsutil Status is unimplemented.
	// PID existence plus creation time still protects against PID reuse there.
	if runtime.GOOS != "windows" {
		status, e := p.Status()
		if e != nil {
			return false, e
		}
		for _, s := range status {
			if s == process.Zombie {
				return false, nil
			}
		}
	}
	return p.IsRunning()
}
func (d NativeDriver) Probe(ctx context.Context, c Component, p Paths, r Resource) error {
	for {
		alive, e := d.Alive(ctx, r)
		if e != nil {
			return e
		}
		if !alive {
			return fmt.Errorf("%s exited before readiness", c.Name)
		}
		argv := expand(c.Readiness.Argv, p)
		env := append(cleanEnv(), "HOME="+p.Data)
		if c.Runtime == "container" {
			argv = append([]string{"exec", r.ID}, c.Readiness.Argv...)
		}
		probeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		if c.Runtime == "container" {
			_, e = d.docker(probeCtx, argv...)
		} else {
			_, e = run(probeCtx, argv, p.Package, env, nil)
		}
		cancel()
		if e == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("%s readiness: %w", c.Name, ctx.Err())
		case <-time.After(250 * time.Millisecond):
		}
	}
}
func (d NativeDriver) Stop(ctx context.Context, c Component, r Resource) error {
	alive, err := d.Alive(ctx, r)
	if err != nil {
		return err
	}
	if !alive {
		return nil
	}
	seconds := c.StopSeconds
	if seconds == 0 {
		seconds = 30
	}
	if r.Runtime == "container" {
		// docker stop kills on timeout. Use TERM + observed exit instead; force stop
		// must be a separate explicit data-loss decision.
		if _, err = d.docker(ctx, "kill", "--signal", "TERM", r.ID); err != nil {
			return err
		}
	} else if err = terminateProcess(r.PID); err != nil {
		return err
	}
	stopCtx, cancel := context.WithTimeout(ctx, time.Duration(seconds)*time.Second)
	defer cancel()
	for {
		alive, err = d.Alive(stopCtx, r)
		if err != nil {
			return err
		}
		if !alive {
			return nil
		}
		select {
		case <-stopCtx.Done():
			return fmt.Errorf("stop blocked: %s has not exited", c.Name)
		case <-time.After(100 * time.Millisecond):
		}
	}
}

func (d NativeDriver) Release(ctx context.Context, r Resource) error {
	if r.Runtime != "container" {
		return nil
	}
	info, err := d.inspectContainer(ctx, r)
	if err != nil {
		return err
	}
	if info.State.Running {
		return fmt.Errorf("cannot release a running container")
	}
	if info.Config.Labels == nil {
		return nil
	} // already removed
	_, err = d.docker(ctx, "container", "rm", r.ID)
	return err
}
func (d NativeDriver) Hook(ctx context.Context, h Hook, p Paths, input map[string]any) (Receipt, error) {
	var receipt Receipt
	b, err := json.Marshal(input)
	if err != nil {
		return receipt, err
	}
	// stdout is the receipt; stderr is deliberately not exposed as a response
	// because App diagnostics can include credentials or document contents.
	argv := expand(h.Argv, p)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	configureProcess(cmd)
	cmd.Cancel = func() error { return killCommandGroup(cmd) }
	cmd.WaitDelay = 2 * time.Second
	cmd.Dir = p.Package
	cmd.Env = append(cleanEnv(), "HOME="+p.Install)
	cmd.Stdin = bytes.NewReader(b)
	var out boundedOutput
	cmd.Stdout = &out
	cmd.Stderr = io.Discard
	if err = cmd.Run(); err != nil {
		return receipt, fmt.Errorf("hook execution failed: %w", err)
	}
	err = StrictDecode(out.Bytes(), &receipt)
	return receipt, err
}

func (d NativeDriver) ContainerHook(ctx context.Context, h Hook, r Resource, input map[string]any) (Receipt, error) {
	var receipt Receipt
	if r.Runtime != "container" || r.Component != h.Component || d.Engine == nil {
		return receipt, fmt.Errorf("invalid container hook target")
	}
	info, err := d.inspectContainer(ctx, r)
	if err != nil {
		return receipt, err
	}
	if !info.State.Running {
		return receipt, fmt.Errorf("hook container is not running; working copies retained")
	}
	s, err := d.Engine.selection()
	if err != nil {
		return receipt, err
	}
	b, err := json.Marshal(input)
	if err != nil {
		return receipt, err
	}
	argv := append([]string{"--host", s.Host, "exec", "-i", r.ID}, h.Argv...)
	cmd := exec.CommandContext(ctx, s.Binary, argv...)
	configureProcess(cmd)
	cmd.Cancel = func() error { return killCommandGroup(cmd) }
	cmd.WaitDelay = 2 * time.Second
	cmd.Env = engineEnv(s.Binary)
	cmd.Stdin = bytes.NewReader(b)
	var output boundedOutput
	cmd.Stdout, cmd.Stderr = &output, io.Discard
	if err = cmd.Run(); err != nil {
		return receipt, fmt.Errorf("container hook failed: %w", err)
	}
	err = StrictDecode(output.Bytes(), &receipt)
	return receipt, err
}
