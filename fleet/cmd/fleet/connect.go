package main

import (
	"context"
	"fmt"
	"time"

	"github.com/nats-io/nats.go"
)

const (
	fleetNATSConnectTimeout  = 10 * time.Second
	fleetNATSConnectAttempts = 3
)

// fleetNATSOptions keeps the NATS initialization deadline wide enough for a
// remote TLS endpoint reached through a TUN or transparent proxy. nats.go's
// default is only two seconds for the whole INFO + TLS initialization sequence.
func fleetNATSOptions(base []nats.Option) []nats.Option {
	opts := append([]nats.Option(nil), base...)
	return append(opts, nats.Timeout(fleetNATSConnectTimeout))
}

// retryNATSConnect retries the initial connection a small, bounded number of
// times. The wait function is injected so the retry policy can be tested without
// sleeping; production uses a context-aware exponential backoff.
func retryNATSConnect(
	ctx context.Context,
	attempts int,
	connect func() error,
	wait func(context.Context, time.Duration) error,
) error {
	if attempts < 1 {
		return fmt.Errorf("NATS connection requires at least one attempt")
	}
	var lastErr error
	for attempt := 0; attempt < attempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		lastErr = connect()
		if lastErr == nil {
			return nil
		}
		if attempt == attempts-1 {
			break
		}
		delay := time.Duration(1<<attempt) * time.Second
		if err := wait(ctx, delay); err != nil {
			return err
		}
	}
	return fmt.Errorf("NATS connection failed after %d attempts: %w", attempts, lastErr)
}

func sleepWithContext(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func connectFleetNATS(ctx context.Context, url string, base []nats.Option) (*nats.Conn, error) {
	var nc *nats.Conn
	attempt := 0
	started := time.Now()
	err := retryNATSConnect(
		ctx,
		fleetNATSConnectAttempts,
		func() error {
			attempt++
			attemptStarted := time.Now()
			var err error
			nc, err = nats.Connect(url, fleetNATSOptions(base)...)
			if err != nil && attempt < fleetNATSConnectAttempts {
				fmt.Printf("NATS connect attempt %d/%d failed after %s: %v; retrying\n",
					attempt, fleetNATSConnectAttempts, time.Since(attemptStarted).Round(time.Millisecond), err)
			}
			return err
		},
		sleepWithContext,
	)
	if err != nil {
		return nil, fmt.Errorf("NATS initialization failed after %s: %w", time.Since(started).Round(time.Millisecond), err)
	}
	return nc, nil
}
