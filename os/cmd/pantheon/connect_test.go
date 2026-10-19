package main

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
)

func TestFleetNATSOptionsUseTenSecondConnectTimeout(t *testing.T) {
	opts := nats.GetDefaultOptions()
	for _, option := range fleetNATSOptions(nil) {
		if err := option(&opts); err != nil {
			t.Fatalf("apply option: %v", err)
		}
	}
	if opts.Timeout != 10*time.Second {
		t.Fatalf("NATS connect timeout = %s, want 10s", opts.Timeout)
	}
}

func TestRetryNATSConnectRetriesUntilSuccess(t *testing.T) {
	attempts := 0
	var waits []time.Duration
	err := retryNATSConnect(
		context.Background(),
		3,
		func() error {
			attempts++
			if attempts < 3 {
				return errors.New("temporary connect failure")
			}
			return nil
		},
		func(_ context.Context, delay time.Duration) error {
			waits = append(waits, delay)
			return nil
		},
	)
	if err != nil {
		t.Fatalf("retryNATSConnect: %v", err)
	}
	if attempts != 3 {
		t.Fatalf("attempts = %d, want 3", attempts)
	}
	if len(waits) != 2 || waits[0] != time.Second || waits[1] != 2*time.Second {
		t.Fatalf("waits = %v, want [1s 2s]", waits)
	}
}

func TestRetryNATSConnectReturnsLastErrorAfterMaxAttempts(t *testing.T) {
	attempts := 0
	want := errors.New("unreachable")
	err := retryNATSConnect(
		context.Background(),
		3,
		func() error {
			attempts++
			return want
		},
		func(_ context.Context, _ time.Duration) error { return nil },
	)
	if !errors.Is(err, want) {
		t.Fatalf("error = %v, want wrapped %v", err, want)
	}
	if attempts != 3 {
		t.Fatalf("attempts = %d, want 3", attempts)
	}
}

func TestRetryNATSConnectStopsWhenContextIsCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	attempts := 0
	err := retryNATSConnect(
		ctx,
		3,
		func() error {
			attempts++
			return errors.New("temporary connect failure")
		},
		func(ctx context.Context, _ time.Duration) error { return ctx.Err() },
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if attempts != 0 {
		t.Fatalf("attempts = %d, want 0 for canceled context", attempts)
	}
}
