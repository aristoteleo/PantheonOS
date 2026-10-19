package apps

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Use the test binary as a portable long-running child with a real listener.
func TestListenerProcess(t *testing.T) {
	file := os.Getenv("FLEET_TEST_LISTENER_FILE")
	if file == "" {
		return
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		os.Exit(2)
	}
	if err := os.WriteFile(file, []byte(listener.Addr().String()), 0600); err != nil {
		os.Exit(3)
	}
	for {
		conn, err := listener.Accept()
		if err != nil {
			os.Exit(4)
		}
		conn.Close()
	}
}

func TestCloseReapsCoreProcessAndReleasesPort(t *testing.T) {
	s := New(nil)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	t.Cleanup(func() { _ = s.Close(ctx) })
	file := filepath.Join(t.TempDir(), "listener")
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Start(Spec{AppID: "desktop", Command: []string{exe, "-test.run=^TestListenerProcess$"}, Env: map[string]string{"FLEET_TEST_LISTENER_FILE": file}}); err != nil {
		t.Fatal(err)
	}
	var address []byte
	for len(address) == 0 {
		address, _ = os.ReadFile(file)
		select {
		case <-ctx.Done():
			t.Fatal("listener did not start")
		default:
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err := s.Close(ctx); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", string(address))
	if err != nil {
		t.Fatalf("core process still owns its port after shutdown: %v", err)
	}
	listener.Close()
	if err := s.Start(Spec{AppID: "desktop", Command: []string{exe}}); err == nil {
		t.Fatal("closed supervisor accepted another start")
	}
}

func TestCloseWaitsForBuiltinCleanup(t *testing.T) {
	s := New(nil)
	started, cleaning, release := make(chan struct{}), make(chan struct{}), make(chan struct{})
	s.RegisterBuiltin("test", func(context.Context, Spec) (func(), error) {
		close(started)
		return func() { close(cleaning); <-release }, nil
	})
	if err := s.Start(Spec{AppID: "test", Runtime: "builtin"}); err != nil {
		t.Fatal(err)
	}
	<-started
	closed := make(chan error, 1)
	go func() { closed <- s.Close(context.Background()) }()
	<-cleaning
	select {
	case <-closed:
		t.Fatal("Close returned before cleanup completed")
	default:
	}
	close(release)
	if err := <-closed; err != nil {
		t.Fatal(err)
	}
}

func waitHealth(t *testing.T, s *Supervisor, appID, want string, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		for _, i := range s.List() {
			if i.AppID == appID && i.Health == want {
				return
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("app %s never reached health %q; instances: %+v", appID, want, s.List())
}

func TestStartReportsHealthyAndStopTerminates(t *testing.T) {
	s := New(nil)
	err := s.Start(Spec{AppID: "sleeper", Command: []string{"sleep", "60"}})
	if err != nil {
		t.Fatal(err)
	}
	waitHealth(t, s, "sleeper", "healthy", 5*time.Second)

	// idempotent: a second Start of a live instance is a no-op
	if err := s.Start(Spec{AppID: "sleeper", Command: []string{"sleep", "60"}}); err != nil {
		t.Fatal(err)
	}
	if n := len(s.List()); n != 1 {
		t.Fatalf("expected 1 instance, got %d", n)
	}

	s.Stop("sleeper", "")
	waitHealth(t, s, "sleeper", "stopped", 2*time.Second)
}

func TestCrashLoopEndsInCrashed(t *testing.T) {
	s := New(nil)
	// exits immediately every time -> crash counter exhausts
	if err := s.Start(Spec{AppID: "flapper", Command: []string{"true"}}); err != nil {
		t.Fatal(err)
	}
	// backoff 1+2+4+8 ≈ 15s worst case; give it room
	waitHealth(t, s, "flapper", "crashed", 30*time.Second)
}

func TestScopesAreIndependentInstances(t *testing.T) {
	s := New(nil)
	if err := s.Start(Spec{AppID: "x", Scope: "app", Command: []string{"sleep", "60"}}); err != nil {
		t.Fatal(err)
	}
	if err := s.Start(Spec{AppID: "x", Scope: "window", Command: []string{"sleep", "60"}}); err != nil {
		t.Fatal(err)
	}
	if n := len(s.List()); n != 2 {
		t.Fatalf("expected 2 scoped instances, got %d: %+v", n, s.List())
	}
	s.StopAll()
}

func TestEmptyCommandIsCrashedNotPanic(t *testing.T) {
	s := New(nil)
	if err := s.Start(Spec{AppID: "empty"}); err != nil {
		t.Fatal(err)
	}
	waitHealth(t, s, "empty", "crashed", 3*time.Second)
}
