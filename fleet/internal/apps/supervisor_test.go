package apps

import (
	"testing"
	"time"
)

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
