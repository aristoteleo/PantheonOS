package main

import (
	"context"
	"errors"
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
	return append(opts,
		nats.Timeout(fleetNATSConnectTimeout),
		// Laptops can be offline for hours. Keep the same connection (and its
		// subscriptions) alive until the runner is explicitly stopped.
		nats.MaxReconnects(-1),
		nats.ReconnectWait(2*time.Second),
		nats.PingInterval(15*time.Second),
		nats.MaxPingsOutstanding(2),
	)
}

// Only renewable credentials may opt out of NATS' repeated-auth-error abort.
// The controller remains authoritative: a revoked node stops the runner.
func fleetRecoveryOptions(ctx context.Context, stop context.CancelFunc, kick chan<- struct{}, renewable bool) []nats.Option {
	var lastAuthLog time.Time // callbacks run serially on NATS' callback queue
	opts := []nats.Option{
		nats.ErrorHandler(func(_ *nats.Conn, _ *nats.Subscription, err error) {
			if err == nil || ctx.Err() != nil {
				return
			}
			if errors.Is(err, nats.ErrAuthExpired) || errors.Is(err, nats.ErrAuthRevoked) || errors.Is(err, nats.ErrAuthorization) {
				if renewable {
					select {
					case kick <- struct{}{}:
					default:
					}
				}
				if time.Since(lastAuthLog) < time.Minute {
					return
				}
				lastAuthLog = time.Now()
			}
			fmt.Printf("%v\n", err)
		}),
		nats.DisconnectErrHandler(func(_ *nats.Conn, err error) {
			if ctx.Err() == nil {
				fmt.Printf("Fleet connection lost; reconnecting automatically: %v\n", err)
			}
		}),
		nats.ReconnectHandler(func(_ *nats.Conn) {
			lastAuthLog = time.Time{}
			fmt.Println("Fleet reconnected; services restored (node status updates on the next heartbeat).")
		}),
		nats.ClosedHandler(func(nc *nats.Conn) {
			if ctx.Err() == nil {
				fmt.Printf("Fleet connection closed: %v; stopping the disconnected runner.\n", nc.LastError())
				stop()
			}
		}),
	}
	if renewable {
		opts = append(opts, nats.IgnoreAuthErrorAbort())
	}
	return opts
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
