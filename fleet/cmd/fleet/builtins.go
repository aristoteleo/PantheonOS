package main

import (
	"context"
	"os"

	"github.com/aristoteleo/pantheon-fleet/internal/apps"
	"github.com/aristoteleo/pantheon-fleet/internal/appsvc"
	"github.com/aristoteleo/pantheon-fleet/internal/builtin/fmapp"
	"github.com/aristoteleo/pantheon-fleet/internal/builtin/shellapp"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

// builtinVersion is what the builtin services report from _ping.
const builtinVersion = "fleet-builtin/0.1"

// registerBuiltins wires the Go builtin Apps (§04c `builtin`) into the
// runner's supervisor. Each factory turns an app_start spec into a live bus
// service on the runner's own NATS connection — no subprocess, no python.
func registerBuiltins(r *runner.Runner, nc *nats.Conn) {
	prefix := os.Getenv("NATS_SUBJECT_PREFIX")

	r.Apps().RegisterBuiltin("shell", func(ctx context.Context, spec apps.Spec) (func(), error) {
		app := shellapp.NewApp(spec.Dir)
		svc := appsvc.New(nc, spec.ServiceID, "shell",
			"Shell toolset for running shell commands (Go builtin).",
			builtinVersion, prefix)
		for _, t := range shellapp.Tools(app) {
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

	r.Apps().RegisterBuiltin("file-manager", func(ctx context.Context, spec apps.Spec) (func(), error) {
		app := fmapp.NewApp(spec.Dir)
		svc := appsvc.New(nc, spec.ServiceID, "file_manager",
			"Workspace file operations (Go builtin, fs core).",
			builtinVersion, prefix)
		for _, t := range fmapp.Tools(app) {
			svc.Register(t)
		}
		if err := svc.Start(ctx); err != nil {
			return nil, err
		}
		return func() { svc.Stop(context.Background()) }, nil
	})

	registerPlatformBuiltins(r, nc, prefix)
}
