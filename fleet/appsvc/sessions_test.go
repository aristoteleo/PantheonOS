package appsvc

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestSessionLeaseRetryRenewReleaseAndExpiry(t *testing.T) {
	var created, closed atomic.Int64
	var now atomic.Int64
	now.Store(time.Now().Unix())
	s := NewSessionRegistry(func(kind string) (SessionResource, error) {
		id := created.Add(1)
		var alive atomic.Bool
		alive.Store(true)
		return SessionResource{ID: fmt.Sprint(id), Alive: alive.Load, Close: func() error {
			alive.Store(false)
			closed.Add(1)
			return nil
		}}, nil
	})
	defer s.Close()
	s.mu.Lock()
	s.now = func() time.Time { return time.Unix(now.Load(), 0) }
	s.mu.Unlock()
	lease := strings.Repeat("a", 64)
	first, err := s.Acquire("owner-a", lease, "shell", 300)
	if err != nil || first.State != "active" {
		t.Fatal(first, err)
	}
	var wg sync.WaitGroup
	for range 10 {
		wg.Go(func() {
			retry, err := s.Acquire("owner-a", lease, "shell", 900)
			if err != nil || retry != first {
				t.Error("lost-reply retry replaced the original lease", retry, err)
			}
		})
	}
	wg.Wait()
	if created.Load() != 1 {
		t.Fatal("creation replayed")
	}
	for _, action := range []func() error{
		func() error { _, err := s.Acquire("owner-b", lease, "shell", 300); return err },
		func() error { _, err := s.Acquire("owner-a", lease, "kernel", 300); return err },
		func() error { _, err := s.Renew("owner-b", lease, 300); return err },
		func() error { _, err := s.Release("owner-b", lease); return err },
	} {
		if action() == nil {
			t.Fatal("changed owner or acquisition intent accepted")
		}
	}
	now.Add(200)
	renewed, err := s.Renew("owner-a", lease, 300)
	if err != nil || renewed.Expires <= first.Expires || renewed.SessionID != first.SessionID {
		t.Fatal(renewed, err)
	}
	short, _ := s.Renew("owner-a", lease, 30)
	if short.Expires != renewed.Expires {
		t.Fatal("retry shortened lease")
	}
	released, err := s.Release("owner-a", lease)
	if err != nil || released.State != "released" || closed.Load() != 1 {
		t.Fatal(released, err)
	}
	s.Release("owner-a", lease)
	if got, _ := s.Acquire("owner-a", lease, "shell", 300); got.State != "released" || created.Load() != 1 || closed.Load() != 1 {
		t.Fatal("released intent resurrected", got)
	}
	other := strings.Repeat("b", 64)
	s.Acquire("owner-a", other, "shell", 30)
	now.Add(31)
	if got, _ := s.Renew("owner-a", other, 900); got.State != "expired" || closed.Load() != 2 {
		t.Fatal("expired lease revived", got)
	}
}

func TestSessionCleanupFailureRetainsTombstoneAndRetries(t *testing.T) {
	var canClose atomic.Bool
	var closed atomic.Int64
	s := NewSessionRegistry(func(string) (SessionResource, error) {
		return SessionResource{ID: "resource", Alive: func() bool { return true }, Close: func() error {
			closed.Add(1)
			if !canClose.Load() {
				return fmt.Errorf("private failure details")
			}
			return nil
		}}, nil
	})
	defer s.Close()
	lease := strings.Repeat("c", 64)
	s.Acquire("owner", lease, "attachment", 300)
	if got, _ := s.Release("owner", lease); got.State != "closing" {
		t.Fatal("cleanup falsely confirmed", got)
	}
	if got, _ := s.Renew("owner", lease, 900); got.State != "closing" {
		t.Fatal("closing resource revived", got)
	}
	canClose.Store(true)
	s.Sweep()
	if got, _ := s.Get("owner", lease); got.State != "released" || closed.Load() < 2 {
		t.Fatal(got)
	}
}

func TestSessionLossFailureAndCapacityNeverRecreate(t *testing.T) {
	var alive atomic.Bool
	alive.Store(true)
	var count atomic.Int64
	s := NewSessionRegistry(func(kind string) (SessionResource, error) {
		count.Add(1)
		if kind == "fail" {
			return SessionResource{}, fmt.Errorf("private spawn path")
		}
		return SessionResource{ID: "resource", Alive: alive.Load, Close: func() error { return nil }}, nil
	})
	defer s.Close()
	s.mu.Lock()
	s.limit = 2
	s.mu.Unlock()
	lease, failed := strings.Repeat("a", 64), strings.Repeat("b", 64)
	s.Acquire("owner", lease, "shell", 300)
	alive.Store(false)
	if got, _ := s.Renew("owner", lease, 900); got.State != "lost" {
		t.Fatal(got)
	}
	for range 2 {
		if got, _ := s.Acquire("owner", failed, "fail", 300); got.State != "failed" {
			t.Fatal(got)
		}
	}
	if _, err := s.Acquire("owner", strings.Repeat("c", 64), "shell", 300); err == nil || count.Load() != 2 {
		t.Fatal("full registry forgot receipts or replayed failure")
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Acquire("owner", lease, "shell", 300); err == nil {
		t.Fatal("stopped provider admitted acquisition")
	}
}

func TestSessionWireRejectsInvalidLifetimeBeforeCreation(t *testing.T) {
	s := NewSessionRegistry(func(string) (SessionResource, error) {
		t.Fatal("invalid request created resource")
		return SessionResource{}, nil
	})
	defer s.Close()
	handler := SessionHandlers(s, nil)["resource_session_acquire"]
	for _, ttl := range []any{true, 30.5, -1, 901, "300", nil} {
		_, err := handler(context.Background(), map[string]any{"owner_ref": "owner", "lease_id": strings.Repeat("a", 64), "kind": "shell", "ttl_seconds": ttl})
		if err == nil {
			t.Fatal("invalid ttl admitted", ttl)
		}
	}
}

func TestSlowSessionCleanupDoesNotBlockOtherOwners(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var closed sync.Once
	s := NewSessionRegistry(func(kind string) (SessionResource, error) {
		return SessionResource{ID: kind, Alive: func() bool { return true }, Close: func() error {
			if kind == "slow" {
				closed.Do(func() { close(entered) })
				<-release
			}
			return nil
		}}, nil
	})
	defer s.Close()
	defer close(release)
	one, _ := s.Acquire("one", strings.Repeat("a", 64), "slow", 300)
	two, _ := s.Acquire("two", strings.Repeat("b", 64), "fast", 300)
	done := make(chan struct{})
	go func() { defer close(done); s.Release(one.OwnerRef, one.LeaseID) }()
	<-entered
	other := make(chan error, 1)
	go func() {
		value, err := s.Renew(two.OwnerRef, two.LeaseID, 300)
		if err == nil && value.State != "active" {
			err = fmt.Errorf("other resource changed state")
		}
		other <- err
	}()
	select {
	case err := <-other:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("one session's cleanup blocked another owner")
	}
}

func TestProviderSweeperExpiresWithoutFurtherCalls(t *testing.T) {
	var now atomic.Int64
	now.Store(time.Now().Unix())
	closed := make(chan struct{})
	s := NewSessionRegistry(func(string) (SessionResource, error) {
		return SessionResource{ID: "ephemeral", Alive: func() bool { return true }, Close: func() error { close(closed); return nil }}, nil
	})
	defer s.Close()
	s.mu.Lock()
	s.now = func() time.Time { return time.Unix(now.Load(), 0) }
	s.mu.Unlock()
	s.Acquire("owner", strings.Repeat("a", 64), "ephemeral", 30)
	now.Add(31)
	select {
	case <-closed:
	case <-time.After(3 * time.Second):
		t.Fatal("abandoned resource required a client call to expire")
	}
}
