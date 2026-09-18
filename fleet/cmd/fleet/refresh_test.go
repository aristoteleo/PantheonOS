package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/join"
)

func TestCredentialRefreshUsesIssuedExpiry(t *testing.T) {
	a, err := auth.Bootstrap(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	previous := auth.AccessTTL
	auth.AccessTTL = 20 * time.Second
	defer func() { auth.AccessTTL = previous }()
	now := time.Now()
	creds, err := a.MintFleetNode("test", "node")
	if err != nil {
		t.Fatal(err)
	}
	delay := credentialRefreshDelay(creds, now)
	if delay < 14*time.Second || delay > 15*time.Second {
		t.Fatalf("renewal delay = %s, want 75%% of issued 20s TTL", delay)
	}
	if delay := credentialRefreshDelay(creds, now.Add(time.Minute)); delay > time.Second {
		t.Fatalf("expired credential delay = %s", delay)
	}
}

func TestRefreshRetriesWithoutWaitingForNormalInterval(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	var attempts int
	err := runCredentialRefresh(ctx, make(chan struct{}), time.Millisecond, 5*time.Millisecond, 10*time.Millisecond, func() (time.Duration, error) {
		attempts++
		if attempts < 4 {
			return 0, errors.New("DNS temporarily unavailable")
		}
		cancel()
		return time.Hour, nil
	})
	if attempts != 4 || !errors.Is(err, context.Canceled) {
		t.Fatalf("attempts=%d, error=%v", attempts, err)
	}
}

func TestRefreshAuthKicksRespectBackoffAndRevocation(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	kick := make(chan struct{}, 1)
	var attempts atomic.Int32
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				select {
				case kick <- struct{}{}:
				default:
				}
			}
		}
	}()
	started := time.Now()
	err := runCredentialRefresh(ctx, kick, time.Hour, 40*time.Millisecond, 40*time.Millisecond, func() (time.Duration, error) {
		if attempts.Add(1) == 1 {
			return 0, errors.New("offline")
		}
		return 0, join.ErrRevoked
	})
	cancel()
	<-done
	if !errors.Is(err, join.ErrRevoked) || attempts.Load() != 2 {
		t.Fatalf("attempts=%d, error=%v", attempts.Load(), err)
	}
	if time.Since(started) < 40*time.Millisecond {
		t.Fatal("auth kicks bypassed retry backoff")
	}
}

func TestRefreshCancellationDuringBackoff(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	started := time.Now()
	err := runCredentialRefresh(ctx, make(chan struct{}), time.Millisecond, time.Hour, time.Hour, func() (time.Duration, error) {
		cancel()
		return 0, errors.New("offline")
	})
	if !errors.Is(err, context.Canceled) || time.Since(started) > time.Second {
		t.Fatalf("cancel failed: %v", err)
	}
}

func TestPrivateFileReplacementNeverExposesPartialCredentials(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fleet.creds")
	first, second := []byte("complete first credential"), []byte("complete replacement credential")
	if err := writePrivateFile(path, first); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	readErr := make(chan error, 1)
	go func() {
		defer close(readErr)
		for {
			select {
			case <-done:
				return
			default:
			}
			got, err := os.ReadFile(path)
			if err != nil {
				readErr <- err
				return
			}
			if string(got) != string(first) && string(got) != string(second) {
				readErr <- errors.New("reader saw partial credential")
				return
			}
		}
	}()
	var writeErr error
	for i := 0; i < 50; i++ {
		next := first
		if i%2 == 0 {
			next = second
		}
		if writeErr = writePrivateFile(path, next); writeErr != nil {
			break
		}
	}
	close(done)
	if err := <-readErr; err != nil {
		t.Fatal(err)
	}
	if writeErr != nil {
		t.Fatal(writeErr)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0600 {
		t.Fatalf("permissions=%v", info.Mode())
	}
}
