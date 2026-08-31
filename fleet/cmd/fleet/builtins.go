package main

import (
	"context"
	"os"
	"strings"

	shellapp "github.com/aristoteleo/pantheon-apps/shell"
	"github.com/aristoteleo/pantheon-fleet/appsvc"
	"github.com/aristoteleo/pantheon-fleet/internal/apps"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

// builtinVersion is what the builtin services report from _ping.
const builtinVersion = "fleet-builtin/0.1"

// builtinConn dials the service-plane NATS named in the spec's env — the
// same coordinates every apphost child gets — so a builtin's tool face
// lives on the bus its CALLERS dial. In production topology the runner's
// own connection is the fleet bus (a different cluster, with node-scoped
// credentials that cannot even subscribe service subjects); registering
// there produced "healthy" instances nobody could reach. In local dev the
// two buses are one server and the spec carries no coordinates, so the
// runner's connection serves. The bool says whether the caller owns (and
// must close) the returned connection.
func builtinConn(spec apps.Spec, runnerNc *nats.Conn) (*nats.Conn, bool, error) {
	servers := spec.Env["NATS_SERVERS"]
	if servers == "" {
		return runnerNc, false, nil
	}
	// The Python side separates servers with "|"; nats.go wants ",".
	urls := strings.ReplaceAll(servers, "|", ",")
	opts := []nats.Option{nats.Name("builtin-" + spec.AppID)}
	if tok := spec.Env["NATS_TOKEN"]; tok != "" {
		opts = append(opts, nats.UserInfo("agent", tok))
	} else if jwt, seed := spec.Env["NATS_JWT"], spec.Env["NATS_SEED"]; jwt != "" && seed != "" {
		opts = append(opts, nats.UserJWTAndSeed(jwt, seed))
	}
	nc, err := nats.Connect(urls, opts...)
	if err != nil {
		return nil, false, err
	}
	return nc, true, nil
}

// builtinPrefix is the service-subject prefix for one instance: the spec's
// env wins (it travels with the service-plane coordinates), the runner's
// own env is the dev fallback.
func builtinPrefix(spec apps.Spec) string {
	if p := spec.Env["NATS_SUBJECT_PREFIX"]; p != "" {
		return p
	}
	return os.Getenv("NATS_SUBJECT_PREFIX")
}

// registerBuiltins wires the Go builtin Apps (§04c `builtin`) into the
// runner's supervisor. Each factory turns an app_start spec into a live bus
// service — no subprocess, no python.
func registerBuiltins(r *runner.Runner, nc *nats.Conn) {
	r.Apps().RegisterBuiltin("shell", func(ctx context.Context, spec apps.Spec) (func(), error) {
		svcNc, owned, err := builtinConn(spec, nc)
		if err != nil {
			return nil, err
		}
		app := shellapp.NewApp(spec.Dir)
		svc := appsvc.New(svcNc, spec.ServiceID, "shell",
			"Shell toolset for running shell commands (Go builtin).",
			builtinVersion, builtinPrefix(spec))
		tools, err := shellapp.Tools(app)
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

	registerPlatformBuiltins(r, nc)
}
