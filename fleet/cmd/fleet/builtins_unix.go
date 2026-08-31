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
func registerPlatformBuiltins(r *runner.Runner, nc *nats.Conn) {
	r.Apps().RegisterBuiltin("pty", func(ctx context.Context, spec apps.Spec) (func(), error) {
		svcNc, owned, err := builtinConn(spec, nc)
		if err != nil {
			return nil, err
		}
		prefix := builtinPrefix(spec)
		// The stream frames (pantheon.stream.pty_*) ride the same
		// service-plane connection the UI listens on.
		app := ptyapp.NewApp(svcNc, prefix, spec.Dir)
		svc := appsvc.New(svcNc, spec.ServiceID, "pty",
			"Pseudo-terminal sessions for a terminal UI (Go builtin).",
			builtinVersion, prefix)
		tools, err := ptyapp.Tools(app)
		if err == nil {
			for _, t := range tools {
				svc.Register(t)
			}
			err = svc.Start(ctx)
		}
		if err != nil {
			app.Close()
			if owned {
				svcNc.Close()
			}
			return nil, err
		}
		return func() {
			svc.Stop(context.Background())
			app.Close()
			if owned {
				svcNc.Close()
			}
		}, nil
	})
}
