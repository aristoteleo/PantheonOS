package lifecycle

// Container engines are node dependencies, not App-supplied shell installers.
// The selected engine is pinned on disk: a reconnect must not silently point
// existing container identities at a different Docker context or daemon.
import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

const engineVersion = "27.5.1"
const engineSHA = "4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640"
const engineURL = "https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz"
const engineSmokeImage = "hello-world@sha256:5e23090353324d887c48ad5e5c56d294eab81588df9605b07d1afe895f9cc8f8"

type engineSelection struct {
	Mode    string `json:"mode"`
	Binary  string `json:"binary"`
	Host    string `json:"host"`
	Version string `json:"version,omitempty"`
}
type ContainerEngine struct {
	Root     string
	mu       sync.Mutex
	verified bool
}

func (e *ContainerEngine) selection() (engineSelection, error) {
	var s engineSelection
	b, err := os.ReadFile(filepath.Join(e.Root, "engine.json"))
	if err != nil {
		return s, err
	}
	if err = StrictDecode(b, &s); err != nil {
		return s, err
	}
	if (s.Mode != "existing" && s.Mode != "managed") || !filepath.IsAbs(s.Binary) || !strings.HasPrefix(s.Host, "unix:///") {
		return s, fmt.Errorf("invalid node engine configuration")
	}
	return s, nil
}
func engineEnv(binary string) []string {
	// Avoid DOCKER_CONTEXT/DOCKER_HOST/DOCKER_CONFIG inherited from a controller.
	return append(cleanEnv(), "PATH="+filepath.Dir(binary)+":"+os.Getenv("PATH"))
}
func engineCommand(ctx context.Context, s engineSelection, args ...string) ([]byte, error) {
	return run(ctx, append([]string{s.Binary, "--host", s.Host}, args...), "", engineEnv(s.Binary), nil)
}
func (e *ContainerEngine) Command(ctx context.Context, args ...string) ([]byte, error) {
	s, err := e.selection()
	if err != nil {
		return nil, fmt.Errorf("install the App's container engine dependency first: %w", err)
	}
	return engineCommand(ctx, s, args...)
}
func engineResponds(ctx context.Context, s engineSelection) bool {
	cc, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	b, err := engineCommand(cc, s, "info", "--format", "{{.OSType}}")
	return err == nil && strings.TrimSpace(string(b)) == "linux"
}
func (e *ContainerEngine) save(s engineSelection) error {
	b, err := json.Marshal(s)
	if err != nil {
		return err
	}
	p := filepath.Join(e.Root, "engine.json")
	if err = os.WriteFile(p+".tmp", b, 0600); err != nil {
		return err
	}
	return os.Rename(p+".tmp", p)
}

func (e *ContainerEngine) Ensure(ctx context.Context, dep EngineDependency) (Receipt, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	failed := func(err error) (Receipt, error) { return Receipt{Status: "failed", Message: err.Error()}, err }
	if dep.Provider != "docker" || (dep.Provision != "if-missing" && dep.Provision != "never") {
		return failed(fmt.Errorf("unsupported engine dependency"))
	}
	if err := os.MkdirAll(e.Root, 0700); err != nil {
		return failed(err)
	}
	s, err := e.selection()
	if os.IsNotExist(err) {
		// Reuse an actually available local Linux engine. Remote Docker contexts
		// would violate the requested Fleet node placement and are not adopted.
		if binary, be := exec.LookPath("docker"); be == nil {
			cc, cancel := context.WithTimeout(ctx, 5*time.Second)
			host, he := run(cc, []string{binary, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"}, "", os.Environ(), nil)
			cancel()
			candidate := engineSelection{Mode: "existing", Binary: binary, Host: strings.TrimSpace(string(host))}
			if he == nil && strings.HasPrefix(candidate.Host, "unix:///") && engineResponds(ctx, candidate) {
				s = candidate
			}
		}
		if s.Mode == "" {
			if dep.Provision == "never" {
				return failed(fmt.Errorf("no usable local Docker engine; this App disallows automatic provisioning"))
			}
			if err = enginePreflight(ctx); err != nil {
				return failed(err)
			}
			if err = installEngine(ctx, e.Root); err != nil {
				return failed(err)
			}
			sum := sha256.Sum256([]byte(e.Root))
			socketDir := filepath.Join(os.TempDir(), fmt.Sprintf("pantheon-engine-%d-%x", os.Getuid(), sum[:8]))
			if err = os.MkdirAll(socketDir, 0700); err != nil {
				return failed(err)
			}
			s = engineSelection{Mode: "managed", Binary: filepath.Join(e.Root, "bin", "docker"), Host: "unix://" + filepath.Join(socketDir, "docker.sock"), Version: engineVersion}
		}
		if err = e.save(s); err != nil {
			return failed(err)
		}
	} else if err != nil {
		return failed(err)
	}
	if !engineResponds(ctx, s) {
		e.verified = false
		if s.Mode != "managed" {
			return failed(fmt.Errorf("configured Docker engine is offline; start it on this node (no fallback to another engine)"))
		}
		if err = enginePreflight(ctx); err != nil {
			return failed(err)
		}
		if err = e.start(ctx, s); err != nil {
			return failed(err)
		}
	}
	if !e.verified {
		cc, cancel := context.WithTimeout(ctx, 3*time.Minute)
		_, err = engineCommand(cc, s, "pull", engineSmokeImage)
		cancel()
		if err != nil {
			return failed(fmt.Errorf("container engine image download failed: %w", err))
		}
		// docker info alone succeeds even on sandboxes that cannot start containers.
		cc, cancel = context.WithTimeout(ctx, 30*time.Second)
		b, se := engineCommand(cc, s, "run", "--rm", "--network=none", "--read-only", engineSmokeImage)
		cancel()
		if se != nil || !strings.Contains(string(b), "Hello from Docker!") {
			return failed(fmt.Errorf("container engine cannot execute containers on this node; Workspace may require VM runtime: %v", se))
		}
		e.verified = true
	}
	return Receipt{Status: "succeeded", Message: "Docker ready on this node (" + s.Mode + "); container execution verified. Shared engine and App working copies are retained on uninstall."}, nil
}

func enginePreflight(ctx context.Context) error {
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" || os.Geteuid() != 0 {
		return fmt.Errorf("automatic Docker preparation needs a Linux amd64 node with root privileges; otherwise configure a local Docker engine on the chosen node")
	}
	cc, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if _, err := run(cc, []string{"unshare", "--mount", "--net", "--", "true"}, "", cleanEnv(), nil); err != nil {
		return fmt.Errorf("this Workspace does not permit container namespaces; enable the VM Workspace runtime before installing Office (existing Workspace data must be retained)")
	}
	return nil
}

func installEngine(ctx context.Context, root string) error {
	// Docker's bridge requires iptables. The only OS dependency installer is
	// maintained by Fleet, and is never taken from a third-party App manifest.
	if _, err := exec.LookPath("iptables"); err != nil {
		if _, err = exec.LookPath("apt-get"); err != nil {
			return fmt.Errorf("install iptables on this node before preparing Docker")
		}
		cc, cancel := context.WithTimeout(ctx, 5*time.Minute)
		defer cancel()
		env := append(cleanEnv(), "DEBIAN_FRONTEND=noninteractive")
		if _, err = run(cc, []string{"apt-get", "update"}, "", env, nil); err != nil {
			return fmt.Errorf("prepare iptables package index: %w", err)
		}
		if _, err = run(cc, []string{"apt-get", "install", "-y", "--no-install-recommends", "iptables"}, "", env, nil); err != nil {
			return fmt.Errorf("prepare iptables: %w", err)
		}
	}
	cc, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	req, err := http.NewRequestWithContext(cc, "GET", engineURL, nil)
	if err != nil {
		return err
	}
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return fmt.Errorf("Docker download HTTP %d", response.StatusCode)
	}
	f, err := os.CreateTemp(root, "download-*.tgz")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(f, h), io.LimitReader(response.Body, 150<<20))
	ce := f.Close()
	if err != nil {
		return err
	}
	if ce != nil {
		return ce
	}
	if n >= 150<<20 || hex.EncodeToString(h.Sum(nil)) != engineSHA {
		return fmt.Errorf("Docker distribution checksum mismatch")
	}
	return extractEngine(f.Name(), root)
}
func extractEngine(archive, root string) error {
	stage, err := os.MkdirTemp(root, "bin-stage-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	var total int64
	for {
		h, e := tr.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			return e
		}
		if h.Typeflag == tar.TypeDir && h.Name == "docker/" {
			continue
		}
		name := strings.TrimPrefix(h.Name, "docker/")
		if !strings.HasPrefix(h.Name, "docker/") || !nameRE.MatchString(name) || h.Typeflag != tar.TypeReg {
			return fmt.Errorf("unsafe Docker distribution entry")
		}
		total += h.Size
		if total > 400<<20 {
			return fmt.Errorf("Docker distribution too large")
		}
		out, e := os.OpenFile(filepath.Join(stage, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0500)
		if e != nil {
			return e
		}
		_, e = io.Copy(out, tr)
		closeErr := out.Close()
		if e != nil {
			return e
		}
		if closeErr != nil {
			return closeErr
		}
	}
	for _, name := range []string{"docker", "dockerd", "containerd", "runc", "docker-proxy"} {
		if _, err = os.Stat(filepath.Join(stage, name)); err != nil {
			return fmt.Errorf("incomplete Docker distribution: %s", name)
		}
	}
	dest := filepath.Join(root, "bin")
	if _, err = os.Stat(dest); err == nil {
		return nil
	} // never replace an engine used by running Apps
	return os.Rename(stage, dest)
}
func (e *ContainerEngine) start(ctx context.Context, s engineSelection) error {
	runtimeDir := filepath.Dir(strings.TrimPrefix(s.Host, "unix://"))
	if err := os.MkdirAll(runtimeDir, 0700); err != nil {
		return err
	}
	// Engine cache is separate from persistent document mounts. vfs works on
	// filesystems where nested overlayfs is unavailable.
	argv := []string{filepath.Join(filepath.Dir(s.Binary), "dockerd"), "--host=" + s.Host, "--data-root=" + filepath.Join(e.Root, "data"), "--exec-root=" + filepath.Join(runtimeDir, "exec"), "--pidfile=" + filepath.Join(runtimeDir, "pid"), "--storage-driver=vfs", "--log-level=error"}
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Env = engineEnv(s.Binary)
	configureProcess(cmd)
	log, err := os.OpenFile(filepath.Join(e.Root, "daemon.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	cmd.Stdout = log
	cmd.Stderr = log
	if err = cmd.Start(); err != nil {
		log.Close()
		return err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait(); log.Close() }()
	cc, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	for {
		if engineResponds(cc, s) {
			return nil
		}
		select {
		case err := <-done:
			return fmt.Errorf("managed Docker failed to start: %v; see node dependency daemon.log", err)
		case <-cc.Done():
			_ = terminateProcess(cmd.Process.Pid)
			return fmt.Errorf("managed Docker readiness timed out")
		case <-time.After(250 * time.Millisecond):
		}
	}
}
