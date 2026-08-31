//go:build !windows

package main

import (
	"context"

	ptyapp "github.com/aristoteleo/pantheon-apps/pty"
	"github.com/aristoteleo/pantheon-fleet/appsvc"
	"github.com/aristoteleo/pantheon-fleet/internal/apps"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

// registerPlatformBuiltins adds the builtins that need a Unix kernel — pty
// sessions have no Windows implementation yet.
func registerPlatformBuiltins(r *runner.Runner, nc *nats.Conn, prefix string) {
	r.Apps().RegisterBuiltin("pty", func(ctx context.Context, spec apps.Spec) (func(), error) {
		app := ptyapp.NewApp(nc, prefix, spec.Dir)
		svc := appsvc.New(nc, spec.ServiceID, "pty",
			"Pseudo-terminal sessions for a terminal UI (Go builtin).",
			builtinVersion, prefix)
		for _, t := range ptyapp.Tools(app) {
			svc.Register(t)
		}
		if err := svc.Start(ctx); err != nil {
			return nil, err
		}
		return func() {
			svc.Stop(context.Background())
			app.Close()
		}, nil
	})
}
