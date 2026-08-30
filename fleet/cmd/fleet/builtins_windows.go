//go:build windows

package main

import (
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

// registerPlatformBuiltins: pty has no Windows implementation (no pty(7));
// a ConPTY port can land here later.
func registerPlatformBuiltins(_ *runner.Runner, _ *nats.Conn, _ string) {}
