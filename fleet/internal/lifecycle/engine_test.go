package lifecycle

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"fmt"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestContainerRequiresDeclaredDependency(t *testing.T) {
	d := definition()
	d.Components[0].Runtime = "container"
	d.Components[0].Image = "example.invalid/app@sha256:" + strings.Repeat("a", 64)
	if err := d.Validate(); err == nil {
		t.Fatal("undeclared engine accepted")
	}
	d.Dependencies.ContainerEngine = &EngineDependency{Provider: "docker", Provision: "if-missing"}
	if err := d.Validate(); err != nil {
		t.Fatal(err)
	}
	d.Dependencies.ContainerEngine.Provision = "run-arbitrary-installer"
	if err := d.Validate(); err == nil {
		t.Fatal("unsupported installer accepted")
	}
}

func TestEngineDistributionRejectsLinksAndTraversal(t *testing.T) {
	for _, name := range []string{"docker/../../escape", "docker/link"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			p := filepath.Join(root, "docker.tgz")
			f, _ := os.Create(p)
			gz := gzip.NewWriter(f)
			tw := tar.NewWriter(gz)
			_ = tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"})
			tw.Close()
			gz.Close()
			f.Close()
			if err := extractEngine(p, root); err == nil {
				t.Fatal("unsafe archive accepted")
			}
		})
	}
}

type dependencyFake struct {
	*fakeDriver
	fail  bool
	calls int
}

func (f *dependencyFake) PrepareDependencies(context.Context, Dependencies) (Receipt, error) {
	f.calls++
	if f.fail {
		return Receipt{}, fmt.Errorf("container namespaces unavailable")
	}
	return Receipt{Status: "succeeded"}, nil
}
func TestDependencyFailureNeverStartsAppOrReportsInstalled(t *testing.T) {
	f := &dependencyFake{fakeDriver: &fakeDriver{alive: map[string]bool{}}, fail: true}
	m, e := Open(t.TempDir(), "owner", "node", proto.Capability{}, f)
	if e != nil {
		t.Fatal(e)
	}
	defer m.Close()
	def := definition()
	def.Dependencies.ContainerEngine = &EngineDependency{Provider: "docker", Provision: "if-missing"}
	b, d := bundle(t, def, nil)
	_, _ = m.Stage(d, 0, b)
	op := submit(t, m, d, "blocked", "start", "app", 0)
	if op.State != "failed" || f.starts != 0 || m.Snapshot().Installations[d].State == "installed" {
		t.Fatal("dependency failure bypassed", op)
	}
	if op.Steps[len(op.Steps)-1].Receipt.Status != "failed" {
		t.Fatal("false success receipt")
	}
	f.fail = false
	op = submit(t, m, d, "retry", "install", "app", 0)
	if op.State != "succeeded" || f.starts != 0 {
		t.Fatal(op)
	}
}

// Explicit opt-in: run only inside a disposable Linux VM without user data.
// This exercises the real dependency provisioner, not a preinstalled Docker CLI.
func TestManagedEngineIntegration(t *testing.T) {
	if os.Getenv("PANTHEON_TEST_MANAGED_ENGINE") != "1" {
		t.Skip("requires disposable VM")
	}
	if runtime.GOOS != "linux" {
		t.Fatal("Linux VM required")
	}
	root := t.TempDir()
	e := &ContainerEngine{Root: root}
	defer func() {
		if s, err := e.selection(); err == nil && s.Mode == "managed" {
			dir := filepath.Dir(strings.TrimPrefix(s.Host, "unix://"))
			if b, err := os.ReadFile(filepath.Join(dir, "pid")); err == nil {
				if pid, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil {
					_ = terminateProcess(pid)
					time.Sleep(time.Second)
				}
			}
			_ = os.RemoveAll(dir)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Minute)
	defer cancel()
	receipt, err := e.Ensure(ctx, EngineDependency{Provider: "docker", Provision: "if-missing"})
	if err != nil {
		if b, e := os.ReadFile(filepath.Join(root, "daemon.log")); e == nil {
			t.Log(string(b))
		}
		t.Fatal(err)
	}
	if receipt.Status != "succeeded" {
		t.Fatal(receipt)
	}
	s, err := e.selection()
	if err != nil || s.Mode != "managed" {
		t.Fatal(s, err)
	}
	// A new manager process reuses the persisted engine selection.
	second := &ContainerEngine{Root: root}
	if _, err = second.Ensure(ctx, EngineDependency{Provider: "docker", Provision: "never"}); err != nil {
		t.Fatal(err)
	}
	if b, err := second.Command(ctx, "run", "--rm", engineSmokeImage); err != nil || !strings.Contains(string(b), "Hello from Docker!") {
		t.Fatal(string(b), err)
	}
}
