package main

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/join"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
	"github.com/nats-io/jwt/v2"
)

// Use the credential's actual expiry, not a client-side assumption about the
// controller's access TTL. Decode only to schedule renewal; NATS validates it.
func credentialRefreshDelay(creds []byte, now time.Time) time.Duration {
	encoded, err := jwt.ParseDecoratedJWT(creds)
	if err == nil {
		claims, err := jwt.DecodeUserClaims(encoded)
		if err == nil && claims.Expires > 0 {
			delay := time.Unix(claims.Expires, 0).Sub(now) * 3 / 4
			if delay > time.Second {
				return delay
			}
			return time.Second
		}
	}
	// Missing/unreadable credentials need prompt repair too.
	return time.Second
}

// runCredentialRefresh keeps transient failures on a short bounded backoff,
// separate from the much longer healthy renewal interval. Repeated auth errors
// cannot bypass that backoff or hammer the controller.
func runCredentialRefresh(ctx context.Context, kick <-chan struct{}, initial, minRetry, maxRetry time.Duration, refresh func() (time.Duration, error)) error {
	timer := time.NewTimer(initial)
	defer timer.Stop()
	var nextAllowed time.Time
	retry := minRetry
	failed := false
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-kick:
			if time.Now().Before(nextAllowed) {
				continue
			}
		case <-timer.C:
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		delay, err := refresh()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if errors.Is(err, join.ErrRevoked) {
			return err
		}
		if err != nil {
			failed = true
			delay = retry
			retry = min(retry*2, maxRetry)
			fmt.Printf("Credential refresh failed; retrying in %s: %v\n", delay, err)
			nextAllowed = time.Now().Add(delay)
		} else {
			if failed {
				fmt.Println("Fleet credentials renewed; resuming connection.")
			}
			failed = false
			retry = minRetry
			nextAllowed = time.Now().Add(minRetry)
		}
		if !timer.Stop() {
			select {
			case <-timer.C:
			default:
			}
		}
		timer.Reset(delay)
	}
}

func refreshCredsLoop(ctx context.Context, stop context.CancelFunc, kick <-chan struct{}, controllerURL, fleetID, refreshToken, nodePub string, nodeKey ed25519.PrivateKey, credsPath, stateDir string, persistedState fleetState, renewed func()) {
	creds, _ := os.ReadFile(credsPath)
	err := runCredentialRefresh(ctx, kick, credentialRefreshDelay(creds, time.Now()), 2*time.Second, 30*time.Second, func() (time.Duration, error) {
		ts := time.Now().Unix()
		sig := node.Sign(nodeKey, token.PoPChallenge(nodePub, fleetID, ts))
		out, err := join.Refresh(ctx, controllerURL, proto.TokenRequest{
			RefreshToken: refreshToken, TS: ts, Sig: sig,
		})
		if err != nil {
			return 0, err
		}
		// Keep rotated tokens in memory even if a disk write fails; a later retry
		// must use the current token. Persist it before publishing access creds.
		if out.RefreshToken != "" {
			refreshToken = out.RefreshToken
			persistedState, err = persistFleetStateRefreshToken(stateDir, persistedState, refreshToken)
			if err != nil {
				return 0, fmt.Errorf("save refreshed login: %w", err)
			}
		}
		if err := writePrivateFile(credsPath, []byte(out.Creds)); err != nil {
			return 0, fmt.Errorf("save refreshed credentials: %w", err)
		}
		if renewed != nil {
			renewed()
		}
		return credentialRefreshDelay([]byte(out.Creds), time.Now()), nil
	})
	if errors.Is(err, join.ErrRevoked) {
		fmt.Print("\nThis node has been revoked by its owner; stopping Fleet. Get a fresh command from Add node to rejoin.\n")
		stop()
	}
}
